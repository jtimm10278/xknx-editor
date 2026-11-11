"""The dry-run download: the real download against a recorder, nothing sent anywhere."""

from __future__ import annotations

import asyncio
import base64
import socket
import struct
import zlib
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.telegram import IndividualAddress
from xknx.telegram.apci import (
    APCI,
    AuthorizeRequest,
    DeviceDescriptorRead,
    FunctionPropertyCommand,
    GroupValueWrite,
    MemoryExtendedRead,
    MemoryExtendedWrite,
    MemoryRead,
    MemoryWrite,
    PropertyDescriptionRead,
    PropertyValueRead,
    PropertyValueWrite,
    Restart,
    RestartMasterReset,
    UserMemoryRead,
    UserMemoryWrite,
)

from xknxeditor.download import dry_run
from xknxeditor.download.dry_run import (
    DryRunRefused,
    FrameKind,
    NoBus,
    Recorder,
    RecordingConnection,
    SimulatedDevice,
    compare_with_stored,
    dry_run_download,
    frame_kind,
)
from xknxeditor.download.factory_image import (
    FactoryImage,
    FactoryLocation,
    FactorySegment,
    parse_factory_image,
)
from xknxeditor.download.load_state import LoadState
from xknxeditor.download.scope import DownloadScope
from xknxeditor.download.session import (
    XknxConnectionManager,
    management_session,
    recording_sessions,
)
from xknxeditor.prod import MasterData

from .test_wire_audit import capture_application, capture_master


def _master() -> MasterData:
    return cast(MasterData, SimpleNamespace(raw=capture_master()))


def _run(scope: DownloadScope = DownloadScope.FULL, **kw: Any) -> dry_run.DryRunResult:
    app, image = capture_application()
    return asyncio.run(
        dry_run_download(
            "1.1.5",
            app,
            master=_master(),
            image=image,
            scope=scope,
            max_apdu_length=15,
            **kw,
        )
    )


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Fail any socket use: a dry run must never open one."""
    tried: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        tried.append(args)
        raise AssertionError("a dry run opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)
    return tried


# --- a whole download ---------------------------------------------------------------------------


def test_full_dry_run_records_the_whole_download(no_network: list[object]) -> None:
    result = _run()
    assert result.error == ""
    assert no_network == []
    kinds = [f.kind for f in result.frames]
    assert kinds[0] is FrameKind.CONNECT and kinds[-1] is FrameKind.DISCONNECT
    assert FrameKind.RESTART in kinds
    assert [f.index for f in result.frames] == list(range(1, len(result.frames) + 1))
    assert result.count(FrameKind.MEMORY_WRITE) > 0
    assert result.writes == sum(1 for f in result.frames if f.writes)
    assert result.bytes_written == sum(len(d) for _, d in result.memory_written)
    # Writes into the segments the device allocates name their object and offset.
    tables = {f.region for f in result.frames if f.kind is FrameKind.MEMORY_WRITE}
    assert {"address table", "association table", "group object table"} <= tables
    relative = [f for f in result.frames if f.segment_object is not None]
    assert relative and all(f.segment_offset is not None for f in relative)
    restart = next(f for f in result.frames if f.kind is FrameKind.RESTART)
    assert not restart.answered and restart.writes
    assert not result.comparison.has_stored_image


def test_partial_dry_run_goes_into_a_device_with_the_application() -> None:
    full = _run()
    partial = _run(DownloadScope.PARAMETERS)
    assert partial.error == ""
    assert partial.scope is DownloadScope.PARAMETERS
    # Only the partial download's own frames are kept, numbered from one connection on.
    assert partial.frames[0].index == 1 and partial.frames[0].connection == 1
    assert len(partial.frames) < len(full.frames)


def test_unload_and_secure_sessions_are_refused() -> None:
    with pytest.raises(DryRunRefused, match="unload"):
        _run(DownloadScope.UNLOAD)
    recorder = Recorder(SimulatedDevice(0x07B0))
    with pytest.raises(DryRunRefused, match="Data Secure"):
        recorder.session(IndividualAddress("1.1.5"), cast(Any, object()))


def test_a_download_that_stops_keeps_its_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(self: SimulatedDevice, index: int, data: bytes) -> None:
        return None  # never allocates: the table reference stays zero

    monkeypatch.setattr(SimulatedDevice, "_allocate", broken)
    result = _run()
    assert "zero table reference" in result.error
    assert result.frames and result.frames[0].kind is FrameKind.CONNECT


def test_no_bus_refuses_every_use() -> None:
    no_bus = NoBus()
    with pytest.raises(DryRunRefused, match=r"xknx\.management"):
        _ = no_bus.management  # type: ignore[attr-defined]


def test_management_sessions_come_from_the_recorder_only_inside_a_dry_run() -> None:
    recorder = Recorder(SimulatedDevice(0x07B0))
    address = IndividualAddress("1.1.5")
    xknx = cast(XKNX, object())
    assert isinstance(management_session(xknx, address, None), XknxConnectionManager)
    token = recording_sessions.set(recorder.session)
    try:
        session = management_session(cast(XKNX, NoBus()), address, None)
        assert isinstance(session, dry_run.RecordingSession)
    finally:
        recording_sessions.reset(token)
    assert recording_sessions.get() is None


async def test_recording_session_records_connects_once() -> None:
    recorder = Recorder(SimulatedDevice(0x07B0))
    session = dry_run.RecordingSession(recorder)
    await session.close()  # nothing open: nothing recorded
    connection = await session.open()
    await connection.send_data(Restart(), wait_for_ack=False)
    await session.close()
    await session.close()
    assert [f.kind for f in recorder.frames] == [
        FrameKind.CONNECT,
        FrameKind.RESTART,
        FrameKind.DISCONNECT,
    ]


# --- the simulated device -----------------------------------------------------------------------


async def test_simulated_device_answers_and_records() -> None:
    device = SimulatedDevice(0x0705, {1: 1})
    recorder = Recorder(device)
    connection = RecordingConnection(recorder)

    async def ask(payload: APCI) -> APCI:
        return (await connection.request(payload, None)).payload

    await ask(MemoryWrite(address=0x0200, count=2, data=b"\x01\x02"))
    assert cast(Any, await ask(MemoryRead(address=0x0200, count=2))).data == b"\x01\x02"
    await ask(UserMemoryWrite(address=0x10, count=1, data=b"\x09"))
    assert cast(Any, await ask(UserMemoryRead(address=0x10, count=1))).data == b"\x09"
    await ask(MemoryExtendedWrite(address=0x10000, data=b"\x05"))
    read = await ask(MemoryExtendedRead(address=0x10000, count=1))
    assert cast(Any, read).data == b"\x05"
    assert cast(Any, await ask(DeviceDescriptorRead(descriptor=0))).value == 0x0705
    assert cast(Any, await ask(AuthorizeRequest(key=0xFFFFFFFF))).level == 0
    typed = await ask(
        PropertyValueRead(object_index=1, property_id=1, count=1, start_index=1)
    )
    assert cast(Any, typed).data == b"\x00\x01"
    await ask(
        PropertyValueWrite(
            object_index=3, property_id=13, count=1, start_index=1, data=b"\xaa\xbb"
        )
    )
    description = await ask(PropertyDescriptionRead(object_index=3, property_id=13))
    assert cast(Any, description).type_ == 18  # element size 2
    state = await ask(
        FunctionPropertyCommand(object_index=0, property_id=55, data=b"\x01")
    )
    assert cast(Any, state).data == b"\x01"
    assert (
        cast(
            Any, await ask(RestartMasterReset(erase_code=1, channel_number=0))
        ).error_code
        == 0
    )
    assert [f.kind for f in recorder.frames] == [
        FrameKind.MEMORY_WRITE,
        FrameKind.MEMORY_READ,
        FrameKind.USER_MEMORY_WRITE,
        FrameKind.USER_MEMORY_READ,
        FrameKind.MEMORY_WRITE,
        FrameKind.MEMORY_READ,
        FrameKind.DEVICE_DESCRIPTOR_READ,
        FrameKind.AUTHORIZE,
        FrameKind.PROPERTY_READ,
        FrameKind.PROPERTY_WRITE,
        FrameKind.PROPERTY_DESCRIPTION_READ,
        FrameKind.FUNCTION_PROPERTY,
        FrameKind.MASTER_RESET,
    ]
    assert (
        recorder.frames[0].data == b"\x01\x02" and recorder.frames[0].address == 0x200
    )
    with pytest.raises(DryRunRefused, match="does not know"):
        frame_kind(GroupValueWrite(cast(Any, None)))
    with pytest.raises(DryRunRefused, match="answered"):
        await connection.request(MemoryRead(address=0, count=1), PropertyValueRead)


def test_simulated_device_allocates_and_reports_loads() -> None:
    device = SimulatedDevice(0x07B0, {4: 3})
    write = PropertyValueWrite
    device.write_property(
        write(
            object_index=4,
            property_id=5,
            count=1,
            start_index=1,
            data=b"\x03\x0b\x00\x00\x00\x04\x01\xee",
        )
    )
    start, size = device.allocations[4]
    assert size == 4 and device.read_memory(start, 4) == b"\xee" * 4
    assert device.load_states[4] is LoadState.LOADING
    assert device.relative(start + 2) == (4, 2) and device.relative(start + 4) is None
    assert device.object_name(4) == "application program"
    assert device.object_name(7) == "object 7"
    # Allocating again keeps the segment where it is.
    device.write_property(
        write(
            object_index=4,
            property_id=5,
            count=1,
            start_index=1,
            data=b"\x03\x0b\x00\x00\x00\x04\x00\x00",
        )
    )
    assert device.allocations[4][0] == start
    device.write_property(
        write(object_index=4, property_id=5, count=1, start_index=1, data=b"\x02")
    )
    assert device.load_states[4] is LoadState.LOADED
    assert device.properties[(4, 27)][:4] == b"\x00\x00\x00\x04"
    reference = device.read_property(
        PropertyValueRead(object_index=4, property_id=7, count=1, start_index=1)
    )
    assert reference.data == start.to_bytes(4, "big")


def test_simulated_device_m112_load_control_in_memory() -> None:
    device = SimulatedDevice(0x0701)
    device.write_memory(0x0104, b"\x11")  # LSM 1, start loading
    assert device.load_states[1] is LoadState.LOADING
    assert device.read_memory(0xB6EA, 1) == bytes([LoadState.LOADING])


# --- comparing with the stored image ------------------------------------------------------------


def _segment(
    location: FactoryLocation,
    address: int,
    data: bytes,
    mask: bytes | None = None,
    ref: int = 0,
    pid: int = 0,
) -> FactorySegment:
    return FactorySegment(location, ref, pid, address, len(data), data, mask)


def _frame(kind: FrameKind, **kw: Any) -> dry_run.RecordedFrame:
    return dry_run.RecordedFrame(
        index=1, connection=1, kind=kind, service=kind.value, answered=True, **kw
    )


def test_compare_with_the_stored_image() -> None:
    stored = FactoryImage(
        (
            _segment(FactoryLocation.MEMORY, 0x100, b"\x01\x02\x03\x04"),
            # Only the low nibble of the second byte counts.
            _segment(FactoryLocation.MEMORY, 0x200, b"\x00\x0f", b"\xff\x0f"),
            _segment(FactoryLocation.USER_MEMORY, 0x10, b"\x07"),
            _segment(FactoryLocation.RELATIVE_MEMORY, 4, b"\xaa\xbb", ref=4),
            _segment(FactoryLocation.PROPERTY, 0, b"\x00\x11", ref=3, pid=13),
            _segment(FactoryLocation.APP_PROPERTY, 0, b"\x01", ref=0, pid=60),
        )
    )
    frames = [
        _frame(FrameKind.MEMORY_WRITE, address=0x100, data=b"\x01\x09\x09\x04"),
        _frame(FrameKind.MEMORY_WRITE, address=0x200, data=b"\x00\xff"),
        _frame(FrameKind.MEMORY_WRITE, address=0x300, data=b"\x01"),
        _frame(FrameKind.USER_MEMORY_WRITE, address=0x10, data=b"\x08"),
        _frame(
            FrameKind.MEMORY_WRITE,
            address=0x4004,
            data=b"\xaa\xbc",
            segment_object=4,
            segment_offset=4,
        ),
        _frame(
            FrameKind.PROPERTY_WRITE, object_index=3, property_id=13, data=b"\x00\x12"
        ),
        _frame(FrameKind.PROPERTY_WRITE, object_index=3, property_id=5, data=b"\x01"),
        _frame(FrameKind.PROPERTY_WRITE, object_index=9, property_id=1, data=b"\x01"),
    ]
    comparison = compare_with_stored(frames, stored, None, {4: "application program"})
    assert comparison.has_stored_image
    assert comparison.compared_bytes == 4 + 2 + 1 + 2
    assert comparison.differing_bytes == 2 + 1 + 1
    assert comparison.not_in_stored_image == 1
    by_address = {(d.region, d.address): d for d in comparison.differences}
    middle = by_address[("", 0x101)]
    assert (middle.written, middle.stored) == (b"\x09\x09", b"\x02\x03")
    relative = by_address[("application program", 5)]
    assert relative.segment_object == 4 and relative.written == b"\xbc"
    assert comparison.differing_by_region == {"": 3, "application program": 1}
    assert comparison.compared_properties == 1
    [prop] = comparison.property_differences
    assert (prop.object_index, prop.property_id, prop.written) == (3, 13, b"\x00\x12")
    assert comparison.not_comparable_segments == 1
    assert not compare_with_stored(frames, None).has_stored_image


def test_a_stored_image_seeds_the_simulated_device_and_is_compared() -> None:
    body = struct.pack("<Ii", 0x00010001, 1) + struct.pack("<IHHII", 1, 0, 0, 0x100, 2)
    body += b"\x01" + b"\x05\x06"
    compressor = zlib.compressobj(wbits=-15)
    raw = compressor.compress(body) + compressor.flush()
    stored = parse_factory_image(raw)
    assert stored is not None
    device = SimulatedDevice(0x0705, stored=stored)
    assert device.read_memory(0x100, 2) == b"\x05\x06"
    assert base64.b64encode(raw)  # the form a project keeps it in


def test_regions_name_tables_and_segments() -> None:
    app, _ = capture_application()
    regions = dry_run._Regions(app)  # pyright: ignore[reportPrivateUsage]
    assert regions.name(0x0104) == "load control"
    assert regions.name(0xFFFFFF) == "other memory"
    empty = dry_run._Regions(cast(Any, SimpleNamespace()))  # pyright: ignore[reportPrivateUsage]
    assert empty.name(0x4000) == "other memory"
    assert dry_run.mask_descriptor(app) == 0x07B0
    weird = SimpleNamespace(program=SimpleNamespace(mask_version="MV-xyz"))
    assert dry_run.mask_descriptor(cast(Any, weird)) == 0

"""Dry-run download: run the real download preparation and load procedure against a recorder.

:func:`dry_run_download` calls :func:`.download.download` exactly as a real download does, but
every management session comes from a :class:`RecordingSession` (through
:data:`.session.recording_sessions`), and the ``xknx`` it is handed is :class:`NoBus`, which
refuses every use. Nothing can reach a bus: no interface, socket or ``xknx`` object exists in a
dry run. The recorder answers like a device (:class:`SimulatedDevice`: a flat memory, interface
object properties and load states, seeded from the device's stored image when there is one) and
keeps every frame the download would send, in order.

The result lists the frames (connects, memory and property writes and reads, restarts, …), the
memory the download would leave written, and a comparison of the bytes and properties it would
write against the stored image of the device's last download (``DeviceInstance/@LoadedImage``,
the same format as a product's factory image).

Downloads that need the bus outside a management session are refused: commissioning a device
without an address, an unload, waiting for programming mode and KNX Data Secure sessions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, NoReturn, cast

from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.apci import (
    APCI,
    AuthorizeRequest,
    AuthorizeResponse,
    DeviceDescriptorRead,
    DeviceDescriptorResponse,
    FunctionPropertyCommand,
    FunctionPropertyStateRead,
    FunctionPropertyStateResponse,
    MemoryExtendedRead,
    MemoryExtendedReadResponse,
    MemoryExtendedWrite,
    MemoryExtendedWriteResponse,
    MemoryRead,
    MemoryResponse,
    MemoryWrite,
    PropertyDescriptionRead,
    PropertyDescriptionResponse,
    PropertyValueRead,
    PropertyValueResponse,
    PropertyValueWrite,
    Restart,
    RestartMasterReset,
    RestartMasterResetResponse,
    UserMemoryRead,
    UserMemoryResponse,
    UserMemoryWrite,
)

from .crc import segment_crc
from .errors import DownloadError
from .factory_image import FactoryImage, FactoryLocation
from .load_state import PID_LOAD_STATE_CONTROL, LoadEvent, LoadState
from .programmer import PID_OBJECT_TYPE, PID_TABLE_REFERENCE
from .scope import DownloadScope
from .session import recording_sessions

if TYPE_CHECKING:
    from xknx import XKNX

    from xknxeditor.prod import Application, MasterData

    from .data_secure import DeviceSecurity
    from .image import DownloadImage, GroupCommunication
    from .programmer import BusConnection, ConnectionManager
    from .project_data import SeedDevice

# BIM M112 shared load-control record and LSM-1 status (MV-0701/MV-0705).
_M112_CONTROL = 0x0104
_M112_STATUS_LSM1 = 0xB6EA
_M112_STATUS_COUNT = 8
_LOAD_TRANSITIONS = {
    LoadEvent.START_LOADING: LoadState.LOADING,
    LoadEvent.LOAD_COMPLETE: LoadState.LOADED,
    LoadEvent.UNLOAD: LoadState.UNLOADED,
    LoadEvent.ADDITIONAL: LoadState.LOADING,
}
_REFUSED_SCOPES = (DownloadScope.UNLOAD, DownloadScope.UNLOAD_ALL)
# The simulated device's address in the telegrams it answers with.
_DEVICE = IndividualAddress("15.15.255")
# Where the simulated device allocates the segments a System B load procedure asks for (a real
# device picks its own addresses; relative memory is compared by its offset in the segment).
_ALLOCATION_START = 0x4000
_PID_MCB = 27
_PID_VERIFY_MODE = 14
# Interface object types whose memory a dry run names.
_OBJECT_NAMES = {
    1: "address table",
    2: "association table",
    3: "application program",
    4: "PEI program",
    9: "group object table",
}
_ALLOCATE = b"\x03\x0b"  # load event "additional load control", allocate a segment


class DryRunRefused(DownloadError):
    """The dry run cannot run this download without the bus (or something tried to use it)."""


class NoBus:
    """Stands in for ``xknx`` in a dry run: every use of it is refused."""

    def __getattr__(self, name: str) -> NoReturn:
        raise DryRunRefused(f"a dry run never uses the bus (tried xknx.{name})")


class FrameKind(Enum):
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    MEMORY_WRITE = "memory_write"
    MEMORY_READ = "memory_read"
    USER_MEMORY_WRITE = "user_memory_write"
    USER_MEMORY_READ = "user_memory_read"
    PROPERTY_WRITE = "property_write"
    PROPERTY_READ = "property_read"
    PROPERTY_DESCRIPTION_READ = "property_description_read"
    FUNCTION_PROPERTY = "function_property"
    DEVICE_DESCRIPTOR_READ = "device_descriptor_read"
    AUTHORIZE = "authorize"
    RESTART = "restart"
    MASTER_RESET = "master_reset"


_WRITES = (
    FrameKind.MEMORY_WRITE,
    FrameKind.USER_MEMORY_WRITE,
    FrameKind.PROPERTY_WRITE,
    FrameKind.FUNCTION_PROPERTY,
    FrameKind.RESTART,
    FrameKind.MASTER_RESET,
)


@dataclass(frozen=True, slots=True)
class RecordedFrame:
    """One frame the download would send (or a connect/disconnect of its session)."""

    index: int  # 1-based, in sending order
    connection: int  # which session (1-based); connects and disconnects included
    kind: FrameKind
    service: str  # the APCI service, e.g. "MemoryWrite"
    answered: bool  # the download waits for an answer to it
    address: int | None = None  # memory address
    count: int | None = None  # octets read, or property elements
    object_index: int | None = None
    property_id: int | None = None
    start_index: int | None = None
    data: bytes = b""
    region: str = ""  # what the memory written belongs to (see dry_run_download)
    # For memory in a segment the device allocated (System B): its object and the offset.
    segment_object: int | None = None
    segment_offset: int | None = None

    @property
    def writes(self) -> bool:
        """Whether the frame changes the device."""
        return self.kind in _WRITES


@dataclass(frozen=True, slots=True)
class ByteRange:
    """Consecutive bytes whose written and stored values differ."""

    address: int
    written: bytes
    stored: bytes
    region: str = ""
    # Set for memory in a segment the device allocates: ``address`` is the offset in it.
    segment_object: int | None = None


@dataclass(frozen=True, slots=True)
class PropertyDifference:
    object_index: int
    property_id: int
    written: bytes
    stored: bytes


@dataclass(slots=True)
class ImageComparison:
    """The bytes and properties a dry run would write against the device's stored image."""

    has_stored_image: bool
    compared_bytes: int = 0
    differing_bytes: int = 0
    # Written bytes the stored image holds nothing for.
    not_in_stored_image: int = 0
    differences: list[ByteRange] = field(default_factory=list[ByteRange])
    compared_properties: int = 0
    property_differences: list[PropertyDifference] = field(
        default_factory=list[PropertyDifference]
    )
    # Stored segments that cannot be compared to absolute writes (relative memory, properties
    # of objects addressed by type or application index).
    not_comparable_segments: int = 0

    @property
    def differing_by_region(self) -> dict[str, int]:
        """The differing bytes per region of memory."""
        out: dict[str, int] = {}
        for diff in self.differences:
            out[diff.region] = out.get(diff.region, 0) + len(diff.written)
        return out


@dataclass(slots=True)
class DryRunResult:
    frames: list[RecordedFrame]
    comparison: ImageComparison
    scope: DownloadScope
    # Memory the download would leave written: (address, data) runs.
    memory_written: list[tuple[int, bytes]]
    # Why the download stopped before its end ("" when it ran through); the frames up to there
    # are kept.
    error: str = ""

    @property
    def writes(self) -> int:
        return sum(1 for f in self.frames if f.writes)

    @property
    def bytes_written(self) -> int:
        return sum(len(data) for _, data in self.memory_written)

    def count(self, kind: FrameKind) -> int:
        return sum(1 for f in self.frames if f.kind is kind)


class SimulatedDevice:
    """Answers the download like a device would: a flat memory, interface object properties,
    per-object load states and the device descriptor. Nothing here touches a bus."""

    def __init__(
        self,
        descriptor: int,
        object_types: Mapping[int, int] | None = None,
        stored: FactoryImage | None = None,
    ) -> None:
        self.descriptor = descriptor
        self.object_types = dict(object_types or {})
        self.memory: dict[int, int] = {}
        self.user_memory: dict[int, int] = {}
        self.properties: dict[tuple[int, int], bytes] = {}
        self.element_sizes: dict[tuple[int, int], int] = {}
        self.load_states: dict[int, LoadState] = {}
        self.function_properties: dict[tuple[int, int], bytes] = {}
        # Object index -> (start address, size) of the segment allocated for it.
        self.allocations: dict[int, tuple[int, int]] = {}
        self._next_allocation = _ALLOCATION_START
        if stored is not None:
            self._seed(stored)

    def _seed(self, stored: FactoryImage) -> None:
        for segment in stored.segments:
            if segment.data is None:
                continue
            if segment.location is FactoryLocation.MEMORY:
                for i, byte in enumerate(segment.data):
                    self.memory[segment.address + i] = byte
            elif segment.location is FactoryLocation.USER_MEMORY:
                for i, byte in enumerate(segment.data):
                    self.user_memory[segment.address + i] = byte
            elif segment.location is FactoryLocation.PROPERTY and segment.offset == 0:
                self.properties[(segment.object_ref, segment.property_id)] = (
                    segment.data
                )

    def answer(self, payload: APCI) -> APCI:
        """The device's answer to ``payload`` (after applying what it writes)."""
        if isinstance(payload, MemoryWrite):
            self.write_memory(payload.address, payload.data)
            return MemoryResponse(address=payload.address, data=payload.data)
        if isinstance(payload, UserMemoryWrite):
            self._write(self.user_memory, payload.address, payload.data)
            return UserMemoryResponse(address=payload.address, data=payload.data)
        if isinstance(payload, MemoryExtendedWrite):
            self.write_memory(payload.address, payload.data)
            return MemoryExtendedWriteResponse(
                return_code=0, address=payload.address, confirmation_data=b""
            )
        if isinstance(payload, MemoryRead):
            return MemoryResponse(
                address=payload.address,
                data=self.read_memory(payload.address, payload.count),
            )
        if isinstance(payload, MemoryExtendedRead):
            return MemoryExtendedReadResponse(
                return_code=0,
                address=payload.address,
                data=self.read_memory(payload.address, payload.count),
            )
        if isinstance(payload, UserMemoryRead):
            data = bytes(
                self.user_memory.get(payload.address + i, 0)
                for i in range(payload.count)
            )
            return UserMemoryResponse(address=payload.address, data=data)
        if isinstance(payload, PropertyValueWrite):
            self.write_property(payload)
            return self.read_property(
                PropertyValueRead(
                    object_index=payload.object_index,
                    property_id=payload.property_id,
                    count=payload.count,
                    start_index=payload.start_index,
                )
            )
        if isinstance(payload, PropertyValueRead):
            return self.read_property(payload)
        if isinstance(payload, PropertyDescriptionRead):
            key = (payload.object_index, payload.property_id)
            width = (
                self.element_sizes.get(key) or len(self.properties.get(key, b"")) or 1
            )
            return PropertyDescriptionResponse(
                object_index=payload.object_index,
                property_id=payload.property_id,
                type_=16 + width,
                max_count=100,
            )
        if isinstance(payload, DeviceDescriptorRead):
            return DeviceDescriptorResponse(descriptor=0, value=self.descriptor)
        if isinstance(payload, AuthorizeRequest):
            return AuthorizeResponse(level=0)
        if isinstance(payload, FunctionPropertyCommand):
            self.function_properties[(payload.object_index, payload.property_id)] = (
                payload.data
            )
            return self._function_state(payload)
        if isinstance(payload, FunctionPropertyStateRead):
            return self._function_state(payload)
        if isinstance(payload, RestartMasterReset):
            return RestartMasterResetResponse(error_code=0, process_time=0)
        raise DryRunRefused(
            f"the simulated device has no answer to {type(payload).__name__}"
        )

    @staticmethod
    def _write(memory: dict[int, int], address: int, data: bytes) -> None:
        for index, byte in enumerate(data):
            memory[address + index] = byte

    def write_memory(self, address: int, data: bytes) -> None:
        """Write memory, interpreting a BIM M112 load-control record."""
        self._write(self.memory, address, data)
        if address != _M112_CONTROL or not data:
            return
        try:
            state = _LOAD_TRANSITIONS[LoadEvent(data[0] & 0x0F)]
        except (ValueError, KeyError):
            return
        self.load_states[data[0] >> 4] = state

    def read_memory(self, address: int, count: int) -> bytes:
        """Read memory, serving the M112 load-status bytes from the load states."""
        out = bytearray()
        for loc in range(address, address + count):
            index = loc - _M112_STATUS_LSM1 + 1
            if 1 <= index <= _M112_STATUS_COUNT and index in self.load_states:
                out.append(self.load_states[index])
            else:
                out.append(self.memory.get(loc, 0))
        return bytes(out)

    def object_name(self, index: int) -> str:
        """What the object at ``index`` holds, for naming its memory."""
        name = _OBJECT_NAMES.get(self.object_types.get(index, -1))
        return name if name is not None else f"object {index}"

    def relative(self, address: int) -> tuple[int, int] | None:
        """The (object index, offset) of an address inside an allocated segment."""
        for index, (start, size) in self.allocations.items():
            if start <= address < start + size:
                return index, address - start
        return None

    def _allocate(self, index: int, data: bytes) -> None:
        """Allocate the segment a System B "additional load control" asks for (size, then a
        fill flag and the fill octet), and point the object's table reference at it."""
        if len(data) < 8:
            return
        size = int.from_bytes(data[2:6], "big")
        previous = self.allocations.get(index)
        if previous is not None and previous[1] >= size:
            start = previous[0]  # the segment stays where it is
        else:
            start = self._next_allocation
            self._next_allocation = (start + size + 0xF) & ~0xF
        self.allocations[index] = (start, size)
        width = 4 if self.descriptor == 0x07B0 else 2
        self.properties[(index, PID_TABLE_REFERENCE)] = start.to_bytes(width, "big")
        if data[6] & 1:
            self._write(self.memory, start, bytes([data[7]]) * size)

    def _load_completed(self, index: int) -> None:
        """A completed load: the object's memory control block holds its segment's size and
        CRC, as a System B device reports it."""
        allocation = self.allocations.get(index)
        if allocation is None:
            return
        start, size = allocation
        actual = bytes(self.memory.get(start + n, 0) for n in range(size))
        self.properties[(index, _PID_MCB)] = (
            size.to_bytes(4, "big")
            + bytes([0, 0x32 if index == 4 else 0x33])
            + segment_crc(actual).to_bytes(2, "big")
        )

    def write_property(self, payload: PropertyValueWrite) -> None:
        if payload.property_id == PID_LOAD_STATE_CONTROL and payload.data:
            if payload.data[:2] == _ALLOCATE:
                self._allocate(payload.object_index, payload.data)
            try:
                event = LoadEvent(payload.data[0])
                state = _LOAD_TRANSITIONS[event]
            except (ValueError, KeyError):
                return
            self.load_states[payload.object_index] = state
            if event is LoadEvent.LOAD_COMPLETE:
                self._load_completed(payload.object_index)
            return
        key = (payload.object_index, payload.property_id)
        width = len(payload.data) // max(1, payload.count)
        data = bytearray(self.properties.get(key, b""))
        start = (max(1, payload.start_index) - 1) * width
        end = start + len(payload.data)
        data.extend(bytes(max(0, end - len(data))))
        data[start:end] = payload.data
        self.properties[key] = bytes(data)
        self.element_sizes[key] = width

    def read_property(self, payload: PropertyValueRead) -> PropertyValueResponse:
        key = (payload.object_index, payload.property_id)
        if payload.property_id == PID_LOAD_STATE_CONTROL:
            data = bytes(
                [self.load_states.get(payload.object_index, LoadState.UNLOADED)]
            )
        elif payload.property_id == PID_OBJECT_TYPE:
            object_type = self.object_types.get(payload.object_index)
            data = object_type.to_bytes(2, "big") if object_type is not None else b""
        elif payload.property_id == PID_TABLE_REFERENCE and key not in self.properties:
            data = bytes(4 if self.descriptor == 0x07B0 else 2)
        elif key == (0, _PID_VERIFY_MODE) and key not in self.properties:
            data = b"\x00"  # device control: verify mode off
        else:
            data = self.properties.get(key, b"")
            width = self.element_sizes.get(key)
            if width is not None and payload.start_index > 0:
                start = (payload.start_index - 1) * width
                data = data[start : start + payload.count * width]
        return PropertyValueResponse(
            object_index=payload.object_index,
            property_id=payload.property_id,
            data=data,
            count=payload.count,
            start_index=payload.start_index,
        )

    def _function_state(
        self, payload: FunctionPropertyCommand | FunctionPropertyStateRead
    ) -> FunctionPropertyStateResponse:
        return FunctionPropertyStateResponse(
            object_index=payload.object_index,
            property_id=payload.property_id,
            return_code=0,
            data=self.function_properties.get(
                (payload.object_index, payload.property_id), b""
            ),
        )


class Recorder:
    """Keeps every frame of a dry run, in order, and hands out its sessions."""

    def __init__(
        self, device: SimulatedDevice, regions: _Regions | None = None
    ) -> None:
        self.device = device
        self.frames: list[RecordedFrame] = []
        self.connections = 0
        self._regions = regions

    def session(
        self, address: IndividualAddress, security: DeviceSecurity | None
    ) -> ConnectionManager:
        if security is not None:
            raise DryRunRefused("a dry run cannot run a KNX Data Secure session")
        return RecordingSession(self)

    def add(self, kind: FrameKind, payload: APCI | None, answered: bool) -> None:
        fields: dict[str, Any] = {}
        if payload is not None:
            for name in (
                "address",
                "count",
                "object_index",
                "property_id",
                "start_index",
            ):
                value = getattr(payload, name, None)
                if isinstance(value, int):
                    fields[name] = value
            data = getattr(payload, "data", None)
            if isinstance(data, bytes | bytearray):
                fields["data"] = bytes(data)
            if kind in (FrameKind.MEMORY_WRITE, FrameKind.MEMORY_READ):
                address = fields.get("address", -1)
                relative = self.device.relative(address)
                if relative is not None:
                    fields["segment_object"], fields["segment_offset"] = relative
                    fields["region"] = self.device.object_name(relative[0])
                elif self._regions is not None:
                    fields["region"] = self._regions.name(address)
        self.frames.append(
            RecordedFrame(
                index=len(self.frames) + 1,
                connection=self.connections,
                kind=kind,
                service=type(payload).__name__ if payload is not None else kind.value,
                answered=answered,
                **fields,
            )
        )


def frame_kind(payload: APCI) -> FrameKind:
    """The kind of frame ``payload`` is."""
    if isinstance(payload, MemoryWrite | MemoryExtendedWrite):
        return FrameKind.MEMORY_WRITE
    if isinstance(payload, MemoryRead | MemoryExtendedRead):
        return FrameKind.MEMORY_READ
    if isinstance(payload, UserMemoryWrite):
        return FrameKind.USER_MEMORY_WRITE
    if isinstance(payload, UserMemoryRead):
        return FrameKind.USER_MEMORY_READ
    if isinstance(payload, PropertyValueWrite):
        return FrameKind.PROPERTY_WRITE
    if isinstance(payload, PropertyValueRead):
        return FrameKind.PROPERTY_READ
    if isinstance(payload, PropertyDescriptionRead):
        return FrameKind.PROPERTY_DESCRIPTION_READ
    if isinstance(payload, FunctionPropertyCommand | FunctionPropertyStateRead):
        return FrameKind.FUNCTION_PROPERTY
    if isinstance(payload, DeviceDescriptorRead):
        return FrameKind.DEVICE_DESCRIPTOR_READ
    if isinstance(payload, AuthorizeRequest):
        return FrameKind.AUTHORIZE
    if isinstance(payload, RestartMasterReset):
        return FrameKind.MASTER_RESET
    if isinstance(payload, Restart):
        return FrameKind.RESTART
    raise DryRunRefused(
        f"the dry run does not know the service {type(payload).__name__}"
    )


class RecordingConnection:
    """A :class:`~.programmer.BusConnection` that records each payload and lets the simulated
    device answer it."""

    def __init__(self, recorder: Recorder) -> None:
        self._recorder = recorder

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        self._recorder.add(frame_kind(payload), payload, answered=False)
        if not isinstance(payload, Restart):
            self._recorder.device.answer(payload)

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        self._recorder.add(frame_kind(payload), payload, answered=True)
        answer = self._recorder.device.answer(payload)
        if expected is not None and not isinstance(answer, expected):
            raise DryRunRefused(
                f"the simulated device answered {type(answer).__name__}, "
                f"not {expected.__name__}"
            )
        return Telegram(destination_address=_DEVICE, payload=answer)


class RecordingSession:
    """A :class:`~.programmer.ConnectionManager` whose connections are recorded."""

    def __init__(self, recorder: Recorder) -> None:
        self._recorder = recorder
        self._open = False

    async def open(self) -> BusConnection:
        self._recorder.connections += 1
        self._open = True
        self._recorder.add(FrameKind.CONNECT, None, answered=True)
        return RecordingConnection(self._recorder)

    async def close(self) -> None:
        if not self._open:
            return
        self._open = False
        self._recorder.add(FrameKind.DISCONNECT, None, answered=False)


class _Regions:
    """Names the part of memory an address belongs to, from the application's tables and code
    segments."""

    def __init__(self, application: Application) -> None:
        self._spans: list[tuple[int, int, str]] = []
        program: object = getattr(application, "program", None)
        static: object = getattr(program, "static", None)
        code: object = getattr(static, "code", None)
        # Segment id -> (start address, size).
        segments: dict[str, tuple[int, int]] = {}
        for item in cast("list[object]", getattr(code, "absolute_segment", None) or []):
            segment_id = getattr(item, "id", None)
            address = getattr(item, "address", None)
            size = getattr(item, "size", None)
            if (
                isinstance(segment_id, str)
                and isinstance(address, int)
                and isinstance(size, int)
            ):
                segments[segment_id] = (address, size)
        for name, attribute in (
            ("address table", "address_table"),
            ("association table", "association_table"),
            ("group object table", "com_object_table"),
        ):
            table: object = getattr(static, attribute, None)
            segment_id = getattr(table, "code_segment", None)
            span = segments.get(segment_id) if isinstance(segment_id, str) else None
            if span is not None:
                offset = getattr(table, "offset", None)
                start = span[0] + (offset if isinstance(offset, int) else 0)
                self._spans.append((start, span[0] + span[1], name))
        for segment_id, (address, size) in segments.items():
            self._spans.append((address, address + size, f"segment {segment_id}"))
        self._spans.append((_M112_CONTROL, _M112_CONTROL + 1, "load control"))

    def name(self, address: int) -> str:
        for start, end, name in self._spans:
            if start <= address < end:
                return name
        return "other memory"


def _runs(memory: Mapping[int, int]) -> list[tuple[int, bytes]]:
    runs: list[tuple[int, bytearray]] = []
    for address in sorted(memory):
        if runs and runs[-1][0] + len(runs[-1][1]) == address:
            runs[-1][1].append(memory[address])
        else:
            runs.append((address, bytearray([memory[address]])))
    return [(a, bytes(d)) for a, d in runs]


def compare_with_stored(
    frames: list[RecordedFrame],
    stored: FactoryImage | None,
    regions: _Regions | None = None,
    object_names: Mapping[int, str] | None = None,
) -> ImageComparison:
    """The memory and properties ``frames`` write against the ``stored`` image (masked bytes
    compare only their mask's bits). The last write of a byte or property counts. Memory in a
    segment the device allocated is compared with the stored relative segment of its object, by
    offset (a difference then names the object; its ``address`` is the offset)."""
    if stored is None:
        return ImageComparison(has_stored_image=False)
    written: dict[int, int] = {}
    written_user: dict[int, int] = {}
    written_rel: dict[tuple[int, int], int] = {}
    properties: dict[tuple[int, int], bytes] = {}
    for frame in frames:
        if frame.kind is FrameKind.MEMORY_WRITE and frame.segment_object is not None:
            offset = frame.segment_offset or 0
            for i, byte in enumerate(frame.data):
                written_rel[(frame.segment_object, offset + i)] = byte
        elif frame.kind is FrameKind.MEMORY_WRITE and frame.address is not None:
            for i, byte in enumerate(frame.data):
                written[frame.address + i] = byte
        elif frame.kind is FrameKind.USER_MEMORY_WRITE and frame.address is not None:
            for i, byte in enumerate(frame.data):
                written_user[frame.address + i] = byte
        elif (
            frame.kind is FrameKind.PROPERTY_WRITE
            and frame.object_index is not None
            and frame.property_id is not None
            and frame.property_id != PID_LOAD_STATE_CONTROL
            and frame.start_index in (None, 1)
        ):
            properties[(frame.object_index, frame.property_id)] = frame.data
    stored_mem: dict[int, tuple[int, int]] = {}
    stored_user: dict[int, tuple[int, int]] = {}
    stored_props: dict[tuple[int, int], bytes] = {}
    stored_rel: dict[tuple[int, int], tuple[int, int]] = {}
    result = ImageComparison(has_stored_image=True)
    for segment in stored.segments:
        if segment.data is None:
            continue
        target = {
            FactoryLocation.MEMORY: stored_mem,
            FactoryLocation.USER_MEMORY: stored_user,
        }.get(segment.location)
        if target is not None:
            for i, byte in enumerate(segment.data):
                mask = segment.mask[i] if segment.mask is not None else 0xFF
                target[segment.address + i] = (byte, mask)
        elif segment.location is FactoryLocation.PROPERTY and segment.offset == 0:
            stored_props[(segment.object_ref, segment.property_id)] = segment.data
        elif segment.location is FactoryLocation.RELATIVE_MEMORY:
            for i, byte in enumerate(segment.data):
                mask = segment.mask[i] if segment.mask is not None else 0xFF
                stored_rel[(segment.object_ref, segment.offset + i)] = (byte, mask)
        else:
            result.not_comparable_segments += 1
    rel_diff: dict[tuple[int, int], tuple[int, int]] = {}
    for key, byte in written_rel.items():
        held = stored_rel.get(key)
        if held is None:
            result.not_in_stored_image += 1
            continue
        value, mask = held
        if mask == 0:
            continue
        result.compared_bytes += 1
        if (byte ^ value) & mask:
            rel_diff[key] = (byte, value)
    names = object_names or {}
    rel_run: list[tuple[int, int]] = []
    for key in [*sorted(rel_diff), None]:
        if rel_run and (key is None or key != (rel_run[-1][0], rel_run[-1][1] + 1)):
            index = rel_run[0][0]
            result.differences.append(
                ByteRange(
                    address=rel_run[0][1],
                    written=bytes(rel_diff[k][0] for k in rel_run),
                    stored=bytes(rel_diff[k][1] for k in rel_run),
                    region=names.get(index, f"object {index}"),
                    segment_object=index,
                )
            )
            rel_run = []
        if key is not None:
            rel_run.append(key)
    differing: dict[int, tuple[int, int]] = {}
    for memory, reference in ((written, stored_mem), (written_user, stored_user)):
        for address, byte in memory.items():
            held = reference.get(address)
            if held is None:
                result.not_in_stored_image += 1
                continue
            value, mask = held
            if mask == 0:
                continue
            result.compared_bytes += 1
            if (byte ^ value) & mask:
                differing[address] = (byte, value)
    result.differing_bytes = len(differing) + len(rel_diff)
    run: list[int] = []
    for address in [*sorted(differing), None]:
        if run and (address is None or address != run[-1] + 1):
            result.differences.append(
                ByteRange(
                    address=run[0],
                    written=bytes(differing[a][0] for a in run),
                    stored=bytes(differing[a][1] for a in run),
                    region=regions.name(run[0]) if regions is not None else "",
                )
            )
            run = []
        if address is not None:
            run.append(address)
    for key, data in properties.items():
        held = stored_props.get(key)
        if held is None:
            continue
        result.compared_properties += 1
        if held[: len(data)] != data:
            result.property_differences.append(
                PropertyDifference(key[0], key[1], data, held[: len(data)])
            )
    return result


def mask_descriptor(application: Application) -> int:
    """The device descriptor (mask version) the application is for, e.g. 0x07B0."""
    from .merge import device_mask_version

    text = device_mask_version(application)
    try:
        return int(text.removeprefix("MV-")[:4], 16)
    except ValueError:
        return 0


async def dry_run_download(
    individual_address: str | IndividualAddress,
    application: Application,
    *,
    master: MasterData | None = None,
    device: SeedDevice | None = None,
    image: DownloadImage | None = None,
    group_communication: GroupCommunication | None = None,
    scope: DownloadScope = DownloadScope.FULL,
    parameter_values: Mapping[str, str] | None = None,
    stored_image: FactoryImage | None = None,
    application2: Application | None = None,
    access_key: int | None = None,
    max_apdu_length: int | None = None,
) -> DryRunResult:
    """Run the download of ``application`` for ``individual_address`` against a recorder and
    return every frame it would send, the memory it would write and how that compares with
    ``stored_image`` (the device's last-download image), writing nothing anywhere.

    The arguments are those of :func:`.download.download`; ``stored_image`` also seeds the
    simulated device's memory and properties, so reads during the download answer as the
    device last programmed would. Unload scopes and KNX Data Secure are refused
    (:class:`DryRunRefused`)."""
    from .download import download
    from .programmer import FREE_ACCESS_KEY
    from .scope import mask_object_types

    if scope in _REFUSED_SCOPES:
        raise DryRunRefused("a dry run of an unload is not offered")
    object_types: dict[int, int] = {}
    raw = getattr(master, "raw", None)
    if raw is not None:
        from .merge import device_mask_version

        object_types = mask_object_types(raw, device_mask_version(application))
    regions = _Regions(application)
    recorder = Recorder(
        SimulatedDevice(mask_descriptor(application), object_types, stored_image),
        regions,
    )

    async def run(run_scope: DownloadScope) -> None:
        token = recording_sessions.set(recorder.session)
        try:
            await download(
                cast("XKNX", NoBus()),
                IndividualAddress(individual_address),
                application,
                master=master,
                device=device,
                image=image,
                group_communication=group_communication,
                scope=run_scope,
                parameter_values=parameter_values,
                max_apdu_length=max_apdu_length,
                application2=application2,
                access_key=FREE_ACCESS_KEY if access_key is None else access_key,
            )
        finally:
            recording_sessions.reset(token)

    error = ""
    try:
        if scope is not DownloadScope.FULL:
            # A partial download goes into a device that runs the projected application: a
            # full download (not kept) brings the simulated device there first.
            await asyncio.ensure_future(run(DownloadScope.FULL))
            recorder.frames.clear()
            recorder.connections = 0
        # Its own task, so the recording sessions never leak into the caller's context.
        await asyncio.ensure_future(run(scope))
    except DryRunRefused:
        raise
    except (
        Exception
    ) as exc:  # the download stopped; keep what it would have sent so far
        cause = exc.__cause__ or exc
        error = f"{type(cause).__name__}: {cause}"
    written: dict[int, int] = {}
    for frame in recorder.frames:
        if frame.kind is FrameKind.MEMORY_WRITE and frame.address is not None:
            for i, byte in enumerate(frame.data):
                written[frame.address + i] = byte
    return DryRunResult(
        error=error,
        frames=recorder.frames,
        comparison=compare_with_stored(
            recorder.frames,
            stored_image,
            regions,
            {i: recorder.device.object_name(i) for i in recorder.device.allocations},
        ),
        scope=scope,
        memory_written=_runs(written),
    )

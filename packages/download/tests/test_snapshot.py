"""Device snapshot before a download: plan from a dry run, take, save, load, restore."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.download.dry_run import (
    DryRunResult,
    FrameKind,
    ImageComparison,
    RecordedFrame,
    Recorder,
    SimulatedDevice,
)
from xknxeditor.download.scope import DownloadScope
from xknxeditor.download.snapshot import (
    Snapshot,
    SnapshotError,
    SnapshotPlan,
    list_snapshots,
    load_snapshot,
    plan_from_dry_run,
    restore_snapshot,
    save_snapshot,
    take_snapshot,
)


def _frame(i: int, kind: FrameKind, **kw: object) -> RecordedFrame:
    return RecordedFrame(
        index=i, connection=1, kind=kind, service=kind.value, answered=True, **kw
    )  # type: ignore[arg-type]


def _result(frames: list[RecordedFrame]) -> DryRunResult:
    return DryRunResult(
        frames=frames,
        comparison=ImageComparison(has_stored_image=False),
        scope=DownloadScope.FULL,
        memory_written=[],
    )


def test_plan_from_dry_run() -> None:
    plan = plan_from_dry_run(
        _result(
            [
                _frame(1, FrameKind.CONNECT),
                _frame(2, FrameKind.MEMORY_WRITE, address=0x0100, data=b"\x01" * 10),
                _frame(3, FrameKind.MEMORY_WRITE, address=0x010A, data=b"\x02" * 4),
                _frame(4, FrameKind.MEMORY_WRITE, address=0x0105, data=b"\x03" * 2),
                _frame(5, FrameKind.MEMORY_WRITE, address=0x0200, data=b"\x04"),
                _frame(
                    6,
                    FrameKind.MEMORY_WRITE,
                    address=0x0010,
                    data=b"\x05",
                    segment_object=4,
                ),
                _frame(7, FrameKind.MEMORY_READ, address=0x0300, count=2),
                _frame(
                    8,
                    FrameKind.PROPERTY_WRITE,
                    object_index=4,
                    property_id=5,
                    count=1,
                    data=b"\x01",
                ),
                _frame(
                    9,
                    FrameKind.PROPERTY_WRITE,
                    object_index=6,
                    property_id=205,
                    count=1,
                    data=b"\x10",
                ),
                _frame(10, FrameKind.FUNCTION_PROPERTY, object_index=1, property_id=2),
            ]
        )
    )
    assert plan.memory == [(0x0100, 14), (0x0200, 1)]
    assert plan.properties == [
        (6, 205, 1, 1)
    ]  # load-state control (property 5) left out
    assert plan.skipped == [
        "function property commands",
        "memory in the segment of object 4",
    ]
    assert not plan.empty
    assert SnapshotPlan().empty


def _device() -> tuple[Recorder, SimulatedDevice]:
    device = SimulatedDevice(0x0010)
    for i in range(0x40):
        device.memory[0x0100 + i] = i
    device.properties[(6, 205)] = b"\x33"
    return Recorder(device), device


async def test_take_save_load_restore(tmp_path: Path) -> None:
    recorder, device = _device()
    plan = SnapshotPlan(memory=[(0x0100, 20), (0x0130, 3)], properties=[(6, 205, 1, 1)])
    snap = await take_snapshot(
        recorder.session(None, None), plan, address="1.1.31", scope="FULL"
    )  # type: ignore[arg-type]
    assert snap.memory == [
        (0x0100, bytes(range(20))),
        (0x0130, bytes([0x30, 0x31, 0x32])),
    ]
    assert snap.properties == [(6, 205, 1, 1, b"\x33")]
    assert snap.octets == 23
    # reads only
    assert not any(f.writes for f in recorder.frames)

    path = save_snapshot(snap, tmp_path)
    assert path.parent == tmp_path / "1.1.31"
    loaded = load_snapshot(path)
    assert loaded.memory == snap.memory and loaded.properties == snap.properties
    older = Snapshot(address="1.1.31", taken="2020-01-01T00:00:00+00:00", scope="FULL")
    save_snapshot(older, tmp_path)
    assert list_snapshots(tmp_path, "1.1.31")[0] == path
    assert len(list_snapshots(tmp_path)) == 2
    assert list_snapshots(tmp_path, "9.9.9") == []

    # the download changes the device; the restore puts the snapshot back and checks it
    for i in range(20):
        device.memory[0x0100 + i] = 0xFF
    device.properties[(6, 205)] = b"\x10"
    differ = await restore_snapshot(recorder.session(None, None), loaded)  # type: ignore[arg-type]
    assert differ == []
    assert bytes(device.memory[0x0100 + i] for i in range(20)) == bytes(range(20))
    assert device.properties[(6, 205)] == b"\x33"


class _Refusing:
    async def open(self) -> object:
        raise TimeoutError("no answer")

    async def close(self) -> None:
        return None


async def test_snapshot_fails_cleanly() -> None:
    with pytest.raises(SnapshotError, match="TimeoutError"):
        await take_snapshot(
            _Refusing(), SnapshotPlan(memory=[(0x100, 2)]), address="1.1.31"
        )  # type: ignore[arg-type]


class _LosesOneReply:
    """A connection manager around the recorder's session whose first connection loses the
    reply to the read at ``lost`` (the device then drops it, as a Crabtree 7172 did)."""

    def __init__(self, recorder: Recorder, lost: int) -> None:
        self._session = recorder.session(None, None)  # type: ignore[arg-type]
        self._lost = lost
        self.opened = 0

    async def open(self) -> object:
        connection = await self._session.open()
        self.opened += 1
        if self.opened == 1:
            inner = connection.request
            lost = self._lost

            async def request(
                payload: object, *args: object, **kwargs: object
            ) -> object:
                if getattr(payload, "address", None) == lost:
                    raise TimeoutError("lost reply")
                return await inner(payload, *args, **kwargs)  # type: ignore[arg-type]

            connection.request = request  # type: ignore[method-assign]
        return connection

    async def close(self) -> None:
        await self._session.close()


async def test_snapshot_retries_a_lost_reply_on_a_fresh_connection() -> None:
    recorder = _device()[0]
    manager = _LosesOneReply(recorder, lost=0x0118)
    plan = SnapshotPlan(memory=[(0x0100, 0x30)], properties=[(6, 205, 1, 1)])
    snap = await take_snapshot(manager, plan, address="1.0.123")  # type: ignore[arg-type]
    assert snap.memory == [(0x0100, bytes(range(0x30)))]
    assert snap.properties == [(6, 205, 1, 1, b"\x33")]
    assert manager.opened == 2


async def test_snapshot_gives_up_after_the_attempts() -> None:
    class _AlwaysLoses(_LosesOneReply):
        async def open(self) -> object:
            self.opened = 0  # every connection loses the reply
            return await super().open()

    recorder = _device()[0]
    with pytest.raises(SnapshotError, match="memory at 0x0100: TimeoutError"):
        await take_snapshot(
            _AlwaysLoses(recorder, lost=0x0100),
            SnapshotPlan(memory=[(0x0100, 4)]),
            address="1.0.123",
            attempts=2,
        )  # type: ignore[arg-type]


async def test_snapshot_reads_each_range_in_full_blocks() -> None:
    recorder = _device()[0]
    plan = SnapshotPlan(memory=[(0x0100, 2), (0x0110, 0x24)])
    session = recorder.session(None, None)  # type: ignore[arg-type]

    class _CapsFirst:
        """The first read learns a one-octet cap (as after a lost reply); the next range
        must still be read in full blocks."""

        async def open(self) -> object:
            connection = await session.open()
            return connection

        async def close(self) -> None:
            await session.close()

    from xknxeditor.download.programmer import DeviceProgrammer

    real_read = DeviceProgrammer.read_memory

    async def read_memory(
        self: DeviceProgrammer, address: int, size: int, **kw: object
    ) -> bytes:
        data = await real_read(self, address, size, **kw)  # type: ignore[arg-type]
        if address == 0x0100:
            self._memory_read_cap = 1  # pyright: ignore[reportPrivateUsage]
        return data

    DeviceProgrammer.read_memory = read_memory  # type: ignore[method-assign]
    try:
        snap = await take_snapshot(_CapsFirst(), plan, address="1.0.123")  # type: ignore[arg-type]
    finally:
        DeviceProgrammer.read_memory = real_read  # type: ignore[method-assign]
    assert snap.memory[1] == (0x0110, bytes(range(0x10, 0x34)))
    reads = [
        f
        for f in recorder.frames
        if f.kind is FrameKind.MEMORY_READ and (f.address or 0) >= 0x0110
    ]
    assert len(reads) == 3  # 36 octets in blocks of 12, not 36 single octets

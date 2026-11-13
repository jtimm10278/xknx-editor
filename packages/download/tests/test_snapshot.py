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

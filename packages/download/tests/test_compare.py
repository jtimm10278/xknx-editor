"""Read-only compare of a device with its projected image."""

from __future__ import annotations

import pytest
from xknx.telegram.apci import MemoryRead, MemoryWrite, PropertyValueWrite, Restart

from xknxeditor.download.compare import (
    Expected,
    ReadOnlyConnection,
    ReadOnlyManager,
    WriteRefused,
    compare_device,
    compare_plan,
    expected_from_dry_run,
)
from xknxeditor.download.dry_run import (
    DryRunResult,
    FrameKind,
    ImageComparison,
    RecordedFrame,
    Recorder,
    SimulatedDevice,
)
from xknxeditor.download.scope import DownloadScope


def _frame(i: int, kind: FrameKind, **kw: object) -> RecordedFrame:
    return RecordedFrame(
        index=i, connection=1, kind=kind, service=kind.value, answered=True, **kw
    )  # type: ignore[arg-type]


def _dry() -> DryRunResult:
    return DryRunResult(
        frames=[
            _frame(
                1,
                FrameKind.MEMORY_WRITE,
                address=0x0100,
                data=b"\x00\x00\x00",
                region="params",
            ),
            _frame(
                2,
                FrameKind.MEMORY_WRITE,
                address=0x0101,
                data=b"\x05\x06",
                region="params",
            ),
            _frame(
                3,
                FrameKind.MEMORY_WRITE,
                address=0x4000,
                data=b"\x09",
                segment_object=4,
            ),
            _frame(
                4,
                FrameKind.PROPERTY_WRITE,
                object_index=6,
                property_id=205,
                count=1,
                data=b"\x10",
            ),
            _frame(
                5,
                FrameKind.PROPERTY_WRITE,
                object_index=4,
                property_id=5,
                count=1,
                data=b"\x02",
            ),
            # the verify mode a download switches on for itself: not configuration
            _frame(
                6,
                FrameKind.PROPERTY_WRITE,
                object_index=0,
                property_id=14,
                count=1,
                data=b"\x04",
            ),
        ],
        comparison=ImageComparison(has_stored_image=False),
        scope=DownloadScope.FULL,
        memory_written=[],
    )


def test_expected_last_write_wins() -> None:
    expected = expected_from_dry_run(_dry())
    assert expected.memory == {0x0100: 0, 0x0101: 5, 0x0102: 6}
    assert expected.properties == {(6, 205, 1, 1): b"\x10"}
    assert expected.skipped == ["memory in the segment of object 4"]
    placed = expected_from_dry_run(_dry(), placed={4})
    assert placed.memory[0x4000] == 9
    plan = compare_plan(placed)
    assert plan.memory == [(0x0100, 3), (0x4000, 1)]
    assert plan.reads == 3  # two memory reads + one property
    assert plan.octets == 4


async def test_compare_finds_differences_and_writes_nothing() -> None:
    device = SimulatedDevice(0x0010)
    device.memory.update({0x0100: 0, 0x0101: 5, 0x0102: 7})
    device.properties[(6, 205)] = b"\x33"
    recorder = Recorder(device)
    result = await compare_device(
        recorder.session(None, None),  # type: ignore[arg-type]
        expected_from_dry_run(_dry()),
        address="1.1.31",
    )
    assert not result.matches
    assert result.octets == 3 and result.properties == 1
    assert [(d.what, d.where, d.expected, d.held) for d in result.differences] == [
        ("memory", "0x0102+1", "06", "07"),
        ("property", "6/205", "10", "33"),
    ]
    assert result.differences[0].region == "params"
    assert not any(f.writes for f in recorder.frames)
    assert result.as_dict()["matches"] is False

    device.memory[0x0102] = 6
    device.properties[(6, 205)] = b"\x10"
    same = await compare_device(
        recorder.session(None, None), expected_from_dry_run(_dry()), address="1.1.31"
    )  # type: ignore[arg-type]
    assert same.matches


class _Conn:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send_data(self, payload: object, wait_for_ack: bool = True) -> None:
        self.sent.append(payload)

    async def request(self, payload: object, expected: object) -> object:
        self.sent.append(payload)
        return None


@pytest.mark.parametrize(
    "payload",
    [
        MemoryWrite(address=0x100, count=1, data=b"\x01"),
        PropertyValueWrite(
            object_index=6, property_id=5, count=1, start_index=1, data=b"\x01"
        ),
        Restart(),
    ],
)
async def test_read_only_guard_refuses_writes(payload: object) -> None:
    conn = _Conn()
    guarded = ReadOnlyConnection(conn)  # type: ignore[arg-type]
    with pytest.raises(WriteRefused):
        await guarded.send_data(payload)  # type: ignore[arg-type]
    with pytest.raises(WriteRefused):
        await guarded.request(payload, None)  # type: ignore[arg-type]
    assert conn.sent == []
    await guarded.request(MemoryRead(address=0x100, count=1), None)
    assert len(conn.sent) == 1


async def test_failure_is_reported() -> None:
    class Refusing:
        async def open(self) -> object:
            raise TimeoutError("no answer")

        async def close(self) -> None:
            return None

    result = await compare_device(
        Refusing(), Expected(memory={0x100: 1}), address="1.0.9"
    )  # type: ignore[arg-type]
    assert "TimeoutError" in result.error and not result.matches
    assert isinstance(ReadOnlyManager(Refusing()), ReadOnlyManager)  # type: ignore[arg-type]

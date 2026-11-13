"""Waiting for a device to answer again after the restart that ends a download."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from xknx import XKNX
from xknx.telegram import Telegram
from xknx.telegram.apci import APCI, DeviceDescriptorRead

from xknxeditor.download import restart_wait
from xknxeditor.download.download import DownloadResult
from xknxeditor.download.errors import (
    DeviceNotAnsweringError,
    ManagementAckTimeout,
    MemoryReadNotAnswered,
)
from xknxeditor.download.restart_wait import wait_until_answering
from xknxeditor.download.scope import DownloadScope

from .conftest import FakeDevice
from .test_wire_audit import CaptureDevice, capture_application, capture_master

if TYPE_CHECKING:
    from xknxeditor.prod import MasterData


class _Clock:
    """Stands in for the event loop's clock and asyncio.sleep: pauses take no real time."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = 0.0
        self.pauses: list[float] = []
        monkeypatch.setattr(restart_wait, "_monotonic", lambda: self.now)
        monkeypatch.setattr(restart_wait, "_sleep", self.sleep)

    async def sleep(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds


class _Booting(FakeDevice):
    """A device that ignores everything for its first ``silent`` descriptor reads."""

    def __init__(
        self, silent: int, error: type[Exception] = ManagementAckTimeout
    ) -> None:
        super().__init__(descriptor=0x0012)
        self.silent = silent
        self.error = error
        self.reads = 0

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        if isinstance(payload, DeviceDescriptorRead):
            self.reads += 1
            if self.reads <= self.silent:
                raise self.error("no ACK")
        return await super().request(payload, expected)


class _Manager:
    def __init__(self, device: FakeDevice, log: list[str]) -> None:
        self.device = device
        self.log = log

    async def open(self) -> FakeDevice:
        self.log.append("open")
        return self.device

    async def close(self) -> None:
        self.log.append("close")


async def test_answers_at_once_without_a_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock(monkeypatch)
    log: list[str] = []
    device = _Booting(0)

    assert await wait_until_answering(lambda: _Manager(device, log)) == 0.0
    assert clock.pauses == []
    assert log == ["open", "close"]


@pytest.mark.parametrize(
    "error", [ManagementAckTimeout, MemoryReadNotAnswered, TimeoutError]
)
async def test_retries_on_fresh_connections_with_growing_pauses(
    monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    clock = _Clock(monkeypatch)
    log: list[str] = []
    device = _Booting(5, error)

    waited = await wait_until_answering(lambda: _Manager(device, log))

    assert clock.pauses == [0.5, 1.0, 2.0, 4.0, 4.0]
    assert waited == sum(clock.pauses)
    assert device.reads == 6
    # Every attempt gets its own connection, and every one is closed.
    assert log == ["open", "close"] * 6


async def test_gives_up_before_the_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock(monkeypatch)
    log: list[str] = []
    device = _Booting(100)

    with pytest.raises(DeviceNotAnsweringError, match="within 15 s") as error:
        await wait_until_answering(lambda: _Manager(device, log))

    assert isinstance(error.value.__cause__, ManagementAckTimeout)
    # 0.5 + 1 + 2 + 4 + 4 = 11.5; one more 4 s pause would pass 15 s.
    assert clock.pauses == [0.5, 1.0, 2.0, 4.0, 4.0]
    assert clock.now <= 15.0
    assert log.count("open") == log.count("close") == 6


async def test_short_limit_means_a_single_try(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock(monkeypatch)
    log: list[str] = []

    with pytest.raises(DeviceNotAnsweringError):
        await wait_until_answering(lambda: _Manager(_Booting(1), log), timeout=0.2)
    assert clock.pauses == []


class _RestartingCapture(CaptureDevice):
    """The captured System B device, silent for its first descriptor reads after a restart."""

    def __init__(self, silent: int) -> None:
        super().__init__()
        self.silent_after_restart = silent
        self.reads_after_restart = 0

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        if self.restarted and isinstance(payload, DeviceDescriptorRead):
            self.reads_after_restart += 1
            if self.reads_after_restart <= self.silent_after_restart:
                raise ManagementAckTimeout("no ACK")
        return await super().request(payload, expected)


async def _download(
    monkeypatch: pytest.MonkeyPatch, device: CaptureDevice, **options: object
) -> DownloadResult:
    dl = importlib.import_module("xknxeditor.download.download")
    app, image = capture_application()
    log: list[str] = []
    monkeypatch.setattr(dl, "_connection_manager", lambda *a: _Manager(device, log))
    return await dl.download(
        cast(XKNX, object()),
        "1.1.5",
        app,
        image=image,
        master=cast("MasterData", SimpleNamespace(raw=capture_master())),
        scope=DownloadScope.APPLICATION,
        max_apdu_length=15,
        expected_descriptor=0x07B0,
        **options,  # type: ignore[arg-type]
    )


async def test_download_waits_for_the_restarted_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Clock(monkeypatch)
    device = _RestartingCapture(silent=2)

    result = await _download(monkeypatch, device)

    assert device.restarted
    assert device.reads_after_restart == 3
    assert result.answered_after_restart_s == 1.5
    assert result.restart_wait_timed_out is False
    assert result.verify_report is None


async def test_download_reports_a_device_that_stays_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Clock(monkeypatch)
    device = _RestartingCapture(silent=100)

    result = await _download(monkeypatch, device, restart_wait_s=5.0)

    assert result.answered_after_restart_s is None
    assert result.restart_wait_timed_out is True


async def test_download_without_a_wait_reads_nothing_after_the_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _RestartingCapture(silent=0)

    result = await _download(monkeypatch, device, restart_wait_s=None)

    assert device.reads_after_restart == 0
    assert result.answered_after_restart_s is None
    assert result.restart_wait_timed_out is False


async def test_verify_after_the_restart_finds_nothing_left_to_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Clock(monkeypatch)
    device = _RestartingCapture(silent=1)

    result = await _download(monkeypatch, device, verify=True)

    report = result.verify_report
    assert report is not None
    assert report.segments
    assert not report.changed_segments

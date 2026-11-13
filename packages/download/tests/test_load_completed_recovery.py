"""A device that acknowledges no LoadCompleted (it restarts to commit the load) gets ETS's
recovery once: reconnect after a pause, verify mode on again, LoadCompleted again."""

from __future__ import annotations

from typing import Any

import pytest
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import Telegram
from xknx.telegram.apci import APCI, PropertyValueWrite

from xknxeditor.download import procedure
from xknxeditor.download.errors import ManagementAckTimeout
from xknxeditor.download.image import DownloadImage
from xknxeditor.download.load_state import LoadState
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)

from .conftest import FakeDevice
from .test_procedure import _ADDRESS_TABLE_TYPE, _application


class _Silent(FakeDevice):
    """Acknowledges none of its first ``silent`` LoadCompleted events."""

    def __init__(self, silent: int) -> None:
        super().__init__(
            object_types={0: 0x0000, 1: _ADDRESS_TABLE_TYPE}, descriptor=0x07B0
        )
        self.silent = silent
        self.load_states[1] = LoadState.LOADING
        self.completes = 0

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        if (
            isinstance(payload, PropertyValueWrite)
            and payload.property_id == 5
            and payload.data[:1] == b"\x02"
        ):
            self.completes += 1
            if self.completes <= self.silent:
                raise ManagementAckTimeout("no ACK")
        return await super().request(payload, expected)


class _Manager:
    def __init__(self, device: FakeDevice) -> None:
        self.device = device
        self.opens = 0
        self.closes = 0

    async def open(self) -> FakeDevice:
        self.opens += 1
        return self.device

    async def close(self) -> None:
        self.closes += 1


def _run(device: FakeDevice) -> tuple[LoadProcedureRunner, _Manager]:
    manager = _Manager(device)
    runner = LoadProcedureRunner(
        _application(
            LdCtrlConnect(),
            LdCtrlLoadCompleted(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        ),
        DownloadImage(segments=(), properties=()),
        connection_manager=cast_manager(manager),
        restart_cooldown=0,
    )
    return runner, manager


def cast_manager(manager: _Manager) -> Any:
    return manager


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procedure, "LOAD_COMPLETED_RECONNECT_S", 0.0)


async def test_a_silent_load_completed_is_asked_again_on_a_new_connection() -> None:
    device = _Silent(silent=1)
    runner, manager = _run(device)
    await runner.run()
    assert device.completes == 2
    assert device.load_states[1] is LoadState.LOADED
    assert (manager.opens, manager.closes) >= (2, 1)


async def test_a_second_silence_fails_the_download() -> None:
    device = _Silent(silent=2)
    runner, _manager = _run(device)
    with pytest.raises(ManagementConnectionError, match="no ACK"):
        await runner.run()
    assert device.completes == 2


async def test_verify_mode_is_switched_on_again_after_the_reconnect() -> None:
    device = _Silent(silent=1)
    runner, manager = _run(device)
    switched: list[int] = []
    original = procedure.DeviceProgrammer.enable_memory_auto_verify

    async def spy(self: Any) -> None:
        switched.append(manager.opens)
        self.memory_auto_verify = True

    procedure.DeviceProgrammer.enable_memory_auto_verify = spy  # type: ignore[method-assign]
    try:
        programmer = await runner._bus()  # pyright: ignore[reportPrivateUsage]
        programmer.memory_auto_verify = True
        await runner.run()
    finally:
        procedure.DeviceProgrammer.enable_memory_auto_verify = original  # type: ignore[method-assign]
    assert switched and switched[-1] >= 2

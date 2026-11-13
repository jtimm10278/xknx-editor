"""Read-only device tools: the plan lists the exact frames and sends nothing, a token runs its
plan once, and the run sends only those frames (a real xknx stack on a fake bus)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from xknx import XKNX
from xknx.management import management
from xknx.telegram import IndividualAddress, Telegram, TelegramDirection, apci, tpci

from editor_gui.plugins.connection.device_tools import (
    TOKEN_SECONDS,
    PlanBook,
    make_plan,
    run_plan,
)
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.connection.write_guard import set_listen_only
from xknxeditor.download import management_transport

from .test_write_guard import _Log


@pytest.fixture(autouse=True)
def _short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    management_transport.install()
    monkeypatch.setattr(management, "MANAGEMENT_ACK_TIMEOUT", 0.005)
    monkeypatch.setattr(management, "MANAGEMENT_CONNECTION_TIMEOUT", 0.01)
    monkeypatch.setattr(management_transport, "_RECONNECT_DELAY", 0.0)
    monkeypatch.setattr(
        "editor_gui.plugins.connection.device_tools.DESCRIPTOR_TIMEOUT", 0.2
    )


class _Bus:
    """A bus with devices that answer a descriptor read (address -> mask)."""

    def __init__(self, devices: dict[str, int]) -> None:
        self.xknx = XKNX()
        self.devices = {IndividualAddress(a): m for a, m in devices.items()}
        self.sent: list[Telegram] = []
        self.xknx.cemi_handler = AsyncMock(send_telegram=self.send)
        self.answer_broadcast: Callable[[Telegram], None] = lambda _t: None

    def _reply(
        self, source: IndividualAddress, control: Any, payload: Any = None
    ) -> None:
        self.xknx.management.process(
            Telegram(
                source_address=source,
                destination_address=self.xknx.current_address,
                direction=TelegramDirection.INCOMING,
                tpci=control,
                payload=payload,
            )
        )

    async def send(self, telegram: Telegram) -> None:
        self.sent.append(telegram)
        target = telegram.destination_address
        if isinstance(telegram.payload, apci.IndividualAddressRead):
            self.answer_broadcast(telegram)
        if (
            isinstance(target, IndividualAddress)
            and target in self.devices
            and isinstance(telegram.tpci, tpci.TDataConnected)
        ):
            self._reply(target, tpci.TAck(telegram.tpci.sequence_number))
            self._reply(
                target,
                tpci.TDataConnected(0),
                apci.DeviceDescriptorResponse(descriptor=0, value=self.devices[target]),
            )

    def kinds(self) -> list[tuple[str, str]]:
        return [
            (type(t.payload or t.tpci).__name__, str(t.destination_address))
            for t in self.sent
        ]


def test_the_plan_lists_the_frames_and_sends_nothing() -> None:
    plan = make_plan("is_there", address="1.1.5")
    assert [f.service for f in plan.frames] == [
        "T_Connect",
        "A_DeviceDescriptor_Read (type 0)",
        "T_Ack",
        "T_Disconnect",
    ]
    assert plan.addresses == ("1.1.5",)
    scan = make_plan("line_scan", line="1.1", first=3, last=5)
    assert scan.addresses == ("1.1.3", "1.1.4", "1.1.5")
    assert len(scan.frames) == 12
    progmode = make_plan("programming_mode")
    assert [(f.service, f.target) for f in progmode.frames] == [
        ("A_IndividualAddress_Read", "broadcast")
    ]
    assert progmode.as_dict()["frame_count"] == 1
    for bad in (
        lambda: make_plan("restart"),
        lambda: make_plan("line_scan", line="1", first=0, last=1),
        lambda: make_plan("line_scan", line="1.1", first=5, last=3),
    ):
        with pytest.raises(ValueError):
            bad()


def test_a_token_runs_once_and_expires() -> None:
    now = [0.0]
    book = PlanBook(clock=lambda: now[0])
    plan = book.add(make_plan("programming_mode"))
    assert book.take(plan.token).tool == "programming_mode"
    with pytest.raises(ValueError, match="already used"):
        book.take(plan.token)
    late = book.add(make_plan("programming_mode"))
    now[0] = TOKEN_SECONDS + 1
    with pytest.raises(ValueError, match="expired"):
        book.take(late.token)


async def test_is_there_and_line_scan_send_exactly_the_planned_reads() -> None:
    bus = _Bus({"1.1.5": 0x0021})
    there = await run_plan(bus.xknx, make_plan("is_there", address="1.1.5"))
    assert there == {
        "tool": "is_there",
        "address": "1.1.5",
        "answered": True,
        "descriptor": "0021",
    }
    assert [k for k, _ in bus.kinds()] == [
        "TConnect",
        "DeviceDescriptorRead",
        "TDisconnect",
    ]
    bus.sent.clear()
    scan = await run_plan(bus.xknx, make_plan("line_scan", line="1.1", first=4, last=6))
    assert scan["scanned"] == 3
    assert [d["address"] for d in scan["answered"]] == ["1.1.5"]
    # Only connects, descriptor reads (repeated while unanswered) and disconnects, only to
    # the planned addresses.
    assert {k for k, _ in bus.kinds()} <= {
        "TConnect",
        "DeviceDescriptorRead",
        "TAck",
        "TDisconnect",
    }
    assert {a for _, a in bus.kinds()} == {"1.1.4", "1.1.5", "1.1.6"}


async def test_programming_mode_is_one_broadcast_read() -> None:
    bus = _Bus({})

    def answer(_telegram: Telegram) -> None:
        bus.xknx.management.process(
            Telegram(
                source_address=IndividualAddress("1.0.9"),
                destination_address=IndividualAddress(0),
                direction=TelegramDirection.INCOMING,
                tpci=tpci.TDataBroadcast(),
                payload=apci.IndividualAddressResponse(),
            )
        )

    bus.answer_broadcast = answer
    result = await run_plan(bus.xknx, make_plan("programming_mode"), timeout=0.05)
    assert result == {"tool": "programming_mode", "in_programming_mode": ["1.0.9"]}
    assert [k for k, _ in bus.kinds()] == ["IndividualAddressRead"]


def test_the_service_needs_a_token_and_refuses_under_listen_only() -> None:
    service = ConnectionService()
    log = _Log()
    service._log = cast(Any, log)  # pyright: ignore[reportPrivateUsage]
    plan = service.device_tool_plan("is_there", address="1.1.5")
    assert plan["frame_count"] == 4
    with pytest.raises(ValueError, match="unknown or already used"):
        service.device_tool_run("not-a-token")
    set_listen_only(True)
    try:
        assert service.device_tool_run(plan["token"]) is None
        assert any("Listen Only is on" in e for e in log.errors)
    finally:
        set_listen_only(False)
    # The token was spent by the refused run: a new plan is needed.
    with pytest.raises(ValueError):
        service.device_tool_run(plan["token"])
    # Without a connection: not run.
    second = service.device_tool_plan("programming_mode")
    assert service.device_tool_run(second["token"]) is None

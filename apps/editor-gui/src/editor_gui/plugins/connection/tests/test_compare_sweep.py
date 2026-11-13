"""The whole-house compare sweep: one device after another with a pause, reads only, a device
without an address reported rather than read."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

import editor_gui.programming as programming
from xknxeditor.download.compare import CompareResult, Difference

from .test_route_apdu import _service  # pyright: ignore[reportPrivateUsage]


def _device(address: str, name: str) -> Any:
    return SimpleNamespace(
        individual_address=address,
        name=name,
        download_plugin_required=False,
        factory_image=None,
        second_application=None,
        node_id=1,
    )


def test_sweep_in_order_with_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    compared: list[str] = []
    pauses: list[float] = []

    async def fake_compare(xknx: Any, device: Any, *args: Any) -> CompareResult:
        compared.append(device.individual_address)
        result = CompareResult(address=device.individual_address, octets=10)
        if device.individual_address == "1.1.6":
            result.differences.append(Difference("memory", "0x0100+1", "", "01", "02"))
        return result

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(programming, "compare_with_project", fake_compare)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    service, _calls = _service(monkeypatch, set())
    monkeypatch.setattr(service, "_download_security", lambda device, key: None)
    service.block_bus_writes = True  # reads only: runs with writes blocked
    future = service.compare_devices(
        [
            (_device("1.1.5", "a"), None),
            (_device("", "no address"), None),
            (_device("1.1.6", "b"), None),
        ],
        pause=1.5,
    )
    assert future is not None
    items = cast(list[dict[str, Any]], future.result())
    assert compared == ["1.1.5", "1.1.6"]
    assert pauses == [1.5, 1.5]
    assert [(i["address"], i["matches"]) for i in items] == [
        ("1.1.5", True),
        ("", False),
        ("1.1.6", False),
    ]
    assert items[1]["error"] == "no address"


def test_sweep_needs_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _calls = _service(monkeypatch, set())
    assert service.compare_devices([]) is None

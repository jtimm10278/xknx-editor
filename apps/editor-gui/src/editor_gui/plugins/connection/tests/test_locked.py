"""Locked devices: nothing that could change them leaves the interface (reads still do) unless a
job unlocks one; the setting is kept, an MCP client may lock more but not unlock, and a download
of a locked device starts only when it was unlocked for it."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.telegram import apci, tpci

from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import ConnectionPlugin
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.connection.write_guard import (
    LockedRefused,
    is_locked,
    locked_devices,
    parse_locked,
    set_locked,
    unlocked,
)

from .test_write_guard import _DEVICE, _frame, _interface, _plugin


@pytest.fixture(autouse=True)
def _none_afterwards() -> Iterator[None]:
    yield
    set_locked([])


def test_addresses_are_individual_addresses_in_order() -> None:
    assert parse_locked("1.1.20, 1.0.3\n1.1.20  1.0.30") == [
        "1.0.3",
        "1.0.30",
        "1.1.20",
    ]
    with pytest.raises(ValueError):
        parse_locked("1/2/3")
    set_locked(["1.1.20"])
    assert locked_devices() == ["1.1.20"]
    assert is_locked("1.1.20")
    assert not is_locked("1.1.2")
    assert not is_locked("not an address")


def test_writes_are_refused_reads_pass_and_a_job_unlocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface, sent = _interface([False], monkeypatch)
    set_locked([str(_DEVICE)])
    write = _frame(apci.MemoryWrite(address=0x4000, count=1, data=b"\x01"))
    with pytest.raises(LockedRefused, match="is a locked device"):
        asyncio.run(interface.send_cemi(write))
    with pytest.raises(LockedRefused):
        asyncio.run(interface.send_cemi(_frame(apci.Restart())))
    assert sent == []
    # Reads, and the transport frames around them, pass.
    asyncio.run(interface.send_cemi(_frame(apci.DeviceDescriptorRead())))
    asyncio.run(interface.send_cemi(_frame(None, tpci.TConnect())))
    assert len(sent) == 2
    # Unlocked for one job: its writes pass, and only while it runs.
    with unlocked(str(_DEVICE)):
        asyncio.run(interface.send_cemi(write))
    assert len(sent) == 3
    with pytest.raises(LockedRefused):
        asyncio.run(interface.send_cemi(write))
    # Other devices are not affected.
    set_locked(["1.1.250"])
    asyncio.run(interface.send_cemi(write))
    assert len(sent) == 4


def _locking_plugin() -> Any:
    plugin = _plugin()
    plugin._api = SimpleNamespace(
        connection=SimpleNamespace(
            use_serial_number=True, block_bus_writes=False, listen_only=False
        )
    )
    plugin._log = SimpleNamespace(
        info=lambda *_a, **_k: None,
        warning=lambda *_a, **_k: None,
        error=lambda *_a, **_k: None,
    )
    return plugin


def test_the_setting_is_kept_and_mcp_may_only_lock_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = _locking_plugin()
    setting = {s.key: s for s in plugin.settings()}["locked_devices"]
    setting.set("1.1.20 1.0.3")
    assert setting.get() == "1.0.3 1.1.20"
    assert plugin_module.load_settings("connection")["locked_devices"] == "1.0.3 1.1.20"
    # Not individual addresses: nothing changes.
    setting.set("1/2/3")
    assert setting.get() == "1.0.3 1.1.20"
    assert setting.mcp_may_set("1.0.3 1.1.20 1.1.21")
    assert not setting.mcp_may_set("1.0.3")
    assert not setting.mcp_may_set("nonsense")
    set_locked([])
    ConnectionPlugin._load_saved_settings(_locking_plugin())  # pyright: ignore[reportPrivateUsage]
    assert locked_devices() == ["1.0.3", "1.1.20"]


def test_a_download_of_a_locked_device_needs_the_unlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = cast(Any, object.__new__(ConnectionService))
    errors: list[str] = []
    service._log = SimpleNamespace(
        error=lambda message, **_k: errors.append(message),
        warning=lambda *_a, **_k: None,
        info=lambda *_a, **_k: None,
    )
    monkeypatch.setattr(service, "writes_refused", lambda _name: False, raising=False)
    monkeypatch.setattr(service, "not_connected", lambda _name: False, raising=False)
    started: list[bool] = []

    def _address_download(*_a: Any, unlock: bool = False) -> None:
        started.append(unlock)

    monkeypatch.setattr(
        service, "_download_individual_address", _address_download, raising=False
    )
    from xknxeditor.download.scope import DownloadScope

    device = SimpleNamespace(individual_address="1.1.20", name="Actuator")
    set_locked(["1.1.20"])
    program = ConnectionService.program_device.__wrapped__  # type: ignore[attr-defined]
    assert program(service, device, DownloadScope.INDIVIDUAL_ADDRESS) is None
    assert errors and "locked" in errors[0]
    assert started == []
    program(service, device, DownloadScope.INDIVIDUAL_ADDRESS, unlock_locked=True)
    assert started == [True]

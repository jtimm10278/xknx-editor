"""Listen Only: every frame to the bus is refused at the interface, reads included; the setting is
kept, can be turned on but not off by an MCP client, and counts as blocked writes."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.cemi.cemi_frame import CEMIFrame, CEMIMPropInfo, CEMIMPropReadRequest
from xknx.cemi.const import CEMIMessageCode
from xknx.dpt import DPTBinary
from xknx.profile.const import ResourceDevicePropertyId, ResourceObjectType
from xknx.telegram import apci, tpci

from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import ConnectionPlugin
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.connection.write_guard import (
    BusWritesBlocked,
    ListenOnlyRefused,
    listen_only_active,
    listen_only_allows,
    set_listen_only,
)

from .test_write_guard import _frame, _interface, _Log, _plugin


@pytest.fixture(autouse=True)
def _off_afterwards() -> Iterator[None]:
    yield
    set_listen_only(False)


@pytest.mark.parametrize(
    "frame",
    [
        _frame(apci.GroupValueRead()),
        _frame(apci.GroupValueWrite(DPTBinary(1))),
        _frame(apci.DeviceDescriptorRead()),
        _frame(None, tpci.TConnect()),
    ],
)
def test_listen_only_refuses_every_bus_frame(frame: CEMIFrame) -> None:
    assert listen_only_allows(frame)
    set_listen_only(True)
    assert not listen_only_allows(frame)


def test_a_property_read_of_the_interface_itself_passes() -> None:
    info = CEMIMPropInfo(
        object_type=ResourceObjectType.OBJECT_DEVICE,
        property_id=ResourceDevicePropertyId.PID_MAX_APDU_LENGTH,
    )
    read = CEMIFrame(
        code=CEMIMessageCode.M_PROP_READ_REQ,
        data=CEMIMPropReadRequest(property_info=info),
    )
    set_listen_only(True)
    assert listen_only_allows(read)


def test_the_interface_refuses_even_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    interface, sent = _interface([False], monkeypatch)
    set_listen_only(True)
    with pytest.raises(ListenOnlyRefused, match="Listen Only is on") as error:
        asyncio.run(interface.send_cemi(_frame(apci.GroupValueRead())))
    assert isinstance(error.value, BusWritesBlocked)
    assert sent == []
    set_listen_only(False)
    asyncio.run(interface.send_cemi(_frame(apci.GroupValueRead())))
    assert len(sent) == 1


def test_the_service_counts_it_as_blocked_writes_and_refuses_reads() -> None:
    service = ConnectionService()
    log = _Log()
    service._log = cast(Any, log)  # pyright: ignore[reportPrivateUsage]
    assert not service.writes_blocked()
    assert not service.sends_refused("scan")
    service.listen_only = True
    assert listen_only_active()
    assert service.writes_blocked()
    assert service.writes_refused("program_device")
    assert service.sends_refused("scan")
    assert all("Listen Only is on" in e for e in log.errors)
    device = cast(Any, SimpleNamespace(individual_address="1.1.5", name="Actuator"))
    assert service.program_device(device) is None


def _listening_plugin() -> Any:
    plugin = _plugin()
    plugin._api = SimpleNamespace(
        connection=SimpleNamespace(
            use_serial_number=True, block_bus_writes=False, listen_only=False
        )
    )
    return plugin


def test_the_setting_is_kept_and_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = _listening_plugin()
    setting = {s.key: s for s in plugin.settings()}["listen_only"]
    assert setting.get() is False
    setting.set(True)
    assert plugin._api.connection.listen_only is True
    assert plugin_module.load_settings("connection")["listen_only"] is True
    later = _listening_plugin()
    ConnectionPlugin._load_saved_settings(later)  # pyright: ignore[reportPrivateUsage]
    assert later._api.connection.listen_only is True


def test_mcp_may_turn_it_on_but_not_off() -> None:
    setting = {s.key: s for s in _listening_plugin().settings()}["listen_only"]
    assert setting.mcp_may_set(True)
    assert not setting.mcp_may_set(False)

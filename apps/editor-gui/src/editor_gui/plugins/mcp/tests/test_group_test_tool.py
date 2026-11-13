"""MCP group test: the plan says what will be sent and refuses protected group addresses; a
token sends it once; every attempt is logged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui import settings as app_settings
from editor_gui.plugins.connection.write_guard import set_protected
from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data


async def test_plan_run_refuse_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    ctx = _build_ctx(tmp_path)
    monitor = ctx.monitor
    sent: list[tuple[str, str]] = []
    monitor.can_send = lambda: True  # type: ignore[method-assign]
    monitor.send_read = lambda address: sent.append(("read", address)) or True  # type: ignore[method-assign]
    monitor.send_write = (  # type: ignore[method-assign]
        lambda address, _dpt, value: (
            sent.append(("write", f"{address}={value}")) or True
        )
    )
    mcp = build_server(ctx)
    set_protected(["7/0/"])
    try:
        async with Client(mcp) as client:
            plan = await _data(
                client, "monitor_test_plan", address="1/0/1", value="1", dpt="1.001"
            )
            assert (plan["service"], plan["dpt"], plan["value"]) == (
                "GroupValueWrite",
                "1.001",
                "1",
            )
            assert "DPTBinary" in plan["payload"]
            assert sent == []
            result = await _data(client, "monitor_test_run", token=plan["token"])
            assert result == {
                "status": "sent",
                "service": "GroupValueWrite",
                "address": "1/0/1",
            }
            assert sent == [("write", "1/0/1=1")]
            with pytest.raises(Exception, match="unknown or already used"):
                await client.call_tool("monitor_test_run", {"token": plan["token"]})
            read = await _data(client, "monitor_test_plan", address="1/0/2")
            await _data(client, "monitor_test_run", token=read["token"])
            assert sent[-1] == ("read", "1/0/2")
            with pytest.raises(Exception, match="protected group 7/0/"):
                await client.call_tool(
                    "monitor_test_plan",
                    {"address": "7/0/1", "value": "0", "dpt": "1.001"},
                )
            with pytest.raises(Exception, match="cannot encode"):
                await client.call_tool(
                    "monitor_test_plan",
                    {"address": "1/0/1", "value": "x", "dpt": "1.001"},
                )
    finally:
        set_protected([])
    log = [
        json.loads(line)
        for line in (tmp_path / "group_tests.jsonl").read_text().splitlines()
    ]
    assert [(e["step"], e["result"].split(":")[0]) for e in log] == [
        ("plan", "planned"),
        ("run", "sent"),
        ("plan", "planned"),
        ("run", "sent"),
        ("plan", "REFUSED"),
    ]

"""MCP: plan a read-only device tool (frames listed, nothing sent), run it with its token."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data


async def test_plan_then_run_with_the_token(tmp_path: Path) -> None:
    mcp = build_server(_build_ctx(tmp_path))
    async with Client(mcp) as client:
        plan = await _data(
            client, "connection_device_tool_plan", tool_name="is_there", address="1.1.5"
        )
        assert plan["frame_count"] == 4
        assert plan["addresses"] == ["1.1.5"]
        assert plan["frames"][1]["service"] == "A_DeviceDescriptor_Read (type 0)"
        scan = await _data(
            client,
            "connection_device_tool_plan",
            tool_name="line_scan",
            line="1.1",
            first=1,
            last=2,
        )
        assert scan["addresses"] == ["1.1.1", "1.1.2"]
        with pytest.raises(Exception, match="needs the device's address"):
            await client.call_tool(
                "connection_device_tool_plan", {"tool_name": "is_there"}
            )
        with pytest.raises(Exception, match="unknown tool"):
            await client.call_tool(
                "connection_device_tool_plan", {"tool_name": "restart"}
            )
        # Without a connection the run refuses, and the token is spent.
        with pytest.raises(Exception, match="not run"):
            await client.call_tool(
                "connection_device_tool_run", {"token": plan["token"]}
            )
        with pytest.raises(Exception, match="unknown or already used"):
            await client.call_tool(
                "connection_device_tool_run", {"token": plan["token"]}
            )

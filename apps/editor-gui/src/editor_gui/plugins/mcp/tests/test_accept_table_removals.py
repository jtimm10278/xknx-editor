"""connection_program_device offers accept_table_removals, documented for the client."""

from __future__ import annotations

from pathlib import Path

from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx


async def test_program_tool_takes_accept_table_removals(tmp_path: Path) -> None:
    async with Client(build_server(_build_ctx(tmp_path))) as client:
        tools = {t.name: t for t in await client.list_tools()}
    tool = tools["connection_program_device"]
    schema = tool.input_schema["properties"]["accept_table_removals"]
    assert schema.get("default") is False
    assert "accept_table_removals=true" in (tool.description or "")

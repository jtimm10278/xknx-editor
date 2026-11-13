"""MCP tools to import, search and export a monitor's recording, and the sends Listen Only
refuses."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.connection.write_guard import set_listen_only
from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data

_CSV = (
    "time,source,source_name,ga,ga_name,type,raw,value,dpt\n"
    "2030-01-02T00:00:00+00:00,1.1.5,,1/0/1,,write,1,,1.001\n"
    "2030-01-02T00:00:01+00:00,1.1.6,,1/0/2,,write,0,,1.001\n"
    "2030-01-02T00:00:02+00:00,1.1.6,,2/0/7,,write,0c1a,,9.001\n"
    "broken row\n"
)


async def test_import_find_and_export(tmp_path: Path) -> None:
    day = tmp_path / "day.csv.gz"
    day.write_bytes(gzip.compress(_CSV.encode()))
    mcp = build_server(_build_ctx(tmp_path))
    async with Client(mcp) as client:
        imported = await _data(client, "monitor_import", path=str(day))
        assert imported == {"imported": 3, "skipped": 1, "path": str(day)}

        found = await _data(client, "monitor_find", filter_text="src:1.1.6")
        assert (found["count"], found["matched"]) == (2, 2)
        assert [i["destination"] for i in found["items"]] == ["1/0/2", "2/0/7"]
        assert found["items"][0]["timestamp"].startswith("2030-01-02T00:00:01")
        newest = await _data(client, "monitor_find", filter_text="ga:1/0/", limit=1)
        assert (newest["count"], newest["matched"]) == (1, 2)
        assert newest["items"][0]["destination"] == "1/0/2"
        everything = await _data(client, "monitor_find", filter_text="", limit=0)
        assert everything["count"] == 3

        out = tmp_path / "out.csv"
        written = await _data(client, "monitor_export", path=str(out))
        assert written == {"telegrams": 3, "path": str(out)}
        assert out.read_text().splitlines()[3].split(",")[5:7] == ["write", "0c1a"]
        xml = tmp_path / "out.xml"
        await _data(client, "monitor_export", path=str(xml))
        assert xml.read_text().lstrip().startswith("<?xml")

        with pytest.raises(Exception, match="unknown monitor"):
            await client.call_tool(
                "monitor_find", {"filter_text": "", "monitor_name": "x"}
            )
        with pytest.raises(Exception, match="cannot import"):
            await client.call_tool(
                "monitor_import", {"path": str(tmp_path / "none.csv")}
            )


async def test_listen_only_refuses_sends_before_anything_is_queued(
    tmp_path: Path,
) -> None:
    ctx = _build_ctx(tmp_path)
    ctx.monitor.can_send = lambda: True  # type: ignore[method-assign]
    mcp = build_server(ctx)
    set_listen_only(True)
    try:
        async with Client(mcp) as client:
            for name, args in (
                ("monitor_send_read", {"address": "1/0/1"}),
                ("monitor_read_value", {"address": "1/0/1"}),
                (
                    "monitor_send_write",
                    {"address": "1/0/1", "value": "1", "dpt": "1.001"},
                ),
            ):
                with pytest.raises(Exception, match="Listen Only is on"):
                    await client.call_tool(name, args)
    finally:
        set_listen_only(False)

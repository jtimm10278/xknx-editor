"""MCP snapshot tools: settings, list, restore refused without a connection."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

import editor_gui.settings as app_settings
import editor_gui.snapshots as snapshots
from editor_gui.plugins.mcp.server import build_server
from xknxeditor.download.snapshot import Snapshot, save_snapshot

from .test_server import _build_ctx, _data


async def test_snapshot_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snapshots, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    folder = tmp_path / "snapshots"
    save_snapshot(
        Snapshot(
            address="1.1.31",
            taken="2026-11-13T21:00:00+00:00",
            scope="FULL",
            memory=[(0x8C00, b"\x01\x02")],
            properties=[(6, 205, 1, 1, b"\x10")],
        ),
        folder,
    )
    save_snapshot(
        Snapshot(address="1.1.32", taken="2026-11-13T20:00:00+00:00", scope="FULL"),
        folder,
    )
    mcp = build_server(_build_ctx(tmp_path))
    async with Client(mcp) as client:
        settings = await _data(client, "snapshot_settings")
        assert settings == {"before_download": True, "folder": str(folder)}
        changed = await _data(client, "snapshot_set_settings", before_download=False)
        assert changed["before_download"] is False
        assert snapshots.snapshots_enabled() is False
        listed = await _data(client, "snapshot_list")
        assert listed["count"] == 2
        one = await _data(client, "snapshot_list", device_address="1.1.31")
        (item,) = one["items"]
        assert item["octets"] == 2
        assert item["memory_ranges"] == [["0x8c00", 2]]
        assert item["properties"] == [[6, 205]]
        with pytest.raises(Exception, match="no such snapshot"):
            await client.call_tool(
                "snapshot_restore", {"path": str(tmp_path / "x.json")}
            )
        with pytest.raises(Exception, match="not started"):
            await client.call_tool("snapshot_restore", {"path": item["path"]})

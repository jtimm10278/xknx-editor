"""MCP project_tested_devices: the whole list, and the status of one device."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server

from .test_server import _FIXTURE, _build_ctx, _data


async def test_the_whole_list_needs_no_project(tmp_path: Path) -> None:
    async with Client(build_server(_build_ctx(tmp_path))) as client:
        result = await _data(client, "project_tested_devices")
    applications = {e["application"] for e in result["devices"]}
    assert "M-0001_A-2207-10" in applications
    entry = next(e for e in result["devices"] if e["application"] == "M-0001_A-2207-10")
    assert entry["proven"] == ["download", "read_back"]


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_a_device_not_on_the_list_is_not_yet_tested(tmp_path: Path) -> None:
    async with Client(build_server(_build_ctx(tmp_path))) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_product", path=str(_FIXTURE))
        products = (await _data(client, "catalog_list_products"))["items"]
        added = await _data(
            client, "project_add_device", product_ref_id=products[0]["product_ref_id"]
        )
        result = await _data(client, "project_tested_devices", node_id=added["node_id"])
    assert result["tested"] is False and result["entry"] is None
    assert result["application"].startswith("M-0008_A-7072-21")

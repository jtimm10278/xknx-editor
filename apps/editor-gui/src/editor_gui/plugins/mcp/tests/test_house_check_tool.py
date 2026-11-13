"""MCP house_check: a .knxproj against bus recordings, read only."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data

PROJECT = """<?xml version="1.0"?>
<KNX xmlns="http://knx.org/xml/project/23"><Project Id="P-0001"><Installations><Installation>
<Topology><Area Address="1"><Line Address="1">
<DeviceInstance Id="P-0001-0_DI-1" Address="5" Description="Switch"
 LastDownload="2025-06-01T10:00:00Z" ApplicationProgramLoaded="true"
 CommunicationPartLoaded="true" IndividualAddressLoaded="true" ParametersLoaded="true">
<ComObjectInstanceRefs><ComObjectInstanceRef RefId="O-1_R-1" Links="GA-1"/></ComObjectInstanceRefs>
</DeviceInstance></Line></Area></Topology>
<GroupAddresses><GroupRanges><GroupRange>
<GroupAddress Id="P-0001-0_GA-1" Address="2563" Name="Light"/>
</GroupRange></GroupRanges></GroupAddresses>
</Installation></Installations></Project></KNX>"""


async def test_house_check_tool(tmp_path: Path) -> None:
    proj = tmp_path / "p.knxproj"
    with zipfile.ZipFile(proj, "w") as z:
        z.writestr("P-0001/0.xml", PROJECT)
    (tmp_path / "knx_1.csv").write_text(
        "time,source,source_name,ga,ga_name,type,raw,value,dpt\n"
        "2026-11-01T08:00:00+00:00,1.1.5,,1/2/99,,write,1,1,\n",
        encoding="utf-8",
    )
    mcp = build_server(_build_ctx(tmp_path))
    async with Client(mcp) as client:
        out = await _data(
            client,
            "house_check",
            knxproj_path=str(proj),
            recordings=[str(tmp_path / "knx_*.csv")],
        )
        assert out["counts"]["sent_not_in_project"] == 1
        assert out["recording"]["files"] == 1
        assert out["findings"][0]["subject"] == "1/2/99"
        assert out["report"].startswith("# House check-up")
        bare = await _data(client, "house_check", knxproj_path=str(proj))
        assert bare["recording"]["files"] == 0
        with pytest.raises(Exception, match="no such file"):
            await client.call_tool(
                "house_check", {"knxproj_path": str(tmp_path / "x.knxproj")}
            )
        with pytest.raises(Exception, match="no recording matches"):
            await client.call_tool(
                "house_check",
                {
                    "knxproj_path": str(proj),
                    "recordings": [str(tmp_path / "none_*.csv")],
                },
            )

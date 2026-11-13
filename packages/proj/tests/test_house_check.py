"""House check-up: project (plain XML) against recorded bus traffic."""

from __future__ import annotations

import datetime as dt
import gzip
import zipfile
from pathlib import Path

import pytest

from xknxeditor.proj.core.house_check import (
    check,
    read_project,
    read_traffic,
    report,
)

PROJECT = """<?xml version="1.0"?>
<KNX xmlns="http://knx.org/xml/project/23"><Project Id="P-0001"><Installations><Installation>
<Topology><Area Address="1"><Line Address="1">
<DeviceInstance Id="P-0001-0_DI-1" Address="5" Description="Switch A"
 LastDownload="2025-06-01T10:00:00Z" ApplicationProgramLoaded="true"
 CommunicationPartLoaded="true" IndividualAddressLoaded="true" ParametersLoaded="true">
<ComObjectInstanceRefs><ComObjectInstanceRef RefId="O-1_R-1" Links="GA-1"/></ComObjectInstanceRefs>
</DeviceInstance>
<DeviceInstance Id="P-0001-0_DI-2" Address="200" Description="REMOVED: old switch"
 LastDownload="2010-01-01T00:00:00Z" ApplicationProgramLoaded="true"
 IndividualAddressLoaded="true" ParametersLoaded="true">
<ComObjectInstanceRefs><ComObjectInstanceRef RefId="O-1_R-1" Links="GA-1 GA-2"/></ComObjectInstanceRefs>
</DeviceInstance>
<DeviceInstance Id="P-0001-0_DI-3" Address="7" Description="New actuator"/>
<DeviceInstance Id="P-0001-0_DI-4" Description="Not addressed yet"/>
</Line></Area></Topology>
<GroupAddresses><GroupRanges><GroupRange>
<GroupAddress Id="P-0001-0_GA-1" Address="2563" Name="Light A"/>
<GroupAddress Id="P-0001-0_GA-2" Address="2564" Name="Light B"/>
<GroupAddress Id="P-0001-0_GA-3" Address="4096" Name="Setpoint (HA)"/>
<GroupAddress Id="P-0001-0_GA-4" Address="4097" Name="Nobody"/>
</GroupRange></GroupRanges></GroupAddresses>
</Installation></Installations></Project></KNX>"""

CSV = """time,source,source_name,ga,ga_name,type,raw,value,dpt
2026-11-01T08:00:00+00:00,1.1.5,,1/2/3,Light A,write,1,1,1.001
2026-11-01T08:00:01+00:00,1.0.255,,2/0/0,Setpoint (HA),write,1,1,9.001
2026-11-01T08:00:02+00:00,1.1.5,,1/2/99,,write,1,1,
2026-11-01T08:00:03+00:00,1.1.5,,1/2/99,,write,0,0,
2026-11-01T08:00:04+00:00,1.1.5,,1/2/3,Light A,read,,,
2026-11-01T08:00:05+00:00,1.1.5,,8/1,two level,write,1,1,
2026-11-01T08:00:06+00:00,1.1.5,,1/2/3,Light A,ack,,,
"""


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    proj = tmp_path / "p.knxproj"
    with zipfile.ZipFile(proj, "w") as z:
        z.writestr("P-0001/0.xml", PROJECT)
    rec = tmp_path / "knx_2026-11-01.csv.gz"
    with gzip.open(rec, "wt", encoding="utf-8") as fh:
        fh.write(CSV)
    return proj, rec


def _kinds(findings: list) -> dict[str, list[str]]:  # type: ignore[type-arg]
    out: dict[str, list[str]] = {}
    for f in findings:
        out.setdefault(f.kind, []).append(f.subject)
    return out


def test_findings(files: tuple[Path, Path]) -> None:
    proj, rec = files
    view = read_project(proj)
    traffic = read_traffic([rec])
    assert traffic.group_count == {"1/2/3": 2, "2/0/0": 1, "1/2/99": 2}
    assert traffic.first.startswith("2026-11-01T08:00:00")
    found = _kinds(check(view, traffic, now=dt.datetime(2026, 11, 13, tzinfo=dt.UTC)))
    assert found["sent_not_in_project"] == ["1/2/99"]
    assert found["parked_with_links"] == ["1.1.200"]
    assert found["unused"] == ["2/0/1"]
    assert found["unlinked_but_used"] == ["2/0/0"]
    assert found["old_download"] == ["1.1.200"]
    assert found["never_downloaded"] == ["1.1.7"]
    # the old switch has links but its group links are not marked loaded; the new actuator has
    # no links, so only its other flags count
    assert sorted(found["not_loaded"]) == ["1.1.200", "1.1.7"]
    assert found["sender_not_in_project"] == ["1.0.255"]


def test_without_traffic(files: tuple[Path, Path]) -> None:
    proj, _ = files
    found = _kinds(
        check(read_project(proj), None, now=dt.datetime(2026, 11, 13, tzinfo=dt.UTC))
    )
    assert "sent_not_in_project" not in found
    assert "sender_not_in_project" not in found
    assert found["unused"] == ["2/0/0", "2/0/1"]


def test_report_lists_every_section(files: tuple[Path, Path]) -> None:
    proj, rec = files
    traffic = read_traffic([rec])
    text = report(
        check(read_project(proj), traffic), project="p.knxproj", traffic=traffic
    )
    assert text.startswith("# House check-up")
    assert "## Sent on the bus but not in the project (1)" in text
    assert "## Group addresses nobody uses (1)" in text
    assert "Bus recording: 2026-11-01T08:00 to 2026-11-01T08:00" in text
    assert "(2 telegrams, from 1.1.5" in text
    assert "holds 2 links" in text
    bare = report(check(read_project(proj), None), project="p.knxproj")
    assert "## Sent on the bus but not in the project (0)\n- None." in bare


def test_protected_project(tmp_path: Path) -> None:
    proj = tmp_path / "x.knxproj"
    with zipfile.ZipFile(proj, "w") as z:
        z.writestr("P-0001.zip", b"x")
    with pytest.raises(ValueError):
        read_project(proj)

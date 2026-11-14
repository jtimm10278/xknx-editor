"""Hand Over to ETS: only the new and changed group addresses go into the import file, removed ones
become checklist items, and the HTML checklist escapes what it shows."""

from __future__ import annotations

import io
import zipfile
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from editor_gui.plugins.project.change_list import change_list_html
from editor_gui.plugins.project.ets_handover import (
    CHANGE_HTML,
    GA_XML,
    README,
    build_handover,
    handed_over_addresses,
    handover_zip,
    readme,
)
from editor_gui.plugins.project.ui.ets_handover_dialog import EtsHandoverDialog
from editor_gui.plugins.project.ui.project_diff import DiffJob, DiffOutcome, DiffResult

_DATA: dict[str, Any] = {
    "format": "xknx-editor-change-list/1",
    "before": "Site <backup>",
    "after": "Edited",
    "made": "2026-11-14T10:00:00+00:00",
    "summary": {"devices": 1, "group_addresses": 3},
    "devices": [
        {
            "address": "1.0.62",
            "change": "added",
            "detail": "new device",
            "parameters": [
                {
                    "label": "Rocker > Function",
                    "ref": "P-1",
                    "change": "changed",
                    "before": "Switch",
                    "after": "Shutter",
                    "before_value": "4",
                    "after_value": "0",
                }
            ],
            "group_objects": [
                {
                    "object": "Shutter",
                    "number": 5,
                    "change": "changed",
                    "before": "",
                    "after": "-> 3/0/2",
                    "links_before": [],
                    "links_after": ["3/0/2"],
                    "link": ["3/0/2"],
                    "unlink": [],
                }
            ],
        }
    ],
    "group_addresses": [
        {"address": "1/0/2", "change": "added", "detail": "New one"},
        {"address": "1/0/3", "change": "changed", "detail": "name"},
        {"address": "1/0/9", "change": "removed", "detail": "Old"},
    ],
}


def test_only_added_and_changed_addresses_are_imported() -> None:
    raw = {"1/0/2": 0x0802, "1/0/3": 0x0803}
    assert handed_over_addresses(_DATA, raw) == [0x0802, 0x0803]
    assert handed_over_addresses({"group_addresses": []}, raw) == []


def test_readme_gives_the_steps_and_the_removed_count() -> None:
    text = readme(_DATA, 2)
    assert "back up the project first" in text
    assert "Import the 2 new or changed group addresses" in text
    assert "Delete the 1 group addresses marked removed by hand" in text
    assert "No group addresses to import." in readme(
        {**_DATA, "group_addresses": []}, 0
    )


def test_html_checklist_escapes_and_has_a_box_per_item() -> None:
    html = change_list_html(_DATA)
    assert "Site &lt;backup&gt;" in html and "<backup>" not in html
    # Three group addresses, the address line, one parameter and one group object.
    assert html.count('type="checkbox"') == 6
    assert "an import never removes one" in html
    assert "Obj 5 Shutter: link 3/0/2" in html
    empty = change_list_html({**_DATA, "devices": [], "group_addresses": []})
    assert "Nothing to change" in empty


def test_bundle_from_a_real_project(tmp_path: Path) -> None:
    from xknxeditor.proj.core.ga_export import import_ga_export

    source = tmp_path / "e.xml"
    source.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<GroupAddress-Export xmlns="http://knx.org/xml/ga-export/01">\n'
        '  <GroupRange Name="Light" RangeStart="2048" RangeEnd="4095">\n'
        '    <GroupRange Name="Switch" RangeStart="2048" RangeEnd="2303">\n'
        '      <GroupAddress Name="Kept" Address="1/0/1" />\n'
        '      <GroupAddress Name="New one" Address="1/0/2" />\n'
        "    </GroupRange>\n"
        "  </GroupRange>\n"
        "</GroupAddress-Export>\n",
        encoding="utf-8",
    )
    project = tmp_path / "p.xknx"
    import_ga_export(source, project)
    files = build_handover(_DATA, project, {"1/0/2": 0x0802})
    assert b'Address="1/0/2"' in files[GA_XML] and b"1/0/1" not in files[GA_XML]
    assert CHANGE_HTML in files and README in files
    with zipfile.ZipFile(io.BytesIO(handover_zip(files))) as archive:
        assert set(archive.namelist()) == set(files)
    no_ga = build_handover({**_DATA, "group_addresses": []}, project, {})
    assert GA_XML not in no_ga


def _job(outcome: DiffOutcome) -> DiffJob:
    job = DiffJob()
    job.future = Future()
    job.future.set_result(outcome)
    return job


def test_dialog_compares_asks_for_a_password_and_saves(tmp_path: Path) -> None:
    outcomes = [
        DiffOutcome(None, "locked", needs_password=True),
        DiffOutcome(DiffResult(devices=[], group_addresses=[])),
    ]
    calls: list[tuple[str, str | None]] = []

    def start(path: str, password: str | None) -> DiffJob:
        calls.append((path, password))
        return _job(outcomes.pop(0))

    saved: list[str] = []
    dialog = EtsHandoverDialog(
        start, lambda result, name: {"README.txt": name.encode()}, saved.append
    )
    dialog.choose_baseline(str(tmp_path / "Site.knxproj"))
    dialog.poll()
    assert dialog.result is None
    dialog._password = "secret"  # pyright: ignore[reportPrivateUsage]
    dialog.compare()
    dialog.poll()
    assert dialog.result is not None and not dialog.comparing
    assert calls == [
        (str(tmp_path / "Site.knxproj"), None),
        (str(tmp_path / "Site.knxproj"), "secret"),
    ]
    target = dialog.save_to(str(tmp_path / "out"))
    assert target.name == "out.zip"
    with zipfile.ZipFile(target) as archive:
        assert archive.read("README.txt") == b"Site"
    assert saved and "out.zip" in saved[0]

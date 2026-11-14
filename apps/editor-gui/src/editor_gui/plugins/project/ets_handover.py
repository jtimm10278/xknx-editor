"""Hand Over to ETS: the files a person needs to bring an ETS project up to date with the
project edited here, without the editor writing or signing anything for ETS.

Made from a comparison of a baseline (the project as ETS has it: an ``.xknx``, a ``.knxproj`` or a
backup ``.zip``) with the open project (see :mod:`.change_list`). The hand-over is one ZIP:

- ``group-addresses.xml`` / ``group-addresses.csv``: only the group addresses that are new or
  changed since the baseline, in the formats ETS exports and imports (``ga-export/01`` XML, CSV
  3/1 with tabs);
- ``change-list.html`` (a checklist to tick off), ``change-list.md`` and ``change-list.json``: per
  device the address change, the parameters by their ETS names and option texts, and the links
  to add and remove per group object; then the group addresses;
- ``README.txt``: the order of the steps in ETS.
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from editor_gui.plugins.project.change_list import (
    change_list_html,
    change_list_markdown,
)

GA_XML = "group-addresses.xml"
GA_CSV = "group-addresses.csv"
CHANGE_HTML = "change-list.html"
CHANGE_MD = "change-list.md"
CHANGE_JSON = "change-list.json"
README = "README.txt"


def handed_over_addresses(
    data: Mapping[str, Any], raw_of: Mapping[str, int]
) -> list[int]:
    """The raw addresses of the group addresses the change list adds or changes (removed ones
    cannot be imported: ETS gets them as checklist items). ``raw_of`` maps the printed
    addresses of the open project to their raw values."""
    found: list[int] = []
    for g in data.get("group_addresses", []):
        if g.get("change") in ("added", "changed") and g.get("address") in raw_of:
            found.append(raw_of[g["address"]])
    return sorted(set(found))


def readme(data: Mapping[str, Any], addresses: int) -> str:
    devices = len(data.get("devices", []))
    removed = sum(
        1 for g in data.get("group_addresses", []) if g.get("change") == "removed"
    )
    lines = [
        f"Hand-over for ETS: {data['before']} -> {data['after']}",
        f"Made by XKNX Editor{' ' + data['made'] if data.get('made') else ''}.",
        "",
        "Nothing here is signed or written into an ETS project: you make the changes in ETS",
        "yourself, so ETS keeps its own project and signatures.",
        "",
        "1. In ETS, back up the project first (Export a .knxproj, or Backup in the overview).",
    ]
    if addresses:
        lines += [
            f"2. Import the {addresses} new or changed group addresses: in the Group Addresses",
            f"   panel choose Import and pick {GA_XML} ({GA_CSV} holds the same as CSV).",
        ]
    else:
        lines += ["2. No group addresses to import."]
    if removed:
        lines += [
            f"   Delete the {removed} group addresses marked removed by hand: an import never",
            "   removes one.",
        ]
    lines += [
        f"3. Open {CHANGE_HTML} and work through it device by device ({devices} devices):",
        "   address changes, parameters, then the links of each group object. Tick each line.",
        "4. Check the changed devices in ETS, then download them from ETS.",
        "",
        f"{CHANGE_MD} and {CHANGE_JSON} hold the same list as text and for programs.",
        "",
    ]
    return "\r\n".join(lines)


def build_handover(
    data: Mapping[str, Any],
    project_file: Path,
    raw_of: Mapping[str, int],
) -> dict[str, bytes]:
    """The hand-over's files (name -> content) for the change list ``data`` of the project in
    ``project_file`` (its ``.xknx``; only read)."""
    from xknxeditor.proj.core.ga_export import write_ga_csv, write_ga_export

    addresses: Collection[int] = handed_over_addresses(data, raw_of)
    files: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="xknx-handover-") as tmp:
        if addresses:
            xml, csv = Path(tmp, GA_XML), Path(tmp, GA_CSV)
            write_ga_export(project_file, xml, only=addresses)
            write_ga_csv(project_file, csv, only=addresses)
            files[GA_XML] = xml.read_bytes()
            files[GA_CSV] = csv.read_bytes()
    files[CHANGE_HTML] = change_list_html(dict(data)).encode()
    files[CHANGE_MD] = change_list_markdown(dict(data)).encode()
    files[CHANGE_JSON] = json.dumps(data, indent=1, ensure_ascii=False).encode()
    files[README] = readme(data, len(addresses)).encode()
    return files


def handover_zip(files: Mapping[str, bytes]) -> bytes:
    """The files as one ZIP."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()

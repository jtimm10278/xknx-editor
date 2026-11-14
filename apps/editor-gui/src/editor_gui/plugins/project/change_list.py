"""A change list for ETS: what to do in an ETS project so it matches the project edited here.

Built from a project comparison (:func:`.ui.project_diff.diff_projects`) whose base is the
project as ETS has it ("before", e.g. a Master backup) and whose other side is the edited
project ("after"). Pure, so it is tested without a project or imgui. :func:`change_list` gives
the JSON form (a dict, ``format`` :data:`FORMAT`), :func:`change_list_markdown` the same for a
person working in ETS: per device the individual address, the parameters by their ETS label
and option text, and the group objects with the links to add and remove; then the group
addresses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from editor_gui.plugins.project.ui.project_diff import (
        DetailRow,
        DiffEntry,
        DiffResult,
    )

FORMAT = "xknx-editor-change-list/1"
# The group-object summary of a comparison ends with its linked group addresses after this.
_LINKS = "-> "
_PART = "  ·  "


def change_list(
    result: DiffResult, before: str, after: str, made: str = ""
) -> dict[str, Any]:
    """The JSON change list from ``before`` (the comparison's base) to ``after``; ``made`` is
    the time it was made (ISO 8601), left out when empty."""
    out: dict[str, Any] = {"format": FORMAT, "before": before, "after": after}
    if made:
        out["made"] = made
    out["summary"] = {
        "devices": len(result.devices),
        "group_addresses": len(result.group_addresses),
    }
    out["devices"] = [_device(e) for e in result.devices]
    out["group_addresses"] = [
        {"address": e.key, "change": e.kind, "detail": e.detail}
        for e in result.group_addresses
    ]
    return out


def _device(entry: DiffEntry) -> dict[str, Any]:
    device: dict[str, Any] = {
        "address": entry.key,
        "change": entry.kind,
        "detail": entry.detail,
    }
    if entry.rows:
        device["parameters"] = [
            _parameter(r)
            for r in entry.rows
            if r.section == "parameter" and (r.current or r.other)
        ]
        device["group_objects"] = [
            _group_object(r) for r in entry.rows if r.section == "group_object"
        ]
    return device


def _parameter(row: DetailRow) -> dict[str, str]:
    return {
        "label": row.label,
        "ref": row.key,
        "change": row.kind,
        "before": row.current_text or row.current,
        "after": row.other_text or row.other,
        "before_value": row.current,
        "after_value": row.other,
    }


def _group_object(row: DetailRow) -> dict[str, Any]:
    before, after = links(row.current), links(row.other)
    return {
        "object": row.label,
        "number": int(row.key) if row.key.isdigit() else None,
        "change": row.kind,
        "before": row.current,
        "after": row.other,
        "links_before": before,
        "links_after": after,
        "link": [a for a in after if a not in before],
        "unlink": [b for b in before if b not in after],
    }


def links(summary: str) -> list[str]:
    """The group addresses a group-object summary of the comparison links, in its order."""
    tail = next(
        (p for p in summary.split(_PART) if p.startswith(_LINKS)), ""
    ).removeprefix(_LINKS)
    return tail.split()


def change_list_markdown(data: dict[str, Any]) -> str:
    """The change list as Markdown, for a person to apply in ETS."""
    lines = [
        f"# Change list for ETS: {data['before']} -> {data['after']}",
        "",
        f"Made by XKNX Editor{' ' + data['made'] if data.get('made') else ''} from a comparison of "
        f"the two projects. Apply it in ETS to **{data['before']}** so it matches "
        f"**{data['after']}**. JSON format `{data['format']}`.",
        "",
    ]
    devices: list[dict[str, Any]] = data["devices"]
    gas: list[dict[str, Any]] = data["group_addresses"]
    if not devices and not gas:
        lines += ["Nothing to change: the projects are the same.", ""]
        return "\n".join(lines)
    lines += [f"## Devices ({len(devices)})", ""] if devices else []
    for device in devices:
        lines += _device_markdown(device)
    if gas:
        lines += [f"## Group addresses ({len(gas)})", ""]
        lines += [f"- **{g['address']}** {g['change']}: {g['detail']}" for g in gas] + [
            ""
        ]
    return "\n".join(lines)


def _device_markdown(device: dict[str, Any]) -> list[str]:
    lines = [f"### {device['address']} ({device['change']})", ""]
    if device["detail"]:
        lines += [device["detail"], ""]
    parameters: list[dict[str, str]] = device.get("parameters", [])
    if parameters:
        lines += ["Parameters:", ""]
        for p in parameters:
            ref = f" `{p['ref']}`" if p.get("ref") else ""
            lines.append(
                f"- {p['label']}{ref}: {_shown(p['before'])} -> "
                f"**{_shown(p['after'])}**" + _raw(p)
            )
        lines.append("")
    objects: list[dict[str, Any]] = device.get("group_objects", [])
    if objects:
        lines += ["Group objects:", ""]
        lines += [f"- {_number(o)}{o['object']}: {_object_todo(o)}" for o in objects]
        lines += [
            "",
            "When a parameter gives an object another function, ETS may drop its links: "
            "check that each object keeps the links shown.",
            "",
        ]
    return lines


def _number(o: dict[str, Any]) -> str:
    return f"Obj {o['number']} " if o.get("number") is not None else ""


def _object_todo(o: dict[str, Any]) -> str:
    """What to do with a group object in ETS."""
    actions = [f"link {', '.join(o['link'])}"] if o["link"] else []
    actions += [f"unlink {', '.join(o['unlink'])}"] if o["unlink"] else []
    if o["before"] and o["after"] and _settings(o["before"]) != _settings(o["after"]):
        actions.append(
            f"settings {_settings(o['before'])} -> **{_settings(o['after'])}**"
        )
    elif not o["before"]:
        actions.append(f"new object: {_settings(o['after']) or '(no settings)'}")
    elif not o["after"]:
        actions.append("object no longer there")
    kept = [a for a in o["links_after"] if a in o["links_before"]]
    if kept:
        actions.append(f"keeps {', '.join(kept)}")
    return "; ".join(actions) or "no change in ETS"


def _settings(summary: str) -> str:
    """A group-object summary without its links."""
    return _PART.join(p for p in summary.split(_PART) if not p.startswith(_LINKS))


def _shown(value: str) -> str:
    return f'"{value}"' if value else "(none)"


def _raw(parameter: dict[str, str]) -> str:
    """The stored values, when the option texts hide them."""
    if (parameter["before"], parameter["after"]) == (
        parameter["before_value"],
        parameter["after_value"],
    ):
        return ""
    return f" (stored {parameter['before_value'] or '-'} -> {parameter['after_value'] or '-'})"


def change_list_html(data: dict[str, Any]) -> str:
    """The change list as a stand-alone HTML page to tick off while working in ETS: a checkbox
    per address change, parameter and group object, and per group address (the boxes are for
    the reader; nothing is saved)."""
    from html import escape

    def box(text: str) -> str:
        return f'<li><label><input type="checkbox"> {text}</label></li>'

    out = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Change list for ETS: {escape(data['before'])}</title>",
        "<style>body{font-family:system-ui,sans-serif;max-width:52rem;margin:1rem auto;"
        "padding:0 1rem;line-height:1.45}ul{list-style:none;padding-left:0}"
        "li{margin:.25rem 0}code{font-size:.9em}h3{margin-top:1.6rem}"
        ".note{color:#666;font-size:.9em}@media print{input{transform:scale(1.2)}}</style>",
        "</head><body>",
        f"<h1>Change list for ETS: {escape(data['before'])} &rarr; {escape(data['after'])}</h1>",
        f'<p class="note">Made by XKNX Editor{" " + escape(data["made"]) if data.get("made") else ""}. '
        f"Apply it in ETS to <b>{escape(data['before'])}</b> so that it matches "
        f"<b>{escape(data['after'])}</b>. Back up the ETS project first.</p>",
    ]
    devices: list[dict[str, Any]] = data["devices"]
    gas: list[dict[str, Any]] = data["group_addresses"]
    if not devices and not gas:
        out += [
            "<p>Nothing to change: the projects are the same.</p>",
            "</body></html>",
        ]
        return "\n".join(out)
    if gas:
        out.append(f"<h2>Group addresses ({len(gas)})</h2><ul>")
        for g in gas:
            todo = (
                "delete it by hand in ETS (an import never removes one)"
                if g["change"] == "removed"
                else "in the group address import, or set it by hand"
            )
            out.append(
                box(
                    f"<b>{escape(g['address'])}</b> {escape(g['change'])}: "
                    f'{escape(g["detail"])} <span class="note">({todo})</span>'
                )
            )
        out.append("</ul>")
    if devices:
        out.append(f"<h2>Devices ({len(devices)})</h2>")
    for device in devices:
        out.append(
            f"<h3>{escape(device['address'])} ({escape(device['change'])})</h3><ul>"
        )
        if device["detail"]:
            out.append(box(escape(device["detail"])))
        for p in device.get("parameters", []):
            out.append(
                box(
                    f"{escape(p['label'])}: {escape(_shown(p['before']))} &rarr; "
                    f"<b>{escape(_shown(p['after']))}</b>{escape(_raw(p))}"
                )
            )
        for o in device.get("group_objects", []):
            todo = _object_todo(o).replace("**", "")
            out.append(
                box(f"{escape(_number(o))}{escape(o['object'])}: {escape(todo)}")
            )
        out.append("</ul>")
        if device.get("group_objects"):
            out.append(
                '<p class="note">When a parameter gives an object another function, ETS may '
                "drop its links: check that each object keeps the links shown.</p>"
            )
    out.append("</body></html>")
    return "\n".join(out)

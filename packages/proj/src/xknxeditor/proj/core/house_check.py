"""House check-up: a project (.knxproj, plain XML only) set against recorded bus traffic.

Read only. It lists, in plain words:

- group addresses sent on the bus that the project does not have;
- devices described as removed/parked/spare/old that still hold group links;
- group addresses no device links (split into "seen on the bus", i.e. used only by tools such as
  Home Assistant or Node-RED, and "never seen");
- devices whose last download is old or missing, or whose ETS "loaded" flags are off;
- senders on the bus that are not in the project.

The recording is the bus recorder's CSV (``time,source,source_name,ga,ga_name,type,...``, plain
or ``.gz``).
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

PARKED_WORDS = re.compile(
    r"\b(removed|parked|spare|old|unused|not installed|dead)\b", re.I
)
LOADED_FLAGS = (
    "ApplicationProgramLoaded",
    "CommunicationPartLoaded",
    "IndividualAddressLoaded",
    "ParametersLoaded",
)


def _ga(value: int) -> str:
    return f"{value >> 11}/{(value >> 8) & 7}/{value & 255}"


@dataclass
class ProjectDevice:
    address: str  # "1.1.5", "" when not addressed
    name: str
    description: str
    product: str
    last_download: dt.datetime | None
    flags: dict[str, bool]
    groups: set[str] = field(default_factory=set[str])


@dataclass
class ProjectView:
    devices: list[ProjectDevice] = field(default_factory=list[ProjectDevice])
    groups: dict[str, str] = field(default_factory=dict[str, str])  # "1/2/3" -> name


@dataclass
class Traffic:
    """Per group address: telegram count, senders and the last time seen; senders overall."""

    group_count: dict[str, int] = field(default_factory=dict[str, int])
    group_senders: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    group_last: dict[str, str] = field(default_factory=dict[str, str])
    senders: dict[str, int] = field(default_factory=dict[str, int])
    first: str = ""
    last: str = ""


def _ns(root: ET.Element) -> str:
    return root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""


def _when(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", text)
        if not m:
            return None
        value = dt.datetime.fromisoformat(m.group(1))
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def read_project(path: str | Path) -> ProjectView:
    """Devices, their links and flags, and the group addresses of a .knxproj (plain XML)."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"P-[0-9A-F]+/0\.xml", n)]
        if not names:
            raise ValueError("no plain P-*/0.xml (protected project?)")
        root = ET.fromstring(z.read(names[0]))
    ns = _ns(root)
    view = ProjectView()
    by_id: dict[str, str] = {}
    for g in root.iter(ns + "GroupAddress"):
        text = _ga(int(g.get("Address", "0")))
        view.groups[text] = g.get("Name", "")
        gid = g.get("Id", "")
        by_id[gid] = text
        by_id[gid.split("_")[-1]] = text
    for area in root.iter(ns + "Area"):
        for line in area.iter(ns + "Line"):
            for d in line.iter(ns + "DeviceInstance"):
                number = d.get("Address")
                address = (
                    f"{area.get('Address')}.{line.get('Address')}.{number}"
                    if number is not None
                    else ""
                )
                groups: set[str] = set()
                for ref in d.iter(ns + "ComObjectInstanceRef"):
                    for link in (ref.get("Links") or "").split():
                        text = by_id.get(link) or by_id.get(link.split("_")[-1])
                        if text:
                            groups.add(text)
                view.devices.append(
                    ProjectDevice(
                        address=address,
                        name=d.get("Name", ""),
                        description=" ".join((d.get("Description") or "").split()),
                        product=d.get("ProductRefId", ""),
                        last_download=_when(d.get("LastDownload")),
                        flags={
                            f: (d.get(f) or "").lower() == "true" for f in LOADED_FLAGS
                        },
                        groups=groups,
                    )
                )
    return view


def read_traffic(paths: Iterable[str | Path]) -> Traffic:
    """Group telegrams (write/response/read) from recorder CSV files (plain or .gz)."""
    t = Traffic()
    for path in sorted(str(p) for p in paths):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", newline="") as fh:  # type: ignore[operator]
            for row in csv.DictReader(fh):  # type: ignore[arg-type]
                ga, src, when = (
                    row.get("ga") or "",
                    row.get("source") or "",
                    row.get("time") or "",
                )
                if ga.count("/") != 2 or row.get("type") not in (
                    "write",
                    "response",
                    "read",
                ):
                    continue
                t.group_count[ga] = t.group_count.get(ga, 0) + 1
                t.group_senders.setdefault(ga, set()).add(src)
                t.group_last[ga] = max(t.group_last.get(ga, ""), when)
                t.senders[src] = t.senders.get(src, 0) + 1
                t.first = min(t.first, when) if t.first else when
                t.last = max(t.last, when)
    return t


@dataclass
class Finding:
    kind: str
    subject: str
    text: str


def check(
    view: ProjectView,
    traffic: Traffic | None = None,
    *,
    now: dt.datetime | None = None,
    old_years: float = 5.0,
) -> list[Finding]:
    """The check-up findings, in report order."""
    now = now or dt.datetime.now(dt.UTC)
    out: list[Finding] = []
    known_devices = {d.address for d in view.devices if d.address}
    linked = {g for d in view.devices for g in d.groups}

    if traffic is not None:
        for ga in sorted(traffic.group_count, key=_key):
            if ga in view.groups:
                continue
            senders = ", ".join(sorted(traffic.group_senders[ga]))
            out.append(
                Finding(
                    "sent_not_in_project",
                    ga,
                    f"{ga} is sent on the bus ({_n(traffic.group_count[ga], 'telegram')}, from "
                    f"{senders}, last {traffic.group_last[ga][:16]}) but is not in the project.",
                )
            )

    for d in view.devices:
        label = d.address or "(no address)"
        if d.groups and PARKED_WORDS.search(f"{d.name} {d.description}"):
            out.append(
                Finding(
                    "parked_with_links",
                    label,
                    f"{label} “{d.description or d.name}” looks removed or parked but "
                    f"still holds {_n(len(d.groups), 'link')}: {', '.join(sorted(d.groups, key=_key))}.",
                )
            )

    for ga in sorted(view.groups, key=_key):
        if ga in linked:
            continue
        name = view.groups[ga]
        if traffic is not None and ga in traffic.group_count:
            senders = ", ".join(sorted(traffic.group_senders[ga]))
            out.append(
                Finding(
                    "unlinked_but_used",
                    ga,
                    f"{ga} “{name}” has no device link but is used on the bus "
                    f"(from {senders}): kept by tools such as Home Assistant or Node-RED.",
                )
            )
        else:
            seen = " and was not seen on the bus" if traffic is not None else ""
            out.append(
                Finding(
                    "unused",
                    ga,
                    f"{ga} “{name}” has no device link{seen}.",
                )
            )

    limit = now - dt.timedelta(days=365.25 * old_years)
    for d in view.devices:
        if not d.address:
            continue
        if d.last_download is None:
            out.append(
                Finding(
                    "never_downloaded",
                    d.address,
                    f"{d.address} “{d.description or d.name}” has no recorded download.",
                )
            )
        elif d.last_download < limit:
            out.append(
                Finding(
                    "old_download",
                    d.address,
                    f"{d.address} “{d.description or d.name}” was last downloaded "
                    f"{d.last_download:%d %b %Y}.",
                )
            )
        off = [f for f, on in d.flags.items() if not on]
        if off:
            # the communication part only matters for a device with group links
            relevant = [f for f in off if f != "CommunicationPartLoaded" or d.groups]
            if relevant:
                words = ", ".join(_flag_words(f) for f in relevant)
                out.append(
                    Finding(
                        "not_loaded",
                        d.address,
                        f"{d.address} “{d.description or d.name}”: ETS does not "
                        f"mark {words} as loaded.",
                    )
                )

    if traffic is not None:
        for src in sorted(traffic.senders, key=_ia_key):
            if src in known_devices:
                continue
            out.append(
                Finding(
                    "sender_not_in_project",
                    src,
                    f"{src} sends on the bus ({_n(traffic.senders[src], 'telegram')}) but is not a "
                    "device in the project (a tunnel, gateway or tool, or a device with the "
                    "wrong address).",
                )
            )
    return out


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _flag_words(flag: str) -> str:
    return {
        "ApplicationProgramLoaded": "the application",
        "CommunicationPartLoaded": "the group links",
        "IndividualAddressLoaded": "the address",
        "ParametersLoaded": "the parameters",
    }[flag]


def _key(ga: str) -> tuple[int, ...]:
    return tuple(int(p) for p in ga.split("/"))


def _ia_key(ia: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in ia.split("."))
    except ValueError:
        return (999,)


SECTIONS = (
    ("sent_not_in_project", "Sent on the bus but not in the project"),
    ("parked_with_links", "Removed or parked devices that still hold links"),
    ("unused", "Group addresses nobody uses"),
    ("unlinked_but_used", "Group addresses with no device link, used by other tools"),
    ("old_download", "Devices last downloaded long ago"),
    ("never_downloaded", "Devices with no recorded download"),
    ("not_loaded", "Devices whose ETS 'loaded' flags are off"),
    ("sender_not_in_project", "Senders on the bus that are not in the project"),
)


def report(
    findings: list[Finding], *, project: str = "", traffic: Traffic | None = None
) -> str:
    """The findings as a plain-words Markdown report."""
    lines = ["# House check-up", ""]
    if project:
        lines.append(f"Project: {project}")
    if traffic is not None and traffic.first:
        lines.append(f"Bus recording: {traffic.first[:16]} to {traffic.last[:16]}")
    lines.append("")
    for kind, title in SECTIONS:
        items = [f for f in findings if f.kind == kind]
        lines.append(f"## {title} ({len(items)})")
        lines.extend(f"- {f.text}" for f in items) if items else lines.append("- None.")
        lines.append("")
    return "\n".join(lines)

"""Devices proven to work with this editor: a community list (``tested_devices.json`` next to this
module) of applications someone has downloaded and/or read back with it.

An entry names its application by manufacturer, number and version (``M-0001_A-2207-10``, the
application id without its hash), so it fits every device that runs that application. How to add
one: CONTRIBUTING.md, "Adding a tested device".
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from editor_gui.device import Device

DATA_FILE = Path(__file__).parent / "tested_devices.json"
FORMAT = 1
# What can be proven: a download, a read-back that matched, an edit made in a plug-in view.
PROOFS = ("download", "read_back", "plugin_edit")

_APPLICATION = re.compile(r"^(M-[0-9A-F]{4})_A-([0-9A-F]{4})-([0-9A-F]{2})", re.I)


class TestedDevicesError(ValueError):
    """The list cannot be read: names the entry and what is wrong."""


@dataclass(frozen=True)
class TestedDevice:
    manufacturer: str
    product: str
    order_number: str
    application: str  # "M-0001_A-2207-10"
    application_name: str
    mask: str  # "MV-0011", as the product data gives it
    proven: tuple[str, ...]
    date: str  # ISO date of the proof
    xets_version: str
    notes: str = ""

    def matches(self, application_id: str) -> bool:
        return application_key(application_id) == application_key(self.application)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manufacturer": self.manufacturer,
            "product": self.product,
            "order_number": self.order_number,
            "application": self.application,
            "application_name": self.application_name,
            "mask": self.mask,
            "proven": list(self.proven),
            "date": self.date,
            "xets_version": self.xets_version,
            "notes": self.notes,
        }


def application_key(application_id: str) -> str | None:
    """``M-0001_A-2207-10`` of an application id (its hash and any suffix dropped), upper case;
    None when it is not an application id."""
    m = _APPLICATION.match(application_id)
    if m is None:
        return None
    manufacturer, number, version = (part.upper() for part in m.groups())
    return f"{manufacturer}_A-{number}-{version}"


_FIELDS = (
    "manufacturer",
    "product",
    "order_number",
    "application",
    "application_name",
    "mask",
    "date",
    "xets_version",
)


def parse(data: Any) -> list[TestedDevice]:
    """The entries of the list's JSON data; raises TestedDevicesError naming what is wrong."""
    if not isinstance(data, dict):
        raise TestedDevicesError(f"expected a JSON object with format {FORMAT}")
    doc: dict[str, Any] = data  # pyright: ignore[reportUnknownVariableType]
    if doc.get("format") != FORMAT:
        raise TestedDevicesError(f"expected a JSON object with format {FORMAT}")
    raw_devices: Any = doc.get("devices")
    if not isinstance(raw_devices, list):
        raise TestedDevicesError("'devices' must be a list")
    entries: list[TestedDevice] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_devices):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        where = f"devices[{index}]"
        if not isinstance(raw, dict):
            raise TestedDevicesError(f"{where}: expected an object")
        item: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
        for name in _FIELDS:
            if not isinstance(item.get(name), str) or not item[name].strip():
                raise TestedDevicesError(f"{where}: {name!r} must be a text")
        key = application_key(item["application"])
        if key is None or key != item["application"].upper():
            raise TestedDevicesError(
                f"{where}: 'application' must look like M-0001_A-2207-10"
            )
        if key in seen:
            raise TestedDevicesError(f"{where}: {key} is listed twice")
        seen.add(key)
        proven = item.get("proven")
        if (
            not isinstance(proven, list)
            or not proven
            or any(p not in PROOFS for p in proven)  # pyright: ignore[reportUnknownVariableType]
        ):
            raise TestedDevicesError(
                f"{where}: 'proven' must list one or more of {', '.join(PROOFS)}"
            )
        try:
            datetime.date.fromisoformat(item["date"])
        except ValueError as exc:
            raise TestedDevicesError(f"{where}: 'date' must be YYYY-MM-DD") from exc
        notes = item.get("notes", "")
        if not isinstance(notes, str):
            raise TestedDevicesError(f"{where}: 'notes' must be a text")
        entries.append(
            TestedDevice(
                manufacturer=item["manufacturer"],
                product=item["product"],
                order_number=item["order_number"],
                application=item["application"],
                application_name=item["application_name"],
                mask=item["mask"],
                proven=tuple(p for p in PROOFS if p in proven),  # pyright: ignore[reportUnknownArgumentType]
                date=item["date"],
                xets_version=item["xets_version"],
                notes=notes,
            )
        )
    return entries


@cache
def tested_devices() -> tuple[TestedDevice, ...]:
    """The bundled list (read once)."""
    return tuple(parse(json.loads(DATA_FILE.read_text(encoding="utf-8"))))


def find(application_id: str | None) -> TestedDevice | None:
    """The entry for an application id, None when it is not (yet) listed."""
    if not application_id:
        return None
    key = application_key(application_id)
    return next((e for e in tested_devices() if key and e.matches(key)), None)


def for_device(device: Device) -> TestedDevice | None:
    return find(device.app.program.id) if device.app is not None else None

"""Device snapshot before a download: read and keep what the download is about to overwrite.

:func:`plan_from_dry_run` turns a dry run of the download (:mod:`.dry_run`, which sends nothing)
into the memory ranges and properties it would write. :func:`take_snapshot` reads exactly those
from the device (reads only) and :func:`save_snapshot` stores them as JSON, one file per device
and time. :func:`restore_snapshot` writes a snapshot back and reads it again to check.

Not covered (listed in ``skipped``): memory addressed relative to a segment the device allocates
(System B), load-state control (property 5, an event rather than a value), function properties.
A restore of memory into a device whose application is loaded may be refused by the device; the
check after the write tells.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .dry_run import FrameKind

if TYPE_CHECKING:
    from .dry_run import DryRunResult
    from .programmer import ConnectionManager, DeviceProgrammer

_LOAD_STATE_CONTROL = 5


@dataclass
class SnapshotPlan:
    memory: list[tuple[int, int]] = field(default_factory=list[tuple[int, int]])
    # (object index, property id, start index, count)
    properties: list[tuple[int, int, int, int]] = field(
        default_factory=list[tuple[int, int, int, int]]
    )
    skipped: list[str] = field(default_factory=list[str])

    @property
    def empty(self) -> bool:
        return not self.memory and not self.properties


@dataclass
class Snapshot:
    address: str
    taken: str  # ISO time, UTC
    scope: str
    memory: list[tuple[int, bytes]] = field(default_factory=list[tuple[int, bytes]])
    properties: list[tuple[int, int, int, int, bytes]] = field(
        default_factory=list[tuple[int, int, int, int, bytes]]
    )
    skipped: list[str] = field(default_factory=list[str])

    @property
    def octets(self) -> int:
        return sum(len(d) for _, d in self.memory)


class SnapshotError(Exception):
    """The snapshot could not be taken (the download is refused unless overridden)."""


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, size in sorted(ranges):
        if out and start <= out[-1][0] + out[-1][1]:
            last_start, last_size = out[-1]
            out[-1] = (
                last_start,
                max(last_start + last_size, start + size) - last_start,
            )
        else:
            out.append((start, size))
    return out


def plan_from_dry_run(
    result: DryRunResult, placed: Collection[int] = ()
) -> SnapshotPlan:
    """The memory ranges and properties the dry-run download writes. Memory in a segment the
    download allocates is kept only for the objects in ``placed`` (the dry run put their
    segments where the device keeps them, see ``table_references``); others are skipped."""
    plan = SnapshotPlan()
    ranges: list[tuple[int, int]] = []
    props: set[tuple[int, int, int, int]] = set()
    skipped: set[str] = set()
    for f in result.frames:
        if f.kind in (FrameKind.MEMORY_WRITE, FrameKind.USER_MEMORY_WRITE):
            if f.segment_object is not None and f.segment_object not in placed:
                skipped.add(f"memory in the segment of object {f.segment_object}")
            elif f.address is not None and f.data:
                ranges.append((f.address, len(f.data)))
        elif f.kind is FrameKind.PROPERTY_WRITE:
            if f.object_index is None or f.property_id is None:
                continue
            if f.property_id == _LOAD_STATE_CONTROL:
                continue  # an event, not a value
            props.add((f.object_index, f.property_id, f.start_index or 1, f.count or 1))
        elif f.kind is FrameKind.FUNCTION_PROPERTY:
            skipped.add("function property commands")
    plan.memory = _merge(ranges)
    plan.properties = sorted(props)
    plan.skipped = sorted(skipped)
    return plan


TABLE_REFERENCE = 7


async def read_table_references(
    manager: ConnectionManager,
    objects: Collection[int] = (1, 2, 3, 4),
    *,
    max_apdu_length: int = 15,
) -> dict[int, int]:
    """The device's table reference (property 7) of each of ``objects`` (reads only); an object
    that does not answer is left out."""
    programmer = await _programmer(manager, max_apdu_length)
    out: dict[int, int] = {}
    try:
        for obj in objects:
            try:
                value = await programmer.read_property(obj, TABLE_REFERENCE)
            except Exception:  # not a table object on this device
                continue
            if value:
                out[obj] = int.from_bytes(value, "big")
    finally:
        await manager.close()
    return out


async def _programmer(
    manager: ConnectionManager, max_apdu_length: int
) -> DeviceProgrammer:
    from .programmer import DeviceProgrammer

    connection = await manager.open()
    return DeviceProgrammer(connection, max_apdu_length=max_apdu_length)


async def take_snapshot(
    manager: ConnectionManager,
    plan: SnapshotPlan,
    *,
    address: str,
    scope: str = "",
    max_apdu_length: int = 15,
) -> Snapshot:
    """Read ``plan`` from the device behind ``manager`` (reads only). Raises
    :class:`SnapshotError` when a read fails."""
    snap = Snapshot(
        address=address,
        taken=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        scope=scope,
        skipped=list(plan.skipped),
    )
    try:
        programmer = await _programmer(manager, max_apdu_length)
        try:
            for start, size in plan.memory:
                data = await programmer.read_memory(start, size)
                if len(data) != size:
                    raise SnapshotError(
                        f"read {len(data)} of {size} octets at {start:#06x}"
                    )
                snap.memory.append((start, data))
            for obj, pid, start_index, count in plan.properties:
                value = await programmer.read_property(
                    obj, pid, count=count, start_index=start_index
                )
                snap.properties.append((obj, pid, start_index, count, value))
        finally:
            await manager.close()
    except SnapshotError:
        raise
    except Exception as exc:  # no answer, refused, timeout: the download must not start
        raise SnapshotError(f"{type(exc).__name__}: {exc}") from exc
    return snap


def save_snapshot(snap: Snapshot, folder: Path) -> Path:
    """Store ``snap`` as ``<folder>/<address>/<time>.json`` and return the path."""
    target = folder / snap.address
    target.mkdir(parents=True, exist_ok=True)
    path = target / (snap.taken.replace(":", "").replace("+0000", "Z") + ".json")
    path.write_text(
        json.dumps(
            {
                "address": snap.address,
                "taken": snap.taken,
                "scope": snap.scope,
                "memory": [{"address": a, "data": d.hex()} for a, d in snap.memory],
                "properties": [
                    {
                        "object_index": o,
                        "property_id": p,
                        "start_index": s,
                        "count": c,
                        "data": v.hex(),
                    }
                    for o, p, s, c, v in snap.properties
                ],
                "skipped": snap.skipped,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return path


def load_snapshot(path: Path) -> Snapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Snapshot(
        address=raw["address"],
        taken=raw["taken"],
        scope=raw.get("scope", ""),
        memory=[(int(m["address"]), bytes.fromhex(m["data"])) for m in raw["memory"]],
        properties=[
            (
                int(p["object_index"]),
                int(p["property_id"]),
                int(p["start_index"]),
                int(p["count"]),
                bytes.fromhex(p["data"]),
            )
            for p in raw.get("properties", [])
        ],
        skipped=list(raw.get("skipped", [])),
    )


def list_snapshots(folder: Path, address: str | None = None) -> list[Path]:
    """Stored snapshots, the newest first (all devices, or one)."""
    base = folder / address if address else folder
    if not base.is_dir():
        return []
    return sorted(base.rglob("*.json"), key=lambda p: p.name, reverse=True)


async def restore_snapshot(
    manager: ConnectionManager,
    snap: Snapshot,
    *,
    max_apdu_length: int = 15,
) -> list[str]:
    """Write ``snap`` back to its device and read it again; return what still differs
    (empty = restored)."""
    programmer = await _programmer(manager, max_apdu_length)
    differ: list[str] = []
    try:
        for start, data in snap.memory:
            await programmer.write_memory(start, data)
        for obj, pid, start_index, count, value in snap.properties:
            await programmer.write_property(
                obj, pid, value, count=count, start_index=start_index, verify=False
            )
        for start, data in snap.memory:
            held = await programmer.read_memory(start, len(data))
            if held != data:
                differ.append(f"memory {start:#06x}+{len(data)}")
        for obj, pid, start_index, count, value in snap.properties:
            held = await programmer.read_property(
                obj, pid, count=count, start_index=start_index
            )
            if held != value:
                differ.append(f"property {obj}/{pid}")
    finally:
        await manager.close()
    return differ

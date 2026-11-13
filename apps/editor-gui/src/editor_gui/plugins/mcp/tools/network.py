# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Connection capture tools, backed by the Group Monitor and Bus Activity recordings.

Together the two monitors hold every frame of the normal connection: the Group Monitor the group
telegrams, Bus Activity everything else including every frame the app sends. These tools start,
stop, clear and read both recordings together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError
from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.const import CEMIMessageCode
from xknx.telegram import GroupAddress, Telegram, TelegramDirection

from editor_gui.plugins.mcp.context import McpContext, make_tool
from editor_gui.plugins.monitor.formatting import ProjectIndex, build_index
from editor_gui.plugins.monitor.frames import individual_address_text
from editor_gui.plugins.monitor.records import ticks_to_datetime
from editor_gui.plugins.monitor.service import LINK_LAYER_KINDS, MonitorKind
from editor_gui.plugins.monitor.statistics import compute

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.monitor.records import MonitorRecord
    from editor_gui.plugins.monitor.service import MonitorService

# Every frame of these recordings comes from the normal connection.
_SOURCE_TYPE = "connection"
_MONITOR_NAMES = {"group": "group_monitor", "activity": "bus_activity"}


def capture_state(monitor: MonitorService) -> dict[str, Any]:
    """Recording state and entry counts of the Group Monitor and Bus Activity together."""
    recording = {
        _MONITOR_NAMES[kind.value]: monitor.channel(kind).recording
        for kind in LINK_LAYER_KINDS
    }
    entries = monitor.merged_log()
    telegrams = sum(
        1
        for _kind, record in entries
        if record.frame is not None and record.frame.is_data
    )
    return {
        "state": "capturing" if any(recording.values()) else "stopped",
        "recording": recording,
        "telegrams": telegrams,
        "cemi_records": len(entries),
    }


def _telegram(record: MonitorRecord) -> Telegram | None:
    """The xknx telegram of an L_Data frame, or ``None``."""
    try:
        data = CEMIFrame.from_knx(record.raw).data
    except Exception:
        return None
    if not isinstance(data, CEMILData):
        return None
    return Telegram(
        source_address=data.src_addr,
        destination_address=data.dst_addr,
        direction=(
            TelegramDirection.INCOMING
            if record.from_bus
            else TelegramDirection.OUTGOING
        ),
        payload=data.payload,
        tpci=data.tpci,
    )


def _message_code(code: int) -> str:
    try:
        return CEMIMessageCode(code).name
    except ValueError:
        return f"0x{code:02x}"


def _cemi_item(kind: MonitorKind, record: MonitorRecord) -> dict[str, Any]:
    frame = record.frame
    src_addr = ""
    dst_addr = ""
    hops: int | None = None
    if frame is not None and frame.is_data:
        src_addr = individual_address_text(frame.source or 0)
        destination = frame.destination or 0
        dst_addr = (
            str(GroupAddress(destination))
            if frame.group
            else individual_address_text(destination)
        )
        hops = frame.hop_count
    return {
        "timestamp": ticks_to_datetime(record.ticks).isoformat(),
        "source_type": _SOURCE_TYPE,
        "monitor": _MONITOR_NAMES[kind.value],
        "msg_code": _message_code(record.raw[0]),
        "src_addr": src_addr,
        "dst_addr": dst_addr,
        "hops": hops,
        "raw": record.raw.hex(" "),
    }


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    monitor = ctx.monitor

    def _project_index() -> ProjectIndex:
        """Names for the statistics; empty (addresses only) when no project is open."""
        project = ctx.api.project
        if not project.is_open or project.loading:
            return ProjectIndex()
        try:
            style = project.group_address_style
        except Exception:
            style = None
        return build_index(
            revision=None,
            group_addresses=project.group_addresses,
            devices=[*project.devices, *project.unloaded_devices],
            spaces=list(project.get_space_tree()),
            ga_style=style,
        )

    def _monitor_kind(name: str) -> MonitorKind:
        try:
            return MonitorKind(name)
        except ValueError:
            raise ToolError(
                f"unknown monitor {name!r}; one of group, bus, activity"
            ) from None

    @tool("write")
    def monitor_import(path: str, monitor_name: str = "group") -> dict[str, Any]:
        """Import a recording into a monitor (``monitor_name``: group, bus or activity), as its
        Import button does: the monitor's log is replaced and recording stops. ``path`` is a file
        on the editor's machine: a CommunicationLog .xml, a telegram CSV (.csv, or .csv.gz:
        columns time, source, ga, type, raw and optionally dpt, e.g. a bus recorder's day file)
        or a KNXnet/IP capture as JSON lines (.jsonl: t, raw). Nothing is sent. Returns
        {imported, skipped, path}: telegrams taken, and rows/lines that could not be read."""
        from pathlib import Path

        from editor_gui.plugins.monitor.csvio import import_log
        from editor_gui.plugins.monitor.xmlio import import_xml

        kind = _monitor_kind(monitor_name)
        try:
            data = Path(path).read_bytes()
            result = (
                import_xml(data)
                if path.lower().endswith(".xml")
                else import_log(Path(path).name, data)
            )
        except (OSError, ValueError) as error:
            raise ToolError(f"cannot import {path}: {error}") from error
        ctx.run_locked(lambda: monitor.load(kind, result.records))
        return {
            "imported": len(result.records),
            "skipped": result.skipped,
            "path": path,
        }

    @tool("write")
    def monitor_export(path: str, monitor_name: str = "group") -> dict[str, Any]:
        """Write a monitor's whole log (``monitor_name``: group, bus or activity) to ``path`` on
        the editor's machine: a telegram CSV when it ends with .csv (time, source,
        source_name, ga, ga_name, type, raw, value, dpt; names and values from the open
        project), else CommunicationLog XML. Returns {telegrams, path}."""
        from pathlib import Path

        from editor_gui.plugins.monitor.csvio import export_csv
        from editor_gui.plugins.monitor.formatting import COLUMN_INDEX, record_cells
        from editor_gui.plugins.monitor.xmlio import export_xml

        kind = _monitor_kind(monitor_name)
        records = monitor.channel(kind).log.snapshot()
        if path.lower().endswith(".csv"):
            index = ctx.run_locked(_project_index)

            def labels(record: MonitorRecord) -> tuple[str, str, str, str]:
                cells = record_cells(record, index, ("mcp", id(index)))
                return (
                    cells[COLUMN_INDEX["source_name"]],
                    cells[COLUMN_INDEX["destination_name"]],
                    cells[COLUMN_INDEX["info"]],
                    cells[COLUMN_INDEX["dpt"]],
                )

            data = export_csv(records, labels)
        else:
            data = export_xml(records)
        try:
            Path(path).write_bytes(data)
        except OSError as error:
            raise ToolError(f"cannot write {path}: {error}") from error
        return {
            "telegrams": sum(1 for record in records if record.is_telegram),
            "path": path,
        }

    @tool("read")
    def monitor_find(
        filter_text: str, monitor_name: str = "group", limit: int = 200
    ) -> dict[str, Any]:
        """Search a monitor's log (``monitor_name``: group, bus or activity) as its filter box
        does: plain text anywhere in the row, or field terms ga: and src: (an address, or a
        prefix ending in / . or *), to: (destination name), dev: (source device name), room:
        (building part), fn: (building function), svc: (service) and dpt: (all have to match;
        double quotes keep spaces together). ``limit`` caps the rows returned, newest last
        (<=0 means all). Returns {items, count, matched}: each item {timestamp, source,
        source_name, destination, destination_name, room, service, value, dpt}; matched counts
        every matching row."""
        from editor_gui.plugins.monitor.filter_terms import compile_filter
        from editor_gui.plugins.monitor.formatting import COLUMN_INDEX, record_cells

        kind = _monitor_kind(monitor_name)
        index = ctx.run_locked(_project_index)
        match = compile_filter(filter_text)
        key = ("mcp", id(index))
        rows: list[dict[str, Any]] = []
        for record in monitor.channel(kind).log.snapshot():
            cells = record_cells(record, index, key)
            if not match(cells, record.haystack):
                continue
            rows.append(
                {
                    "timestamp": ticks_to_datetime(record.ticks).isoformat(),
                    **{
                        name: cells[COLUMN_INDEX[column]]
                        for name, column in (
                            ("source", "source_address"),
                            ("source_name", "source_name"),
                            ("destination", "destination_address"),
                            ("destination_name", "destination_name"),
                            ("room", "building_part"),
                            ("service", "service"),
                            ("value", "info"),
                            ("dpt", "dpt"),
                        )
                    },
                }
            )
        shown = rows[-limit:] if limit > 0 else rows
        return {"items": shown, "count": len(shown), "matched": len(rows)}

    @tool("read")
    def network_state() -> dict[str, Any]:
        """Read recording state for the normal connection's Group Monitor and Bus Activity.
        Returns {state, recording, telegrams, cemi_records}; recording has {group_monitor, bus_activity}
        booleans, and state is "capturing" while either records, otherwise "stopped". telegrams counts
        buffered L_Data frames; cemi_records counts all buffered frames. Recording state does not
        establish whether a bus connection is open."""
        return capture_state(monitor)

    @tool("write")
    def network_start() -> dict[str, str]:
        """Enable recording in the Group Monitor and Bus Activity for the normal connection.
        Existing entries are kept; network_clear clears them. Returns {status: "capturing"}.
        This enables local recording without opening a bus connection."""
        for kind in LINK_LAYER_KINDS:
            monitor.start(kind)
        return {"status": "capturing"}

    @tool("write")
    def network_stop() -> dict[str, str]:
        """Stop Group Monitor and Bus Activity recording while keeping buffered entries and the
        normal bus connection unchanged. Returns {status: "stopped"}."""
        for kind in LINK_LAYER_KINDS:
            monitor.stop(kind)
        return {"status": "stopped"}

    @tool("destructive")
    def network_clear() -> dict[str, str]:
        """Clear the Group Monitor and Bus Activity logs; active recording continues and cached
        latest values remain. Returns {status: "cleared"}. Cleared logs cannot be restored with
        project_undo."""
        for kind in LINK_LAYER_KINDS:
            monitor.clear_log(kind)
        return {"status": "cleared"}

    @tool("read")
    def network_telegrams(limit: int = 100) -> dict[str, Any]:
        """Read decoded L_Data telegrams from the Group Monitor and Bus Activity, merged oldest first.
        limit selects the most recent decodable entries (<=0 means all). Returns {items, count}; each
        item has {timestamp, source_type, monitor, telegram}. timestamp is ISO-formatted, source_type
        is "connection", monitor is "group_monitor" or "bus_activity", and telegram is a display
        string. count is the returned count."""
        items: list[dict[str, Any]] = []
        for kind, record in reversed(monitor.merged_log()):
            if 0 < limit <= len(items):
                break
            telegram = _telegram(record)
            if telegram is None:
                continue
            items.append(
                {
                    "timestamp": ticks_to_datetime(record.ticks).isoformat(),
                    "source_type": _SOURCE_TYPE,
                    "monitor": _MONITOR_NAMES[kind.value],
                    "telegram": str(telegram),
                }
            )
        items.reverse()
        return {"items": items, "count": len(items)}

    @tool("read")
    def network_cemi_records(limit: int = 100) -> dict[str, Any]:
        """Read raw cEMI frames from the Group Monitor and Bus Activity, merged oldest first.
        limit selects the most recent frames (<=0 means all). Returns {items, count}; each item has
        {timestamp, source_type, monitor, msg_code, src_addr, dst_addr, hops, raw}. timestamp is
        ISO-formatted, source_type is "connection", monitor is "group_monitor" or "bus_activity",
        and raw is space-separated hex. Non-data frames have empty addresses and null hops.
        count is the returned count."""
        entries = monitor.merged_log()
        if limit > 0:
            entries = entries[-limit:]
        items = [_cemi_item(kind, record) for kind, record in entries]
        return {"items": items, "count": len(items)}

    @tool("read")
    def network_statistics(limit: int = 50) -> dict[str, Any]:
        """Aggregate the Group Monitor's group telegrams: who sends how much, and to which group
        address. limit caps how many of each ranking is returned (top N by count; <=0 means all).
        Returns {total, per_minute, peak_per_minute, peak_at, bus_load, span_seconds, sources,
        destinations}. total is the number of group telegrams in the log; per_minute is their
        average rate over the recorded span; peak_per_minute is the highest count in any one
        clock-minute and peak_at is that minute's ISO timestamp (null when empty); bus_load is the
        live rate in telegrams/second over the last seconds (every bus frame, independent of the
        log); span_seconds is first-to-last telegram. sources and destinations
        are lists of {address, name, description, count}, most frequent first; source is the sending
        device (individual address), destination the group address. Names and descriptions are empty
        when no project is open or an address is unknown. Reads the same log the Group Monitor
        shows."""

        def _run() -> dict[str, Any]:
            index = _project_index()
            records = monitor.channels[MonitorKind.GROUP].log.snapshot()
            stats = compute(records, index)

            def _rows(items: list[Any]) -> list[dict[str, Any]]:
                ranked = items[:limit] if limit > 0 else items
                return [
                    {
                        "address": r.address,
                        "name": r.name,
                        "description": r.description,
                        "count": r.count,
                    }
                    for r in ranked
                ]

            return {
                "total": stats.total,
                "per_minute": round(stats.per_minute, 3),
                "peak_per_minute": stats.peak_per_minute,
                "peak_at": (
                    ticks_to_datetime(stats.peak_ticks).isoformat()
                    if stats.peak_ticks
                    else None
                ),
                "bus_load": round(monitor.bus_load(), 3),
                "span_seconds": round(stats.span_seconds, 3),
                "sources": _rows(stats.sources),
                "destinations": _rows(stats.destinations),
            }

        return ctx.run_locked(_run)

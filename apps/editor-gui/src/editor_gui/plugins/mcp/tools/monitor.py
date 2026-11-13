# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Group/bus monitor tools: read latest values and the telegram log, send GroupValue read/write."""

from __future__ import annotations

import contextlib
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError
from xknx.telegram.apci import GroupValueRead

from editor_gui.plugins.mcp.context import McpContext, make_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP


# Seconds a planned group test may be run.
_TEST_PLAN_SECONDS = 300.0


def _coerce_value(text: str) -> Any:
    """The value as the Monitor's command bar reads it."""
    from editor_gui.plugins.monitor.service import _coerce  # pyright: ignore[reportPrivateUsage]

    return _coerce(text)


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    monitor = ctx.monitor

    def _require_connection() -> None:
        # Also through Home Assistant's connection while the editor has none.
        if not monitor.can_send():
            raise ToolError("no KNX connection")

    @tool("read")
    def monitor_latest(address: str) -> dict[str, Any]:
        """Read the cached value last seen on a group-address string such as "1/2/3"; no bus request
        is sent. Returns {address, service, timestamp, payload}; service is "Write" or "Response",
        timestamp is ISO-formatted, and payload is a raw payload string. Raises if no value was seen;
        a cached value may be stale."""
        value = monitor.latest(address)
        if value is None:
            raise ToolError(f"no value seen for {address!r}")
        return {
            "address": address,
            "service": value.service,
            "timestamp": value.timestamp.isoformat(),
            "payload": str(value.payload),
        }

    @tool("read")
    def monitor_telegrams(limit: int = 100) -> dict[str, Any]:
        """Read the Group Monitor's recorded group telegrams, oldest first. limit selects the most
        recent entries (<=0 means all). Returns {items, count}; each item has {timestamp, source,
        destination, service, payload}. timestamp is ISO-formatted; service is "Read", "Write" or
        "Response"; payload is a raw payload string or null for reads. count is the returned count."""
        records = monitor.telegrams()
        if limit > 0:
            records = records[-limit:]
        items = [
            {
                "timestamp": r.timestamp.isoformat(),
                "source": r.source,
                "destination": r.destination,
                "service": r.service,
                "payload": None if r.payload is None else str(r.payload),
            }
            for r in records
        ]
        return {"items": items, "count": len(items)}

    @tool("read", open_world=True)
    def monitor_read_value(address: str, timeout: float = 3.0) -> dict[str, Any]:
        """Send a GroupValueRead to a group-address string (e.g. "1/2/3") and wait up to timeout
        seconds for a fresh value. Requires an open connection; raises on timeout. Any Write or
        Response seen on that address since the request can satisfy the wait, including unrelated
        traffic. Returns {address, service, timestamp, payload}; timestamp is ISO-formatted and payload
        is a raw payload string. This sends a bus read without editing the project."""
        _require_connection()
        _refused(GroupValueRead())
        sent_at = datetime.now()
        monitor.send_read(address)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = monitor.latest(address)
            if value is not None and value.timestamp >= sent_at:
                return {
                    "address": address,
                    "service": value.service,
                    "timestamp": value.timestamp.isoformat(),
                    "payload": str(value.payload),
                }
            time.sleep(0.1)
        raise ToolError(
            f"no response on {address!r} within {timeout}s (device may not answer reads)"
        )

    def _refused(payload: Any) -> None:
        """Raise when Listen Only (anything) or Block Bus Writes (``None``: a write) refuses
        sending, before anything is queued."""
        if why := monitor.sending_refused(payload):
            raise ToolError(f"not sent: {why} (Settings > Connection)")

    def _kind(name: str) -> Any:
        from editor_gui.plugins.monitor.service import MonitorKind

        try:
            return MonitorKind(name)
        except ValueError:
            raise ToolError(
                f"unknown monitor {name!r}; one of group, bus, activity"
            ) from None

    test_plans: dict[str, dict[str, Any]] = {}

    def _test_log(entry: dict[str, Any]) -> None:
        """Every group test attempt (sent or refused) goes to group_tests.jsonl in the config
        folder."""
        import json

        from editor_gui import settings as app_settings

        path = app_settings.config_dir() / "group_tests.jsonl"
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(entry) + "\n")

    def _project_dpt(address: str) -> tuple[str | None, str]:
        return ctx.run_locked(
            lambda: next(
                (
                    (g.datapoint_type, g.name)
                    for g in ctx.api.project.group_addresses
                    if g.address == address
                ),
                (None, ""),
            )
        )

    @tool("read")
    def monitor_test_plan(
        address: str, value: str | None = None, dpt: str | None = None
    ) -> dict[str, Any]:
        """The confirm step of a group test: what monitor_test_run will send, before anything is
        sent. With ``value`` a GroupValueWrite of it to ``address`` (encoded with ``dpt``, or the
        address's project DPT), without one a GroupValueRead. Refused at once (no token) for a
        protected group address (Settings > Connection), and for a write while Block Bus Writes
        or Listen Only is on. Returns {token, service, address, name, dpt, value, payload,
        valid_seconds}: the token runs it once with monitor_test_run within valid_seconds."""
        import secrets

        from editor_gui.dpt import transcoder_for

        service_name = "GroupValueWrite" if value is not None else "GroupValueRead"
        refused = monitor.sending_refused(
            GroupValueRead() if value is None else None, address
        )
        project_dpt, name = _project_dpt(address)
        resolved = dpt or project_dpt
        payload = ""
        if value is not None:
            transcoder = transcoder_for(resolved)
            if transcoder is None:
                raise ToolError(f"no DPT for {address}: pass dpt")
            try:
                payload = repr(transcoder.to_knx(_coerce_value(value)))
            except Exception as error:
                raise ToolError(
                    f"cannot encode {value!r} as {resolved}: {error}"
                ) from error
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "step": "plan",
            "service": service_name,
            "address": address,
            "value": value,
            "dpt": resolved,
        }
        if refused:
            _test_log({**entry, "result": f"REFUSED: {refused}"})
            raise ToolError(f"refused: {refused}")
        token = secrets.token_hex(8)
        test_plans[token] = {**entry, "made": time.monotonic()}
        _test_log({**entry, "result": "planned", "token": token})
        return {
            "token": token,
            "service": service_name,
            "address": address,
            "name": name,
            "dpt": resolved,
            "value": value,
            "payload": payload,
            "valid_seconds": _TEST_PLAN_SECONDS,
        }

    @tool("destructive", open_world=True)
    def monitor_test_run(token: str) -> dict[str, Any]:
        """Send the group test planned with monitor_test_plan, once: exactly that write or
        read. The protected group addresses, Block Bus Writes and Listen Only are checked again
        before it is sent. Every attempt is logged (group_tests.jsonl in the config folder).
        Returns {status: "sent", service, address}; a write can actuate hardware."""
        plan = test_plans.pop(token, None)
        if plan is None:
            raise ToolError("unknown or already used token: plan the test first")
        if time.monotonic() - plan["made"] > _TEST_PLAN_SECONDS:
            raise ToolError("the plan expired: plan the test again")
        _require_connection()
        entry = {k: v for k, v in plan.items() if k != "made"}
        entry["step"] = "run"
        entry["time"] = datetime.now().astimezone().isoformat(timespec="seconds")
        entry["token"] = token
        is_read = plan["value"] is None
        refused = monitor.sending_refused(
            GroupValueRead() if is_read else None, plan["address"]
        )
        if refused:
            _test_log({**entry, "result": f"REFUSED: {refused}"})
            raise ToolError(f"refused: {refused}")
        sent = (
            monitor.send_read(plan["address"])
            if is_read
            else monitor.send_write(plan["address"], plan["dpt"], plan["value"])
        )
        _test_log({**entry, "result": "sent" if sent else "not sent"})
        if not sent:
            raise ToolError("not sent (see the log)")
        return {
            "status": "sent",
            "service": plan["service"],
            "address": plan["address"],
        }

    @tool("read")
    def monitor_max_records() -> dict[str, int]:
        """The most telegrams each monitor keeps (the Max field in its toolbar): {group, bus,
        activity}; the oldest tenth is dropped when one is exceeded."""
        from editor_gui.plugins.monitor.service import MonitorKind

        return {kind.value: monitor.max_records(kind) for kind in MonitorKind}

    @tool("write")
    def monitor_set_max_records(name: str, value: int) -> dict[str, int]:
        """Set the most telegrams the monitor ``name`` (group, bus or activity) keeps, at
        least 10; it is trimmed down to it at once. Not part of the project's undo history.
        Returns the caps of all monitors (see monitor_max_records)."""
        from editor_gui.plugins.monitor.service import MonitorKind

        monitor.set_max_records(_kind(name), max(value, 10))
        return {kind.value: monitor.max_records(kind) for kind in MonitorKind}

    @tool("destructive")
    def monitor_clear() -> dict[str, str]:
        """Clear cached latest values and the Group Monitor log; active recording continues.
        Returns {status: "cleared"}. Cleared data cannot be restored with project_undo."""
        monitor.clear()
        return {"status": "cleared"}

    @tool("destructive", open_world=True)
    def monitor_send_write(
        address: str, value: str, dpt: str | None = None
    ) -> dict[str, str]:
        """Queue a GroupValueWrite to a group-address string such as "1/2/3". Requires an open
        connection. value is encoded using dpt, or the address's project DPT when omitted; missing
        or unsupported DPTs and values that cannot be encoded raise an error.
        Returns {status: "queued", address}; this does not confirm delivery or device action.
        This can actuate hardware and is not reversible with project_undo; it does not edit the
        project. Inspect fresh bus feedback to verify the effect."""
        _require_connection()
        resolved = dpt
        if resolved is None:
            resolved = ctx.run_locked(
                lambda: next(
                    (
                        g.datapoint_type
                        for g in ctx.api.project.group_addresses
                        if g.address == address
                    ),
                    None,
                )
            )
        _refused(None)
        if not monitor.send_write(address, resolved, value):
            raise ToolError(f"could not encode value {value!r} for DPT {resolved!r}")
        # Fire-and-forget: the value was encoded and queued onto the bus loop; actual delivery is not
        # awaited here. Read it back with monitor_latest to confirm the effect.
        return {"status": "queued", "address": address}

    @tool("read", open_world=True)
    def monitor_send_read(address: str) -> dict[str, str]:
        """Queue a GroupValueRead to a group-address string such as "1/2/3". Requires an open
        connection. Returns {status: "queued", address} without waiting for a reply; monitor_read_value
        waits for a fresh value. When polling monitor_latest, check timestamp to avoid stale data.
        This sends a bus read without editing the project."""
        _require_connection()
        _refused(GroupValueRead())
        monitor.send_read(address)
        return {"status": "queued", "address": address}

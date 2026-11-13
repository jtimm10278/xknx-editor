# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Device snapshots taken before every download: the setting, the list and the restore."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool
from editor_gui.snapshots import (
    set_snapshots_enabled,
    snapshot_folder,
    snapshots_enabled,
)
from xknxeditor.download.snapshot import list_snapshots, load_snapshot

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _entry(path: Path) -> dict[str, Any]:
    snap = load_snapshot(path)
    return {
        "path": str(path),
        "address": snap.address,
        "taken": snap.taken,
        "scope": snap.scope,
        "octets": snap.octets,
        "memory_ranges": [[hex(a), len(d)] for a, d in snap.memory],
        "properties": [[o, p] for o, p, _s, _c, _v in snap.properties],
        "skipped": snap.skipped,
    }


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    @tool("read")
    def snapshot_settings() -> dict[str, Any]:
        """Whether a snapshot is taken before every download (on by default) and where the
        snapshots are kept: {before_download, folder}. A snapshot holds what the download is
        about to overwrite (the memory and properties a dry run of it writes), read from the
        device first; a download is refused when its snapshot fails, unless
        connection_program_device(snapshot_override=true)."""
        return {
            "before_download": snapshots_enabled(),
            "folder": str(snapshot_folder()),
        }

    @tool("write")
    def snapshot_set_settings(before_download: bool) -> dict[str, Any]:
        """Turn the snapshot before every download on or off (for every project). Returns the
        settings as kept (see snapshot_settings)."""
        set_snapshots_enabled(before_download)
        return {
            "before_download": snapshots_enabled(),
            "folder": str(snapshot_folder()),
        }

    @tool("read")
    def snapshot_list(device_address: str | None = None) -> dict[str, Any]:
        """The kept snapshots, the newest first, of every device or of the one at
        ``device_address`` (its individual address, like "1.1.31"): {items: [{path, address,
        taken (ISO time, UTC), scope, octets, memory_ranges [[start, length]], properties
        [[object index, property id]], skipped (what it could not keep)}], count}."""
        items = [_entry(p) for p in list_snapshots(snapshot_folder(), device_address)]
        return {"items": items, "count": len(items)}

    @tool("destructive", open_world=True)
    def snapshot_restore(
        path: str, timeout: float = 300.0, unlock_locked: bool = False
    ) -> dict[str, Any]:
        """Write a kept snapshot (``path`` from snapshot_list) back to its device and read it
        again. WRITES TO THE DEVICE ON THE BUS: use only when the user explicitly asked to put a
        device back as it was before a download. Needs an open connection; refused while bus
        writes are blocked, for a locked device (unless ``unlock_locked=true``, set only after
        the user explicitly confirmed), or while another bus operation runs. Not undoable.
        Returns {status: "restored" | "differs", address, differs: [what still differs]}."""
        file = Path(path).expanduser()
        if not file.is_file():
            raise ToolError(f"no such snapshot: {file}")
        snap = load_snapshot(file)
        service = ctx.api.connection
        future = ctx.run_locked(
            lambda: service.restore_snapshot(file, unlock_locked=unlock_locked)
        )
        if future is None:
            raise ToolError(
                "the restore was not started (not connected, writes blocked, device locked "
                "or another bus operation running; see the log)"
            )
        try:
            differs = future.result(timeout=timeout)
        except TimeoutError as exc:
            raise ToolError(f"the restore did not end within {timeout} s") from exc
        return {
            "status": "restored" if not differs else "differs",
            "address": snap.address,
            "differs": list(differs),
        }

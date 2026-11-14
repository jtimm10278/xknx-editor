"""The list of devices proven with this editor, as the "Tested" badge beside a device shows it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.project import require_device

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    project = ctx.api.project

    @tool("read")
    def project_tested_devices(
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """The community list of applications proven with this editor: someone downloaded and/or
        read back a device with that application. The device panel shows the same as a "Tested" or
        "Not yet tested" badge beside the device's name.

        Without arguments: {devices: [entry, ...]} - the whole list. With node_id OR
        device_address (e.g. "1.0.60"): {node_id, application, tested, entry} for that device;
        ``tested`` is false (entry null) when its application is not listed yet, which is a reason
        to try a first download on a spare device.

        Each entry: {manufacturer, product, order_number, application (e.g. "M-0001_A-2207-10":
        manufacturer, number and version, without the hash), application_name, mask (as the
        product data gives it), proven (list of "download", "read_back", "plugin_edit"), date
        (YYYY-MM-DD), xets_version (the editor version used), notes}."""
        from editor_gui.tested_devices import for_device, tested_devices

        if node_id is None and device_address is None:
            return {"devices": [e.to_dict() for e in tested_devices()]}
        require_project(ctx)

        def _read() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            entry = for_device(device)
            return {
                "node_id": device.node_id,
                "application": device.app.program.id if device.app else None,
                "tested": entry is not None,
                "entry": entry.to_dict() if entry is not None else None,
            }

        return ctx.run_locked(_read)

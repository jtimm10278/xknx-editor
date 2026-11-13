# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""House check-up: a .knxproj set against recorded bus traffic (read only, offline)."""

from __future__ import annotations

import glob
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool
from xknxeditor.proj.core.house_check import (
    SECTIONS,
    check,
    read_project,
    read_traffic,
    report,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    @tool("read")
    def house_check(
        knxproj_path: str,
        recordings: list[str] | None = None,
        old_years: float = 5.0,
    ) -> dict[str, Any]:
        """House check-up of a project file against recorded bus traffic. Read only: it opens
        the .knxproj (plain XML, not a protected project) and the bus recorder's CSV files
        (``time,source,source_name,ga,ga_name,type,...``, plain or .gz), and touches neither the
        bus nor the open project.

        ``knxproj_path``: the .knxproj to check (e.g. a backup copy of the master project).
        ``recordings``: CSV paths or glob patterns; none = project-only checks.
        ``old_years``: a device last downloaded longer ago than this is listed.

        Returns {project, recording: {first, last, files}, counts: {kind: n}, findings:
        [{kind, subject, text}], report (plain-words Markdown)}. Kinds: sent_not_in_project
        (group addresses sent on the bus the project lacks), parked_with_links (devices
        described as removed/parked/spare/old that still hold links), unused (group addresses
        no device links and never seen), unlinked_but_used (no device link but used on the
        bus, e.g. by Home Assistant or Node-RED), old_download, never_downloaded, not_loaded
        (ETS 'loaded' flags off), sender_not_in_project (bus senders not in the project)."""
        path = Path(knxproj_path).expanduser()
        if not path.is_file():
            raise ToolError(f"no such file: {path}")
        files: list[str] = []
        for pattern in recordings or []:
            found = sorted(glob.glob(str(Path(pattern).expanduser())))
            if not found:
                raise ToolError(f"no recording matches {pattern}")
            files.extend(found)
        try:
            view = read_project(path)
        except (ValueError, OSError) as exc:
            raise ToolError(f"cannot read {path.name}: {exc}") from exc
        traffic = read_traffic(files) if files else None
        findings = check(view, traffic, old_years=old_years)
        counts = {
            kind: sum(1 for f in findings if f.kind == kind) for kind, _ in SECTIONS
        }
        return {
            "project": str(path),
            "recording": {
                "first": traffic.first if traffic else "",
                "last": traffic.last if traffic else "",
                "files": len(files),
            },
            "counts": counts,
            "findings": [
                {"kind": f.kind, "subject": f.subject, "text": f.text} for f in findings
            ],
            "report": report(findings, project=path.name, traffic=traffic),
        }

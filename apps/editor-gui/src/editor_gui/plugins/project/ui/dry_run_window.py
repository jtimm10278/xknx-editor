"""The "Simulate Download" window: every frame a download of a device would send, recorded
offline, and how the bytes it would write compare with the device's stored image.

The simulation runs the editor's real download preparation and load procedure against a recorder
(``xknxeditor.download.dry_run``): no bus connection is used or needed, and nothing is sent. The
window shows a summary, the comparison with the image of the device's last download (when the
project keeps one) and the list of frames, which can be filtered, copied and exported as CSV.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from concurrent.futures import Future
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.device import device_label
from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets.dialog import begin_tool_window
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.tables import begin_data_table, data_table_headers

if TYPE_CHECKING:
    from editor_gui.device import Device
    from xknxeditor.download.dry_run import DryRunResult, RecordedFrame
    from xknxeditor.download.scope import DownloadScope

DryRunStarter = Callable[["Device", "DownloadScope"], "Future[DryRunResult]"]

_COLUMNS = 6


def frame_target(frame: RecordedFrame) -> str:
    """Where a frame goes: a memory address (with its object offset), or an object and
    property."""
    if frame.address is not None:
        text = f"0x{frame.address:04X}"
        if frame.segment_object is not None:
            text += f" (+{frame.segment_offset or 0})"
        return text
    if frame.object_index is not None and frame.property_id is not None:
        text = f"{frame.object_index}/{frame.property_id}"
        if frame.start_index not in (None, 1):
            text += f"[{frame.start_index}]"
        return text
    return ""


def frame_rows(frames: list[RecordedFrame]) -> list[list[str]]:
    """The table/CSV rows of ``frames``: #, connection, service, target, region, data (hex)."""
    return [
        [
            str(f.index),
            str(f.connection),
            f.service,
            frame_target(f),
            f.region,
            f.data.hex(" ").upper(),
        ]
        for f in frames
    ]


def frames_csv(frames: list[RecordedFrame]) -> str:
    """The frames as CSV with a header row (column names in English, as data)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["index", "connection", "service", "target", "region", "data"])
    writer.writerows(frame_rows(frames))
    return out.getvalue()


def summary_lines(result: DryRunResult) -> list[tuple[str, str]]:
    """The (status kind, text) lines above the frames."""
    from xknxeditor.download.dry_run import FrameKind

    lines: list[tuple[str, str]] = [
        (
            "info",
            S.DRY_RUN_SUMMARY.format(
                frames=len(result.frames),
                writes=result.writes,
                bytes=result.bytes_written,
                connections=result.count(FrameKind.CONNECT),
            ),
        )
    ]
    if result.error:
        lines.append(("warning", S.DRY_RUN_STOPPED.format(error=result.error)))
    comparison = result.comparison
    if not comparison.has_stored_image:
        lines.append(("info", S.DRY_RUN_NO_STORED_IMAGE))
        return lines
    lines.append(
        (
            "warning" if comparison.differing_bytes else "success",
            S.DRY_RUN_COMPARED.format(
                compared=comparison.compared_bytes,
                differing=comparison.differing_bytes,
                missing=comparison.not_in_stored_image,
            ),
        )
    )
    for region, count in sorted(comparison.differing_by_region.items()):
        lines.append(("warning", S.DRY_RUN_REGION.format(region=region, count=count)))
    if comparison.compared_properties:
        lines.append(
            (
                "warning" if comparison.property_differences else "success",
                S.DRY_RUN_PROPERTIES.format(
                    compared=comparison.compared_properties,
                    differing=len(comparison.property_differences),
                ),
            )
        )
    return lines


def filter_frames(
    frames: list[RecordedFrame], needle: str, only_writes: bool
) -> list[RecordedFrame]:
    out = [f for f in frames if f.writes] if only_writes else frames
    if not needle:
        return out
    return [
        f
        for f, row in zip(out, frame_rows(out), strict=True)
        if any(needle in cell.lower() for cell in row)
    ]


class DryRunWindow:
    """Runs a simulated download in the background and shows its result."""

    def __init__(self, start: DryRunStarter) -> None:
        self._start = start
        self._show = False
        self._device: Device | None = None
        self._scope: DownloadScope | None = None
        self._future: Future[DryRunResult] | None = None
        self._result: DryRunResult | None = None
        self._error: str | None = None
        self._filter = ""
        self._only_writes = False
        self._save_dialog: file_dialogs.SaveFileDialog | None = None
        self._file_error: str | None = None

    def open(self, device: Device, scope: DownloadScope) -> None:
        """Start a simulation of ``device``'s download with ``scope`` and show the window."""
        self._device = device
        self._scope = scope
        self._show = True
        self._result = None
        self._error = None
        try:
            self._future = self._start(device, scope)
        except Exception as exc:  # e.g. no application or address: shown in the window
            self._future = None
            self._error = str(exc)

    @property
    def running(self) -> bool:
        return self._future is not None and not self._future.done()

    def _poll(self) -> None:
        if self._future is not None and self._future.done():
            future, self._future = self._future, None
            try:
                self._result = future.result()
            except Exception as exc:
                self._error = str(exc)
        if self._save_dialog is not None and self._save_dialog.ready():
            path = self._save_dialog.result()
            self._save_dialog = None
            if path and self._result is not None:
                try:
                    with open(path, "w", encoding="utf-8", newline="") as file:
                        file.write(frames_csv(self._result.frames))
                except OSError as exc:
                    self._file_error = str(exc)

    def render(self) -> None:
        self._poll()
        if not self._show:
            return
        opened, p_open = begin_tool_window(
            S.DRY_RUN_TITLE, self._show, width=860, height=560
        )
        if p_open is not None:
            self._show = p_open
        if opened:
            self._render_body()
        imgui.end()

    def _render_body(self) -> None:
        if self._device is not None and self._scope is not None:
            imgui.text(
                S.DRY_RUN_HEADER.format(
                    device=device_label(self._device), scope=self._scope.name
                )
            )
        imgui.text_wrapped(S.DRY_RUN_NOTE)
        if self._error is not None:
            imgui.text_colored(
                status_color("error"), S.DRY_RUN_FAILED.format(error=self._error)
            )
            return
        if self.running:
            imgui.text_disabled(S.DRY_RUN_RUNNING)
            return
        result = self._result
        if result is None:
            return
        for kind, text in summary_lines(result):
            imgui.text_colored(status_color(kind), text)
        if self._file_error is not None:
            imgui.text_colored(status_color("error"), self._file_error)
        self._render_toolbar(result)
        self._render_frames(result)

    def _render_toolbar(self, result: DryRunResult) -> None:
        self._filter = filter_box(
            "##dry_run_filter", S.DRY_RUN_FILTER_HINT, self._filter
        )
        _, self._only_writes = imgui.checkbox(S.DRY_RUN_ONLY_WRITES, self._only_writes)
        imgui.same_line()
        if imgui.button(S.DRY_RUN_COPY):
            imgui.set_clipboard_text(frames_csv(result.frames))
        imgui.same_line()
        imgui.begin_disabled(self._save_dialog is not None)
        if imgui.button(S.DRY_RUN_EXPORT):
            self._file_error = None
            self._save_dialog = file_dialogs.save_file(
                S.DRY_RUN_EXPORT_TITLE,
                self._file_name(),
                [S.DRY_RUN_CSV_FILTER, "*.csv"],
            )
        imgui.end_disabled()

    def _file_name(self) -> str:
        address = self._device.individual_address if self._device is not None else ""
        scope = self._scope.name.lower() if self._scope is not None else ""
        name = "-".join(p for p in (address, scope, "dry-run") if p)
        return re.sub(r'[<>:"/\\|?*\s]+', "_", name) + ".csv"

    def _render_frames(self, result: DryRunResult) -> None:
        frames = filter_frames(
            result.frames, self._filter.lower().strip(), self._only_writes
        )
        if not frames:
            imgui.text_disabled(S.DRY_RUN_NO_FRAMES)
            return
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
        )
        if not begin_data_table("##dry_run_frames", _COLUMNS, flags):
            return
        imgui.table_setup_scroll_freeze(0, 1)
        for header, weight in (
            ("#", 0.05),
            (S.DRY_RUN_COL_CONNECTION, 0.07),
            (S.DRY_RUN_COL_SERVICE, 0.2),
            (S.DRY_RUN_COL_TARGET, 0.13),
            (S.DRY_RUN_COL_REGION, 0.17),
            (S.DRY_RUN_COL_DATA, 0.38),
        ):
            imgui.table_setup_column(
                header, imgui.TableColumnFlags_.width_stretch, weight
            )
        data_table_headers()
        rows = frame_rows(frames)
        clipper = imgui.ListClipper()
        clipper.begin(len(rows))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                imgui.table_next_row()
                for cell in rows[i]:
                    imgui.table_next_column()
                    if frames[i].writes:
                        imgui.text_unformatted(cell)
                    else:
                        imgui.text_disabled(cell)
        clipper.end()
        imgui.end_table()

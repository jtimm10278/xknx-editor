"""Dialog of "Hand Over to ETS…": choose the baseline (the project as ETS has it), compare it with
the open project and save the hand-over ZIP (see :mod:`..ets_handover`)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog

if TYPE_CHECKING:
    from editor_gui.file_dialogs import OpenFileDialog, SaveFileDialog
    from editor_gui.plugins.project.ui.project_diff import DiffJob, DiffResult

_POPUP = "###ets_handover"


class EtsHandoverDialog:
    def __init__(
        self,
        start_compare: Callable[[str, str | None], DiffJob],
        build_files: Callable[[DiffResult, str], dict[str, bytes]],
        notify: Callable[[str], None] | None = None,
    ) -> None:
        """``start_compare(path, password)`` compares the baseline with the open project (the
        file first); ``build_files(result, baseline_name)`` makes the hand-over's files."""
        self._start_compare = start_compare
        self._build_files = build_files
        self._notify = notify
        self._open_requested = False
        self._reset()

    def _reset(self) -> None:
        self._baseline: str = ""
        self._password = ""
        self._needs_password = False
        self._job: DiffJob | None = None
        self._result: DiffResult | None = None
        self._error: str | None = None
        self._open_dialog: OpenFileDialog | None = None
        self._save_dialog: SaveFileDialog | None = None
        self._saved: str | None = None

    def open(self) -> None:
        self._reset()
        self._open_requested = True

    # --- steps, callable without imgui (tests, MCP use the same pieces) ---

    def choose_baseline(self, path: str) -> None:
        self._baseline = path
        self._result = None
        self._error = None
        self._needs_password = False
        self.compare()

    def compare(self) -> None:
        if not self._baseline:
            return
        self._error = None
        self._job = self._start_compare(self._baseline, self._password or None)

    def poll(self) -> None:
        """Take the comparison's outcome once it has ended."""
        job = self._job
        if job is None:
            return
        if not job.future.done():
            return
        self._job = None
        outcome = job.future.result()
        if outcome.canceled:
            return
        if outcome.result is None:
            self._needs_password = bool(outcome.needs_password)
            self._error = outcome.error
            return
        self._result = outcome.result

    def save_to(self, path: str) -> Path:
        """Write the hand-over ZIP to ``path`` (".zip" added when missing)."""
        from editor_gui.plugins.project.ets_handover import handover_zip

        if self._result is None:
            raise ValueError("nothing compared yet")
        target = Path(path)
        if target.suffix.lower() != ".zip":
            target = target.with_suffix(".zip")
        files = self._build_files(self._result, Path(self._baseline).stem)
        target.write_bytes(handover_zip(files))
        self._saved = str(target)
        if self._notify is not None:
            self._notify(S.ETS_HANDOVER_SAVED.format(path=target.name))
        return target

    @property
    def comparing(self) -> bool:
        return self._job is not None

    @property
    def result(self) -> DiffResult | None:
        return self._result

    # --- rendering ---

    def _poll_dialogs(self) -> None:
        if self._open_dialog is not None and self._open_dialog.ready():
            chosen = self._open_dialog.result()
            self._open_dialog = None
            if chosen:
                self.choose_baseline(chosen[0])
        if self._save_dialog is not None and self._save_dialog.ready():
            target = self._save_dialog.result()
            self._save_dialog = None
            if target:
                try:
                    self.save_to(target)
                except (OSError, ValueError) as exc:
                    self._error = str(exc)

    def render(self) -> None:
        popup = f"{S.ETS_HANDOVER_TITLE}{_POPUP}"
        if self._open_requested:
            self._open_requested = False
            imgui.open_popup(popup)
        self.poll()
        self._poll_dialogs()
        if not begin_dialog(popup, width=560):
            return
        imgui.push_text_wrap_pos(0.0)
        imgui.text_wrapped(S.ETS_HANDOVER_INTRO)
        imgui.pop_text_wrap_pos()
        imgui.spacing()
        if imgui.button(S.ETS_HANDOVER_CHOOSE) and self._open_dialog is None:
            self._open_dialog = file_dialogs.open_file(
                S.ETS_HANDOVER_CHOOSE_TITLE,
                "",
                [S.ETS_HANDOVER_PROJECT_FILES, "*.knxproj *.xknx *.zip"],
            )
        if self._baseline:
            imgui.same_line()
            imgui.text(Path(self._baseline).name)
        if self._needs_password:
            imgui.set_next_item_width(240)
            _, self._password = imgui.input_text(
                S.ETS_HANDOVER_PASSWORD,
                self._password,
                imgui.InputTextFlags_.password,
            )
            imgui.same_line()
            if imgui.button(S.ETS_HANDOVER_COMPARE):
                self.compare()
        if self.comparing:
            imgui.text_disabled(S.ETS_HANDOVER_COMPARING)
        result = self._result
        if result is not None:
            imgui.separator()
            if not result.devices and not result.group_addresses:
                imgui.text(S.ETS_HANDOVER_NOTHING)
            else:
                n = len(result.devices)
                imgui.text(S.ETS_HANDOVER_DEVICES(n).format(count=n))
                n = len(result.group_addresses)
                imgui.text(S.ETS_HANDOVER_GROUP_ADDRESSES(n).format(count=n))
        if self._saved:
            imgui.text_disabled(
                S.ETS_HANDOVER_SAVED.format(path=Path(self._saved).name)
            )
        if self._error:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(imgui.ImVec4(1.0, 0.45, 0.35, 1.0), self._error)
            imgui.pop_text_wrap_pos()
        clicked = dialog_buttons(
            S.ETS_HANDOVER_SAVE,
            S.BTN_CLOSE,
            enabled=[result is not None and self._save_dialog is None, True],
        )
        if clicked == 0:
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            self._save_dialog = file_dialogs.save_file(
                S.ETS_HANDOVER_SAVE_TITLE,
                f"ets-handover-{stamp}.zip",
                [S.ETS_HANDOVER_ZIP_FILES, "*.zip"],
            )
        elif clicked == 1:
            if self._job is not None:
                self._job.cancel()
            self._reset()
            imgui.close_current_popup()
        end_dialog()

"""Dialog after a download was refused because it would drop group addresses or links the device
holds and the project lacks: lists them and offers to program again, removing them."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog

if TYPE_CHECKING:
    from editor_gui.plugins.project.program_queue import QueueItem
    from xknxeditor.download.table_check import TableExtras

_POPUP = "###link_removal"


def removal_lines(extras: TableExtras | None) -> list[str]:
    """One line per group address and per link the download would remove."""
    if extras is None:
        return []
    from xknxeditor.download.table_check import format_group_address

    lines = [
        S.LINK_REMOVAL_ADDRESS.format(address=format_group_address(a))
        for a in extras.addresses
    ]
    lines.extend(
        S.LINK_REMOVAL_LINK.format(address=format_group_address(a), number=o)
        for a, o in extras.links
    )
    return lines


class LinkRemovalDialog:
    def __init__(self, on_confirm: Callable[[QueueItem], None]) -> None:
        self._on_confirm = on_confirm
        self._lock = threading.Lock()
        # Set from the download's thread, taken on the UI thread.
        self._pending: tuple[QueueItem, TableExtras | None] | None = None
        self._item: QueueItem | None = None
        self._lines: list[str] = []
        self._open_requested = False

    def ask(self, item: QueueItem, extras: TableExtras | None) -> None:
        """Ask the user (on the next frame) whether to program ``item`` again, removing
        ``extras``. Safe to call from any thread."""
        with self._lock:
            self._pending = (item, extras)

    @property
    def is_open(self) -> bool:
        return self._item is not None

    def _take_pending(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is not None:
            self._item, extras = pending
            self._lines = removal_lines(extras)
            self._open_requested = True

    def confirm(self) -> None:
        if self._item is not None:
            self._on_confirm(self._item)
        self._item = None

    def cancel(self) -> None:
        self._item = None

    def render(self) -> None:
        self._take_pending()
        popup = f"{S.LINK_REMOVAL_TITLE}{_POPUP}"
        if self._open_requested:
            self._open_requested = False
            imgui.open_popup(popup)
        if not begin_dialog(popup, width=520):
            return
        item = self._item
        if item is not None:
            imgui.text(f"{item.address} {item.name}".strip())
        imgui.push_text_wrap_pos(0.0)
        imgui.text_wrapped(S.LINK_REMOVAL_TEXT)
        imgui.pop_text_wrap_pos()
        imgui.spacing()
        for line in self._lines:
            imgui.bullet_text(line)
        imgui.spacing()
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(S.LINK_REMOVAL_HINT)
        imgui.pop_text_wrap_pos()
        clicked = dialog_buttons(S.LINK_REMOVAL_CONFIRM, S.BTN_CANCEL)
        if clicked == 0:
            self.confirm()
            imgui.close_current_popup()
        elif clicked == 1:
            self.cancel()
            imgui.close_current_popup()
        end_dialog()

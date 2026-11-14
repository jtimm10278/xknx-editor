"""After a download refused to drop links the device holds: the dialog lists them and programs
again, removing them, only when the user confirms."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.program_queue import QueueItem
from editor_gui.plugins.project.ui.link_removal_dialog import (
    LinkRemovalDialog,
    removal_lines,
)
from xknxeditor.download.table_check import TableExtras

_EXTRAS = TableExtras(addresses=(0x0A27,), links=((0x0A27, 0), (0x190D, 5)))


def _item() -> QueueItem:
    return QueueItem(node_id=3, address="1.0.62", name="Bench", scope=None)


def test_lines_name_each_address_and_link() -> None:
    assert removal_lines(_EXTRAS) == [
        "Group address 1/2/39",
        "Link to 1/2/39 on object 0",
        "Link to 3/1/13 on object 5",
    ]
    assert removal_lines(None) == []


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frames(dialog: LinkRemovalDialog, count: int = 2) -> None:
    for _ in range(count):
        imgui.new_frame()
        dialog.render()
        imgui.end_frame()


def test_asked_from_another_thread_it_opens_on_the_next_frame(context: Any) -> None:
    confirmed: list[QueueItem] = []
    dialog = LinkRemovalDialog(confirmed.append)
    assert not dialog.is_open
    dialog.ask(_item(), _EXTRAS)
    _frames(dialog)
    assert dialog.is_open and confirmed == []


def test_confirm_programs_again_and_cancel_does_not(context: Any) -> None:
    confirmed: list[QueueItem] = []
    dialog = LinkRemovalDialog(confirmed.append)
    dialog.ask(_item(), _EXTRAS)
    _frames(dialog, 1)
    dialog.cancel()
    assert confirmed == [] and not dialog.is_open
    dialog.ask(_item(), None)
    _frames(dialog, 1)
    dialog.confirm()
    assert [i.address for i in confirmed] == ["1.0.62"]
    assert not dialog.is_open


def test_programming_again_accepts_the_removals() -> None:
    from editor_gui.plugins.project.plugin import ProjectPlugin

    queued: list[QueueItem] = []

    class _Queue:
        def enqueue(self, item: QueueItem) -> None:
            queued.append(item)

    stub = object.__new__(ProjectPlugin)
    stub._program_queue = _Queue()  # pyright: ignore[reportAttributeAccessIssue]
    first = _item()
    ProjectPlugin._program_accepting_removals(stub, first)  # pyright: ignore[reportPrivateUsage]
    assert len(queued) == 1
    again = queued[0]
    assert again.accept_table_removals and not first.accept_table_removals
    assert (again.node_id, again.address, again.scope) == (3, "1.0.62", None)

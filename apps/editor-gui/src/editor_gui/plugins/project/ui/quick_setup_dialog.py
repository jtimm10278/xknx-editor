"""Dialog of "Quick Setup…": pick a ready-made set-up for the device (see
:mod:`editor_gui.quick_setup`) and a group address per object, then apply it as one undo step."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.filter_box import filter_box

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import GroupAddressInfo
    from editor_gui.quick_setup import Template

_POPUP = "###quick_setup"


def ga_text(ga: GroupAddressInfo) -> str:
    return f"{ga.address} {ga.name}".strip()


def filter_group_addresses(
    addresses: Sequence[GroupAddressInfo], text: str
) -> list[GroupAddressInfo]:
    """The group addresses whose address or name contains every word of ``text``."""
    words = text.lower().split()
    return [ga for ga in addresses if all(w in ga_text(ga).lower() for w in words)]


class QuickSetupDialog:
    def __init__(
        self,
        get_group_addresses: Callable[[], Sequence[GroupAddressInfo]],
        on_apply: Callable[[int, Template, Mapping[str, int | None], bool], str | None],
        language: Callable[[], str],
    ) -> None:
        """``on_apply(node_id, template, assignments, replace_links)`` applies it and returns
        the reason it was refused (None: applied)."""
        self._get_group_addresses = get_group_addresses
        self._on_apply = on_apply
        self._language = language
        self._node_id: int | None = None
        self._device_label = ""
        self._templates: list[Template] = []
        self._index = 0
        self._assignments: dict[str, int | None] = {}
        self._filters: dict[str, str] = {}
        self._replace_links = True
        self._error: str | None = None
        self._open_requested = False

    @property
    def is_open(self) -> bool:
        return self._node_id is not None

    def open(self, node_id: int, device_label: str, templates: list[Template]) -> None:
        if not templates:
            return
        self._node_id = node_id
        self._device_label = device_label
        self._templates = templates
        self._index = 0
        self._assignments = {}
        self._filters = {}
        self._replace_links = True
        self._error = None
        self._open_requested = True

    @property
    def template(self) -> Template | None:
        if not self._templates:
            return None
        return self._templates[min(self._index, len(self._templates) - 1)]

    def select_template(self, index: int) -> None:
        if index != self._index:
            self._index = index
            self._assignments = {}
            self._filters = {}
            self._error = None

    def assign(self, slot_id: str, ga_id: int | None) -> None:
        self._assignments[slot_id] = ga_id

    def ready(self) -> bool:
        """Every slot that is not optional has a group address."""
        template = self.template
        return template is not None and all(
            not slot.required or self._assignments.get(slot.id) is not None
            for slot in template.slots
        )

    def apply(self) -> bool:
        """Apply the chosen set-up; True when it was applied (the dialog then closes)."""
        template = self.template
        if self._node_id is None or template is None or not self.ready():
            return False
        error = self._on_apply(
            self._node_id, template, dict(self._assignments), self._replace_links
        )
        if error is not None:
            self._error = error
            return False
        self._node_id = None
        return True

    def close(self) -> None:
        self._node_id = None

    def render(self) -> None:
        popup = f"{S.QUICK_SETUP_TITLE}{_POPUP}"
        if self._open_requested:
            self._open_requested = False
            imgui.open_popup(popup)
        if not begin_dialog(popup, width=560):
            return
        template = self.template
        language = self._language()
        imgui.text(self._device_label)
        imgui.spacing()
        if template is not None:
            self._render_template_combo(template, language)
            imgui.push_text_wrap_pos(0.0)
            imgui.text_disabled(template.description(language))
            imgui.pop_text_wrap_pos()
            imgui.separator_text(S.QUICK_SETUP_GROUP_ADDRESSES)
            addresses = self._get_group_addresses()
            for slot in template.slots:
                self._render_slot(
                    slot.id, slot.label(language), slot.required, addresses
                )
            _, self._replace_links = imgui.checkbox(
                S.QUICK_SETUP_REPLACE_LINKS, self._replace_links
            )
            imgui.push_text_wrap_pos(0.0)
            imgui.text_disabled(S.QUICK_SETUP_HINT)
            if not self.ready():
                imgui.text_disabled(S.QUICK_SETUP_CHOOSE_REQUIRED)
            if self._error:
                imgui.text_colored(
                    imgui.ImVec4(1.0, 0.45, 0.35, 1.0),
                    S.QUICK_SETUP_FAILED.format(error=self._error),
                )
            imgui.pop_text_wrap_pos()
        clicked = dialog_buttons(
            S.QUICK_SETUP_APPLY, S.BTN_CANCEL, enabled=[self.ready(), True]
        )
        if clicked == 0:
            if self.apply():
                imgui.close_current_popup()
        elif clicked == 1:
            self.close()
            imgui.close_current_popup()
        end_dialog()

    def _render_template_combo(self, template: Template, language: str) -> None:
        imgui.align_text_to_frame_padding()
        imgui.text(S.QUICK_SETUP_TEMPLATE)
        imgui.same_line()
        imgui.set_next_item_width(-1)
        if imgui.begin_combo("##qs_template", template.name(language)):
            for index, candidate in enumerate(self._templates):
                selected = index == self._index
                if imgui.selectable(
                    f"{candidate.name(language)}##qs_t{index}", selected
                )[0]:
                    self.select_template(index)
                if selected:
                    imgui.set_item_default_focus()
            imgui.end_combo()

    def _render_slot(
        self,
        slot_id: str,
        label: str,
        required: bool,
        addresses: Sequence[GroupAddressInfo],
    ) -> None:
        imgui.push_id(f"qs_slot_{slot_id}")
        imgui.text(label if required else S.QUICK_SETUP_OPTIONAL.format(label=label))
        chosen = self._assignments.get(slot_id)
        current = next((ga for ga in addresses if ga.id == chosen), None)
        preview = ga_text(current) if current is not None else S.QUICK_SETUP_NOT_LINKED
        imgui.set_next_item_width(-1)
        if imgui.begin_combo("##ga", preview):
            self._filters[slot_id] = filter_box(
                "##ga_filter", S.QUICK_SETUP_FILTER_HINT, self._filters.get(slot_id, "")
            )
            if imgui.selectable(S.QUICK_SETUP_NOT_LINKED, chosen is None)[0]:
                self.assign(slot_id, None)
            for ga in filter_group_addresses(addresses, self._filters.get(slot_id, "")):
                if imgui.selectable(f"{ga_text(ga)}##ga{ga.id}", ga.id == chosen)[0]:
                    self.assign(slot_id, ga.id)
            imgui.end_combo()
        imgui.pop_id()

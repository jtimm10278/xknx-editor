"""The "Quick Setup…" dialog: a template and a group address per object, applied only when every
object that is not optional has one; a refusal keeps the dialog open with the reason."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.service import GroupAddressInfo
from editor_gui.plugins.project.ui.quick_setup_dialog import (
    QuickSetupDialog,
    filter_group_addresses,
)
from editor_gui.quick_setup import Template, parse_templates

_GAS = [
    GroupAddressInfo(id=1, address="1/2/3", name="Kitchen light"),
    GroupAddressInfo(id=2, address="3/1/13", name="Bedroom blinds"),
]


def _templates() -> list[Template]:
    slots = [
        {"id": "a", "label": "Light", "object": 0},
        {"id": "b", "label": "Other", "object": 1, "required": False},
    ]
    return parse_templates(
        {
            "format": 1,
            "device": {
                "name": "Switch",
                "manufacturer": "M-0001",
                "application_number": "2207",
                "application_versions": ["10"],
            },
            "templates": [
                {"id": "one", "name": "One", "parameters": [], "slots": slots},
                {"id": "two", "name": "Two", "parameters": [], "slots": slots[:1]},
            ],
        }
    )


class _Recorder:
    def __init__(self, error: str | None = None) -> None:
        self.calls: list[tuple[int, str, dict[str, int | None], bool]] = []
        self.error = error

    def __call__(
        self,
        node_id: int,
        template: Template,
        assignments: Mapping[str, int | None],
        replace: bool,
    ) -> str | None:
        self.calls.append((node_id, template.id, dict(assignments), replace))
        return self.error


def _dialog(recorder: _Recorder) -> QuickSetupDialog:
    return QuickSetupDialog(lambda: _GAS, recorder, lambda: "en")


def test_filter_matches_every_word_of_address_and_name() -> None:
    assert [g.id for g in filter_group_addresses(_GAS, "bed 3/1")] == [2]
    assert [g.id for g in filter_group_addresses(_GAS, "")] == [1, 2]
    assert filter_group_addresses(_GAS, "garage") == []


def test_apply_needs_every_required_object_then_closes() -> None:
    recorder = _Recorder()
    dialog = _dialog(recorder)
    dialog.open(7, "1.1.5 Switch", _templates())
    assert dialog.is_open and not dialog.ready()
    assert dialog.apply() is False and recorder.calls == []
    dialog.assign("a", 1)
    assert dialog.ready()
    assert dialog.apply() is True
    assert recorder.calls == [(7, "one", {"a": 1}, True)]
    assert not dialog.is_open


def test_a_refusal_keeps_the_dialog_open() -> None:
    recorder = _Recorder(error="link refused")
    dialog = _dialog(recorder)
    dialog.open(7, "Switch", _templates())
    dialog.assign("a", 2)
    assert dialog.apply() is False
    assert dialog.is_open


def test_another_template_starts_with_no_group_addresses() -> None:
    dialog = _dialog(_Recorder())
    dialog.open(7, "Switch", _templates())
    dialog.assign("a", 1)
    dialog.select_template(1)
    template = dialog.template
    assert template is not None and template.id == "two"
    assert not dialog.ready()


def test_nothing_opens_without_templates() -> None:
    dialog = _dialog(_Recorder())
    dialog.open(7, "Switch", [])
    assert not dialog.is_open and dialog.template is None


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def test_the_dialog_renders_headless(context: Any) -> None:
    dialog = _dialog(_Recorder())
    dialog.open(7, "1.1.5 Switch", _templates())
    dialog.assign("a", 1)
    for _ in range(3):
        imgui.new_frame()
        dialog.render()
        imgui.end_frame()
    assert dialog.is_open

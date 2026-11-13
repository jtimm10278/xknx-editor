"""The Generic Plug-in's parameter passes in a download, on the made-up "Demo" device of the
reader tests: the code segment size from the description, the load controls per merge id, and
the hook that hands them to the download image."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import editor_gui.programming as programming
import pytest
from editor_gui.plugin_blob_readers.siemens_generic import parse_description
from editor_gui.plugin_blob_readers.siemens_generic.encoder import MemoryImage
from editor_gui.plugin_blob_readers.siemens_generic.plugin_download import (
    PARAMETER_FRAME,
    plugin_merge_controls,
)
from test_siemens_generic_reader import DESCRIPTION

from xknxeditor.download.image import DownloadImage
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp


def test_alloc_length_comes_from_the_first_code_segment() -> None:
    plain = parse_description(DESCRIPTION)
    assert plain is not None and plain.alloc_length is None
    with_code = DESCRIPTION.replace(
        b"<Static>",
        b'<Static><Code><CodeSegment Id="c" AllocLength="32512"/>'
        b'<CodeSegment Id="d" AllocLength="16"/></Code>',
        1,
    )
    described = parse_description(with_code)
    assert described is not None and described.alloc_length == 0x7F00


def _set_up(controls: tuple[object, ...]) -> list[tuple[str, int]]:
    return [
        (type(c).__name__, cast("Any", c).lsm_idx)
        for c in controls
        if isinstance(c, LdCtrlUnload | LdCtrlLoad | LdCtrlRelSegment)
    ]


def test_merge_controls_load_the_owned_parameter_bytes_and_finish() -> None:
    owned = (True,) * 12 + (False,) + (True,) * 3
    image = MemoryImage(0x8C00, bytes(range(16)), owned, ())
    controls = plugin_merge_controls(image, 0x7F00)
    assert sorted(controls) == [1, 6, 7]
    assert controls[7] == ()

    # Merge 1: the application object set up afresh, then the parameter memory.
    first = controls[1]
    assert _set_up(first) == [
        ("LdCtrlUnload", 4),
        ("LdCtrlLoad", 4),
        ("LdCtrlRelSegment", 4),
    ]
    segment = first[2]
    assert isinstance(segment, LdCtrlRelSegment) and segment.size == 0x7F00
    writes = [c for c in first if isinstance(c, LdCtrlWriteMem)]
    assert [(w.address, w.inline_data) for w in writes] == [
        (0x8C00, bytes(range(PARAMETER_FRAME))),
        (0x8C0A, b"\x0a\x0b"),
        (0x8C0D, b"\x0d\x0e\x0f"),
    ]
    assert not any(w.verify for w in writes)

    # Merge 6: set up again, completed, then object 6 property 205 = 10h.
    last = controls[6]
    assert _set_up(last)[:3] == _set_up(first)
    assert isinstance(last[3], LdCtrlLoadCompleted) and last[3].lsm_idx == 4
    finish = last[4]
    assert isinstance(finish, LdCtrlWriteProp)
    assert (finish.obj_idx, finish.prop_id, finish.inline_data) == (6, 205, b"\x10")
    with pytest.raises(TypeError):
        cast("Any", controls)[1] = ()


def _image_for(monkeypatch: pytest.MonkeyPatch, hook: object) -> DownloadImage:
    built = DownloadImage((), ())
    monkeypatch.setattr(programming, "build_image", lambda *a, **k: built)
    monkeypatch.setattr(programming, "plugin_load_controls", hook)
    device = SimpleNamespace(app=object(), dynamic_ui=None)
    return programming._image_for(cast("Any", device))  # pyright: ignore[reportPrivateUsage]


def test_the_hook_adds_its_controls_to_the_download_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controls = {7: ()}
    assert _image_for(monkeypatch, lambda device: controls).plugin_load_controls == (
        controls
    )
    # No hook, or no controls for this device: the image as built.
    assert _image_for(monkeypatch, None).plugin_load_controls == {}
    assert _image_for(monkeypatch, lambda device: None).plugin_load_controls == {}


def test_the_plugin_question_only_when_the_editor_lacks_its_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    needs_plugin = SimpleNamespace(download_plugin_required=True)
    plain = SimpleNamespace(download_plugin_required=False)
    monkeypatch.setattr(programming, "plugin_steps_available", None)
    assert programming.needs_manufacturer_plugin(cast("Any", needs_plugin))
    assert not programming.needs_manufacturer_plugin(cast("Any", plain))
    monkeypatch.setattr(programming, "plugin_steps_available", lambda device: True)
    assert not programming.needs_manufacturer_plugin(cast("Any", needs_plugin))
    monkeypatch.setattr(programming, "plugin_steps_available", lambda device: False)
    assert programming.needs_manufacturer_plugin(cast("Any", needs_plugin))


def _steps_plugin(views: list[Any], controls: list[Any]) -> Any:
    from editor_gui.plugins.project.plugin import ProjectPlugin

    plugin = cast("Any", object.__new__(ProjectPlugin))
    plugin._generic_steps = {}
    plugin.generic_plugin_view = lambda _device: views[-1]
    calls: list[int] = []

    def load_controls(_device: Any) -> Any:
        calls.append(1)
        return controls[-1]

    plugin.generic_plugin_load_controls = load_controls
    plugin.calls = calls
    return plugin


def test_steps_available_is_cached_until_the_settings_change() -> None:
    first, second = object(), object()
    views: list[Any] = [first]
    controls: list[Any] = [{7: ()}]
    plugin = _steps_plugin(views, controls)
    device = SimpleNamespace(node_id=3)
    assert plugin.generic_plugin_steps_available(device)
    assert plugin.generic_plugin_steps_available(device)
    assert len(plugin.calls) == 1
    # Changed settings give a new view: asked again.
    views.append(second)
    controls.append(None)
    assert not plugin.generic_plugin_steps_available(device)
    assert len(plugin.calls) == 2
    views.append(None)
    assert not plugin.generic_plugin_steps_available(device)


def test_no_steps_for_settings_of_another_application() -> None:
    from editor_gui.plugins.project.plugin import ProjectPlugin

    plugin = cast("Any", object.__new__(ProjectPlugin))
    plugin.generic_plugin_view = lambda _device: SimpleNamespace(
        application_matches=False
    )
    assert plugin.generic_plugin_load_controls(SimpleNamespace(app=None)) is None

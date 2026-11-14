"""The "Tested" / "Not yet tested" badge beside a device's name: whether its application is on the
list of devices proven with this editor (see :mod:`editor_gui.tested_devices`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.tested_devices import TestedDevice, for_device
from editor_gui.theme import status_color

if TYPE_CHECKING:
    from editor_gui.device import Device


def proof_texts(entry: TestedDevice) -> list[str]:
    words = {
        "download": S.TESTED_PROOF_DOWNLOAD,
        "read_back": S.TESTED_PROOF_READ_BACK,
        "plugin_edit": S.TESTED_PROOF_PLUGIN_EDIT,
        "live_test": S.TESTED_PROOF_LIVE_TEST,
    }
    return [words[p] for p in entry.proven if p in words]


def badge_text(entry: TestedDevice | None) -> tuple[str, str]:
    """The badge and its tooltip."""
    if entry is None:
        return S.NOT_TESTED_BADGE, S.NOT_TESTED_TOOLTIP
    tooltip = S.TESTED_TOOLTIP.format(
        version=entry.editor_version,
        date=entry.date,
        proofs=", ".join(proof_texts(entry)),
    )
    if entry.notes:
        tooltip += f"\n{entry.notes}"
    return S.TESTED_BADGE, tooltip


def render_tested_badge(device: Device) -> None:
    """On the same line as the item before it; nothing for a device without an application."""
    if device.app is None:
        return
    entry = for_device(device)
    text, tooltip = badge_text(entry)
    imgui.same_line()
    imgui.align_text_to_frame_padding()
    if entry is None:
        imgui.text_disabled(f"[{text}]")
    else:
        imgui.text_colored(status_color("success"), f"[{text}]")
    imgui.set_item_tooltip(tooltip)

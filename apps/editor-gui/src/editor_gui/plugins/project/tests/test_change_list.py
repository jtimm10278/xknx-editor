"""The change list for ETS built from a project comparison (before -> after)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.project.change_list import (
    FORMAT,
    change_list,
    change_list_markdown,
    links,
)
from editor_gui.plugins.project.ui.device_diff import parameter_text
from editor_gui.plugins.project.ui.project_diff import DetailRow, DiffEntry, DiffResult


def _result() -> DiffResult:
    """A site project as ETS has it (before) vs the edited one (after)."""
    return DiffResult(
        devices=[
            DiffEntry(
                "1.0.250",
                "changed",
                "address '1.0.250' -> '1.1.20'",
                (
                    DetailRow(
                        "parameter",
                        "General > Input A",
                        "changed",
                        "1",
                        "2",
                        "Switch (rising edge)",
                        "Switch (rising and falling edge)",
                        "P-355_R-355",
                    ),
                    DetailRow(
                        "parameter",
                        "General > Value on falling edge",
                        "added",
                        "",
                        "0",
                        "",
                        "Off",
                    ),
                    DetailRow("parameter", "General > Name", "changed", "a", "b"),
                    DetailRow(
                        "group_object",
                        "Switch, Input B",
                        "changed",
                        "1.001  ·  C - W T -  ·  Low  ·  -> 2/0/2",
                        "1.001  ·  C - W T -  ·  Low  ·  -> 2/0/1",
                        key="00001",
                    ),
                    DetailRow(
                        "group_object",
                        "Switch, Input A",
                        "added",
                        "",
                        "1.001  ·  C - W T -  ·  Low  ·  -> 1/0/1",
                    ),
                ),
            ),
            DiffEntry("1.1.21", "added", "Push-button interface"),
        ],
        group_addresses=[DiffEntry("15/7/250", "removed", "Test (1.001)")],
    )


def test_links_of_a_group_object_summary() -> None:
    assert links("1.001  ·  C - W T -  ·  -> 1/0/1 2/0/1") == ["1/0/1", "2/0/1"]
    assert links("1.001  ·  C - W T -") == []
    assert links("") == []


def test_change_list_json() -> None:
    data = change_list(_result(), "Site backup 7", "Edited", "2030-01-02T03:04:05")

    assert data["format"] == FORMAT
    assert (data["before"], data["after"], data["made"]) == (
        "Site backup 7",
        "Edited",
        "2030-01-02T03:04:05",
    )
    assert data["summary"] == {"devices": 2, "group_addresses": 1}
    device = data["devices"][0]
    assert (device["address"], device["change"]) == ("1.0.250", "changed")
    assert device["detail"] == "address '1.0.250' -> '1.1.20'"
    input_a = device["parameters"][0]
    assert input_a == {
        "label": "General > Input A",
        "ref": "P-355_R-355",
        "change": "changed",
        "before": "Switch (rising edge)",
        "after": "Switch (rising and falling edge)",
        "before_value": "1",
        "after_value": "2",
    }
    # Without an option text the value itself is shown.
    assert device["parameters"][2]["before"] == "a"
    input_b, new_a = device["group_objects"]
    assert (input_b["link"], input_b["unlink"]) == (["2/0/1"], ["2/0/2"])
    assert (input_b["number"], new_a["number"]) == (1, None)
    assert (new_a["links_before"], new_a["link"], new_a["unlink"]) == (
        [],
        ["1/0/1"],
        [],
    )
    added = data["devices"][1]
    assert added == {
        "address": "1.1.21",
        "change": "added",
        "detail": "Push-button interface",
    }
    assert data["group_addresses"] == [
        {"address": "15/7/250", "change": "removed", "detail": "Test (1.001)"}
    ]
    assert "made" not in change_list(_result(), "a", "b")


def test_change_list_markdown() -> None:
    text = change_list_markdown(change_list(_result(), "Site backup 7", "Edited"))

    assert text.startswith("# Change list for ETS: Site backup 7 -> Edited\n")
    assert "## Devices (2)" in text
    assert "### 1.0.250 (changed)\n\naddress '1.0.250' -> '1.1.20'" in text
    assert (
        '- General > Input A `P-355_R-355`: "Switch (rising edge)" -> '
        '**"Switch (rising and falling edge)"** (stored 1 -> 2)'
    ) in text
    assert (
        '- General > Value on falling edge: (none) -> **"Off"** (stored - -> 0)' in text
    )
    assert '- General > Name: "a" -> **"b"**\n' in text
    assert "- Obj 1 Switch, Input B: link 2/0/1; unlink 2/0/2" in text
    assert (
        "- Switch, Input A: link 1/0/1; new object: 1.001  ·  C - W T -  ·  Low" in text
    )
    assert "ETS may drop its links" in text
    assert "### 1.1.21 (added)\n\nPush-button interface" in text
    assert "## Group addresses (1)\n\n- **15/7/250** removed: Test (1.001)" in text


def test_group_object_settings_change_without_links() -> None:
    result = DiffResult(
        devices=[
            DiffEntry(
                "1.1.5",
                "changed",
                "",
                (
                    DetailRow(
                        "group_object",
                        "Status",
                        "changed",
                        "1.001  ·  C R - T -  ·  -> 1/1/1",
                        "1.001  ·  C - W T -  ·  -> 1/1/1",
                    ),
                    DetailRow(
                        "group_object", "Gone", "removed", "1.001  ·  C - W T -", ""
                    ),
                    DetailRow(
                        "group_object", "Same", "changed", "-> 1/1/2", "-> 1/1/2"
                    ),
                    DetailRow("parameter", "Hidden > ", "added", "", ""),
                ),
            )
        ],
        group_addresses=[],
    )
    text = change_list_markdown(change_list(result, "a", "b"))
    assert (
        "- Status: settings 1.001  ·  C R - T - -> **1.001  ·  C - W T -**; keeps 1/1/1"
    ) in text
    assert "### 1.1.5 (changed)\n\nGroup objects:" in text
    assert "- Gone: object no longer there" in text
    assert "- Same: keeps 1/1/2" in text
    assert "Hidden" not in text


def test_nothing_to_change() -> None:
    text = change_list_markdown(change_list(DiffResult([], []), "a", "b"))
    assert "Nothing to change: the projects are the same." in text


def test_parameter_text_is_the_chosen_option() -> None:
    from xknxeditor.prod.parser_v2.ui.parameter import EnumChoice, EnumWidget

    widget = EnumWidget(
        choices=(
            EnumChoice(1, "Switch (rising edge)", "e1"),
            EnumChoice(2, "Toggle", "e2"),
        ),
        base=cast(Any, None),
    )

    def parameter(value: str, w: object = widget) -> Any:
        return SimpleNamespace(value=value, widget=w)

    assert parameter_text(parameter("2")) == "Toggle"
    assert parameter_text(parameter("9")) == ""
    assert parameter_text(parameter("x")) == ""
    assert parameter_text(parameter("2", None)) == ""

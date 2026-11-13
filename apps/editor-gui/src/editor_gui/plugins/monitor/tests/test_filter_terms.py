"""Field terms in the monitor filter box."""

from __future__ import annotations

from editor_gui.plugins.monitor.filter_terms import compile_filter, has_field_terms
from editor_gui.plugins.monitor.formatting import COLUMN_INDEX, COLUMNS


def _row(**cells: str) -> tuple[list[str], str]:
    row = [""] * len(COLUMNS)
    for key, value in cells.items():
        row[COLUMN_INDEX[key]] = value
    return row, "\n".join(row).lower()


_LIGHT = _row(
    destination_address="1/0/1",
    destination_name="Kitchen Ceiling",
    source_address="1.1.20",
    source_name="Push-button Kitchen",
    building_part="Kitchen",
    service="Write",
    dpt="1.001",
)
_BLIND = _row(
    destination_address="2/0/11",
    destination_name="Office Blind",
    source_address="1.1.21",
    source_name="Push-button Office",
    building_part="Office",
    service="Write",
)


def _shown(text: str) -> list[str]:
    match = compile_filter(text)
    rows = {"light": _LIGHT, "blind": _BLIND}
    return [name for name, (cells, hay) in rows.items() if match(cells, hay)]


def test_plain_text_is_matched_as_before() -> None:
    assert not has_field_terms("kitchen ceiling")
    assert _shown("kitchen ceiling") == ["light"]
    assert _shown("ceiling kitchen") == []
    assert _shown("") == ["light", "blind"]
    # A colon alone is no field term.
    assert not has_field_terms("12:30")


def test_addresses_match_exactly_or_by_prefix() -> None:
    assert _shown("ga:1/0/1") == ["light"]
    assert _shown("ga:2/0/1") == []
    assert _shown("ga:2/0/") == ["blind"]
    assert _shown("ga:2/*") == ["blind"]
    assert _shown("src:1.1.") == ["light", "blind"]
    assert _shown("src:1.1.21") == ["blind"]


def test_names_rooms_and_combined_terms() -> None:
    assert _shown("room:office") == ["blind"]
    assert _shown("dev:push-button") == ["light", "blind"]
    assert _shown('to:"kitchen ceiling"') == ["light"]
    assert _shown("dpt:1.001 svc:write") == ["light"]
    assert _shown("src:1.1. blind") == ["blind"]
    assert _shown("room:office ga:1/") == []
    assert _shown("fn:anything") == []


def test_unclosed_quote_is_split_on_spaces() -> None:
    assert _shown('room:"office') == []
    assert _shown("room:kitchen ga:1/0/1") == ["light"]

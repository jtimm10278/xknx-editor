"""The list of devices proven with this editor: the bundled file is valid, entries fit their
application whatever its hash, and the badge says what was proven."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from editor_gui.plugins.project.ui.tested_badge import badge_text, proof_texts
from editor_gui.tested_devices import (
    DATA_FILE,
    PROOFS,
    TestedDevicesError,
    application_key,
    find,
    parse,
    tested_devices,
)


def _data() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def test_the_bundled_list_is_valid_and_seeded() -> None:
    entries = tested_devices()
    assert {e.application for e in entries} == {
        "M-0001_A-2207-10",
        "M-0021_A-9084-02",
        "M-0001_A-9820-03",
    }
    for entry in entries:
        assert entry.proven and set(entry.proven) <= set(PROOFS)


def test_an_entry_fits_its_application_whatever_the_hash() -> None:
    up220 = find("M-0001_A-2207-10-E8C8")
    assert up220 is not None and up220.order_number == "5WG1 220-2AB03"
    crabtree = find("M-0021_A-9084-02-0F47-O0001")
    assert crabtree is not None and crabtree.proven == ("read_back",)
    assert find("M-0001_A-2207-11-E8C8") is None
    assert find(None) is None and find("") is None


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("M-0001_A-2207-10-E8C8", "M-0001_A-2207-10"),
        ("m-0021_a-9084-02-0f47-o0001", "M-0021_A-9084-02"),
        ("M-0001_A-2207", None),
        ("whatever", None),
    ],
)
def test_application_key(value: str, key: str | None) -> None:
    assert application_key(value) == key


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda d: d.update(format=2), "format"),
        (lambda d: d["devices"][0].update(proven=[]), "proven"),
        (lambda d: d["devices"][0].update(proven=["flashed"]), "proven"),
        (lambda d: d["devices"][0].update(date="13 Nov 2026"), "date"),
        (
            lambda d: d["devices"][0].update(application="M-0001_A-2207-10-E8C8"),
            "application",
        ),
        (lambda d: d["devices"][0].pop("order_number"), "order_number"),
        (lambda d: d["devices"].append(copy.deepcopy(d["devices"][0])), "twice"),
        (lambda d: d["devices"][0].update(notes=3), "notes"),
        (lambda d: d.update(devices={}), "list"),
    ],
)
def test_broken_entries_are_refused_with_the_reason(change: Any, match: str) -> None:
    data = _data()
    change(data)
    with pytest.raises(TestedDevicesError, match=match):
        parse(data)


def test_proofs_keep_their_order() -> None:
    data = _data()
    data["devices"][0]["proven"] = ["read_back", "download"]
    assert parse(data)[0].proven == ("download", "read_back")


def test_badge_names_the_proofs_and_notes() -> None:
    entry = find("M-0001_A-9820-03-371E")
    assert entry is not None
    text, tooltip = badge_text(entry)
    assert text == "Tested"
    assert len(proof_texts(entry)) == 3
    assert entry.date in tooltip and entry.notes in tooltip
    text, tooltip = badge_text(None)
    assert text == "Not yet tested" and "spare device" in tooltip

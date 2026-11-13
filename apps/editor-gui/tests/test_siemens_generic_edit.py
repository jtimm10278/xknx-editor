"""Changing Generic Plug-in settings on the made-up "Demo" device of the reader tests: which values
a setting takes, the stored settings with one value changed (the rest of the document as it
was), and the project plugin's change as one stored blob."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from editor_gui.plugin_blob_readers.siemens_generic import (
    EditError,
    build_view,
    check_value,
    edit_spec,
    parse_description,
    parse_settings,
    record_download,
    set_value,
)
from editor_gui.plugins.project.plugin import ProjectPlugin
from test_siemens_generic_reader import DESCRIPTION, _sources, settings_blob

_WHEN = datetime.datetime(2026, 1, 2, 3, 4, 5, 678000)


def _description() -> Any:
    description = parse_description(DESCRIPTION)
    assert description is not None
    return description


def test_what_each_setting_takes() -> None:
    d = _description()
    mode = edit_spec(d, "r_mode")
    assert mode is not None
    assert [c.value for c in mode.choices] == ["dm_Off", "dm_A", "dm_B"]
    assert mode.choices[1].text == "Mode A"
    delay = edit_spec(d, "r_delay")
    assert delay is not None and (delay.minimum, delay.maximum) == (0, 0xFFFF)
    offset = edit_spec(d, "r_offset")
    assert offset is not None and (offset.minimum, offset.maximum) == (-128, 127)
    run_time = edit_spec(d, "r_time")
    assert run_time is not None and run_time.unit == "s"
    # A text, a value the device gives, an unknown ref: not changeable.
    assert edit_spec(d, "r_name") is None
    assert edit_spec(d, "r_order") is None
    assert edit_spec(d, "nothing") is None
    assert check_value(d, "r_mode", "dm_A") == ""
    assert "not one of" in check_value(d, "r_mode", "dm_C")
    assert check_value(d, "r_delay", "65535") == ""
    assert "outside" in check_value(d, "r_delay", "65536")
    assert "outside" in check_value(d, "r_offset", "-129")
    assert "whole number" in check_value(d, "r_delay", "1.5")
    assert "cannot be changed" in check_value(d, "r_name", "x")


def test_number_limits_of_the_description_count() -> None:
    limited = DESCRIPTION.replace(
        b'<TypeNumber Size="16" IntelOrderSwap="true" Type="unsigned"/>',
        b'<TypeNumber Size="16" IntelOrderSwap="true" Type="unsigned" '
        b'minInclusive="10" maxInclusive="900"/>',
    )
    d = parse_description(limited)
    assert d is not None
    spec = edit_spec(d, "r_delay")
    assert spec is not None and (spec.minimum, spec.maximum) == (10, 900)
    assert "outside" in check_value(d, "r_delay", "9")


def test_one_value_changes_and_nothing_else() -> None:
    blob = settings_blob()
    changed = set_value(blob, "r_delay", "450", _WHEN)
    before, after = parse_settings(blob), parse_settings(changed)
    assert before is not None and after is not None
    assert after.values == {**before.values, "r_delay": "450"}
    assert after.memory_image == before.memory_image
    assert after.last_download == before.last_download
    assert after.modified == "2026-01-02T03:04:05.678"
    # Same encoding (UTF-16 LE without a byte order mark), and only those two places differ.
    text = changed.decode("utf-16-le")
    assert text == blob.decode("utf-16-le").replace(
        "<Value>600</Value>", "<Value>450</Value>"
    ).replace(
        'LastModified="2025-01-01T00:00:00"', 'LastModified="2026-01-02T03:04:05.678"'
    )


def test_a_value_the_plugin_never_stored_is_added() -> None:
    blob = settings_blob({"r_mode": "dm_A"})
    changed = parse_settings(set_value(blob, "r_offset", "-5", _WHEN))
    assert changed is not None
    assert changed.values == {"r_mode": "dm_A", "r_offset": "-5"}
    empty = settings_blob({}).replace(
        '<ParameterInstanceRefs><ParameterInstanceRef RefId=""/></ParameterInstanceRefs>'.encode(
            "utf-16-le"
        ),
        "<ParameterInstanceRefs />".encode("utf-16-le"),
    )
    added = parse_settings(set_value(empty, "r_mode", "dm_B", _WHEN))
    assert added is not None and added.values == {"r_mode": "dm_B"}


def test_byte_order_mark_and_utf8_are_kept() -> None:
    with_bom = b"\xff\xfe" + settings_blob()
    changed = set_value(with_bom, "r_mode", "dm_A", _WHEN)
    assert changed.startswith(b"\xff\xfe")
    plain = settings_blob(encoding="utf-8")
    changed_plain = set_value(plain, "r_mode", "dm_A", _WHEN)
    assert b"<Value>dm_A</Value>" in changed_plain
    settings = parse_settings(changed_plain)
    assert settings is not None and settings.values["r_mode"] == "dm_A"
    with pytest.raises(EditError):
        set_value(b"<other/>", "r_mode", "dm_A")


def test_rows_say_what_can_be_changed() -> None:
    sources = _sources()
    settings = parse_settings(
        settings_blob({**dict.fromkeys(["r_mode"], "dm_B"), "r_fb": "dm_Yes"})
    )
    assert settings is not None
    view = build_view(sources.description, settings)
    rows = {r.ref_id: r for p in view.pages for r in p.rows if not r.separator}
    assert rows["r_mode"].edit is not None
    assert rows["r_delay"].edit is not None
    # Set by an Assign rule: shown, not changeable.
    assert rows["r_derived"].assigned and rows["r_derived"].edit is None


def _plugin(blob: bytes) -> tuple[Any, list[tuple[str, bytes]]]:
    stored: list[tuple[str, bytes]] = []
    sources = _sources()

    def view(_device: Any, **_kw: Any) -> Any:
        settings = parse_settings(blob)
        assert settings is not None
        return build_view(sources.description, settings)

    plugin = cast(Any, object.__new__(ProjectPlugin))
    plugin.generic_plugin_view = view
    plugin._generic_plugin_sources = lambda _device, _ids: sources
    plugin._api = SimpleNamespace(
        project=SimpleNamespace(
            persist_device_blob=lambda _d, name, data, label=None: stored.append(
                (name, data)
            )
        ),
        log=SimpleNamespace(
            info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None
        ),
    )
    return plugin, stored


def test_the_project_plugin_stores_the_changed_settings() -> None:
    blob = settings_blob()
    plugin, stored = _plugin(blob)
    device = SimpleNamespace(
        app=None,
        individual_address="1.2.3",
        binary_data=[SimpleNamespace(name="BLOB_STRING", data=blob)],
    )
    assert plugin.generic_plugin_set_value(device, "r_delay", "450") == ""
    assert [name for name, _ in stored] == ["BLOB_STRING"]
    changed = parse_settings(stored[0][1])
    assert changed is not None and changed.values["r_delay"] == "450"
    # Refused: an out-of-range value, a rule's value, a setting not shown.
    assert "outside" in plugin.generic_plugin_set_value(device, "r_delay", "70000")
    assert "cannot be changed" in plugin.generic_plugin_set_value(
        device, "r_derived", "dm_No"
    )
    assert "not a setting shown" in plugin.generic_plugin_set_value(
        device, "r_count_b", "dm_A"
    )
    assert len(stored) == 1


def test_a_download_is_recorded_in_the_stored_image() -> None:
    sources = _sources()
    base = sources.description.memory_base
    assert base is not None
    changed = set_value(settings_blob(), "r_delay", "450", _WHEN)
    settings = parse_settings(changed)
    assert settings is not None
    view = build_view(sources.description, settings)
    rows = {r.ref_id: r for p in view.pages for r in p.rows if not r.separator}
    assert rows["r_delay"].memory.value == "mismatch"
    # The download wrote 450 (little endian) at base + 2 and read it back unchanged.
    recorded = record_download(
        changed, base, [(base + 2, (450).to_bytes(2, "little"))], _WHEN
    )
    after = parse_settings(recorded)
    assert after is not None and settings.memory_image is not None
    assert after.memory_image is not None
    assert after.memory_image[2:4] == (450).to_bytes(2, "little")
    assert after.memory_image[:2] == settings.memory_image[:2]
    assert after.memory_image[4:] == settings.memory_image[4:]
    assert after.last_download == "2026-01-02T03:04:05.678"
    assert after.values == settings.values
    view = build_view(sources.description, after)
    rows = {r.ref_id: r for p in view.pages for r in p.rows if not r.separator}
    assert rows["r_delay"].memory.value == "match"
    # Only the image and the times differ in the document.
    before_text, after_text = changed.decode("utf-16-le"), recorded.decode("utf-16-le")
    assert (
        before_text.split("<DownloadedSettings")[0]
        == after_text.split("<DownloadedSettings")[0]
    )


def test_a_download_without_a_stored_image_is_not_recorded() -> None:
    with pytest.raises(EditError, match="no stored image"):
        record_download(settings_blob(image=None), 0x4000, [(0x4000, b"\x01")])
    with pytest.raises(EditError, match="outside"):
        record_download(settings_blob(), 0x4000, [(0x3FFF, b"\x01")])


def test_the_project_plugin_records_a_verified_download() -> None:
    blob = settings_blob()
    plugin, stored = _plugin(blob)
    device = SimpleNamespace(
        node_id=4,
        app=None,
        individual_address="1.2.3",
        binary_data=[SimpleNamespace(name="BLOB_STRING", data=blob)],
    )
    plugin._api.main_thread = None
    plugin._api.project.find_device_by_node_id = lambda _nid: device
    plugin.generic_plugin_downloaded(device, ((0x4002, b"\x01\x00"),))
    assert [name for name, _ in stored] == ["BLOB_STRING"]
    recorded = parse_settings(stored[0][1])
    assert recorded is not None and recorded.memory_image is not None
    assert recorded.memory_image[2:4] == b"\x01\x00"

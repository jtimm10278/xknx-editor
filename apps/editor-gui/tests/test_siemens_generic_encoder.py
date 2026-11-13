"""The parameter memory of Generic Plug-in settings, from the plain-XML description alone, on
the made-up "Demo" device of the reader tests."""

from __future__ import annotations

from editor_gui.plugin_blob_readers.siemens_generic import (
    parse_description,
    parse_settings,
)
from editor_gui.plugin_blob_readers.siemens_generic.encoder import (
    MemoryImage,
    memory_image,
)
from test_siemens_generic_reader import (
    _BASE,
    _IMAGE,
    _VALUES,
    DESCRIPTION,
    settings_blob,
)


def _image(values: dict[str, str] | None = None) -> MemoryImage:
    description = parse_description(DESCRIPTION)
    settings = parse_settings(settings_blob(values))
    assert description is not None and settings is not None
    image = memory_image(description, settings)
    assert image is not None
    return image


def test_the_stored_settings_give_the_image_ets_stored() -> None:
    image = _image()
    assert image.base == _BASE
    # The parameters own the first 9 bytes, exactly the plug-in's stored image there.
    assert image.data == _IMAGE[:9]
    assert all(image.owned)
    assert image.problems == ()


def test_a_changed_setting_moves_its_own_bytes_and_those_it_assigns() -> None:
    before = _image()
    after = _image(
        {"r_mode": "dm_B", "r_fb": "dm_No", "r_delay": "600", "r_offset": "-2"}
    )
    changed = [
        i
        for i, (a, b) in enumerate(zip(before.data, after.data, strict=True))
        if a != b
    ]
    # Feedback itself, and "Derived", which an Assign sets only while feedback is on.
    assert changed == [1, 5]
    delay = _image({**_VALUES, "r_delay": "1"}).data
    assert delay[2:4] == b"\x01\x00"  # little-endian (IntelOrderSwap)
    # With the mode Off the delay is not shown: it takes its default (300), whatever is stored.
    hidden = _image({**_VALUES, "r_mode": "dm_Off", "r_delay": "1"}).data
    assert hidden[2:4] == (300).to_bytes(2, "little")


def test_runs_split_at_the_frame_size_and_at_bytes_no_parameter_owns() -> None:
    image = MemoryImage(
        0x100, bytes(range(7)), (True, True, True, False, True, True, True), ()
    )
    assert image.runs(2) == [
        (0x100, b"\x00\x01"),
        (0x102, b"\x02"),
        (0x104, b"\x04\x05"),
        (0x106, b"\x06"),
    ]
    assert image.runs(10) == [(0x100, b"\x00\x01\x02"), (0x104, b"\x04\x05\x06")]


def test_a_value_that_cannot_be_encoded_is_reported() -> None:
    image = _image({**_VALUES, "r_delay": "not a number"})
    assert any("DemoDelay" in p for p in image.problems)

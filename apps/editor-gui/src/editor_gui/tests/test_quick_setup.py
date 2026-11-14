"""Quick Setup templates: the data files, which devices they fit, and applying one as one undo
step that is rolled back whole when a step is refused."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from editor_gui.quick_setup import (
    BUNDLED_DIR,
    QuickSetupError,
    TemplateLibrary,
    apply_template,
    check_assignments,
    describe,
    localized,
    parse_templates,
)

APP = "M-0001_A-2207-10-E8C8"


def _doc(**template: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "t1",
        "name": {"en": "Two lights", "de": "Zwei Lichter"},
        "parameters": [{"parameter": "P-1", "value": "2"}],
        "slots": [
            {"id": "a", "label": "Light A", "object": 0},
            {"id": "b", "label": "Light B", "object": 1, "required": False},
        ],
    }
    base.update(template)
    return {
        "format": 1,
        "device": {
            "name": "Switch",
            "manufacturer": "M-0001",
            "application_number": "2207",
            "application_versions": ["10"],
        },
        "templates": [base],
    }


def test_bundled_files_parse_and_up220_has_its_three_templates() -> None:
    library = TemplateLibrary([BUNDLED_DIR])
    library.reload()
    assert library.errors == []
    ids = {t.id for t in library.for_application(APP)}
    assert ids == {
        "latching-lights-blinds",
        "push-toggle-lights",
        "push-toggle-light-a",
        "push-blinds",
    }
    for template in library.templates:
        assert template.name("en") and template.description("en")
        numbers = [n for s in template.slots for n in s.objects]
        assert len(set(numbers)) == len(numbers)


def test_up220_latching_template_matches_the_proven_setup() -> None:
    """Input A: on when closed, off when opened; input B: up (off) when closed, down (on) when
    opened; both on every edge; inputs A and B only."""
    library = TemplateLibrary([BUNDLED_DIR])
    library.reload()
    template = library.find("latching-lights-blinds")
    assert template is not None
    steps = {s.parameter: s.value for s in template.parameters}
    assert steps == {
        "P-543": "0",
        "P-108": "0",
        "P-355": "2",
        "UP-363": "1",
        "UP-356": "0",
        "P-382": "2",
        "UP-391": "0",
        "UP-392": "1",
    }
    assert [(s.id, s.objects) for s in template.slots] == [
        ("lights", (0,)),
        ("blinds", (1,)),
    ]


@pytest.mark.parametrize(
    ("application_id", "fits"),
    [
        (APP, True),
        ("M-0001_A-2207-10-E8C8-O0001", True),
        ("m-0001_a-2207-10-e8c8", True),
        ("M-0001_A-2207-11-E8C8", False),
        ("M-0002_A-2207-10-E8C8", False),
        ("M-0001_A-2208-10-E8C8", False),
        ("garbage", False),
    ],
)
def test_a_template_fits_only_its_application_and_version(
    application_id: str, fits: bool
) -> None:
    (template,) = parse_templates(_doc())
    assert template.device.matches(application_id) is fits


def test_texts_fall_back_to_english() -> None:
    texts = {"en": "Light", "de": "Licht"}
    assert localized(texts, "de") == "Licht"
    assert localized(texts, "de-AT") == "Licht"
    assert localized(texts, "fr") == "Light"
    (template,) = parse_templates(_doc())
    assert template.name("de") == "Zwei Lichter"
    # No description given: the name stands in.
    assert template.description("en") == "Two lights"
    assert describe(template, "de")["slots"][1] == {
        "id": "b",
        "label": "Light B",
        "objects": [1],
        "sending": True,
        "required": False,
    }


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"name": {"de": "Nur deutsch"}}, "English"),
        ({"slots": []}, "at least one slot"),
        ({"slots": [{"id": "a", "label": "A", "object": True}]}, "'object'"),
        ({"parameters": [{"parameter": "P-1", "value": 2}]}, "'value'"),
        (
            {
                "slots": [
                    {"id": "a", "label": "A", "object": 0},
                    {"id": "a", "label": "B", "object": 1},
                ]
            },
            "duplicate slot",
        ),
    ],
)
def test_broken_templates_are_refused_with_the_reason(
    change: dict[str, Any], match: str
) -> None:
    with pytest.raises(QuickSetupError, match=match):
        parse_templates(_doc(**change), "x.json")


def test_unknown_format_and_duplicate_ids_are_refused() -> None:
    doc = _doc()
    doc["format"] = 2
    with pytest.raises(QuickSetupError, match="format"):
        parse_templates(doc)
    doc = _doc()
    doc["templates"].append(doc["templates"][0])
    with pytest.raises(QuickSetupError, match="duplicate id"):
        parse_templates(doc)


def test_user_files_replace_bundled_templates_and_bad_files_are_listed(
    tmp_path: Path,
) -> None:
    bundled, user = tmp_path / "bundled", tmp_path / "user"
    bundled.mkdir()
    user.mkdir()
    (bundled / "a.json").write_text(json.dumps(_doc()), encoding="utf-8")
    mine = _doc(name={"en": "Mine"})
    (user / "a.json").write_text(json.dumps(mine), encoding="utf-8")
    (user / "broken.json").write_text("{", encoding="utf-8")
    library = TemplateLibrary([bundled, user, tmp_path / "missing"])
    library.reload()
    assert [t.name("en") for t in library.templates] == ["Mine"]
    assert len(library.errors) == 1 and library.errors[0].startswith("broken.json")
    assert library.find("t1") is library.find("M-0001_A-2207:t1")
    assert library.for_application(None) == []


def test_the_library_reads_again_only_when_a_file_changed(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_text(json.dumps(_doc()), encoding="utf-8")
    library = TemplateLibrary([tmp_path])
    library.reload()
    first = library.templates
    library.reload()
    assert library.templates is first
    library.reload(force=True)
    assert library.templates is not first


def test_required_slots_need_a_group_address() -> None:
    (template,) = parse_templates(_doc())
    assert check_assignments(template, {"a": 5}) == []
    assert check_assignments(template, {"b": 5}) == ["no group address for 'a'"]
    assert check_assignments(template, {"a": 5, "x": 1}) == ["unknown slot 'x'"]


# --- applying, against a stand-in of the ProjectService -------------------------------------------


@dataclass
class _Param:
    ref_id: str
    label: str = ""
    value: str = ""
    widget: Any = None


@dataclass
class _Program:
    id: str


@dataclass
class _App:
    program: _Program


@dataclass
class _ComObject:
    number: int
    db_id: int | None


@dataclass
class _Link:
    id: int
    group_address_id: int


@dataclass
class _Device:
    node_id: int
    values: dict[str, str]
    app: _App = field(default_factory=lambda: _App(_Program(APP)))
    com_objects: list[_ComObject] = field(
        default_factory=lambda: [_ComObject(0, 100), _ComObject(1, 101)]
    )
    plugin_type: str | None = None

    def get_ui(self) -> list[Any]:
        from xknxeditor.prod.parser_v2.ui import UiParameter

        return [
            UiParameter(ref_id=ref, label="", value=value, widget=None)
            for ref, value in self.values.items()
        ]

    def get_param_value(self, ref_id: str) -> str | None:
        return self.values.get(ref_id)


class _Project:
    """Records edits; every edit is one history entry, one_undo_step groups them."""

    def __init__(self, device: _Device, refuse_link: bool = False) -> None:
        self.device = device
        self.history: list[tuple[str, Any]] = []
        self.groups: list[str | None] = []
        self.links: dict[int, list[_Link]] = {100: [_Link(1, 7)], 101: []}
        self.refuse_link = refuse_link
        self.rejected: set[str] = set()

    @property
    def cursor(self) -> int:
        return len(self.history)

    @contextlib.contextmanager
    def one_undo_step(self, label: str | None = None) -> Iterator[None]:
        yield
        self.groups.append(label)

    def undo(self) -> bool:
        if not self.history:
            return False
        kind, data = self.history.pop()
        if kind == "param":
            ref, old = data
            self.device.values[ref] = old
        elif kind == "link":
            co, ga = data
            self.links[co] = [lk for lk in self.links[co] if lk.group_address_id != ga]
        elif kind == "unlink":
            co, link = data
            self.links[co].append(link)
        return True

    def find_device_by_node_id(self, node_id: int) -> _Device | None:
        return self.device if node_id == self.device.node_id else None

    def get_group_address(self, ga_id: int) -> object | None:
        return object() if ga_id < 50 else None

    def edit_params(
        self, device: _Device, edits: list[tuple[str, str]], **_kw: Any
    ) -> dict[str, Any]:
        for ref, value in edits:
            if value in self.rejected:
                continue  # the device keeps its value, as a rejected edit does
            self.history.append(("param", (ref, device.values[ref])))
            device.values[ref] = value
        return {}

    def get_links_for_com_object(self, com_object_db_id: int) -> list[_Link]:
        return list(self.links.get(com_object_db_id, []))

    def unlink_com_object_from_ga(
        self,
        assignment_id: int,
        com_object_id: int = 0,
        group_address_id: int = 0,
        is_sending: bool = False,
    ) -> None:
        link = next(lk for lk in self.links[com_object_id] if lk.id == assignment_id)
        self.links[com_object_id].remove(link)
        self.history.append(("unlink", (com_object_id, link)))

    def link_com_object_to_ga(
        self, com_object_id: int, group_address_id: int, is_sending: bool = False
    ) -> int | None:
        if self.refuse_link:
            return None
        self.links[com_object_id].append(_Link(99, group_address_id))
        self.history.append(("link", (com_object_id, group_address_id)))
        return 99


@pytest.fixture(autouse=True)
def _no_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "editor_gui.device_plugins.parameters_in_plugin",
        lambda device: device.plugin_type == "download",
    )


def _project(**kw: Any) -> _Project:
    device = _Device(node_id=1, values={f"{APP}_P-1_R-5": "0"})
    return _Project(device, **kw)


def test_apply_sets_parameters_and_links_as_one_undo_step() -> None:
    (template,) = parse_templates(_doc())
    project = _project()
    result = apply_template(project, 1, template, {"a": 3, "b": None}, label="QS")
    assert project.device.values[f"{APP}_P-1_R-5"] == "2"
    assert result.parameters == ((f"{APP}_P-1_R-5", "2"),)
    assert [(lk.slot, lk.group_address_id) for lk in result.links] == [("a", 3)]
    # The object's old link to group address 7 was replaced.
    assert result.removed_links == 1
    assert [lk.group_address_id for lk in project.links[100]] == [3]
    assert project.groups == ["QS"]


def test_apply_can_keep_the_other_links() -> None:
    (template,) = parse_templates(_doc())
    project = _project()
    result = apply_template(project, 1, template, {"a": 3}, replace_links=False)
    assert result.removed_links == 0
    assert sorted(lk.group_address_id for lk in project.links[100]) == [3, 7]


def test_a_refused_link_rolls_everything_back() -> None:
    (template,) = parse_templates(_doc())
    project = _project(refuse_link=True)
    with pytest.raises(QuickSetupError, match="link refused"):
        apply_template(project, 1, template, {"a": 3})
    assert project.device.values[f"{APP}_P-1_R-5"] == "0"
    assert [lk.group_address_id for lk in project.links[100]] == [7]
    assert project.cursor == 0


def test_a_value_the_device_does_not_take_is_refused() -> None:
    (template,) = parse_templates(_doc())
    project = _project()
    project.rejected = {"2"}
    with pytest.raises(QuickSetupError, match="not accepted"):
        apply_template(project, 1, template, {"a": 3})
    assert project.cursor == 0


@pytest.mark.parametrize(
    ("values", "match"),
    [
        ({}, "not shown"),
        ({f"{APP}_P-1_R-5": "0", f"{APP}_P-1_R-6": "0"}, "more than once"),
    ],
)
def test_a_parameter_must_be_shown_exactly_once(
    values: dict[str, str], match: str
) -> None:
    (template,) = parse_templates(_doc())
    project = _Project(_Device(node_id=1, values=values))
    with pytest.raises(QuickSetupError, match=match):
        apply_template(project, 1, template, {"a": 3})


def test_nothing_is_changed_for_the_wrong_device_or_missing_addresses() -> None:
    (template,) = parse_templates(_doc())
    project = _project()
    with pytest.raises(QuickSetupError, match="no device"):
        apply_template(project, 2, template, {"a": 3})
    with pytest.raises(QuickSetupError, match="no group address for 'a'"):
        apply_template(project, 1, template, {})
    with pytest.raises(QuickSetupError, match="no group address with id 60"):
        apply_template(project, 1, template, {"a": 60})
    project.device.app = _App(_Program("M-0001_A-2207-11-E8C8"))
    with pytest.raises(QuickSetupError, match="not for this device"):
        apply_template(project, 1, template, {"a": 3})
    assert project.cursor == 0


def test_a_missing_group_object_is_refused() -> None:
    (template,) = parse_templates(_doc())
    project = _project()
    project.device.com_objects = [_ComObject(1, 101)]
    with pytest.raises(QuickSetupError, match="group object 0"):
        apply_template(project, 1, template, {"a": 3})
    assert project.cursor == 0


def test_a_device_set_in_its_plug_in_is_refused() -> None:
    (template,) = parse_templates(_doc())
    project = _project()
    project.device.plugin_type = "download"
    with pytest.raises(QuickSetupError, match="plug-in"):
        apply_template(project, 1, template, {"a": 3})


def test_crabtree_7172_templates_fit_its_application() -> None:
    library = TemplateLibrary([BUNDLED_DIR])
    library.reload()
    ids = {t.id for t in library.for_application("M-0021_A-9084-02-0F47-O0001")}
    assert ids == {"rocker-toggle-lights", "rocker-on-off", "rocker-blinds"}
    on_off = library.find("rocker-on-off")
    assert on_off is not None and on_off.slots[0].objects == (0, 1)


def test_a_slot_can_link_several_objects_to_one_group_address() -> None:
    (template,) = parse_templates(
        _doc(slots=[{"id": "a", "label": "Light", "objects": [0, 1]}])
    )
    project = _project()
    result = apply_template(project, 1, template, {"a": 3})
    assert [(lk.object, lk.group_address_id) for lk in result.links] == [(0, 3), (1, 3)]
    assert [lk.group_address_id for lk in project.links[101]] == [3]


@pytest.mark.parametrize("objects", [[], [0, 0], ["0"], True])
def test_bad_object_lists_are_refused(objects: Any) -> None:
    with pytest.raises(QuickSetupError, match="objects"):
        parse_templates(_doc(slots=[{"id": "a", "label": "A", "objects": objects}]))


def test_up220_input_a_only_template_leaves_input_b_alone() -> None:
    library = TemplateLibrary([BUNDLED_DIR])
    library.reload()
    template = library.find("push-toggle-light-a")
    assert template is not None
    touched = {s.parameter for s in template.parameters}
    # Input B's own parameters (P-382, UP-391/392/395) are not touched.
    assert not touched & {"P-382", "UP-391", "UP-392", "UP-395"}
    assert [s.objects for s in template.slots] == [(0,)]


def test_crabtree_7176_templates_each_touch_only_their_rocker() -> None:
    library = TemplateLibrary([BUNDLED_DIR])
    library.reload()
    templates = library.for_application("M-0021_A-2302-01-E518-O0001")
    assert {t.id for t in templates} == {
        "upper-rocker-on-off",
        "middle-rocker-on-off",
        "middle-rocker-blinds",
        "lower-rocker-blinds",
    }
    upper = {"P-108", "P-36", "P-112"}
    lower = {"P-287", "P-288"}
    found = {t.id: {s.parameter for s in t.parameters} for t in templates}
    assert found["upper-rocker-on-off"] == upper
    assert found["lower-rocker-blinds"] == lower

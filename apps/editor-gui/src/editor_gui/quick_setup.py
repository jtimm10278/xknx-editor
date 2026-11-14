"""Quick Setup: ready-made set-ups for common wall-switch patterns.

A template sets a device's parameters and links its group objects to group addresses the user
picks, as one undo step. Templates are data: JSON files in ``quick_setup_templates/`` next to this
module (bundled) and in ``quick_setup/`` under the user's config folder (their own). One file per
application, for example::

    {
      "format": 1,
      "device": {
        "name": "Siemens UP 220/03",
        "manufacturer": "M-0001",
        "application_number": "2207",
        "application_versions": ["10"]
      },
      "templates": [
        {
          "id": "push-toggle-lights",
          "name": {"en": "Push-buttons: toggle lights", "de": "Taster: Licht umschalten"},
          "description": {"en": "Each press toggles the light."},
          "parameters": [{"parameter": "P-108", "value": "0"}],
          "slots": [
            {"id": "light_a", "label": {"en": "Light (input A)"}, "object": 0,
             "sending": true, "required": true}
          ]
        }
      ]
    }

``parameter`` is a parameter id (the ParameterRef id without its ``_R-…``): it is resolved, at its
turn, to the ref the device shows it with, so an earlier step that changes a mode can show the
next parameter under another ref. ``value`` is the raw value (an enumeration's value, not its
text). ``object`` is the group object number, looked up after all parameters are set. ``name``,
``description`` and ``label`` give a text per UI language code; English (``en``) is required and
used when the language has none.

To add a device, add a file: no code changes are needed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

    from editor_gui.device import Device

FORMAT = 1
BUNDLED_DIR = Path(__file__).parent / "quick_setup_templates"
USER_DIR_NAME = "quick_setup"

_APPLICATION_ID = re.compile(r"^(M-[0-9A-F]{4})_A-([0-9A-F]{4})-([0-9A-F]{2})", re.I)


class QuickSetupError(Exception):
    """A template that cannot be applied, or a template file that cannot be read."""


def localized(texts: Mapping[str, str], language: str) -> str:
    """The text for ``language`` (e.g. "de" or "de-DE"), else the English one."""
    if language in texts:
        return texts[language]
    base = language.split("-")[0].split("_")[0]
    return texts.get(base) or texts.get("en", "")


@dataclass(frozen=True)
class ParameterStep:
    parameter: str  # a parameter id, e.g. "P-108" or "UP-356"
    value: str


@dataclass(frozen=True)
class Slot:
    id: str
    labels: Mapping[str, str]
    object: int  # group object number
    sending: bool = True
    required: bool = True

    def label(self, language: str) -> str:
        return localized(self.labels, language)


@dataclass(frozen=True)
class DeviceMatch:
    name: str
    manufacturer: str  # "M-0001"
    application_number: str  # "2207" (hexadecimal, as in the application id)
    application_versions: tuple[str, ...]  # ("10",) (hexadecimal)

    def matches(self, application_id: str) -> bool:
        m = _APPLICATION_ID.match(application_id)
        if m is None:
            return False
        manufacturer, number, version = (part.upper() for part in m.groups())
        return (
            manufacturer == self.manufacturer.upper()
            and number == self.application_number.upper()
            and version in {v.upper() for v in self.application_versions}
        )


@dataclass(frozen=True)
class Template:
    id: str
    device: DeviceMatch
    names: Mapping[str, str]
    descriptions: Mapping[str, str]
    parameters: tuple[ParameterStep, ...]
    slots: tuple[Slot, ...]
    source: str = ""  # the file it came from

    @property
    def key(self) -> str:
        """Unique across files: the application and the template id."""
        d = self.device
        return f"{d.manufacturer}_A-{d.application_number}:{self.id}"

    def name(self, language: str) -> str:
        return localized(self.names, language)

    def description(self, language: str) -> str:
        return localized(self.descriptions, language)


def _texts(value: Any, where: str) -> dict[str, str]:
    if isinstance(value, str):
        return {"en": value}
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in value.items()  # pyright: ignore[reportUnknownVariableType]
    ):
        raise QuickSetupError(
            f"{where}: expected a text or a {{language: text}} object"
        )
    texts: dict[str, str] = dict(value)  # pyright: ignore[reportUnknownArgumentType]
    if not texts.get("en"):
        raise QuickSetupError(f"{where}: an English (en) text is required")
    return texts


def _require(data: Mapping[str, Any], key: str, kind: type, where: str) -> Any:
    value = data.get(key)
    if not isinstance(value, kind) or isinstance(value, bool) is not (kind is bool):
        raise QuickSetupError(f"{where}: {key!r} missing or not a {kind.__name__}")
    return value


def parse_templates(data: Any, source: str = "") -> list[Template]:
    """The templates of one file's JSON data; raises QuickSetupError naming what is wrong."""
    if not isinstance(data, dict):
        raise QuickSetupError(f"{source}: expected a JSON object")
    doc: dict[str, Any] = data  # pyright: ignore[reportUnknownVariableType]
    if doc.get("format") != FORMAT:
        raise QuickSetupError(f"{source}: unsupported format {doc.get('format')!r}")
    dev = _require(doc, "device", dict, source)
    versions = _require(dev, "application_versions", list, f"{source}: device")
    if not versions or not all(isinstance(v, str) for v in versions):
        raise QuickSetupError(f"{source}: device: application_versions must be texts")
    device = DeviceMatch(
        name=_require(dev, "name", str, f"{source}: device"),
        manufacturer=_require(dev, "manufacturer", str, f"{source}: device"),
        application_number=_require(
            dev, "application_number", str, f"{source}: device"
        ),
        application_versions=tuple(versions),
    )
    templates: list[Template] = []
    seen: set[str] = set()
    for index, raw in enumerate(_require(doc, "templates", list, source)):
        where = f"{source}: templates[{index}]"
        if not isinstance(raw, dict):
            raise QuickSetupError(f"{where}: expected an object")
        t: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
        template_id = _require(t, "id", str, where)
        if template_id in seen:
            raise QuickSetupError(f"{where}: duplicate id {template_id!r}")
        seen.add(template_id)
        steps: list[ParameterStep] = []
        for s_index, step in enumerate(_require(t, "parameters", list, where)):
            s_where = f"{where}.parameters[{s_index}]"
            if not isinstance(step, dict):
                raise QuickSetupError(f"{s_where}: expected an object")
            steps.append(
                ParameterStep(
                    parameter=_require(step, "parameter", str, s_where),  # pyright: ignore[reportUnknownArgumentType]
                    value=_require(step, "value", str, s_where),  # pyright: ignore[reportUnknownArgumentType]
                )
            )
        slots: list[Slot] = []
        slot_ids: set[str] = set()
        for s_index, slot in enumerate(_require(t, "slots", list, where)):
            s_where = f"{where}.slots[{s_index}]"
            if not isinstance(slot, dict):
                raise QuickSetupError(f"{s_where}: expected an object")
            s: dict[str, Any] = slot  # pyright: ignore[reportUnknownVariableType]
            slot_id = _require(s, "id", str, s_where)
            if slot_id in slot_ids:
                raise QuickSetupError(f"{s_where}: duplicate slot id {slot_id!r}")
            slot_ids.add(slot_id)
            slots.append(
                Slot(
                    id=slot_id,
                    labels=_texts(s.get("label"), f"{s_where}.label"),
                    object=_require(s, "object", int, s_where),
                    sending=bool(s.get("sending", True)),
                    required=bool(s.get("required", True)),
                )
            )
        if not slots:
            raise QuickSetupError(f"{where}: a template needs at least one slot")
        templates.append(
            Template(
                id=template_id,
                device=device,
                names=_texts(t.get("name"), f"{where}.name"),
                descriptions=_texts(
                    t.get("description", t.get("name")), f"{where}.description"
                ),
                parameters=tuple(steps),
                slots=tuple(slots),
                source=source,
            )
        )
    return templates


@dataclass
class TemplateLibrary:
    """The templates of the bundled folder and the user's folder (a user file with a template of
    the same application and id replaces the bundled one); files that cannot be read are listed
    in ``errors`` and left out."""

    folders: Sequence[Path]
    templates: list[Template] = field(default_factory=lambda: list[Template]())
    errors: list[str] = field(default_factory=lambda: list[str]())
    _stamp: tuple[tuple[str, float], ...] | None = None

    def _files(self) -> list[Path]:
        files: list[Path] = []
        for folder in self.folders:
            if folder.is_dir():
                files.extend(sorted(folder.glob("*.json")))
        return files

    def reload(self, *, force: bool = False) -> None:
        files = self._files()
        stamp = tuple((str(f), f.stat().st_mtime) for f in files)
        if not force and stamp == self._stamp:
            return
        by_key: dict[str, Template] = {}
        errors: list[str] = []
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for template in parse_templates(data, path.name):
                    by_key[template.key] = template
            except (OSError, ValueError, QuickSetupError) as exc:
                errors.append(
                    str(exc)
                    if isinstance(exc, QuickSetupError)
                    else f"{path.name}: {exc}"
                )
        self.templates = list(by_key.values())
        self.errors = errors
        self._stamp = stamp

    def for_application(self, application_id: str | None) -> list[Template]:
        if not application_id:
            return []
        return [t for t in self.templates if t.device.matches(application_id)]

    def for_device(self, device: Device) -> list[Template]:
        return self.for_application(application_id(device))

    def find(self, key: str) -> Template | None:
        return next((t for t in self.templates if t.key == key or t.id == key), None)


def application_id(device: Device) -> str | None:
    return device.app.program.id if device.app is not None else None


_library: TemplateLibrary | None = None


def library() -> TemplateLibrary:
    """The app's library (bundled + the user's folder), re-read when a file changed."""
    global _library
    if _library is None:
        from editor_gui.settings import config_dir

        _library = TemplateLibrary([BUNDLED_DIR, config_dir() / USER_DIR_NAME])
    _library.reload()
    return _library


class _Project(Protocol):
    """What applying a template needs of the ProjectService (the methods the GUI uses)."""

    @property
    def cursor(self) -> int: ...

    def one_undo_step(
        self, label: str | None = None
    ) -> AbstractContextManager[None]: ...

    def undo(self) -> bool: ...

    def find_device_by_node_id(self, node_id: int) -> Device | None: ...

    def get_group_address(self, ga_id: int) -> Any: ...

    def edit_params(
        self,
        device: Device,
        edits: list[tuple[str, str]],
        *,
        mode: Any = "edit",
        label: str | None = None,
        skip_invalid: bool = False,
    ) -> Any: ...

    def get_links_for_com_object(self, com_object_db_id: int) -> list[Any]: ...

    def unlink_com_object_from_ga(
        self,
        assignment_id: int,
        com_object_id: int = 0,
        group_address_id: int = 0,
        is_sending: bool = False,
    ) -> None: ...

    def link_com_object_to_ga(
        self, com_object_id: int, group_address_id: int, is_sending: bool = False
    ) -> int | None: ...


@dataclass(frozen=True)
class AppliedLink:
    slot: str
    object: int
    group_address_id: int
    sending: bool


@dataclass(frozen=True)
class QuickSetupResult:
    node_id: int
    template: str  # key
    parameters: tuple[tuple[str, str], ...]  # (ref id, value) as set
    links: tuple[AppliedLink, ...]
    removed_links: int


def resolve_ref(device: Device, application: str, parameter: str) -> str:
    """The ref the device shows ``parameter`` with now; QuickSetupError when it shows none or
    several."""
    from editor_gui.device import collect_ui_parameters

    pattern = re.compile(rf"_{re.escape(parameter)}_R-[^_]+$")
    shown = sorted(
        {
            p.ref_id
            for p in collect_ui_parameters(device.get_ui())
            if p.ref_id.startswith(application) and pattern.search(p.ref_id)
        }
    )
    if len(shown) == 1:
        return shown[0]
    if shown:
        raise QuickSetupError(
            f"parameter {parameter} is shown more than once ({', '.join(shown)})"
        )
    raise QuickSetupError(f"parameter {parameter} is not shown on this device")


def check_assignments(
    template: Template, assignments: Mapping[str, int | None]
) -> list[str]:
    """What is wrong with the chosen group addresses (slot ids), empty when nothing is."""
    problems: list[str] = []
    known = {s.id for s in template.slots}
    problems.extend(f"unknown slot {k!r}" for k in assignments if k not in known)
    problems.extend(
        f"no group address for {s.id!r}"
        for s in template.slots
        if s.required and assignments.get(s.id) is None
    )
    return problems


def apply_template(
    project: _Project,
    node_id: int,
    template: Template,
    assignments: Mapping[str, int | None],
    *,
    replace_links: bool = True,
    label: str | None = None,
) -> QuickSetupResult:
    """Set the template's parameters on the device and link its slots' objects to the chosen group
    addresses (slot id -> group address id; an optional slot may be None), as ONE undo step.
    With ``replace_links`` the objects' other links are removed first. Nothing reaches the bus.
    Raises QuickSetupError (and leaves the project as it was) when any step is refused."""
    from editor_gui.device_plugins import parameters_in_plugin

    device = project.find_device_by_node_id(node_id)
    if device is None:
        raise QuickSetupError(f"no device with node_id {node_id}")
    application = application_id(device)
    if application is None or not template.device.matches(application):
        raise QuickSetupError(
            f"template {template.id!r} is not for this device's application"
        )
    if parameters_in_plugin(device):
        raise QuickSetupError("this device's parameters are set in its plug-in")
    problems = check_assignments(template, assignments)
    for slot_id, ga_id in assignments.items():
        if ga_id is not None and project.get_group_address(ga_id) is None:
            problems.append(f"no group address with id {ga_id} (for {slot_id!r})")
    if problems:
        raise QuickSetupError("; ".join(problems))

    start = project.cursor
    set_params: list[tuple[str, str]] = []
    links: list[AppliedLink] = []
    removed = 0
    try:
        with project.one_undo_step(label or template.name("en")):
            for step in template.parameters:
                device = _device(project, node_id)
                ref = resolve_ref(device, application, step.parameter)
                project.edit_params(device, [(ref, step.value)], mode="edit")
                device = _device(project, node_id)
                if device.get_param_value(ref) != step.value:
                    raise QuickSetupError(
                        f"parameter {step.parameter}: value {step.value!r} not accepted"
                    )
                set_params.append((ref, step.value))
            device = _device(project, node_id)
            objects = {co.number: co for co in device.com_objects}
            for slot in template.slots:
                ga_id = assignments.get(slot.id)
                if ga_id is None:
                    continue
                co = objects.get(slot.object)
                if co is None or co.db_id is None:
                    raise QuickSetupError(
                        f"group object {slot.object} ({slot.id}) is not shown"
                    )
                if replace_links:
                    for link in project.get_links_for_com_object(co.db_id):
                        if link.group_address_id != ga_id:
                            project.unlink_com_object_from_ga(
                                link.id, co.db_id, link.group_address_id
                            )
                            removed += 1
                if (
                    project.link_com_object_to_ga(
                        co.db_id, ga_id, is_sending=slot.sending
                    )
                    is None
                ):
                    raise QuickSetupError(
                        f"group object {slot.object} ({slot.id}): link refused"
                    )
                links.append(AppliedLink(slot.id, slot.object, ga_id, slot.sending))
    except QuickSetupError:
        _rewind(project, start)
        raise
    except Exception as exc:
        _rewind(project, start)
        raise QuickSetupError(str(exc)) from exc
    return QuickSetupResult(
        node_id=node_id,
        template=template.key,
        parameters=tuple(set_params),
        links=tuple(links),
        removed_links=removed,
    )


def _device(project: _Project, node_id: int) -> Device:
    device = project.find_device_by_node_id(node_id)
    if device is None:
        raise QuickSetupError(f"device {node_id} disappeared")
    return device


def _rewind(project: _Project, start: int) -> None:
    while project.cursor > start and project.undo():
        pass


def describe(template: Template, language: str) -> dict[str, Any]:
    """The template as MCP and the GUI show it."""
    return {
        "key": template.key,
        "id": template.id,
        "device": template.device.name,
        "name": template.name(language),
        "description": template.description(language),
        "parameters": [
            {"parameter": s.parameter, "value": s.value} for s in template.parameters
        ],
        "slots": [
            {
                "id": s.id,
                "label": s.label(language),
                "object": s.object,
                "sending": s.sending,
                "required": s.required,
            }
            for s in template.slots
        ],
        "source": template.source,
    }


def iter_templates(templates: Iterable[Template]) -> Iterator[Template]:
    yield from sorted(templates, key=lambda t: (t.device.name, t.id))

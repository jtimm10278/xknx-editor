"""Changing a Generic Plug-in setting: which values a setting takes, and the BinaryData with one
value changed.

Only the plain-XML description and the stored settings are used (the plug-in itself is never
run). A change touches nothing but the setting's ``<Value>`` in ``CurrentSettings`` (or adds its
``ParameterInstanceRef`` when the plug-in stored none) and the ``LastModified`` time; the rest of
the document, the last download's ``DownloadedSettings`` included, stays byte for byte as it was,
in the encoding it had.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from .blob import decode_text
from .codec import encode
from .description import Description, ParameterType, TypeKind

_INTEGER_KINDS = (TypeKind.NUMBER, TypeKind.TIME_S, TypeKind.TIME_MS)


@dataclass(frozen=True, slots=True)
class Choice:
    value: str  # the symbolic value stored, e.g. "yn_Yes"
    text: str  # what it means, in the product language


@dataclass(frozen=True, slots=True)
class EditSpec:
    """What a setting may be set to: one of ``choices`` (an enumeration), else a whole number
    from ``minimum`` to ``maximum``."""

    choices: tuple[Choice, ...] = ()
    minimum: int = 0
    maximum: int = 0
    unit: str = ""  # "s" or "ms" for times


def _type_of(description: Description, ref_id: str) -> ParameterType | None:
    ref = description.refs.get(ref_id)
    if ref is None or ref.global_variable:
        return None
    param = description.parameters.get(ref.parameter_id)
    return description.types.get(param.type_id) if param is not None else None


def _range(ptype: ParameterType) -> tuple[int, int] | None:
    if ptype.size_bits <= 0 or ptype.size_bits % 8 or ptype.size_bits > 32:
        return None
    if ptype.signed:
        low, high = -(1 << (ptype.size_bits - 1)), (1 << (ptype.size_bits - 1)) - 1
    else:
        low, high = 0, (1 << ptype.size_bits) - 1
    if ptype.minimum is not None:
        low = max(low, ptype.minimum)
    if ptype.maximum is not None:
        high = min(high, ptype.maximum)
    return (low, high) if low <= high else None


def edit_spec(
    description: Description, ref_id: str, texts: dict[str, str] | None = None
) -> EditSpec | None:
    """How the setting of parameter ref ``ref_id`` can be changed; None when it can't (a text,
    a value taken from the device, or a type that does not fit whole bytes). ``texts`` are the
    translated enumeration texts by id."""
    ptype = _type_of(description, ref_id)
    if ptype is None:
        return None
    if ptype.kind is TypeKind.ENUM:
        choices = tuple(
            Choice(e.name, (texts or {}).get(e.id) or e.text or e.name)
            for e in ptype.enumerations
            if encode(e.name, ptype) is not None
        )
        return EditSpec(choices=choices) if choices else None
    if ptype.kind in _INTEGER_KINDS:
        limits = _range(ptype)
        if limits is None:
            return None
        unit = {TypeKind.TIME_S: "s", TypeKind.TIME_MS: "ms"}.get(ptype.kind, "")
        return EditSpec(minimum=limits[0], maximum=limits[1], unit=unit)
    return None


def check_value(description: Description, ref_id: str, value: str) -> str:
    """ "" when ``ref_id`` may be set to ``value``, else why not."""
    spec = edit_spec(description, ref_id)
    if spec is None:
        return f"{ref_id} cannot be changed"
    if spec.choices:
        if any(c.value == value for c in spec.choices):
            return ""
        names = ", ".join(c.value for c in spec.choices)
        return f"{value!r} is not one of {names}"
    try:
        number = int(value)
    except ValueError:
        return f"{value!r} is not a whole number"
    if not spec.minimum <= number <= spec.maximum:
        return f"{number} is outside {spec.minimum}..{spec.maximum}"
    return ""


def _encoding(data: bytes) -> tuple[str, bytes]:
    """The codec ``data`` is written in and the byte order mark it starts with."""
    if data.startswith(b"\xff\xfe"):
        return "utf-16-le", b"\xff\xfe"
    if data.startswith(b"\xfe\xff"):
        return "utf-16-be", b"\xfe\xff"
    if len(data) >= 2 and data[1] == 0 and data[0] != 0:
        return "utf-16-le", b""
    if len(data) >= 2 and data[0] == 0 and data[1] != 0:
        return "utf-16-be", b""
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8", b"\xef\xbb\xbf"
    return "utf-8", b""


class EditError(ValueError):
    """The settings document cannot be changed (it is not the plug-in's)."""


def set_value(
    blob: bytes,
    ref_id: str,
    value: str,
    modified: datetime.datetime | None = None,
) -> bytes:
    """``blob`` (a device's plug-in BinaryData) with the setting ``ref_id`` set to ``value`` and
    ``LastModified`` set to ``modified`` (now, local time, when None)."""
    if decode_text(blob) is None:
        raise EditError("not a settings document")
    codec, bom = _encoding(blob)
    text = blob[len(bom) :].decode(codec)
    current = re.search(r"<CurrentSettings\b[^>]*>.*?</CurrentSettings>", text, re.S)
    if current is None:
        raise EditError("no CurrentSettings")
    part = current.group(0)
    ref = re.escape(quoteattr(ref_id)[1:-1])
    existing = re.search(
        rf'<ParameterInstanceRef\b[^>]*\bRefId="{ref}"[^>]*?(/>|>(.*?)</ParameterInstanceRef>)',
        part,
        re.S,
    )
    entry = (
        f"<ParameterInstanceRef Id={quoteattr('idInst_' + ref_id)} "
        f"RefId={quoteattr(ref_id)}><Value>{escape(value)}</Value></ParameterInstanceRef>"
    )
    if existing is not None:
        inner = existing.group(2)
        if inner is not None and re.search(r"<Value>.*?</Value>", inner, re.S):
            new_inner = re.sub(
                r"<Value>.*?</Value>",
                lambda _m: f"<Value>{escape(value)}</Value>",
                inner,
                count=1,
                flags=re.S,
            )
            replaced = (
                existing.group(0)[: existing.start(2) - existing.start(0)]
                + new_inner
                + existing.group(0)[existing.end(2) - existing.start(0) :]
            )
        else:
            replaced = entry
        part = part[: existing.start()] + replaced + part[existing.end() :]
    elif "</ParameterInstanceRefs>" in part:
        at = part.index("</ParameterInstanceRefs>")
        part = part[:at] + entry + part[at:]
    elif "<ParameterInstanceRefs />" in part or "<ParameterInstanceRefs/>" in part:
        part = re.sub(
            r"<ParameterInstanceRefs\s*/>",
            lambda _m: f"<ParameterInstanceRefs>{entry}</ParameterInstanceRefs>",
            part,
            count=1,
        )
    else:
        at = part.index("</CurrentSettings>")
        part = (
            part[:at]
            + f"<ParameterInstanceRefs>{entry}</ParameterInstanceRefs>"
            + part[at:]
        )
    stamp = (modified or datetime.datetime.now()).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    head = re.match(r"<CurrentSettings\b[^>]*>", part)
    assert head is not None
    opening = head.group(0)
    if re.search(r'\bLastModified="[^"]*"', opening):
        opening = re.sub(
            r'\bLastModified="[^"]*"',
            lambda _m: f'LastModified="{stamp}"',
            opening,
            count=1,
        )
    part = opening + part[head.end() :]
    text = text[: current.start()] + part + text[current.end() :]
    return bom + text.encode(codec)

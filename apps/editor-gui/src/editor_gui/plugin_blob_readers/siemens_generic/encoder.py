"""The parameter memory a Generic Plug-in device's settings stand for, from its plain-XML
description alone (the plug-in itself is never run or opened).

Rule (checked against ETS's stored images of two units, byte for byte): a parameter shown in the
Dynamic tree for these settings takes its value there (stored, assigned or its default); every
other parameter takes its own default from the description. Bytes no parameter owns are left
out (``owned`` is false there): a download does not write them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .blob import PluginSettings
from .codec import byte_size, encode
from .description import Description
from .view import _Builder  # pyright: ignore[reportPrivateUsage]


@dataclass(frozen=True)
class MemoryImage:
    """The parameter memory from ``base`` on: its bytes and which of them a parameter owns."""

    base: int
    data: bytes
    owned: tuple[bool, ...]
    # Parameters that could not be placed (e.g. not a whole-byte type, or an overlap).
    problems: tuple[str, ...]

    def runs(self, max_length: int) -> list[tuple[int, bytes]]:
        """The owned bytes as (address, data) runs of at most ``max_length`` bytes each; a byte
        no parameter owns ends a run."""
        runs: list[tuple[int, bytes]] = []
        start: int | None = None
        for i in range(len(self.owned) + 1):
            owned = i < len(self.owned) and self.owned[i]
            if start is not None and (not owned or i - start == max_length):
                runs.append((self.base + start, self.data[start:i]))
                start = None
            if owned and start is None:
                start = i
        return runs


def memory_image(
    description: Description,
    settings: PluginSettings,
    globals_: dict[str, str] | None = None,
) -> MemoryImage | None:
    """The parameter memory of ``settings``; None when the description places no parameter."""
    base = description.memory_base
    if base is None:
        return None
    placed = [
        (p, description.types.get(p.type_id))
        for p in description.parameters.values()
        if p.offset is not None
    ]
    end = max((p.offset or 0) + (byte_size(t) or 1) for p, t in placed if t is not None)
    data = bytearray(end - base)
    owner: list[str | None] = [None] * (end - base)
    problems: list[str] = []
    builder = _Builder(description, settings, {}, globals_ or {})
    walk = builder.run()
    for parameter, ptype in placed:
        assert parameter.offset is not None
        size = byte_size(ptype) if ptype is not None else None
        if ptype is None or size is None:
            problems.append(f"{parameter.name}: not a whole-byte type")
            continue
        ref = walk.active_by_parameter.get(parameter.id)
        value = builder.value(ref, walk) if ref else parameter.default
        encoded = encode(value, ptype)
        if encoded is None:
            problems.append(f"{parameter.name}: value {value!r} cannot be encoded")
            continue
        pos = parameter.offset - base
        for i, byte in enumerate(encoded):
            if owner[pos + i] is not None:
                problems.append(
                    f"{parameter.name} overlaps {owner[pos + i]} at {parameter.offset + i:#06x}"
                )
            data[pos + i] = byte
            owner[pos + i] = parameter.name
    return MemoryImage(
        base, bytes(data), tuple(o is not None for o in owner), tuple(problems)
    )

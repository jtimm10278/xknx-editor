"""Field terms in a monitor's filter box: ``ga:1/0/`` ``src:1.1.5`` ``room:Kitchen``.

A filter without any field term is matched as before: the whole text anywhere in the row. With
at least one, the text is split into terms (double quotes keep spaces together) and a row has to
match every term:

- ``ga:`` the group (destination) address, ``src:`` the source address: an address ending in
  ``/`` or ``.`` (or ``*``) matches every address starting with it, any other only itself.
- ``to:`` the destination's name, ``dev:`` the source device's name, ``room:`` the building part,
  ``fn:`` the building function, ``svc:`` the service, ``dpt:`` the datapoint type: contained, in
  any case.
- A term without a field: anywhere in the row, like the plain filter.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence

from editor_gui.plugins.monitor.formatting import COLUMN_INDEX

# Field -> (column, whether it is an address).
_FIELDS: dict[str, tuple[str, bool]] = {
    "ga": ("destination_address", True),
    "src": ("source_address", True),
    "to": ("destination_name", False),
    "dev": ("source_name", False),
    "room": ("building_part", False),
    "fn": ("building_function", False),
    "svc": ("service", False),
    "dpt": ("dpt", False),
}

# Whether a row (its cells and its lower-case haystack) is shown.
RowMatch = Callable[[Sequence[str], str], bool]


def _terms(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:  # an unclosed quote
        return text.split()


def has_field_terms(text: str) -> bool:
    """Whether ``text`` uses at least one field term."""
    return any(
        term.partition(":")[0].lower() in _FIELDS and ":" in term
        for term in _terms(text)
    )


def _address_match(pattern: str, value: str) -> bool:
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    if pattern.endswith(("/", ".")):
        return value.startswith(pattern)
    return value == pattern


def compile_filter(text: str) -> RowMatch:
    """The match for a filter box's ``text`` (see the module documentation)."""
    needle = text.strip().lower()
    if not has_field_terms(needle):
        return lambda _cells, haystack: needle in haystack
    checks: list[RowMatch] = []
    for term in _terms(needle):
        field, colon, value = term.partition(":")
        if colon and field in _FIELDS:
            column, address = _FIELDS[field]
            index = COLUMN_INDEX[column]

            def check(
                cells: Sequence[str],
                _haystack: str,
                index: int = index,
                value: str = value,
                address: bool = address,
            ) -> bool:
                cell = cells[index].lower() if index < len(cells) else ""
                return _address_match(value, cell) if address else value in cell

            checks.append(check)
        else:
            checks.append(lambda _cells, haystack, term=term: term in haystack)
    return lambda cells, haystack: all(check(cells, haystack) for check in checks)

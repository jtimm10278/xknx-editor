"""Group addresses and links on the device that the project does not have.

A download writes the project's address and association tables over the device's. When the
device holds a group address or a link that the project no longer has (a link deleted in the
project but never downloaded), the download drops it. That changes the device's behaviour
without anyone seeing it, so :func:`device_table_extras` lists those entries and the download
refuses unless the caller accepts the removals.

Covered: the memory-mapped tables of BCU1 and BCU2 devices (masks ``0x001x``/``0x002x``).
The address table sits at 0x0116: a count (individual address included), then 2 octets per
address. The association table sits at 0x0100 plus the octet at 0x0111: a count, then
(address index, object number) pairs. Other masks are not checked (logged).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .errors import DownloadError
from .image import DownloadImage

logger = logging.getLogger(__name__)

ADDRESS_TABLE = 0x0116
ASSOCIATION_POINTER = 0x0111
_FREE_INDEX = 0xFE


class TableRemovalError(DownloadError):
    """The download would drop group addresses or links the device holds; ``extras`` lists them
    (None when the caller did not give them)."""

    def __init__(self, message: str, extras: TableExtras | None = None) -> None:
        super().__init__(message)
        self.extras = extras


@dataclass(frozen=True, slots=True)
class GroupTables:
    """Decoded tables: group addresses (index 1..) and (group address, object) links."""

    addresses: tuple[int, ...]
    links: frozenset[tuple[int, int]]


@dataclass(frozen=True, slots=True)
class TableExtras:
    """What the device has and the image does not."""

    addresses: tuple[int, ...]
    links: tuple[tuple[int, int], ...]

    def __bool__(self) -> bool:
        return bool(self.addresses or self.links)

    def describe(self) -> str:
        parts: list[str] = []
        if self.addresses:
            parts.append(
                "group addresses "
                + ", ".join(format_group_address(a) for a in self.addresses)
            )
        if self.links:
            parts.append(
                "links "
                + ", ".join(
                    f"{format_group_address(a)} on object {o}" for a, o in self.links
                )
            )
        return "; ".join(parts)


def format_group_address(raw: int) -> str:
    """``0x0802`` -> ``1/0/2`` (3-level)."""
    return f"{raw >> 11}/{(raw >> 8) & 7}/{raw & 0xFF}"


def checked_mask(mask_version: str) -> bool:
    """Whether the tables of a device of ``mask_version`` are decoded here."""
    digits = mask_version.removeprefix("MV-")[:4]
    try:
        number = int(digits, 16)
    except ValueError:
        return False
    return 0x0010 <= number <= 0x002F


async def decode_tables(read: Callable[[int, int], Awaitable[bytes]]) -> GroupTables:
    """Decode the address and association tables through ``read(address, size)``."""
    count = (await read(ADDRESS_TABLE, 1))[0]
    addresses: tuple[int, ...] = ()
    if 1 < count < 0xFF:
        raw = await read(ADDRESS_TABLE + 3, 2 * (count - 1))
        addresses = tuple(
            int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)
        )
    pointer = (await read(ASSOCIATION_POINTER, 1))[0]
    start = 0x0100 + pointer
    entries = (await read(start, 1))[0]
    links: set[tuple[int, int]] = set()
    if 0 < entries < 0xFF:
        raw = await read(start + 1, 2 * entries)
        for i in range(0, len(raw), 2):
            index, number = raw[i], raw[i + 1]
            if index == _FREE_INDEX or index == 0 or index > len(addresses):
                continue
            links.add((addresses[index - 1], number))
    return GroupTables(addresses, frozenset(links))


def image_reader(image: DownloadImage) -> Callable[[int, int], Awaitable[bytes]]:
    """``read`` over the image's memory as written (a later segment wins); missing octets
    read 0xFF."""
    memory: dict[int, int] = {}
    for segment in image.segments:
        for offset, value in enumerate(segment.data):
            if segment.mask is None or segment.mask[offset]:
                memory[segment.address + offset] = value

    async def read(address: int, size: int) -> bytes:
        return bytes(memory.get(address + i, 0xFF) for i in range(size))

    return read


def holds_tables(image: DownloadImage) -> bool:
    """Whether the image writes the address table (count octet at 0x0116)."""
    return any(
        s.address <= ADDRESS_TABLE < s.end
        and (s.mask is None or s.mask[ADDRESS_TABLE - s.address])
        for s in image.segments
    )


def extras(device: GroupTables, project: GroupTables) -> TableExtras:
    """Entries of ``device`` that ``project`` lacks."""
    return TableExtras(
        addresses=tuple(a for a in device.addresses if a not in project.addresses),
        links=tuple(sorted(device.links - project.links)),
    )


async def device_table_extras(
    read_device: Callable[[int, int], Awaitable[bytes]], image: DownloadImage
) -> TableExtras:
    """What the device's tables have that the image's do not."""
    device = await decode_tables(read_device)
    project = await decode_tables(image_reader(image))
    found = extras(device, project)
    if found:
        logger.warning(
            "download: the device holds %s, which the project does not",
            found.describe(),
        )
    return found

"""Group addresses and links the device holds and the project does not."""

from __future__ import annotations

import asyncio

import pytest

from xknxeditor.download.image import DownloadImage, MemorySegment
from xknxeditor.download.table_check import (
    TableExtras,
    checked_mask,
    decode_tables,
    device_table_extras,
    format_group_address,
    holds_tables,
    image_reader,
)

# A BCU1 device as read: IA 1.1.5, 1/0/1, 1/0/2, 2/0/1, 2/0/2; links 1/0/1->0,
# 1/0/2->1, 2/0/2->4, 2/0/1->5 (association table at 0x121, pointer 0x21).
DEVICE = bytes.fromhex("051105080108021001100206010002") + bytes.fromhex(
    "01fe02fe0304040305"
)
# The project: no 1/0/2 (pointer 0x1f).
PROJECT = bytes.fromhex("041105080110011002") + bytes.fromhex(
    "060100fe01fe02fe0303040205"
)


def _memory(table: bytes, pointer: int) -> dict[int, int]:
    memory = {0x0116 + i: b for i, b in enumerate(table)}
    memory[0x0111] = pointer
    return memory


def _reader(memory: dict[int, int]):  # type: ignore[no-untyped-def]
    async def read(address: int, size: int) -> bytes:
        return bytes(memory.get(address + i, 0xFF) for i in range(size))

    return read


def test_decode_device_tables() -> None:
    tables = asyncio.run(decode_tables(_reader(_memory(DEVICE, 0x21))))
    assert [format_group_address(a) for a in tables.addresses] == [
        "1/0/1",
        "1/0/2",
        "2/0/1",
        "2/0/2",
    ]
    assert (0x0802, 1) in tables.links
    assert (0x1001, 5) in tables.links


def _image(table: bytes, pointer: int) -> DownloadImage:
    return DownloadImage(
        segments=(
            MemorySegment(0x0111, bytes([pointer])),
            MemorySegment(0x0116, table),
        ),
        properties=(),
    )


def test_extras_name_the_dropped_address_and_link() -> None:
    found = asyncio.run(
        device_table_extras(_reader(_memory(DEVICE, 0x21)), _image(PROJECT, 0x1F))
    )
    assert found == TableExtras(addresses=(0x0802,), links=((0x0802, 1),))
    assert found.describe() == "group addresses 1/0/2; links 1/0/2 on object 1"


def test_same_tables_have_no_extras() -> None:
    found = asyncio.run(
        device_table_extras(_reader(_memory(PROJECT, 0x1F)), _image(PROJECT, 0x1F))
    )
    assert not found


def test_blank_device_has_no_extras() -> None:
    found = asyncio.run(device_table_extras(_reader({}), _image(PROJECT, 0x1F)))
    assert not found


@pytest.mark.parametrize(
    ("mask", "checked"),
    [("MV-0012", True), ("MV-0021", True), ("MV-0705", False), ("x", False)],
)
def test_checked_masks(mask: str, checked: bool) -> None:
    assert checked_mask(mask) is checked


def test_holds_tables_and_masked_image() -> None:
    assert holds_tables(_image(PROJECT, 0x1F))
    masked = DownloadImage(
        segments=(MemorySegment(0x0116, b"\x04\x11", mask=b"\x00\xff"),), properties=()
    )
    assert not holds_tables(masked)
    assert asyncio.run(image_reader(masked)(0x0116, 2)) == b"\xff\x11"

"""Fit a download image to the mask the device actually reports.

An application names one mask (``MV-0012`` for a BCU1 program), yet a device with a newer,
downward-compatible mask (``MV-0020``/``MV-0021``) runs it too. Two things in the image then
depend on the device's mask, not the project's:

- **Fixups.** The application's ``Static/FixupList`` names, per code segment offset, a
  function of the mask (``FunctionRef="MV-0012_ME-U.5FtransRequest"``). The 2 octets there
  (big endian) hold that function's address. The master data's ``MaskEntries`` give the
  address of the function of the same name for every mask, so the octets get the address of
  the device's mask.
- **Bit 7 of a BCU1 group-object flag byte.** The image sets it in every record of a BCU1
  table (:data:`.image.BCU1_FLAGS`). A device whose mask does not have a BCU1 group-object
  table reads bit 7 as the update flag, so those bits are cleared again
  (:attr:`.image.DownloadImage.forced_flag_bits`).

A fixup whose function the device's mask does not export, or a mask the master data does not
know, is refused (:class:`MaskMismatchError`): the program would call a wrong address.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from .errors import DownloadError
from .image import DownloadImage, MemorySegment
from .resources import bcu1_group_object_table

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.fixup_t import Fixup
    from xknxeditor.prod import Application
    from xknxeditor.prod.master import MasterData

logger = logging.getLogger(__name__)


class MaskMismatchError(DownloadError):
    """The device's mask cannot run the image as the project builds it."""


def mask_id(descriptor: int) -> str:
    """The master data id of a device descriptor type 0 (``0x0021`` -> ``MV-0021``)."""
    return f"MV-{descriptor & 0xFFFF:04X}"


def mask_entries(master: MasterData | None, mask: str) -> dict[str, int] | None:
    """Function name -> address of ``mask`` from the master data; None for an unknown mask."""
    raw = getattr(master, "raw", None)
    versions = getattr(raw, "mask_versions", None)
    for version in getattr(versions, "mask_version", None) or ():
        if version.id == mask:
            entries = version.mask_entries
            return {e.name: e.address for e in (entries.mask_entry if entries else ())}
    return None


def _function_name(function_ref: str) -> str:
    """``MV-0012_ME-U.5FtransRequest`` -> ``U_transRequest`` (the id escapes ``_`` as ``.5F``)."""
    name = function_ref.split("_ME-", 1)[-1]
    return name.replace(".5F", "_")


def fixup_writes(
    application: Application, master: MasterData | None, device_mask: str
) -> list[tuple[int, bytes]]:
    """(address, 2 octets) for every fixup of ``application`` on a device of ``device_mask``."""
    static = application.program.static
    fixups: list[Fixup] = list(static.fixup_list.fixup) if static.fixup_list else []
    if not fixups:
        return []
    entries = mask_entries(master, device_mask)
    if entries is None:
        raise MaskMismatchError(
            f"the device reports mask {device_mask}, which the master data does not know: "
            "its function addresses for the program's fixups are unknown"
        )
    code = static.code
    segments = {s.id: s for s in (code.absolute_segment if code else ())}
    writes: list[tuple[int, bytes]] = []
    for fixup in fixups:
        segment = segments.get(fixup.code_segment)
        if segment is None:
            raise MaskMismatchError(
                f"fixup {fixup.function_ref}: code segment {fixup.code_segment} has no address"
            )
        name = _function_name(fixup.function_ref)
        address = entries.get(name)
        if address is None:
            raise MaskMismatchError(
                f"the device's mask {device_mask} has no function {name} "
                f"(fixup {fixup.function_ref}): the program cannot run on it"
            )
        octets = address.to_bytes(2, "big")
        writes.extend((segment.address + offset, octets) for offset in fixup.offset)
    return writes


def _patch(segments: list[MemorySegment], address: int, octets: bytes) -> bool:
    """Write ``octets`` at ``address`` into every segment holding them (several segments may
    cover the same memory, e.g. the code segment and the group tables built over it); False
    if none holds all of them."""
    found = False
    for index, segment in enumerate(segments):
        if segment.address <= address and address + len(octets) <= segment.end:
            data = bytearray(segment.data)
            start = address - segment.address
            data[start : start + len(octets)] = octets
            segments[index] = replace(segment, data=bytes(data))
            found = True
    return found


def _clear_bits(segments: list[MemorySegment], address: int, bits: int) -> bool:
    """Clear ``bits`` of the octet at ``address`` in every segment holding it."""
    found = False
    for index, segment in enumerate(segments):
        if segment.address <= address < segment.end:
            data = bytearray(segment.data)
            data[address - segment.address] &= ~bits & 0xFF
            segments[index] = replace(segment, data=bytes(data))
            found = True
    return found


def needs_retarget(image: DownloadImage, application: Application) -> bool:
    """Whether the device's mask can change ``image``: the program has fixups, or the image
    holds BCU1 flag bits. Otherwise nothing is read from the device."""
    if getattr(image, "forced_flag_bits", ()):
        return True
    # Stand-in applications (tests, tools) may carry no static part.
    program = getattr(application, "program", None)
    fixup_list = getattr(getattr(program, "static", None), "fixup_list", None)
    return bool(getattr(fixup_list, "fixup", None))


def retarget_image(
    image: DownloadImage,
    application: Application,
    master: MasterData | None,
    descriptor: int,
) -> DownloadImage:
    """``image`` fitted to a device that reports ``descriptor`` (see the module text)."""
    device_mask = mask_id(descriptor)
    project_mask = application.program.mask_version
    segments = list(image.segments)
    changed = False
    if device_mask != project_mask:
        for address, octets in fixup_writes(application, master, device_mask):
            if _patch(segments, address, octets):
                changed = True
                logger.info(
                    "download: fixup at %#06x -> %s for mask %s (project %s)",
                    address,
                    octets.hex(),
                    device_mask,
                    project_mask,
                )
            else:
                logger.debug(
                    "download: fixup at %#06x not in this download's image", address
                )
    forced = image.forced_flag_bits
    if forced and not bcu1_group_object_table(device_mask):
        for address, bits in forced:
            changed = _clear_bits(segments, address, bits) or changed
        logger.info(
            "download: %d BCU1 flag bits cleared for mask %s (project %s)",
            len(forced),
            device_mask,
            project_mask,
        )
    if not changed:
        return image
    return replace(
        image,
        segments=tuple(segments),
        forced_flag_bits=()
        if forced and not bcu1_group_object_table(device_mask)
        else forced,
    )

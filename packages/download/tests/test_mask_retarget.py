"""A download image fitted to the mask the device reports (fixups, BCU1 flag bits).

Example: a Siemens BCU1 program (MV-0012) on a device reporting 0x0021.
Its four fixups at 0x191/0x1AC/0x1D6/0x1ED call U_deb30, U_GetTMx, U_transRequest and
U_readAD; ETS wrote the MV-0021 addresses there (0x504E, 0x5063, 0x507E, 0x506C), and the
group-object flag bytes without the BCU1 bit 7.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from xknxeditor.download.image import DownloadImage, MemorySegment
from xknxeditor.download.mask_retarget import (
    MaskMismatchError,
    fixup_writes,
    mask_entries,
    mask_id,
    retarget_image,
)
from xknxeditor.namespaces.intermediate.fixup_t import Fixup

SEGMENT = "M-0021_A-2302-01-E518-O0001_AS-0100"
BCU1 = {
    "U_deb30": 0x0C6F,
    "U_GetTMx": 0x0D6C,
    "U_transRequest": 0x0DB9,
    "U_readAD": 0x0D54,
}
BCU21 = {
    "U_deb30": 0x504E,
    "U_GetTMx": 0x5063,
    "U_transRequest": 0x507E,
    "U_readAD": 0x506C,
}
FIXUPS = {"U_deb30": 145, "U_GetTMx": 172, "U_transRequest": 214, "U_readAD": 237}


def _master(**masks: dict[str, int]) -> Any:
    versions = [
        SimpleNamespace(
            id=mask,
            mask_entries=SimpleNamespace(
                mask_entry=[
                    SimpleNamespace(name=n, address=a) for n, a in entries.items()
                ]
            ),
        )
        for mask, entries in masks.items()
    ]
    return SimpleNamespace(
        raw=SimpleNamespace(mask_versions=SimpleNamespace(mask_version=versions))
    )


def _application(mask: str = "MV-0012", fixups: dict[str, int] | None = None) -> Any:
    fixup_list = [
        Fixup(
            function_ref=f"{mask}_ME-{name.replace('_', '.5F')}",
            code_segment=SEGMENT,
            offset=[offset],
        )
        for name, offset in (FIXUPS if fixups is None else fixups).items()
    ]
    static = SimpleNamespace(
        fixup_list=SimpleNamespace(fixup=fixup_list) if fixup_list else None,
        code=SimpleNamespace(
            absolute_segment=[SimpleNamespace(id=SEGMENT, address=0x100)]
        ),
    )
    return SimpleNamespace(program=SimpleNamespace(mask_version=mask, static=static))


def _code() -> bytes:
    data = bytearray(256)
    for name, offset in FIXUPS.items():
        data[offset : offset + 2] = BCU1[name].to_bytes(2, "big")
    return bytes(data)


def _image(forced: tuple[tuple[int, int], ...] = ()) -> DownloadImage:
    code = _code()
    # The code segment and the group tables built over it both cover 0x100.
    return DownloadImage(
        segments=(MemorySegment(0x100, code), MemorySegment(0x100, code)),
        properties=(),
        forced_flag_bits=forced,
    )


MASTER = _master(**{"MV-0012": BCU1, "MV-0021": BCU21})


def test_mask_id_and_entries() -> None:
    assert mask_id(0x0021) == "MV-0021"
    assert mask_entries(MASTER, "MV-0021") == BCU21
    assert mask_entries(MASTER, "MV-0705") is None
    assert mask_entries(None, "MV-0021") is None


def test_fixups_get_the_device_mask_addresses_in_every_segment() -> None:
    image = retarget_image(_image(), cast("Any", _application()), MASTER, 0x0021)
    for segment in image.segments:
        for name, offset in FIXUPS.items():
            assert segment.data[offset : offset + 2] == BCU21[name].to_bytes(2, "big")


def test_same_mask_leaves_the_image_alone() -> None:
    image = _image(forced=((0x141, 0x80),))
    assert retarget_image(image, cast("Any", _application()), MASTER, 0x0012) is image


def test_bcu1_flag_bits_cleared_for_a_bcu2_device() -> None:
    code = bytearray(_code())
    code[0x41] = 0xC7
    image = DownloadImage(
        segments=(MemorySegment(0x100, bytes(code)),),
        properties=(),
        forced_flag_bits=((0x141, 0x80),),
    )
    out = retarget_image(image, cast("Any", _application(fixups={})), MASTER, 0x0021)
    assert out.segments[0].data[0x41] == 0x47
    assert out.forced_flag_bits == ()


def test_missing_function_on_the_device_mask_is_refused() -> None:
    master = _master(**{"MV-0012": BCU1, "MV-0021": {"U_deb30": 0x504E}})
    with pytest.raises(MaskMismatchError, match="U_GetTMx"):
        fixup_writes(cast("Any", _application()), master, "MV-0021")


def test_unknown_device_mask_is_refused() -> None:
    with pytest.raises(MaskMismatchError, match="MV-0099"):
        retarget_image(_image(), cast("Any", _application()), MASTER, 0x0099)


def test_no_fixups_needs_no_master_data() -> None:
    assert fixup_writes(cast("Any", _application(fixups={})), None, "MV-0099") == []


def test_needs_retarget_only_with_fixups_or_forced_bits() -> None:
    from xknxeditor.download.mask_retarget import needs_retarget

    assert needs_retarget(_image(), cast("Any", _application()))
    assert not needs_retarget(_image(), cast("Any", _application(fixups={})))
    assert needs_retarget(
        _image(forced=((0x141, 0x80),)), cast("Any", _application(fixups={}))
    )
    assert not needs_retarget(cast("Any", object()), cast("Any", None))

"""Inline memory writes that are steps of the procedure reach the device as given."""

from __future__ import annotations

from xknx.telegram.apci import MemoryWrite, PropertyValueWrite

from xknxeditor.download.image import DownloadImage, MemorySegment
from xknxeditor.download.procedure import (
    LoadProcedureRunner,
    _inline_memory_steps,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_mem_t import (
    LdCtrlLoadImageMem,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_mem_addr_space_t import (
    LdCtrlMemAddrSpace,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_set_control_variable_t import (
    LdCtrlSetControlVariable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem

from .conftest import FakeDevice
from .test_procedure import _application, _device


def _write(
    address: int, size: int, inline: bytes | None = None, **kw: object
) -> LdCtrlWriteMem:
    return LdCtrlWriteMem(
        address=address, size=size, verify=True, inline_data=inline, **kw
    )  # type: ignore[arg-type]


def _bcu1_load_all() -> list[object]:
    """MV-0012 "Load all" from the KNX master, without Connect and Restart."""
    return [
        LdCtrlSetControlVariable(name="EnableVerifyOnWriteDirect", value=True),
        _write(0x10D, 1, b"\x00"),
        LdCtrlLoadImageMem(address=0x116, size=1),
        _write(0x116, 1, b"\x01"),
        _write(0x100, 1),
        _write(0x104, 9),
        _write(0x10E, 8),
        _write(0x119, 230),
        _write(0xCE, 9, bytes(9)),
        _write(0xD7, 9, bytes(9)),
        _write(0x116, 1),
        _write(0x10D, 1, b"\xff"),
    ]


def _bcu1_image() -> DownloadImage:
    """EEPROM 0x100-0x1FF without the run control octet 0x10D, as a BCU 1 image has it; the
    address table holds the IA and three group addresses: length octet 0x116 = 04."""
    eeprom = bytearray((0x100 + n) & 0xFF for n in range(0x100))
    eeprom[0x16] = 0x04
    return DownloadImage(
        (
            MemorySegment(0x100, bytes(eeprom[:0x0D])),
            MemorySegment(0x10E, bytes(eeprom[0x0E:])),
        ),
        (),
    )


def _writes_at(device: FakeDevice, address: int) -> list[bytes]:
    return [
        p.data
        for p in device.sent
        if isinstance(p, MemoryWrite) and p.address <= address < p.address + len(p.data)
    ]


def test_steps_are_inline_writes_repeated_later_from_the_image() -> None:
    controls = _bcu1_load_all()
    steps = _inline_memory_steps(controls)
    by_id = {id(c): c for c in controls}
    assert [(by_id[i].address, by_id[i].inline_data) for i in steps] == [
        (0x116, b"\x01")
    ]


def test_overlap_and_address_space_decide() -> None:
    partial = _write(0x200, 4, b"\x00\x00\x00\x00")
    elsewhere = _write(0x300, 1, b"\x01")
    other_space = _write(0x116, 1, b"\x01", address_space=LdCtrlMemAddrSpace.LC_SLAVE)
    controls = [partial, elsewhere, other_space, _write(0x203, 2), _write(0x116, 1)]
    assert _inline_memory_steps(controls) == {id(partial)}
    # A later inline write does not make an earlier one a step.
    first, second = _write(0x10D, 1, b"\x00"), _write(0x10D, 1, b"\xff")
    assert _inline_memory_steps([first, second]) == set()


async def test_bcu1_load_all_cuts_the_address_table_while_loading() -> None:
    device = FakeDevice(descriptor=0x0012)
    device.memory = {0x116: 0x04}
    runner = LoadProcedureRunner(
        _application(*_bcu1_load_all()), _bcu1_image(), DeviceProgrammer(device)
    )
    await runner.run()

    # 01 first (only the individual address while the tables load), the image's 04 at the end.
    assert _writes_at(device, 0x116) == [b"\x01", b"\x04"]
    assert device.memory[0x116] == 0x04
    assert _writes_at(device, 0x10D) == [b"\x00", b"\xff"]


async def test_inline_filler_still_gives_way_to_the_image() -> None:
    """Inline data the procedure never rewrites stays a filler: the image wins (Hager)."""
    device = FakeDevice(descriptor=0x0012)
    image = DownloadImage((MemorySegment(0x120, b"AB"),), ())
    runner = LoadProcedureRunner(
        _application(_write(0x120, 2, b"\x00\x00")), image, DeviceProgrammer(device)
    )
    await runner.run()
    assert _writes_at(device, 0x120) == [b"AB"]


async def test_preflight_plans_the_step_and_the_final_value() -> None:
    device = FakeDevice(descriptor=0x0012)
    device.memory = {0x100 + n: (0x100 + n) & 0xFF for n in range(0x100)}
    device.memory[0x116] = 0x04
    runner = LoadProcedureRunner(
        _application(*_bcu1_load_all()), _bcu1_image(), DeviceProgrammer(device)
    )
    report = await runner.preflight()
    planned = [s.planned for s in report.segments if s.address == 0x116]
    assert planned == [b"\x01", b"\x04"]
    assert not any(isinstance(p, MemoryWrite) for p in device.sent)


def _sent_order(device: FakeDevice) -> list[str]:
    """``M`` per memory write, ``V`` per write of the verify-mode control (PID 14)."""
    order: list[str] = []
    for p in device.sent:
        if isinstance(p, MemoryWrite):
            order.append("M")
        elif isinstance(p, PropertyValueWrite) and (p.object_index, p.property_id) == (
            0,
            14,
        ):
            order.append("V")
    return order


async def test_unverified_inline_writes_go_before_the_device_verifies() -> None:
    """A System B device verifies memory writes itself once PID 14 bit 2 is set; an inline
    write the procedure does not verify (a plug-in's parameter pass) is sent before that,
    the first verified write switches it on (as ETS does)."""
    device = _device()
    image = DownloadImage((MemorySegment(0x200, b"AB"),), ())
    controls = [
        LdCtrlWriteMem(address=0x100, size=2, verify=False, inline_data=b"\x01\x02"),  # type: ignore[call-arg]
        _write(0x200, 2),
    ]
    runner = LoadProcedureRunner(
        _application(*controls), image, DeviceProgrammer(device)
    )
    await runner.run()
    assert _sent_order(device) == ["M", "V", "M"]
    assert _writes_at(device, 0x100) == [b"\x01\x02"]
    assert _writes_at(device, 0x200) == [b"AB"]

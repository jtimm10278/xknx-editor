"""Does the device match the project? A read-only compare of a device with its projected image.

The expected bytes come from a dry run of the device's full download (:mod:`.dry_run`, which
sends nothing), placed where the device keeps its segments (see
:func:`.snapshot.read_table_references`): the memory and the properties the download would write.
:func:`compare_device` reads exactly those from the device and lists what differs.

Everything sent goes through :class:`ReadOnlyManager`, which refuses any service that is not a
read before it leaves (memory, property, descriptor reads; transport control carries no payload).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xknx.telegram.apci import (
    APCI,
    DeviceDescriptorRead,
    MemoryExtendedRead,
    MemoryRead,
    PropertyDescriptionRead,
    PropertyExtDescriptionRead,
    PropertyExtValueRead,
    PropertyValueRead,
    UserMemoryRead,
)

from .dry_run import FrameKind
from .snapshot import plan_from_dry_run

if TYPE_CHECKING:
    from xknx.telegram import Telegram

    from .dry_run import DryRunResult
    from .programmer import BusConnection, ConnectionManager

READS: tuple[type[APCI], ...] = (
    DeviceDescriptorRead,
    MemoryRead,
    MemoryExtendedRead,
    UserMemoryRead,
    PropertyValueRead,
    PropertyExtValueRead,
    PropertyDescriptionRead,
    PropertyExtDescriptionRead,
)
_LOAD_STATE_CONTROL = 5
# Properties a download sets for itself while it runs, not configuration: device object
# PID_DEVICE_CONTROL (14, e.g. the verify mode a download switches on), cleared by a restart.
_SESSION_PROPERTIES = frozenset({(0, 14)})
_CHUNK = 12  # octets per A_Memory_Read on the smallest (BCU1) APDU


class WriteRefused(PermissionError):
    """A compare tried to send something other than a read."""


class ReadOnlyConnection:
    """A :class:`~.programmer.BusConnection` that only lets reads through."""

    def __init__(self, connection: BusConnection) -> None:
        self._connection = connection

    @staticmethod
    def _check(payload: APCI) -> None:
        if not isinstance(payload, READS):
            raise WriteRefused(f"read-only compare: {type(payload).__name__} refused")

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        self._check(payload)
        await self._connection.send_data(payload, wait_for_ack)

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        self._check(payload)
        return await self._connection.request(payload, expected)


class ReadOnlyManager:
    """A :class:`~.programmer.ConnectionManager` whose connections only read."""

    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager

    async def open(self) -> BusConnection:
        return ReadOnlyConnection(await self._manager.open())

    async def close(self) -> None:
        await self._manager.close()


@dataclass
class Expected:
    memory: dict[int, int] = field(default_factory=dict[int, int])
    regions: dict[int, str] = field(default_factory=dict[int, str])
    properties: dict[tuple[int, int, int, int], bytes] = field(
        default_factory=dict[tuple[int, int, int, int], bytes]
    )
    skipped: list[str] = field(default_factory=list[str])


def expected_from_dry_run(
    result: DryRunResult, placed: Collection[int] = ()
) -> Expected:
    """The bytes and property values the dry-run download leaves written (the last write of each
    byte wins), for memory the device can be asked for (see :func:`.snapshot.plan_from_dry_run`)."""
    out = Expected(skipped=plan_from_dry_run(result, placed).skipped)
    for f in result.frames:
        if f.kind in (FrameKind.MEMORY_WRITE, FrameKind.USER_MEMORY_WRITE):
            if f.address is None or not f.data:
                continue
            if f.segment_object is not None and f.segment_object not in placed:
                continue
            for i, byte in enumerate(f.data):
                out.memory[f.address + i] = byte
                out.regions[f.address + i] = f.region
        elif f.kind is FrameKind.PROPERTY_WRITE:
            if f.object_index is None or f.property_id is None:
                continue
            if f.property_id == _LOAD_STATE_CONTROL:
                continue
            if (f.object_index, f.property_id) in _SESSION_PROPERTIES:
                continue
            key = (f.object_index, f.property_id, f.start_index or 1, f.count or 1)
            out.properties[key] = f.data
    return out


def _ranges(addresses: Collection[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a in sorted(addresses):
        if out and a == out[-1][0] + out[-1][1]:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((a, 1))
    return out


@dataclass
class ComparePlan:
    """What a compare would read, without the bus."""

    memory: list[tuple[int, int]]
    properties: list[tuple[int, int, int, int]]
    skipped: list[str]

    @property
    def reads(self) -> int:
        """Read requests at the smallest APDU (an upper bound)."""
        return sum(-(-size // _CHUNK) for _, size in self.memory) + len(self.properties)

    @property
    def octets(self) -> int:
        return sum(size for _, size in self.memory)


def compare_plan(expected: Expected) -> ComparePlan:
    return ComparePlan(
        memory=_ranges(expected.memory),
        properties=sorted(expected.properties),
        skipped=list(expected.skipped),
    )


@dataclass
class Difference:
    what: str  # "memory" | "property"
    where: str  # "0x8c68+2" | "6/205"
    region: str
    expected: str
    held: str


@dataclass
class CompareResult:
    address: str
    octets: int = 0
    properties: int = 0
    differences: list[Difference] = field(default_factory=list[Difference])
    skipped: list[str] = field(default_factory=list[str])
    error: str = ""

    @property
    def matches(self) -> bool:
        return not self.error and not self.differences

    def as_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "matches": self.matches,
            "octets_compared": self.octets,
            "properties_compared": self.properties,
            "differences": [d.__dict__ for d in self.differences],
            "skipped": self.skipped,
            "error": self.error,
        }


async def compare_device(
    manager: ConnectionManager,
    expected: Expected,
    *,
    address: str,
    max_apdu_length: int = 15,
) -> CompareResult:
    """Read ``expected``'s memory and properties from the device (reads only) and list what
    differs. A failure is reported in ``error`` (nothing is retried)."""
    from .programmer import DeviceProgrammer

    plan = compare_plan(expected)
    result = CompareResult(address=address, skipped=plan.skipped)
    guarded = ReadOnlyManager(manager)
    try:
        connection = await guarded.open()
        programmer = DeviceProgrammer(connection, max_apdu_length=max_apdu_length)
        try:
            for start, size in plan.memory:
                held = await programmer.read_memory(start, size)
                want = bytes(expected.memory[start + i] for i in range(size))
                result.octets += size
                i = 0
                while i < size:
                    if i < len(held) and held[i] == want[i]:
                        i += 1
                        continue
                    j = i
                    while j < size and not (j < len(held) and held[j] == want[j]):
                        j += 1
                    result.differences.append(
                        Difference(
                            "memory",
                            f"{start + i:#06x}+{j - i}",
                            expected.regions.get(start + i, ""),
                            want[i:j].hex(),
                            held[i:j].hex(),
                        )
                    )
                    i = j
            for key in plan.properties:
                obj, pid, start_index, count = key
                held = await programmer.read_property(
                    obj, pid, count=count, start_index=start_index
                )
                result.properties += 1
                if held != expected.properties[key]:
                    result.differences.append(
                        Difference(
                            "property",
                            f"{obj}/{pid}",
                            "",
                            expected.properties[key].hex(),
                            held.hex(),
                        )
                    )
        finally:
            await guarded.close()
    except Exception as exc:  # no answer, refused, a refused write: report, don't retry
        result.error = f"{type(exc).__name__}: {exc}"[:300]
    return result

"""Read-only device tools with a confirm step: first the plan (the exact frames, nothing sent),
then the run, which needs the plan's token.

- ``is_there``: a device descriptor read at one individual address (connect, read, the
  acknowledgement of its answer, disconnect; the read is repeated up to 3 times while unanswered).
- ``programming_mode``: one A_IndividualAddress_Read broadcast; devices in programming mode answer.
- ``line_scan``: the descriptor read at every address of a range of one line, one after another.

All three only read. They are refused while Listen Only is on (it refuses every frame); Block Bus
Writes lets them through. A token is good for one run of exactly the planned frames, for
:data:`TOKEN_SECONDS`.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xknx.exceptions import ManagementConnectionError, ManagementConnectionRefused
from xknx.management.procedures.network.nm_individual_address_read import (
    nm_individual_address_read,
)
from xknx.telegram import IndividualAddress
from xknx.telegram.apci import DeviceDescriptorRead

if TYPE_CHECKING:
    from xknx import XKNX

TOOLS = ("is_there", "programming_mode", "line_scan")
TOKEN_SECONDS = 300.0
# The most addresses one line scan may read (a whole line: 0..255).
MAX_SCAN = 256
# Seconds a descriptor read waits for the answer before it counts as "nobody there".
DESCRIPTOR_TIMEOUT = 2.5


@dataclass(frozen=True)
class PlannedFrame:
    """One frame a tool will send: the service, its target and what it is for."""

    service: str
    target: str
    purpose: str


@dataclass(frozen=True)
class Plan:
    tool: str
    frames: tuple[PlannedFrame, ...]
    addresses: tuple[str, ...]
    token: str
    made: float = field(default_factory=time.monotonic)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "token": self.token,
            "frames": [
                {"service": f.service, "target": f.target, "purpose": f.purpose}
                for f in self.frames
            ],
            "frame_count": len(self.frames),
            "addresses": list(self.addresses),
            "valid_seconds": TOKEN_SECONDS,
        }


def _descriptor_frames(address: str) -> list[PlannedFrame]:
    return [
        PlannedFrame("T_Connect", address, "open a connection"),
        PlannedFrame("A_DeviceDescriptor_Read (type 0)", address, "ask for its mask"),
        PlannedFrame("T_Ack", address, "acknowledge its answer (only when it answers)"),
        PlannedFrame("T_Disconnect", address, "close the connection"),
    ]


def _line_range(line: str, first: int, last: int) -> list[str]:
    parts = line.split(".")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"a line is written area.line, e.g. 1.1, not {line!r}")
    if not 0 <= first <= last <= 255:
        raise ValueError("the device numbers must be 0..255, first <= last")
    if last - first + 1 > MAX_SCAN:
        raise ValueError(f"at most {MAX_SCAN} addresses per scan")
    return [str(IndividualAddress(f"{line}.{n}")) for n in range(first, last + 1)]


def make_plan(
    tool: str, *, address: str = "", line: str = "", first: int = 0, last: int = 255
) -> Plan:
    """The frames ``tool`` will send, and the token to run it with. Sends nothing."""
    token = secrets.token_hex(8)
    if tool == "is_there":
        target = str(IndividualAddress(address))
        return Plan(tool, tuple(_descriptor_frames(target)), (target,), token)
    if tool == "programming_mode":
        frame = PlannedFrame(
            "A_IndividualAddress_Read",
            "broadcast",
            "devices in programming mode answer",
        )
        return Plan(tool, (frame,), (), token)
    if tool == "line_scan":
        addresses = _line_range(line, first, last)
        frames = [f for a in addresses for f in _descriptor_frames(a)]
        return Plan(tool, tuple(frames), tuple(addresses), token)
    raise ValueError(f"unknown tool {tool!r}; one of {', '.join(TOOLS)}")


class PlanBook:
    """Plans handed out and not yet run; a token runs its plan once."""

    def __init__(self, clock: Any = time.monotonic) -> None:
        self._plans: dict[str, Plan] = {}
        self._clock = clock

    def add(self, plan: Plan) -> Plan:
        plan = Plan(plan.tool, plan.frames, plan.addresses, plan.token, self._clock())
        self._plans[plan.token] = plan
        return plan

    def take(self, token: str) -> Plan:
        """The plan of ``token``, removed; ``ValueError`` when unknown, used or expired."""
        plan = self._plans.pop(token, None)
        if plan is None:
            raise ValueError("unknown or already used token: make a plan first")
        if self._clock() - plan.made > TOKEN_SECONDS:
            raise ValueError("the plan expired: make a new one")
        return plan


async def descriptor(xknx: XKNX, address: str) -> int | None:
    """The device descriptor type 0 of the device at ``address``, ``None`` when nobody answers."""
    connection = await xknx.management.connect(IndividualAddress(address))
    try:
        telegram = await asyncio.wait_for(
            connection.request(DeviceDescriptorRead(descriptor=0)), DESCRIPTOR_TIMEOUT
        )
        value = getattr(telegram.payload, "value", None)
        return int(value) if value is not None else None
    except (ManagementConnectionError, TimeoutError):
        return None
    finally:
        with contextlib.suppress(
            ManagementConnectionError, ManagementConnectionRefused
        ):
            await connection.disconnect()


async def run_plan(xknx: XKNX, plan: Plan, timeout: float = 3.0) -> dict[str, Any]:
    """Send the frames of ``plan`` and gather the answers."""
    if plan.tool == "programming_mode":
        found = await nm_individual_address_read(xknx, timeout=timeout)
        return {"tool": plan.tool, "in_programming_mode": sorted(str(a) for a in found)}
    found_devices: list[dict[str, Any]] = []
    for address in plan.addresses:
        mask = await descriptor(xknx, address)
        found_devices.append(
            {
                "address": address,
                "answered": mask is not None,
                "descriptor": f"{mask:04X}" if mask is not None else None,
            }
        )
    if plan.tool == "is_there":
        return {"tool": plan.tool, **found_devices[0]}
    return {
        "tool": plan.tool,
        "answered": [d for d in found_devices if d["answered"]],
        "scanned": len(found_devices),
    }

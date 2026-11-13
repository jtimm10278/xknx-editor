"""Block Bus Writes: with it on, the editor reads from the bus but changes nothing there.

Every frame the editor sends passes the observable interfaces' ``send_cemi``
(:mod:`editor_gui.plugins.connection.interface`), so the check sits there and holds for every
code path: downloads, individual address and serial number writes, restarts, property and memory
writes, group writes and responses, KNX Secure key writes, DALI commands and MCP tools alike.

What is let through is a fixed list of reads (and the transport layer's connect, disconnect and
acknowledgements they need); everything else counts as a write. Listen Only goes further: it
refuses every frame to the bus, reads included. Frames the editor cannot look
into count as writes too: a KNX Data Secure frame is encrypted before it reaches the interface,
so reads of secured devices are blocked as well while the setting is on.
"""

from __future__ import annotations

from collections.abc import Iterable

from xknx.cemi.cemi_frame import CEMIFrame, CEMILData, CEMIMPropReadRequest
from xknx.exceptions import XKNXException
from xknx.telegram import GroupAddress, apci, tpci

# Services that only ask a device, the interface or the bus for information.
_READS: tuple[type[apci.APCI], ...] = (
    apci.GroupValueRead,
    apci.IndividualAddressRead,
    apci.IndividualAddressSerialRead,
    apci.ADCRead,
    apci.FunctionPropertyStateRead,
    apci.FunctionPropertyExtStateRead,
    apci.SystemNetworkParameterRead,
    apci.NetworkParameterRead,
    apci.PropertyValueRead,
    apci.PropertyDescriptionRead,
    apci.PropertyExtValueRead,
    apci.PropertyExtDescriptionRead,
    apci.MemoryRead,
    apci.MemoryExtendedRead,
    apci.UserMemoryRead,
    apci.UserManufacturerInfoRead,
    apci.DeviceDescriptorRead,
    apci.FilterTableRead,
    apci.RouterMemoryRead,
    apci.RouterStatusRead,
    apci.LinkRead,
    apci.GroupPropValueRead,
    apci.DomainAddressRead,
    apci.DomainAddressSelectiveRead,
    apci.DomainAddressSerialNumberRead,
    # Gives the access level a read of protected memory needs; it changes nothing.
    apci.AuthorizeRequest,
)
# The transport layer's own frames around a connection-oriented read.
_TRANSPORT_CONTROL = (tpci.TConnect, tpci.TDisconnect, tpci.TAck, tpci.TNak)


class BusWritesBlocked(XKNXException):
    """A frame that would change something on the bus, refused because Block Bus Writes is on."""

    def __init__(self, cemi: CEMIFrame) -> None:
        super().__init__(
            f"not sent: Block Bus Writes is on (Settings > Connection) - {describe(cemi)}"
        )


def is_bus_write(cemi: CEMIFrame) -> bool:
    """Whether sending ``cemi`` could change something on the bus or in the interface."""
    data = cemi.data
    if isinstance(data, CEMIMPropReadRequest):
        return False
    if not isinstance(data, CEMILData):
        # Property writes, function property commands and resets of the interface itself.
        return True
    if isinstance(data.tpci, _TRANSPORT_CONTROL):
        return False
    if data.payload is None:
        return True
    return not isinstance(data.payload, _READS)


class ListenOnlyRefused(BusWritesBlocked):
    """A frame refused because Listen Only is on: the editor sends nothing to the bus."""

    def __init__(self, cemi: CEMIFrame) -> None:
        XKNXException.__init__(
            self,
            f"not sent: Listen Only is on (Settings > Connection) - {describe(cemi)}",
        )


class _ListenOnly:
    active: bool = False


_listen_only = _ListenOnly()


def set_listen_only(active: bool) -> None:
    """Listen Only: while it is on, every frame to the bus is refused, reads included (Block
    Bus Writes still lets reads through). Only property reads of the interface itself, which
    never reach the bus, pass."""
    _listen_only.active = active


def listen_only_active() -> bool:
    return _listen_only.active


def listen_only_allows(cemi: CEMIFrame) -> bool:
    """Whether Listen Only lets ``cemi`` through (always, while it is off)."""
    return not _listen_only.active or isinstance(cemi.data, CEMIMPropReadRequest)


class ProtectedRefused(BusWritesBlocked):
    """A frame to a protected group address: the editor never sends to these."""

    def __init__(self, cemi: CEMIFrame, reason: str) -> None:
        XKNXException.__init__(self, f"not sent: {reason} - {describe(cemi)}")


class _Protected:
    exact: frozenset[str] = frozenset()
    prefixes: tuple[str, ...] = ()


_protected = _Protected()


def parse_protected(text: str | Iterable[str]) -> list[str]:
    """Group addresses and prefixes from a setting: separated by commas, spaces or new lines; a
    prefix ends with ``/`` (e.g. ``7/0/``, ``7/``)."""
    items = text.replace(",", " ").split() if isinstance(text, str) else list(text)
    return sorted({item.strip() for item in items if item.strip()})


def set_protected(patterns: Iterable[str]) -> None:
    """Protected group addresses: no frame is sent to them, reads included, whatever else is
    allowed (exact addresses, or prefixes ending with ``/``)."""
    items = parse_protected(patterns)
    _protected.exact = frozenset(p for p in items if not p.endswith("/"))
    _protected.prefixes = tuple(p for p in items if p.endswith("/"))


def protected_patterns() -> list[str]:
    return sorted([*_protected.exact, *_protected.prefixes])


def protected_reason(group_address: str) -> str:
    """Why ``group_address`` (printed, e.g. ``1/2/3``) may not be sent to, "" when it may."""
    if group_address in _protected.exact:
        return f"{group_address} is a protected group address"
    for prefix in _protected.prefixes:
        if group_address.startswith(prefix):
            return f"{group_address} is in the protected group {prefix}"
    return ""


def protected_allows(cemi: CEMIFrame) -> str:
    """ "" when ``cemi`` may go out, else why not (a frame to a protected group address)."""
    data = cemi.data
    if not isinstance(data, CEMILData) or not isinstance(data.dst_addr, GroupAddress):
        return ""
    return protected_reason(str(data.dst_addr))


def describe(cemi: CEMIFrame) -> str:
    """The service and destination of ``cemi``, for the log."""
    data = cemi.data
    if isinstance(data, CEMILData):
        service = (
            type(data.payload).__name__ if data.payload is not None else "no payload"
        )
        return f"{service} to {data.dst_addr}"
    return cemi.code.name

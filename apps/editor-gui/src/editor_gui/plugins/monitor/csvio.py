"""Group telegram logs as CSV, and KNXnet/IP captures as JSON lines, for the monitors.

**CSV** (also gzip-compressed, ``.csv.gz``): one telegram per row with a header row. The columns
read are ``time`` (ISO 8601, with or without a UTC offset; none means UTC), ``source`` (an
individual address), ``ga`` (the group address), ``type`` (``write``, ``response`` or
``read``), ``raw`` (the value) and, when there, ``dpt``. Other columns (names, decoded values)
are ignored on import and written on export: ``time, source, source_name, ga, ga_name, type,
raw, value, dpt``. ``raw`` is the payload as hex octets, or for a value of up to 6 bits that fits
in the APCI octet (DPT 1, 2 and 3, or an odd number of hex digits when there is no DPT) its
number. This is the layout bus recorders built on xknx commonly write.

**JSON lines**: a capture of KNXnet/IP frames, one object per line with ``t`` (seconds since the
epoch) and ``raw`` (the frame as hex). The cEMI frames of tunnelling requests and routing
indications are taken; everything else is skipped.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.const import CEMIMessageCode
from xknx.dpt.payload import DPTArray, DPTBinary
from xknx.telegram import GroupAddress, IndividualAddress, Telegram
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from editor_gui.plugins.monitor.records import (
    MonitorRecord,
    RecordKind,
    datetime_to_ticks,
    ticks_to_datetime,
)
from editor_gui.plugins.monitor.xmlio import ImportResult

CSV_COLUMNS = (
    "time",
    "source",
    "source_name",
    "ga",
    "ga_name",
    "type",
    "raw",
    "value",
    "dpt",
)
_GZIP_MAGIC = b"\x1f\x8b"
# KNXnet/IP service types that carry a cEMI frame, and the offset of the cEMI frame in them.
_TUNNELLING_REQUEST = 0x0420
_ROUTING_INDICATION = 0x0530
_SMALL_DPT_MAJORS = frozenset({"1", "2", "3"})


def _text(data: bytes) -> str:
    if data[:2] == _GZIP_MAGIC:
        data = gzip.decompress(data)
    return data.decode("utf-8-sig")


def _moment(text: str) -> datetime:
    moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _payload(raw: str, dpt: str) -> DPTBinary | DPTArray:
    raw = raw.strip()
    major = dpt.strip().split(".", 1)[0].removeprefix("DPST-").removeprefix("DPT-")
    small = major in _SMALL_DPT_MAJORS if dpt.strip() else len(raw) % 2 == 1
    if small:
        return DPTBinary(int(raw, 16) if not raw.isdigit() else int(raw))
    return DPTArray(bytes.fromhex(raw))


def _apci(
    kind: str, raw: str, dpt: str
) -> GroupValueRead | GroupValueWrite | GroupValueResponse:
    kind = kind.strip().lower()
    if kind == "read":
        return GroupValueRead()
    payload = _payload(raw, dpt)
    if kind == "response":
        return GroupValueResponse(payload)
    if kind == "write":
        return GroupValueWrite(payload)
    raise ValueError(f"unknown telegram type {kind!r}")


def telegram_cemi(source: str, ga: str, apci: object) -> bytes:
    """An L_Data.ind cEMI frame of a group telegram, as a bus monitor receives it."""
    telegram = Telegram(destination_address=GroupAddress(ga), payload=apci)  # type: ignore[arg-type]
    data = CEMILData.init_from_telegram(telegram, src_addr=IndividualAddress(source))
    return CEMIFrame(code=CEMIMessageCode.L_DATA_IND, data=data).to_knx()


def import_csv(data: bytes, connection_name: str = "") -> ImportResult:
    """Read a telegram CSV (also gzip-compressed). Rows that cannot be read are counted as
    skipped; raises ``ValueError`` when the header lacks the needed columns."""
    reader = csv.DictReader(io.StringIO(_text(data)))
    needed = {"time", "source", "ga", "type"}
    if reader.fieldnames is None or not needed <= set(reader.fieldnames):
        raise ValueError(
            "not a telegram CSV: the header needs the columns time, source, ga and type"
        )
    records: list[MonitorRecord] = []
    skipped = 0
    for row in reader:
        try:
            raw = telegram_cemi(
                row["source"],
                row["ga"],
                _apci(row["type"], row.get("raw") or "", row.get("dpt") or ""),
            )
            ticks = datetime_to_ticks(_moment(row["time"]))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            del error
            skipped += 1
            continue
        except Exception:  # xknx raises its own errors for bad addresses and payloads
            skipped += 1
            continue
        records.append(
            MonitorRecord(
                kind=RecordKind.TELEGRAM,
                ticks=ticks,
                raw=raw,
                connection_name=connection_name,
            )
        )
    records.sort(key=lambda r: r.ticks)
    return ImportResult(records, skipped)


def _knxip_cemi(frame: bytes) -> bytes | None:
    """The cEMI frame of a tunnelling request or routing indication, else ``None``."""
    if len(frame) < 6 or frame[0] != 0x06:
        return None
    service = int.from_bytes(frame[2:4], "big")
    if service == _ROUTING_INDICATION:
        return frame[6:] or None
    if service == _TUNNELLING_REQUEST and len(frame) > 10:
        return frame[6 + frame[6] :] or None
    return None


def import_jsonl(data: bytes, connection_name: str = "") -> ImportResult:
    """Read a KNXnet/IP capture (JSON lines with ``t`` and ``raw``). Lines that are not JSON or
    carry no cEMI frame are skipped; raises ``ValueError`` when no line is a capture line."""
    records: list[MonitorRecord] = []
    skipped = 0
    lines = 0
    for line in _text(data).splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            stamp = float(item["t"])
            frame = bytes.fromhex(item["raw"])
        except (ValueError, KeyError, TypeError):
            skipped += 1
            continue
        lines += 1
        cemi = _knxip_cemi(frame)
        if cemi is None:
            skipped += 1
            continue
        records.append(
            MonitorRecord(
                kind=RecordKind.TELEGRAM,
                ticks=datetime_to_ticks(datetime.fromtimestamp(stamp, UTC)),
                raw=cemi,
                connection_name=connection_name,
            )
        )
    if not lines:
        raise ValueError("not a KNXnet/IP capture: no line with t and raw")
    return ImportResult(records, skipped)


def import_log(name: str, data: bytes) -> ImportResult:
    """Import ``data`` by the file ``name``: ``.jsonl`` a capture, ``.csv``/``.csv.gz`` a CSV."""
    lower = name.lower()
    if lower.endswith((".jsonl", ".jsonl.gz", ".ndjson")):
        return import_jsonl(data, name)
    return import_csv(data, name)


# Names and the decoded value of a telegram: (source name, group address name, value, dpt).
Labels = Callable[[MonitorRecord], tuple[str, str, str, str]]


def export_csv(records: Iterable[MonitorRecord], labels: Labels | None = None) -> bytes:
    """The group telegrams among ``records`` as CSV (see the module documentation)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for record in records:
        frame = record.frame
        if (
            not record.is_telegram
            or frame is None
            or frame.source is None
            or frame.destination is None
            or not isinstance(
                frame.apci, GroupValueRead | GroupValueWrite | GroupValueResponse
            )
        ):
            continue
        apci = frame.apci
        kind = (
            "read"
            if isinstance(apci, GroupValueRead)
            else "response"
            if isinstance(apci, GroupValueResponse)
            else "write"
        )
        value = getattr(apci, "value", None)
        raw = (
            ""
            if value is None
            else str(value.value)
            if isinstance(value, DPTBinary)
            else bytes(value.value).hex()
        )
        source_name, ga_name, shown, dpt = (
            labels(record) if labels else ("", "", "", "")
        )
        writer.writerow(
            (
                ticks_to_datetime(record.ticks)
                .astimezone(UTC)
                .isoformat(timespec="milliseconds"),
                str(IndividualAddress(frame.source)),
                source_name,
                str(GroupAddress(frame.destination)),
                ga_name,
                kind,
                raw,
                shown,
                dpt,
            )
        )
    return out.getvalue().encode("utf-8")

"""Telegram CSV (also gzip-compressed) and KNXnet/IP capture JSON lines for the monitors."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime

import pytest
from xknx.dpt.payload import DPTArray, DPTBinary
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from editor_gui.plugins.monitor.csvio import (
    CSV_COLUMNS,
    export_csv,
    import_csv,
    import_jsonl,
    import_log,
    telegram_cemi,
)
from editor_gui.plugins.monitor.records import (
    MonitorRecord,
    RecordKind,
    ticks_to_datetime,
)

_CSV = (
    "time,source,source_name,ga,ga_name,type,raw,value,dpt\n"
    "2030-01-02T00:00:00.172+00:00,1.1.5,,1/0/1,Ceiling,write,1,On,1.001\n"
    "2030-01-02T00:00:01.641+00:00,1.1.6,Meter,8/0/1,Voltage,write,436bee14,235.93 V,14.027\n"
    "2030-01-02T00:00:02+01:00,1.1.7,,1/0/2,,read,,,\n"
    "2030-01-02T00:00:03,1.1.8,,1/0/3,,response,05,,\n"
    "2030-01-02T00:00:04,1.1.9,,1/0/4,,response,1,,\n"
    "not a time,1.1.9,,1/0/4,,write,1,,1.001\n"
    "2030-01-02T00:00:05,1.1.9,,1/0/4,,dance,1,,1.001\n"
)


def test_import_csv_reads_every_kind_of_row() -> None:
    result = import_csv(_CSV.encode(), "recorder")
    assert result.skipped == 2
    frames = [r.frame for r in result.records]
    assert all(f is not None for f in frames)
    # Sorted by time: the +01:00 row is an hour earlier than the others.
    assert [str(r.connection_name) for r in result.records] == ["recorder"] * 5
    first = result.records[0]
    assert ticks_to_datetime(first.ticks) == datetime(2030, 1, 1, 23, 0, 2, tzinfo=UTC)
    assert isinstance(frames[0].apci, GroupValueRead)  # type: ignore[union-attr]
    write = frames[1].apci  # type: ignore[union-attr]
    assert isinstance(write, GroupValueWrite) and write.value == DPTBinary(1)
    meter = frames[2].apci  # type: ignore[union-attr]
    assert meter.value == DPTArray(bytes.fromhex("436bee14"))  # type: ignore[union-attr]
    # Without a DPT: an even number of hex digits is octets, an odd number a small value.
    response = frames[3].apci  # type: ignore[union-attr]
    assert isinstance(response, GroupValueResponse)
    assert response.value == DPTArray(b"\x05")
    assert frames[4].apci.value == DPTBinary(1)  # type: ignore[union-attr]


def test_gzip_and_the_file_name_choose_the_reader() -> None:
    packed = gzip.compress(_CSV.encode())
    assert len(import_log("knx_2030-01-02.csv.gz", packed).records) == 5
    assert len(import_log("day.csv", _CSV.encode()).records) == 5


def test_a_csv_without_the_needed_columns_is_refused() -> None:
    with pytest.raises(ValueError, match="time, source, ga and type"):
        import_csv(b"a,b\n1,2\n")


def test_export_writes_what_import_reads() -> None:
    records = import_csv(_CSV.encode()).records
    records.append(
        MonitorRecord(kind=RecordKind.COMMENT, ticks=0, text="not a telegram")
    )
    data = export_csv(records, lambda r: ("Dev", "Name", "value", "1.001"))
    lines = data.decode().splitlines()
    assert lines[0] == ",".join(CSV_COLUMNS)
    assert len(lines) == 6
    assert lines[2].endswith(",1.1.5,Dev,1/0/1,Name,write,1,value,1.001")
    assert ",1.1.6,Dev,8/0/1,Name,write,436bee14," in lines[3]
    assert lines[1].startswith("2030-01-01T23:00:02.000+00:00,1.1.7,")
    again = import_csv(export_csv(records)).records
    assert [r.raw for r in again] == [r.raw for r in records if r.is_telegram]
    assert export_csv(records).decode().splitlines()[2].endswith(",1/0/1,,write,1,,")


def _line(stamp: float, frame: bytes) -> str:
    return json.dumps({"t": stamp, "dir": "g2c", "raw": frame.hex()})


def test_import_jsonl_takes_the_cemi_of_tunnelling_and_routing_frames() -> None:
    cemi = telegram_cemi("1.1.5", "1/0/1", GroupValueWrite(DPTBinary(1)))
    tunnelling = bytes.fromhex("06100420") + (10 + len(cemi)).to_bytes(2, "big")
    tunnelling += bytes.fromhex("04090100") + cemi
    routing = bytes.fromhex("06100530") + (6 + len(cemi)).to_bytes(2, "big") + cemi
    ack = bytes.fromhex("06100421000a04090100")
    text = "\n".join(
        [
            json.dumps({"t": 1.0, "kind": "start"}),
            _line(2.0, tunnelling),
            _line(3.0, routing),
            _line(4.0, ack),
            "garbage",
            "",
        ]
    )
    result = import_jsonl(text.encode(), "tap")
    assert [r.raw for r in result.records] == [cemi, cemi]
    assert ticks_to_datetime(result.records[0].ticks) == datetime(
        1970, 1, 1, 0, 0, 2, tzinfo=UTC
    )
    assert result.skipped == 3
    assert (
        import_log("tap.jsonl", text.encode()).records[0].connection_name == "tap.jsonl"
    )


def test_a_file_without_capture_lines_is_refused() -> None:
    with pytest.raises(ValueError, match="not a KNXnet/IP capture"):
        import_jsonl(b"[1, 2]\nnot json\n")

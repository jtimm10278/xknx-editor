"""The "Simulate Download" window: its texts, filters, CSV and a headless render (made-up
frames), and reading a device's stored image."""

from __future__ import annotations

import base64
import struct
import zlib
from collections.abc import Iterator
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import dry_run_window as window_module
from editor_gui.plugins.project.ui.dry_run_window import (
    DryRunWindow,
    filter_frames,
    frame_rows,
    frame_target,
    frames_csv,
    summary_lines,
)
from editor_gui.programming import stored_image
from xknxeditor.download.dry_run import (
    ByteRange,
    DryRunResult,
    FrameKind,
    ImageComparison,
    PropertyDifference,
    RecordedFrame,
)
from xknxeditor.download.scope import DownloadScope


def _frame(index: int, kind: FrameKind, **kw: Any) -> RecordedFrame:
    return RecordedFrame(
        index=index, connection=1, kind=kind, service=kind.value, answered=True, **kw
    )


def _result(**kw: Any) -> DryRunResult:
    fields: dict[str, Any] = {
        "frames": [
            _frame(1, FrameKind.CONNECT),
            _frame(
                2,
                FrameKind.MEMORY_WRITE,
                address=0x4000,
                data=b"\x01\xab",
                region="application program",
                segment_object=4,
                segment_offset=8,
            ),
            _frame(
                3,
                FrameKind.PROPERTY_WRITE,
                object_index=4,
                property_id=5,
                start_index=2,
                data=b"\x02",
            ),
            _frame(4, FrameKind.PROPERTY_READ, object_index=0, property_id=14),
            _frame(5, FrameKind.DISCONNECT),
        ],
        "comparison": ImageComparison(has_stored_image=False),
        "scope": DownloadScope.FULL,
        "memory_written": [(0x4000, b"\x01\xab")],
    } | kw
    return DryRunResult(**fields)


def test_frame_texts_and_csv() -> None:
    frames = _result().frames
    assert frame_target(frames[1]) == "0x4000 (+8)"
    assert frame_target(frames[2]) == "4/5[2]"
    assert frame_target(frames[3]) == "0/14"
    assert frame_target(frames[0]) == ""
    assert frame_rows(frames)[1] == [
        "2",
        "1",
        "memory_write",
        "0x4000 (+8)",
        "application program",
        "01 AB",
    ]
    lines = frames_csv(frames).splitlines()
    assert lines[0] == "index,connection,service,target,region,data"
    assert len(lines) == 6


def test_filter_frames() -> None:
    frames = _result().frames
    assert [f.index for f in filter_frames(frames, "", True)] == [2, 3]
    assert [f.index for f in filter_frames(frames, "application", False)] == [2]
    assert filter_frames(frames, "", False) == frames


def test_summary_lines() -> None:
    plain = summary_lines(_result())
    assert plain[0] == (
        "info",
        S.DRY_RUN_SUMMARY.format(frames=5, writes=2, bytes=2, connections=1),
    )
    assert plain[-1] == ("info", S.DRY_RUN_NO_STORED_IMAGE)
    compared = summary_lines(
        _result(
            error="LoadStateError: stopped",
            comparison=ImageComparison(
                has_stored_image=True,
                compared_bytes=10,
                differing_bytes=2,
                not_in_stored_image=3,
                differences=[
                    ByteRange(0, b"\x01\x02", b"\x00\x00", "application program")
                ],
                compared_properties=1,
                property_differences=[PropertyDifference(3, 13, b"\x01", b"\x02")],
            ),
        )
    )
    assert ("warning", S.DRY_RUN_STOPPED.format(error="LoadStateError: stopped")) in (
        compared
    )
    assert (
        "warning",
        S.DRY_RUN_COMPARED.format(compared=10, differing=2, missing=3),
    ) in compared
    assert (
        "warning",
        S.DRY_RUN_REGION.format(region="application program", count=2),
    ) in compared
    assert compared[-1] == (
        "warning",
        S.DRY_RUN_PROPERTIES.format(compared=1, differing=1),
    )
    same = summary_lines(
        _result(comparison=ImageComparison(has_stored_image=True, compared_bytes=4))
    )
    assert same[-1] == (
        "success",
        S.DRY_RUN_COMPARED.format(compared=4, differing=0, missing=0),
    )


def test_stored_image_from_the_project_attribute() -> None:
    body = struct.pack("<Ii", 0x00010001, 1) + struct.pack("<IHHII", 1, 0, 0, 0x100, 1)
    body += b"\x01\x05"
    compressor = zlib.compressobj(wbits=-15)
    encoded = base64.b64encode(compressor.compress(body) + compressor.flush()).decode()
    image = stored_image(encoded)
    assert image is not None and image.segments[0].data == b"\x05"
    assert stored_image(None) is None
    assert stored_image("") is None
    assert stored_image("not base64!") is None
    assert stored_image(base64.b64encode(b"junk").decode()) is None


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frames(window: DryRunWindow, count: int = 2) -> None:
    for _ in range(count):
        imgui.new_frame()
        window.render()
        imgui.end_frame()


def test_render_headless(context: None, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(
        window_module.imgui, "text_disabled", lambda text: shown.append(text)
    )
    colored: list[str] = []
    monkeypatch.setattr(
        window_module.imgui, "text_colored", lambda _c, text: colored.append(text)
    )
    device = cast(
        "Any",
        SimpleNamespace(individual_address="1.1.1", display_name="Demo", name="Demo"),
    )
    pending: Future[DryRunResult] = Future()
    window = DryRunWindow(lambda _d, _s: pending)
    _frames(window, 1)  # not opened: nothing shown
    window.open(device, DownloadScope.FULL)
    assert window.running
    _frames(window, 1)
    assert S.DRY_RUN_RUNNING in shown
    pending.set_result(_result())
    _frames(window)
    assert not window.running
    assert any("Frames" in text or "5" in text for text in colored)
    window._only_writes = True  # pyright: ignore[reportPrivateUsage]
    window._filter = "nothing like this"  # pyright: ignore[reportPrivateUsage]
    shown.clear()
    _frames(window, 1)
    assert S.DRY_RUN_NO_FRAMES in shown
    assert window._file_name() == "1.1.1-full-dry-run.csv"  # pyright: ignore[reportPrivateUsage]

    failed: Future[DryRunResult] = Future()
    failed.set_exception(ValueError("no application"))
    broken = DryRunWindow(lambda _d, _s: failed)
    broken.open(device, DownloadScope.FULL)
    colored.clear()
    _frames(broken, 1)
    assert S.DRY_RUN_FAILED.format(error="no application") in colored

    def refuse(_d: Any, _s: Any) -> Future[DryRunResult]:
        raise ValueError(S.DRY_RUN_ADDRESS_ONLY)

    refused = DryRunWindow(refuse)
    refused.open(device, DownloadScope.FULL)
    colored.clear()
    _frames(refused, 1)
    assert S.DRY_RUN_FAILED.format(error=S.DRY_RUN_ADDRESS_ONLY) in colored


def test_export_writes_the_csv(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "frames.csv"

    class Dialog:
        def ready(self) -> bool:
            return True

        def result(self) -> str:
            return str(target)

    done: Future[DryRunResult] = Future()
    done.set_result(_result())
    window = DryRunWindow(lambda _d, _s: done)
    device = cast("Any", SimpleNamespace(individual_address="1.1.1"))
    window.open(device, DownloadScope.FULL)
    window._save_dialog = cast("Any", Dialog())  # pyright: ignore[reportPrivateUsage]
    window._poll()  # pyright: ignore[reportPrivateUsage]
    assert target.read_text(encoding="utf-8") == frames_csv(_result().frames)

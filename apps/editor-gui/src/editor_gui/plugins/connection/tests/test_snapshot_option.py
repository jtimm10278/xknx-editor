"""The snapshot before a download: the service hands its folder and the override to the
download while snapshots are on, and nothing while they are off; a restore follows the guards."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

import editor_gui.settings as app_settings
import editor_gui.snapshots as snapshots
from xknxeditor.download.scope import DownloadScope
from xknxeditor.download.snapshot import Snapshot, save_snapshot

from .test_route_apdu import _DEVICE, _service  # pyright: ignore[reportPrivateUsage]


@pytest.fixture(autouse=True)
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snapshots, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)


def test_on_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert snapshots.snapshots_enabled()
    service, calls = _service(monkeypatch, {"1.3.39"})
    service.program_device(cast(Any, _DEVICE), DownloadScope.FULL)
    (kwargs,) = calls
    assert kwargs["snapshot_folder"] == tmp_path / "snapshots"
    assert kwargs["snapshot_override"] is False


def test_override_and_off(monkeypatch: pytest.MonkeyPatch) -> None:
    service, calls = _service(monkeypatch, {"1.3.39"})
    service.program_device(
        cast(Any, _DEVICE), DownloadScope.FULL, snapshot_override=True
    )
    assert calls[-1]["snapshot_override"] is True
    snapshots.set_snapshots_enabled(False)
    assert not snapshots.snapshots_enabled()
    service.program_device(cast(Any, _DEVICE), DownloadScope.FULL)
    assert calls[-1]["snapshot_folder"] is None


def test_restore_is_guarded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = save_snapshot(
        Snapshot(address="1.3.39", taken="2026-11-13T21:00:00+00:00", scope="FULL"),
        tmp_path / "snapshots",
    )
    service, _calls = _service(monkeypatch, {"1.3.39"})
    service.block_bus_writes = True
    assert service.restore_snapshot(path) is None

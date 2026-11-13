"""Device snapshots before a download: where they are kept and whether they are on.

The snapshot itself is :mod:`xknxeditor.download.snapshot`; the connection service takes one
before every download while :func:`snapshots_enabled` (on by default) and refuses the download
when it fails, unless the user overrides it for that download.
"""

from __future__ import annotations

from pathlib import Path

from editor_gui.settings import config_dir, load_settings, save_settings

_NAME = "snapshots"


def snapshot_folder() -> Path:
    """The folder the snapshots are kept in: ``<config>/snapshots/<address>/<time>.json``."""
    return config_dir() / "snapshots"


def snapshots_enabled() -> bool:
    """Whether a snapshot is taken before every download (default: on)."""
    return bool(load_settings(_NAME).get("before_download", True))


def set_snapshots_enabled(enabled: bool) -> None:
    data = load_settings(_NAME)
    data["before_download"] = bool(enabled)
    save_settings(_NAME, data)

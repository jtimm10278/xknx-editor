"""``export_knxproj(as_copy=True)``: a copy ETS keeps beside the original."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.core.knxproj_export import copy_project_id


def _project(tmp_path: Path) -> tuple[Path, str]:
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-0A1B")
    svc.close(pid)
    return src, pid


def _members(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def _guid(path: Path, pid: str) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read(f"{pid}/project.xml").decode("utf-8")
    found = re.search(r'Guid="([^"]+)"', xml)
    assert found is not None
    return found.group(1)


def test_copy_project_id_is_a_new_valid_id() -> None:
    ids = {copy_project_id("P-0A1B") for _ in range(200)}
    assert "P-0A1B" not in ids
    assert all(re.fullmatch(r"P-[0-9A-F]{4}", pid) for pid in ids)
    assert "P-0000" not in ids
    assert len(ids) > 150  # random, not counted up


def test_copy_has_new_id_and_guid(tmp_path: Path) -> None:
    src, pid = _project(tmp_path)
    plain = tmp_path / "plain.knxproj"
    copy = tmp_path / "copy.knxproj"
    first = export_knxproj(src, plain)
    second = export_knxproj(src, copy, as_copy=True, project_name="House (copy)")
    assert first.project_id == pid
    assert second.project_id != pid
    assert re.fullmatch(r"P-[0-9A-F]{4}", second.project_id)
    names = _members(copy)
    assert f"{second.project_id}/project.xml" in names
    assert f"{second.project_id}.signature" in names
    assert f"{second.project_id}.info" in names
    assert not any(name.startswith(pid) for name in names)
    assert _guid(copy, second.project_id) != _guid(plain, pid)
    assert second.project_name == "House (copy)"


def test_copy_leaves_the_source_alone(tmp_path: Path) -> None:
    src, pid = _project(tmp_path)
    export_knxproj(src, tmp_path / "copy.knxproj", as_copy=True)
    again = export_knxproj(src, tmp_path / "again.knxproj")
    assert again.project_id == pid


def test_copy_reads_back(tmp_path: Path) -> None:
    src, _ = _project(tmp_path)
    copy = tmp_path / "copy.knxproj"
    result = export_knxproj(src, copy, as_copy=True)
    round_path = tmp_path / "round.xknx"
    import_knxproj(copy, round_path)
    svc = ProjectService()
    opened = svc.open(round_path)
    assert svc.project(opened).original_project_id == result.project_id

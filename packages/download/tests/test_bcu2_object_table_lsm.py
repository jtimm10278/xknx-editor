"""BCU 2 load procedures: the group object table's LSM 5 step (Crabtree 7172, app 9084)."""

from __future__ import annotations

import logging

import pytest

from xknxeditor.download import load_state
from xknxeditor.download.merge import (
    _finalize_controls,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_disconnect_t import LdCtrlDisconnect
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ctrl2_t import LdCtrlTaskCtrl2
from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import LdCtrlTaskSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload

_OBJECT_TABLE = (
    436  # 0x1B4, where ETS and this download both put the 7172's object table
)


def _crabtree_7172() -> list[object]:
    """The load procedure of M-0021_A-9084-02-0F47-O0001, LSM 3 shortened."""
    return [
        LdCtrlConnect(),
        LdCtrlUnload(lsm_idx=1),
        LdCtrlUnload(lsm_idx=2),
        LdCtrlUnload(lsm_idx=3),
        LdCtrlLoad(lsm_idx=3),
        LdCtrlAbsSegment(
            lsm_idx=3,
            seg_type=0,
            address=_OBJECT_TABLE,
            size=76,
            access=51,
            mem_type=3,
            seg_flags=0,
        ),
        LdCtrlTaskCtrl2(
            lsm_idx=3, callback=20609, address=_OBJECT_TABLE, seg0=2418, seg1=2448
        ),
        LdCtrlLoadCompleted(lsm_idx=3),
        LdCtrlTaskSegment(lsm_idx=5, address=_OBJECT_TABLE),
        LdCtrlDisconnect(),
    ]


def _finalize(mask: str) -> list[object]:
    return _finalize_controls(_crabtree_7172(), None, mask, DownloadScope.FULL, False)


@pytest.mark.parametrize("mask", ["MV-0020", "MV-0021"])
def test_bcu2_leaves_out_lsm5_and_says_so(
    mask: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="xknxeditor.download.merge"):
        controls = _finalize(mask)

    assert not any(
        isinstance(c, LdCtrlTaskSegment) and c.lsm_idx == 5 for c in controls
    )
    assert f"LdCtrlTaskSegment LSM 5 address 436 (0x01B4) left out, mask {mask}" in (
        caplog.text
    )
    # The table address still reaches the device: the application's TaskCtrl2 carries it.
    (task_ctrl2,) = [c for c in controls if isinstance(c, LdCtrlTaskCtrl2)]
    assert task_ctrl2.address == _OBJECT_TABLE


def test_task_ctrl2_event_matches_the_7172_download() -> None:
    """The LSM 3 event a download of the 7172 sends."""
    event = load_state.task_control_2(20609, _OBJECT_TABLE, 2418, 2448)
    assert event == bytes.fromhex("0305508101B409720990")


def test_mask_with_an_object_table_lsm_keeps_the_step(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="xknxeditor.download.merge"):
        controls = _finalize("MV-07B0")

    assert any(isinstance(c, LdCtrlTaskSegment) and c.lsm_idx == 5 for c in controls)
    assert "left out" not in caplog.text

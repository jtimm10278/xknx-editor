"""The load controls a Siemens Generic Plug-in runs in a download, as ETS hands it the merge points
of the mask's procedure (MV-07B0; recorded on an N 512/11 switching actuator, 9820-03):

- merge id 1, before the mask's procedure unloads anything: the application object (LSM 4) is
  unloaded, loaded and allocated with the description's code segment size, then the parameter
  memory is written in 10-octet frames without read-back, only the bytes a parameter owns;
- merge id 6, after the mask's procedure: the application object is set up again the same way and
  completed, then property 205 of object 6 is written with 10h;
- merge id 7: nothing.

Built from the plain-XML description and the stored settings only (see :mod:`.encoder`).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .encoder import MemoryImage

# The application program's load state machine.
_APPLICATION_LSM = 4
# Octets per parameter memory frame, as the plug-in sends them.
PARAMETER_FRAME = 10
# The plug-in's last write before the restart: object 6, property 205 = 10h.
_FINISH_OBJECT, _FINISH_PROPERTY, _FINISH_VALUE = 6, 205, b"\x10"


def _set_up_application(size: int) -> list[object]:
    return [
        LdCtrlUnload(lsm_idx=_APPLICATION_LSM),
        LdCtrlLoad(lsm_idx=_APPLICATION_LSM),
        LdCtrlRelSegment(lsm_idx=_APPLICATION_LSM, size=size, mode=0, fill=0),
    ]


def plugin_merge_controls(
    image: MemoryImage, alloc_length: int
) -> Mapping[int, tuple[object, ...]]:
    """The plug-in's load controls by merge id, for ``DownloadImage.plugin_load_controls``."""
    parameters = [
        LdCtrlWriteMem(address=address, size=len(data), verify=False, inline_data=data)
        for address, data in image.runs(PARAMETER_FRAME)
    ]
    finish = LdCtrlWriteProp(
        obj_idx=_FINISH_OBJECT,
        prop_id=_FINISH_PROPERTY,
        start_element=1,
        count=1,
        verify=True,
        inline_data=_FINISH_VALUE,
    )
    return MappingProxyType(
        {
            1: (*_set_up_application(alloc_length), *parameters),
            6: (
                *_set_up_application(alloc_length),
                LdCtrlLoadCompleted(lsm_idx=_APPLICATION_LSM),
                finish,
            ),
            7: (),
        }
    )

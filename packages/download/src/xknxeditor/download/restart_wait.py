"""Wait for a device to answer again after the restart that ends a download.

The Restart control at the end of a load procedure reboots the device; for a few seconds
it acknowledges nothing, so a read straight afterwards fails with "no ACK" although the
download succeeded. :func:`wait_until_answering` reconnects with a growing pause until a
device descriptor read gets an answer, so a following read-back or verify can run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionError

from .errors import DeviceNotAnsweringError, DownloadError
from .programmer import DeviceProgrammer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .programmer import ConnectionManager

logger = logging.getLogger(__name__)

# Default time a device may take to answer after its restart.
RESTART_WAIT_S = 15.0
# Pause before each new attempt; the last value repeats.
_PAUSES_S: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)
# The clock and the pause, kept here so tests can run the wait without real time passing.
_monotonic = time.monotonic
_sleep = asyncio.sleep


async def wait_until_answering(
    open_manager: Callable[[], ConnectionManager],
    *,
    timeout: float = RESTART_WAIT_S,
    pauses: Sequence[float] = _PAUSES_S,
) -> float:
    """Read the device descriptor until the device answers; return the seconds it took.

    Each attempt opens a fresh connection from ``open_manager`` and closes it again. Raises
    :class:`.errors.DeviceNotAnsweringError` when no attempt succeeds within ``timeout``.
    """
    started = _monotonic()
    attempt = 0
    while True:
        manager = open_manager()
        try:
            connection = await manager.open()
            await DeviceProgrammer(connection).read_device_descriptor()
            waited = _monotonic() - started
            logger.debug("device answered %.1f s after the restart", waited)
            return waited
        except (ManagementConnectionError, DownloadError, TimeoutError) as exc:
            last: Exception = exc
        finally:
            with contextlib.suppress(ManagementConnectionError, DownloadError):
                await manager.close()
        pause = pauses[min(attempt, len(pauses) - 1)]
        attempt += 1
        if _monotonic() - started + pause > timeout:
            raise DeviceNotAnsweringError(
                f"the device did not answer within {timeout:.0f} s after its restart: {last}"
            ) from last
        await _sleep(pause)

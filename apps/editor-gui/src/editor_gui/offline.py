"""Offline mode: while it is on, the editor fetches nothing from the internet.

No update check, master data refresh, online catalogue, alternative catalogue, documentation lookup,
product download or myKNX call leaves the computer. Addresses on this computer (``localhost``,
``127.0.0.0/8``, ``::1``) still work, and so does the KNX connection, which is not a web request
(Settings > Connection decides about that).

Every ``urllib`` request passes the handler :func:`install` adds to the global opener, so the code
that uses ``urlopen`` needs no change. The few ``httpx`` clients call :func:`check` before they
send. A refused request raises :class:`OfflineError`, a ``URLError`` (so an ``OSError``), which
the callers already handle as "no network": the master data falls back to its cache, a lookup
finds nothing, a catalogue shows its error.
"""

from __future__ import annotations

import ipaddress
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse

_lock = threading.Lock()
_offline = False
_installed = False


class OfflineError(urllib.error.URLError):
    """A request to the internet refused because offline mode is on."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"offline mode is on: not fetching {_host(url) or url}")


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def is_local(url: str) -> bool:
    """Whether ``url`` points at this computer (which offline mode leaves alone)."""
    host = _host(url).rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_offline() -> bool:
    return _offline


def set_offline(offline: bool) -> None:
    """Turn offline mode on or off (for the whole program, all threads)."""
    global _offline
    install()
    _offline = bool(offline)


def check(url: str) -> None:
    """Raise :class:`OfflineError` when offline mode is on and ``url`` is not on this computer."""
    if _offline and not is_local(url):
        raise OfflineError(url)


class _OfflineHandler(urllib.request.BaseHandler):
    """Refuses requests while offline; runs before every request (redirect hops included)."""

    handler_order = 100

    def http_request(self, req: urllib.request.Request) -> urllib.request.Request:
        check(req.full_url)
        return req

    https_request = http_request
    ftp_request = http_request


def install() -> None:
    """Add the offline check to the global ``urllib`` opener (once; harmless while online)."""
    global _installed
    with _lock:
        if _installed:
            return
        urllib.request.install_opener(urllib.request.build_opener(_OfflineHandler))
        _installed = True

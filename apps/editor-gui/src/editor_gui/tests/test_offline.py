"""Offline mode: nothing leaves the computer while it is on (offline.py)."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from editor_gui import main, offline


@pytest.fixture(autouse=True)
def _online_again() -> Iterator[None]:
    yield
    offline.set_offline(False)


def test_check_refuses_internet_only_while_offline() -> None:
    offline.check("https://update.knx.org/data.xml")
    offline.set_offline(True)
    with pytest.raises(offline.OfflineError, match=r"update\.knx\.org"):
        offline.check("https://update.knx.org/data.xml")
    offline.set_offline(False)
    offline.check("https://update.knx.org/data.xml")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8770/",
        "http://127.0.0.1:3000/x",
        "http://127.8.9.1/",
        "http://[::1]:80/",
        "http://editor.localhost/",
    ],
)
def test_this_computer_stays_reachable(url: str) -> None:
    offline.set_offline(True)
    offline.check(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/repos/x/y/releases",
        "http://192.168.1.10/",
        "https://localhost.example.com/",
        "not a url",
    ],
)
def test_everything_else_is_refused(url: str) -> None:
    offline.set_offline(True)
    with pytest.raises(offline.OfflineError):
        offline.check(url)


def test_offline_error_is_a_network_error() -> None:
    """Callers already treat ``OSError``/``URLError`` as "no network" (e.g. the master data falls
    back to its cache), so the refusal needs no new handling there."""
    error = offline.OfflineError("https://example.com/")
    assert isinstance(error, urllib.error.URLError)
    assert isinstance(error, OSError)


def test_urlopen_is_refused_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_connection(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("tried to connect")

    monkeypatch.setattr("socket.create_connection", no_connection)
    offline.set_offline(True)
    with pytest.raises(offline.OfflineError):
        urllib.request.urlopen("https://update.knx.org/x", timeout=1)
    with pytest.raises(offline.OfflineError):
        urllib.request.urlopen(urllib.request.Request("http://example.com/"), timeout=1)


def test_urlopen_is_not_touched_while_online(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*_args: object, **_kwargs: object) -> object:
        raise ConnectionRefusedError("no network in tests")

    monkeypatch.setattr("socket.create_connection", refused)
    offline.set_offline(False)
    with pytest.raises(urllib.error.URLError) as caught:
        urllib.request.urlopen("http://example.com/", timeout=1)
    assert not isinstance(caught.value, offline.OfflineError)


def test_redirect_hops_are_checked_too() -> None:
    """The handler runs for every request the opener makes, redirects included."""
    handler = offline._OfflineHandler()  # pyright: ignore[reportPrivateUsage]
    offline.set_offline(True)
    with pytest.raises(offline.OfflineError):
        handler.https_request(urllib.request.Request("https://cdn.example.com/a.zip"))
    request = urllib.request.Request("http://127.0.0.1/a")
    assert handler.http_request(request) is request


def test_offline_flag() -> None:
    assert main.offline_flag(["editor", "--offline"])
    assert not main.offline_flag(["editor"])
    assert not main.offline_flag(["--offline"])  # the program name, not an option


def test_master_data_falls_back_to_its_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from editor_gui import master_data

    cache = tmp_path / "master.xml"
    cache.write_bytes(b"<cached/>")
    monkeypatch.setattr(master_data, "_cache_path", lambda: cache)
    monkeypatch.setattr(master_data, "_is_stale", lambda _cache: True)
    offline.set_offline(True)
    assert master_data._load_bytes() == (b"<cached/>", "cached")  # pyright: ignore[reportPrivateUsage]


def test_update_check_fails_offline() -> None:
    from editor_gui.update_check import UpdateCheckError, fetch_update

    offline.set_offline(True)
    with pytest.raises(UpdateCheckError, match="offline"):
        fetch_update("0.0.1")


def test_alternative_catalog_refuses_offline(tmp_path: Path) -> None:
    import httpx

    from editor_gui.plugins.catalog.alternative import (
        AlternativeCatalogClient,
        AlternativeCatalogError,
    )

    def never(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("sent a request while offline")

    client = AlternativeCatalogClient(tmp_path, transport=httpx.MockTransport(never))
    offline.set_offline(True)
    with pytest.raises(AlternativeCatalogError, match="offline"):
        client.refresh()


def test_mcp_download_refuses_offline() -> None:
    from fastmcp.exceptions import ToolError

    from editor_gui.plugins.mcp.tools import catalog

    offline.set_offline(True)
    with pytest.raises(ToolError, match="offline"):
        catalog._http_get("https://www.mdt.de/manual.pdf")  # pyright: ignore[reportPrivateUsage]


def test_doc_lookup_finds_nothing_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from editor_gui import doc_links

    def no_client(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("opened an HTTP client while offline")

    monkeypatch.setattr("httpx.Client", no_client)
    monkeypatch.setattr("httpx.stream", no_client)
    offline.set_offline(True)
    assert doc_links._get_text("https://www.mdt.de/") is None  # pyright: ignore[reportPrivateUsage]
    assert not doc_links._pdf_matches("https://www.mdt.de/a.pdf", "X")  # pyright: ignore[reportPrivateUsage]


_SRC = Path(__file__).resolve().parents[1]
_HTTPX_SEND = re.compile(r"httpx\.(Client|stream|get|post|AsyncClient)\(")


def test_every_httpx_client_checks_offline_mode() -> None:
    """``urllib`` is covered by the global opener; an ``httpx`` client must call
    ``offline.check`` itself. A new one without it fails here."""
    missing = [
        str(path.relative_to(_SRC))
        for path in _SRC.rglob("*.py")
        if "tests" not in path.parts
        and _HTTPX_SEND.search(text := path.read_text(encoding="utf-8"))
        and "offline.check(" not in text
    ]
    assert missing == []


def test_no_urlopen_with_its_own_opener() -> None:
    """``urlopen(..., context=...)`` builds a private opener that skips the offline check."""
    roots = [_SRC, *(_SRC.parents[3] / "packages").glob("*/src")]
    found = [
        str(path)
        for root in roots
        for path in root.rglob("*.py")
        if "tests" not in path.parts
        and re.search(r"urlopen\([^)]*context=", path.read_text(encoding="utf-8"))
    ]
    assert found == []

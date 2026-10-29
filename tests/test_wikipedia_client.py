"""Wikipedia client: offline tests with a mocked network, plus one live check that only runs when online."""
import os
import socket

import pytest
import requests

from src.utils.wikipedia_client import WikipediaClient


class FakeResp:
    def __init__(self, code=200, data=None):
        self.status_code, self._data = code, data or {}

    def json(self):
        return self._data


def _client(tmp_path, monkeypatch, handler):
    client = WikipediaClient(cache_dir=tmp_path)
    monkeypatch.setattr(client.session, "get", handler)
    return client


def test_search_ok_and_cached(tmp_path, monkeypatch):
    calls = []

    def handler(url, params=None, timeout=None):
        calls.append(params)
        return FakeResp(200, {"query": {"search": [{"title": "Rust"}]}})

    client = _client(tmp_path, monkeypatch, handler)
    first = client.search("rust", limit=1)
    assert first["success"] and first["data"]["query"]["search"][0]["title"] == "Rust"
    assert calls[0]["srsearch"] == "rust"
    assert client.search("rust", limit=1)["success"]
    assert len(calls) == 1, "second call should come from the cache"


def test_http_error_and_offline_are_reported_not_raised(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, lambda *a, **k: FakeResp(503))
    result = client.search("x")
    assert result["success"] is False and result["error"] == "HTTP 503"

    def offline(*a, **k):
        raise requests.exceptions.ConnectionError("no network")

    client = _client(tmp_path, monkeypatch, offline)
    result = client.get_page_extract("Anything")
    assert not result["success"] and "Request failed" in result["error"]


def _online() -> bool:
    if os.environ.get("OXIDUS_OFFLINE_TESTS"):
        return False
    try:
        socket.create_connection(("en.wikipedia.org", 443), timeout=2).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _online(), reason="no internet (live Wikipedia check)")
def test_live_wikipedia_search(tmp_path):
    result = WikipediaClient(cache_dir=tmp_path).search("Python programming language", limit=1)
    assert result["success"], result.get("error")
    assert result["data"]["query"]["search"]

"""LM Studio settings + connection checks (no network: requests is mocked)."""
import json

import pytest
import requests

from src.utils import lm_settings
from src.utils import lm_studio_client as lmc


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(lm_settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(lm_settings, "SETTINGS_FILE", tmp_path / "lm_studio.json")
    monkeypatch.setattr(lm_settings, "KEY_FILE", tmp_path / "lmstudio_api_key.txt")
    monkeypatch.setattr(lm_settings, "TOKEN_FILE", tmp_path / "lm_studio_token.txt")
    for k in ("OXIDUS_LM_URL", "OXIDUS_LM_MODEL") + lm_settings.ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return tmp_path


class Resp:
    def __init__(self, code, data=None):
        self.status_code, self._data, self.text = code, data or {}, json.dumps(data or {})

    def json(self):
        return self._data


def test_defaults_and_precedence(cfg, monkeypatch):
    assert lm_settings.base_url() == "http://127.0.0.1:1234"
    assert lm_settings.model() == "auto"
    (cfg / "lm_studio.json").write_text(json.dumps({"base_url": "http://x:1", "model": "google/gemma-4-12b"}))
    assert lm_settings.base_url() == "http://x:1"
    assert lm_settings.model() == "google/gemma-4-12b"
    monkeypatch.setenv("OXIDUS_LM_MODEL", "other")
    assert lm_settings.model() == "other"


def test_key_file_env_and_public_never_leaks(cfg, monkeypatch):
    assert lm_settings.token() is None
    (cfg / "lmstudio_api_key.txt").write_text("secret-file\n")
    assert lm_settings.token() == "secret-file"
    monkeypatch.setenv("LMSTUDIO_API_KEY", "secret-env")
    assert lm_settings.token() == "secret-env"
    pub = lm_settings.public()
    assert pub["token_set"] is True
    assert "secret" not in json.dumps(pub)


def test_save_validates_and_clears(cfg):
    with pytest.raises(ValueError):
        lm_settings.save(base_url_value="ftp://nope")
    lm_settings.save(model_value="google/gemma-4-12b", token_value="abc")
    assert lm_settings.token() == "abc"
    lm_settings.save(clear_token=True)
    assert lm_settings.token() is None


def test_bearer_header_sent(cfg, monkeypatch):
    (cfg / "lmstudio_api_key.txt").write_text("k123")
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen[url] = headers
        if url.endswith("/api/v0/models"):
            return Resp(200, {"data": [{"id": "google/gemma-4-12b", "type": "llm", "state": "loaded"}]})
        return Resp(200, {"data": [{"id": "google/gemma-4-12b"}, {"id": "text-embedding-nomic-embed-text-v1.5"}]})

    monkeypatch.setattr(lmc.requests, "get", fake_get)
    client = lmc.LMStudioClient()
    info = client.diagnose()
    assert info["ok"] and info["model"] == "google/gemma-4-12b"
    assert all(h.get("Authorization") == "Bearer k123" for h in seen.values())


def test_auto_never_picks_embedding_or_missing_20b(cfg, monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/api/v0/models"):
            return Resp(200, {"data": []})
        return Resp(200, {"data": [{"id": "text-embedding-nomic-embed-text-v1.5"}, {"id": "google/gemma-4-12b"}]})

    monkeypatch.setattr(lmc.requests, "get", fake_get)
    client = lmc.LMStudioClient()
    client.ensure_model_selected()
    assert client.model == "google/gemma-4-12b"


def test_diagnose_server_off_and_auth_required(cfg, monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(lmc.requests, "get", boom)
    info = lmc.LMStudioClient().diagnose()
    assert not info["ok"] and info.get("server") == "off"
    assert "server is off" in info["message"]

    monkeypatch.setattr(lmc.requests, "get", lambda *a, **k: Resp(401, {"error": "token required"}))
    info = lmc.LMStudioClient().diagnose()
    assert info["reachable"] and info["auth"] == "required"


class StreamResp:
    status_code = 200
    text = ""

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sse(**delta):
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


def test_round_trip_streams_and_no_key_when_unset(cfg, monkeypatch):
    monkeypatch.setattr(lmc.requests, "get", lambda url, headers=None, timeout=None: Resp(
        200, {"data": [{"id": "google/gemma-4-12b", "type": "llm", "state": "loaded"}]}))
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None, stream=False):
        seen.update(headers=headers, stream=stream, body=json)
        return StreamResp([_sse(reasoning_content="thinking..."), _sse(content="Oxidus "), _sse(content="link OK"),
                           "data: [DONE]"])

    monkeypatch.setattr(lmc.requests, "post", fake_post)
    result = lmc.LMStudioClient().test_round_trip()
    assert result["ok"] and result["reply"] == "Oxidus link OK"
    assert seen["stream"] and seen["body"]["stream"] is True
    assert "Authorization" not in (seen["headers"] or {})


def test_round_trip_reports_thinking_only(cfg, monkeypatch):
    monkeypatch.setattr(lmc.requests, "get", lambda url, headers=None, timeout=None: Resp(
        200, {"data": [{"id": "google/gemma-4-12b"}]}))
    monkeypatch.setattr(lmc.requests, "post", lambda *a, **k: StreamResp([_sse(reasoning_content="hmm"), "data: [DONE]"]))
    result = lmc.LMStudioClient().test_round_trip()
    assert not result["ok"] and "thinking" in result["message"]

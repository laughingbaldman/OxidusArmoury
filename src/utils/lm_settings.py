"""Where Oxidus finds LM Studio: address, model, and API token.

Order of precedence (first one set wins):
  address: OXIDUS_LM_URL env  -> config/lm_studio.json "base_url" -> http://127.0.0.1:1234
  model:   OXIDUS_LM_MODEL env -> config/lm_studio.json "model"    -> "auto" (whatever LM Studio has loaded)
  token:   LMSTUDIO_API_KEY / LM_API_TOKEN / OXIDUS_LM_TOKEN env -> config/lmstudio_api_key.txt -> config/lm_studio_token.txt -> none

config/lm_studio.json and config/lm_studio_token.txt are machine-local and git-ignored.
The token is never returned by any API; only whether one is set.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "lm_studio.json"
KEY_FILE = CONFIG_DIR / "lmstudio_api_key.txt"      # primary (Jonathon's key lives here)
TOKEN_FILE = CONFIG_DIR / "lm_studio_token.txt"    # older name, still read
DEFAULT_URL = "http://127.0.0.1:1234"


def _read_json() -> Dict[str, Any]:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def base_url() -> str:
    url = os.environ.get("OXIDUS_LM_URL", "").strip() or str(_read_json().get("base_url") or "").strip()
    return (url or DEFAULT_URL).rstrip("/")


def model() -> str:
    return os.environ.get("OXIDUS_LM_MODEL", "").strip() or str(_read_json().get("model") or "").strip() or "auto"


ENV_KEYS = ("LMSTUDIO_API_KEY", "LM_API_TOKEN", "OXIDUS_LM_TOKEN")


def _file_value(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8-sig").strip() or None
    except OSError:
        return None


def _token_and_source():
    for key in ENV_KEYS:
        val = os.environ.get(key, "").strip()
        if val:
            return val, f"environment variable {key}"
    for path in (KEY_FILE, TOKEN_FILE):
        val = _file_value(path)
        if val:
            return val, f"config/{path.name}"
    return None, None


def token() -> Optional[str]:
    """The LM Studio API key. Never returned by any web route."""
    return _token_and_source()[0]


def token_source() -> Optional[str]:
    return _token_and_source()[1]


def save(base_url_value: Optional[str] = None, model_value: Optional[str] = None,
         token_value: Optional[str] = None, clear_token: bool = False) -> Dict[str, Any]:
    """Save settings. Only keys that are passed change; nothing else is touched."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = _read_json()
    if base_url_value is not None:
        url = base_url_value.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("Address must start with http:// or https://")
        data["base_url"] = url or DEFAULT_URL
    if model_value is not None:
        data["model"] = model_value.strip() or "auto"
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if clear_token:
        for path in (KEY_FILE, TOKEN_FILE):
            if path.exists():
                path.write_text("", encoding="utf-8")
    elif token_value:
        KEY_FILE.write_text(token_value.strip(), encoding="utf-8")
    return public()


def public() -> Dict[str, Any]:
    return {
        "base_url": base_url(),
        "model": model(),
        "token_set": bool(token()),
        "token_source": token_source(),
        "env_overrides": [k for k in ("OXIDUS_LM_URL", "OXIDUS_LM_MODEL") + ENV_KEYS
                          if os.environ.get(k, "").strip()],
    }

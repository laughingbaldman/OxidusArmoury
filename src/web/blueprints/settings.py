"""One-stop Settings page plus an LM Studio connection check.

Routes
  GET  /settings                     the page
  GET  /api/settings/lm-studio       connection status in plain words (never returns the token)
  POST /api/settings/lm-studio       save address / model / token (local machine only)
  POST /api/settings/lm-studio/test  send one tiny chat request through LM Studio
  POST /api/settings/obsidian        change the vault folder (local machine only)
  GET  /api/settings/overview        what each settings section can do right now
  GET  /api/app/routes               every route the app exposes (used by scripts/app_audit.py)
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request

from src.utils import lm_settings

bp = Blueprint("settings", __name__)
ROOT = Path(__file__).resolve().parents[3]
LOCAL = {"127.0.0.1", "::1", "localhost"}


def _local_only():
    if request.remote_addr not in LOCAL:
        return jsonify({"ok": False, "error": "Settings can only be changed on this computer."}), 403
    return None


def _clients():
    """The shared client plus the bootstrap one, so a change applies everywhere."""
    from src.utils.lm_studio_client import get_lm_studio_client
    out = [get_lm_studio_client()]
    try:
        from src.web.hub import hub
        extra = getattr(hub, "lm_client", None)
        if extra is not None and extra not in out:
            out.append(extra)
    except Exception:
        pass
    return out


@bp.get("/settings")
def settings_page():
    return render_template("settings.html")


@bp.get("/api/settings/lm-studio")
def lm_status():
    client = _clients()[0]
    info = client.diagnose()
    info["settings"] = lm_settings.public()
    return jsonify(info)


@bp.post("/api/settings/lm-studio")
def lm_save():
    blocked = _local_only()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    try:
        lm_settings.save(base_url_value=data.get("base_url"), model_value=data.get("model"),
                         token_value=data.get("token") or None, clear_token=bool(data.get("clear_token")))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    for client in _clients():
        try:
            client.reload_settings()
        except Exception:
            pass
    info = _clients()[0].diagnose()
    info["settings"] = lm_settings.public()
    return jsonify(info)


@bp.post("/api/settings/lm-studio/test")
def lm_test():
    result = _clients()[0].test_round_trip()
    result["settings"] = lm_settings.public()
    return jsonify(result)


@bp.post("/api/settings/obsidian")
def obsidian_save():
    blocked = _local_only()
    if blocked:
        return blocked
    vault = str((request.get_json(silent=True) or {}).get("vault") or "").strip()
    if not vault:
        return jsonify({"ok": False, "error": "Enter the vault folder."}), 400
    if not Path(vault).exists():
        return jsonify({"ok": False, "error": f"Folder not found: {vault}"}), 400
    cfg = ROOT / "config" / "obsidian.json"
    data = {}
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8")) or {}
        except ValueError:
            data = {}
    data["vault"] = vault
    cfg.write_text(json.dumps(data), encoding="utf-8")
    return jsonify({"ok": True, "vault": vault})


def _memory_summary() -> dict:
    out = {"notes": None, "conversation_turns": None}
    try:
        idx = json.loads((ROOT / "data" / "knowledge_base" / "notes" / "notes_index.json").read_text(encoding="utf-8"))
        notes = idx.get("notes", idx) if isinstance(idx, dict) else idx
        out["notes"] = len(notes) if hasattr(notes, "__len__") else None
    except (OSError, ValueError):
        pass
    try:
        hist = json.loads((ROOT / "conversation_history.json").read_text(encoding="utf-8"))
        out["conversation_turns"] = len(hist) if isinstance(hist, list) else len(hist.get("history", []))
    except (OSError, ValueError, AttributeError):
        pass
    return out


@bp.get("/api/settings/overview")
def overview():
    from src.web.blueprints.obsidian import vault_path
    return jsonify({
        "lm_studio": lm_settings.public(),
        "obsidian": {"vault": str(vault_path()), "exists": vault_path().exists()},
        "memory": _memory_summary(),
    })


@bp.get("/api/app/routes")
def app_routes():
    rules = []
    for rule in current_app.url_map.iter_rules():
        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        rules.append({"rule": rule.rule, "endpoint": rule.endpoint, "methods": methods,
                      "args": sorted(rule.arguments)})
    rules.sort(key=lambda r: r["rule"])
    return jsonify({"count": len(rules), "routes": rules})


def register(app):
    app.register_blueprint(bp)

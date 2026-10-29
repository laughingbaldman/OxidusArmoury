"""Two-way Obsidian bridge.

Reads every note in the vault (except Oxidus's own folder) into a small
searchable index, and writes Oxidus's notes ONLY into "<vault>/Oxidus Brain/Oxidus Notes".
It never edits or deletes a user note, and never overwrites an existing file.
Vault path: env OXIDUS_OBSIDIAN_VAULT, else config/obsidian.json {"vault": "..."},
else D:\\Obsidian Oxidus brain.
"""
from __future__ import annotations
import json, os, re, time
from datetime import datetime
from pathlib import Path
from flask import Blueprint, jsonify, request

ROOT = Path(__file__).resolve().parents[3]
INDEX_FILE = ROOT / "data" / "obsidian_index.json"
DEFAULT_VAULT = r"D:\Obsidian Oxidus brain"
OXIDUS_SUBDIR = Path("Oxidus Brain") / "Oxidus Notes"
_TAG = re.compile(r"(?<!\w)#([\w/-]+)")
_LINK = re.compile(r"\[\[([^\]|#]+)")

def vault_path() -> Path:
    env = os.environ.get("OXIDUS_OBSIDIAN_VAULT", "").strip()
    if env:
        return Path(env)
    cfg = ROOT / "config" / "obsidian.json"
    if cfg.exists():
        try:
            v = json.loads(cfg.read_text(encoding="utf-8")).get("vault")
            if v:
                return Path(v)
        except Exception:
            pass
    return Path(DEFAULT_VAULT)

def _oxidus_dir() -> Path:
    return vault_path() / OXIDUS_SUBDIR

def build_index() -> dict:
    vault = vault_path()
    notes = []
    if vault.exists():
        own = _oxidus_dir().resolve()
        for p in vault.rglob("*.md"):
            if ".obsidian" in p.parts or ".trash" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            notes.append({
                "title": p.stem,
                "path": str(p.relative_to(vault)),
                "author": "oxidus" if own in p.resolve().parents else "user",
                "tags": sorted(set(_TAG.findall(text))),
                "links": sorted(set(l.strip() for l in _LINK.findall(text))),
                "modified": p.stat().st_mtime,
                "text": text[:20000],
            })
    idx = {"vault": str(vault), "built": time.time(), "count": len(notes), "notes": notes}
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(idx), encoding="utf-8")
    return idx

def load_index() -> dict:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return build_index()

def search(query: str, limit: int = 10) -> list:
    terms = [t for t in re.findall(r"\w+", query.lower()) if t]
    if not terms:
        return []
    scored = []
    for n in load_index().get("notes", []):
        body = n["text"].lower(); title = n["title"].lower()
        score = sum(body.count(t) + 5 * title.count(t) + 3 * sum(t in g.lower() for g in n["tags"]) for t in terms)
        if score:
            i = body.find(terms[0])
            snippet = n["text"][max(0, i - 120): i + 240] if i >= 0 else n["text"][:240]
            scored.append({"score": score, "title": n["title"], "path": n["path"],
                           "author": n["author"], "tags": n["tags"], "snippet": snippet})
    scored.sort(key=lambda r: -r["score"])
    return scored[:limit]

def write_note(title: str, body: str, tags: list | None = None) -> Path:
    safe = re.sub(r'[<>:"/\\|?*]+', "-", title).strip() or "Oxidus note"
    folder = _oxidus_dir(); folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{safe}.md"
    if target.exists():  # never overwrite
        target = folder / f"{safe} {datetime.now():%Y-%m-%d %H%M%S}.md"
    tag_line = " ".join(f"#{t.lstrip('#')}" for t in (tags or []))
    front = f"---\nauthor: Oxidus\ncreated: {datetime.now().isoformat(timespec='seconds')}\n---\n"
    target.write_text(front + (tag_line + "\n\n" if tag_line else "") + body, encoding="utf-8")
    build_index()
    return target

bp = Blueprint("obsidian", __name__)

@bp.get("/api/obsidian/status")
def status():
    idx = load_index()
    from src.integrations.obsidian_brain_export import last_export
    last = last_export() or {}
    brain = {k: last.get(k) for k in ("brain_folder", "exported", "notes", "links", "by_kind")} if last else None
    return jsonify({"vault": str(vault_path()), "exists": vault_path().exists(),
                    "notes": idx.get("count", 0), "built": idx.get("built"), "brain_export": brain})

@bp.post("/api/obsidian/sync")
def sync():
    idx = build_index()
    return jsonify({"ok": True, "notes": idx["count"]})

@bp.get("/api/obsidian/search")
def search_route():
    return jsonify({"results": search(request.args.get("q", ""), int(request.args.get("limit", 10)))})

@bp.post("/api/obsidian/note")
def note_route():
    from src.web.hub import hub
    guard = hub.require_admin_token()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    if not data.get("title") or not data.get("body"):
        return jsonify({"error": "title and body required"}), 400
    p = write_note(data["title"], data["body"], data.get("tags"))
    return jsonify({"ok": True, "path": str(p)})

@bp.post("/api/obsidian/export-brain")
def export_brain_route():
    """Mirror Oxidus's brain into <vault>/Oxidus Brain/Brain (generated files only)."""
    from src.web.hub import hub
    guard = hub.require_admin_token()
    if guard:
        return guard
    from src.integrations.obsidian_brain_export import export_brain
    data = request.get_json(silent=True) or {}
    result = export_brain(vault=vault_path(), dry_run=bool(data.get("dry_run")))
    if not result.get("dry_run"):
        build_index()
    result["stale_generated_files"] = len(result.get("stale_generated_files", []))
    return jsonify(result)

def register(app):
    app.register_blueprint(bp)

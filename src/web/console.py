"""One page for the settings Jonathon actually changes."""

from __future__ import annotations

from pathlib import Path
import json

from flask import Blueprint, current_app, jsonify, render_template, request

from src.core.ethics import OxidusEthics
from src.core.question_loop import QuestionLoop
from src.library.bible import list_books, read_chapter, read_verse
from src.library.shelf import ReferenceLibrary
from src.runtime.tiers import TIER_NOTES, choose_tier, current_tier, detect_hardware, save_settings
from src.skills.registry import builtin_skills
from src.tools.desktop import DesktopControl
from src.tools.media import ImageTool, VideoTool
from src.tools.predict import forecast
from src.training.lora_data import build_dataset, check_jsonl, write_jsonl
from src.utils.grok_backend import GrokBackend
from src.voice.engine import VoiceEngine

console_bp = Blueprint("console", __name__)


def root() -> Path:
    configured = current_app.config.get("OXIDUS_ROOT")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2]


def library() -> ReferenceLibrary:
    return ReferenceLibrary(root())


def voice() -> VoiceEngine:
    cached = current_app.config.setdefault("_voice", {})
    key = str(root())
    if key not in cached:
        cached[key] = VoiceEngine(root())
    return cached[key]


def desktop() -> DesktopControl:
    cached = current_app.config.setdefault("_desktop", {})
    key = str(root())
    if key not in cached:
        cached[key] = DesktopControl(root())
    return cached[key]


@console_bp.route("/console")
def console_page():
    return render_template("console.html")


@console_bp.route("/api/console/status", methods=["GET"])
def console_status():
    hardware = detect_hardware()
    tier = current_tier(root(), hardware)
    return jsonify({
        "tier": tier,
        "tier_note": TIER_NOTES.get(tier, ""),
        "hardware": hardware.as_dict(),
        "grok": GrokBackend(root()).status(),
        "voice": voice().status(),
        "desktop": desktop().status(),
    })


@console_bp.route("/api/console/tier", methods=["POST"])
def console_tier():
    payload = request.get_json(silent=True) or {}
    choice = str(payload.get("tier") or "").lower()
    if choice not in {"lite", "standard", "full", ""}:
        return jsonify({"ok": False, "error": "Tier must be lite, standard, or full."}), 400
    if choice:
        save_settings(root(), {"tier": choice})
    else:
        hardware = detect_hardware()
        choice = choose_tier(hardware)
        save_settings(root(), {"tier": choice})
    return jsonify({"ok": True, "tier": current_tier(root())})


@console_bp.route("/api/console/questions", methods=["GET"])
def console_questions():
    return jsonify({"questions": QuestionLoop(root(), library()).list_all()})


@console_bp.route("/api/console/questions/<question_id>/answer", methods=["POST"])
def console_answer(question_id: str):
    payload = request.get_json(silent=True) or {}
    result = QuestionLoop(root(), library()).answer(question_id, payload.get("answer") or "")
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@console_bp.route("/api/console/search", methods=["GET"])
def console_search():
    query = request.args.get("q") or ""
    found = library().retrieve(query, limit=8)
    return jsonify(found)


@console_bp.route("/api/console/entry", methods=["POST"])
def console_add_entry():
    payload = request.get_json(silent=True) or {}
    saved = library().add_entry(
        store=payload.get("store") or "knowledge",
        topic=payload.get("topic") or "notes",
        subtopic=payload.get("subtopic") or "general",
        title=payload.get("title") or "untitled",
        text=payload.get("text") or "",
        summary=payload.get("summary") or "",
        source=payload.get("source") or "jonathon",
    )
    return jsonify(saved)


@console_bp.route("/api/console/entry/<entry_id>", methods=["GET"])
def console_entry(entry_id: str):
    record = library().get_entry(entry_id)
    if not record:
        return jsonify({"error": "No entry."}), 404
    record["full"] = library().get_full(entry_id)
    return jsonify(record)


@console_bp.route("/api/console/entry/<entry_id>/archive", methods=["POST"])
def console_archive(entry_id: str):
    return jsonify(library().archive_entry(entry_id))


@console_bp.route("/api/console/toc", methods=["GET"])
def console_toc():
    return jsonify(library().table_of_contents())


@console_bp.route("/api/console/thesaurus", methods=["GET", "POST"])
def console_thesaurus():
    lib = library()
    if request.method == "GET":
        lib._ensure()
        return jsonify(lib.thesaurus)
    payload = request.get_json(silent=True) or {}
    if payload.get("delete"):
        return jsonify({"ok": lib.remove_synonym_term(payload.get("term") or "")})
    return jsonify(lib.set_synonyms(payload.get("term") or "", payload.get("synonyms") or []))


@console_bp.route("/api/console/glossary", methods=["GET", "POST"])
def console_glossary():
    lib = library()
    if request.method == "GET":
        lib._ensure()
        return jsonify(lib.glossary)
    payload = request.get_json(silent=True) or {}
    if payload.get("delete"):
        return jsonify({"ok": lib.delete_glossary(payload.get("term") or "")})
    return jsonify(lib.upsert_glossary(payload.get("term") or "", payload.get("definition") or "", loc=payload.get("loc") or ""))


@console_bp.route("/api/console/graph", methods=["GET", "POST"])
def console_graph():
    lib = library()
    if request.method == "GET":
        lib._ensure()
        return jsonify({"edges": lib.graph.get("edges") or [], "backlinks": lib.backlinks})
    payload = request.get_json(silent=True) or {}
    if payload.get("delete"):
        return jsonify({"removed": lib.unlink(payload.get("from") or "", payload.get("to") or "", payload.get("rel") or "")})
    return jsonify(lib.link(payload.get("from") or "", payload.get("to") or "", payload.get("rel") or "related"))


def _bible_dirs():
    base = root() / "data" / "knowledge_base" / "bible"
    return [base / "kjv", base / "book-of-mormon"]


@console_bp.route("/api/console/bible/books", methods=["GET"])
def console_books():
    rows = []
    for folder in _bible_dirs():
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.json")):
            if path.name == "catalog.json":
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.append({
                "book": data.get("book") or path.stem,
                "traditions": data.get("traditions") or [],
                "translation": data.get("translation") or "",
                "weight": data.get("weight") or "established",
            })
    return jsonify({"books": rows})


def _book_dir(book: str):
    from src.library.bible import file_slug
    for folder in _bible_dirs():
        if (folder / f"{file_slug(book)}.json").exists():
            return folder
    return _bible_dirs()[0]


@console_bp.route("/api/console/bible/chapter", methods=["GET"])
def console_chapter():
    book = request.args.get("book") or "Genesis"
    chapter = int(request.args.get("chapter") or 1)
    return jsonify({"book": book, "chapter": chapter, "verses": read_chapter(_book_dir(book), book, chapter)})


@console_bp.route("/api/console/bible/verse", methods=["GET"])
def console_verse():
    book = request.args.get("book") or ""
    chapter = int(request.args.get("chapter") or 1)
    verse = int(request.args.get("verse") or 1)
    return jsonify({"text": read_verse(_book_dir(book), book, chapter, verse)})


@console_bp.route("/api/console/ethics", methods=["GET", "POST"])
def console_ethics():
    path = root() / "data" / "stores" / "ethics" / "rules.json"
    ethics = OxidusEthics()
    if request.method == "GET":
        extra = []
        if path.exists():
            try:
                extra = json.loads(path.read_text(encoding="utf-8")).get("extra") or []
            except Exception:
                extra = []
        return jsonify({"immutable": ethics.immutable_boundaries, "extra": extra})
    payload = request.get_json(silent=True) or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"extra": payload.get("extra") or []}, indent=2) + "\n", encoding="utf-8")
    return jsonify({"ok": True})


@console_bp.route("/api/console/voice", methods=["GET", "POST"])
def console_voice():
    engine = voice()
    if request.method == "GET":
        return jsonify(engine.status())
    payload = request.get_json(silent=True) or {}
    if "mic_enabled" in payload:
        engine.enable_mic(bool(payload.get("mic_enabled")))
    if "muted" in payload:
        engine.set_muted(bool(payload.get("muted")))
    if "device_id" in payload:
        engine.set_device(str(payload.get("device_id") or "default"))
    if "conversation" in payload:
        engine.set_conversation(bool(payload.get("conversation")))
    if payload.get("speak"):
        engine.speak(str(payload.get("speak")))
    if "ptt" in payload:
        return jsonify(engine.push_to_talk(bool(payload.get("ptt"))))
    if payload.get("interrupt"):
        return jsonify(engine.barge_in())
    return jsonify(engine.status())


@console_bp.route("/api/console/tools/image", methods=["POST"])
def console_image():
    payload = request.get_json(silent=True) or {}
    tier = current_tier(root())
    return jsonify(ImageTool(tier).generate(payload.get("prompt") or ""))


@console_bp.route("/api/console/tools/video", methods=["POST"])
def console_video():
    payload = request.get_json(silent=True) or {}
    tier = current_tier(root())
    return jsonify(VideoTool(tier).generate(payload.get("prompt") or ""))


@console_bp.route("/api/console/tools/predict", methods=["POST"])
def console_predict():
    payload = request.get_json(silent=True) or {}
    questions = QuestionLoop(root(), library()).list_open()
    return jsonify(forecast(payload.get("events") or [], questions))


@console_bp.route("/api/console/desktop/propose", methods=["POST"])
def console_desktop_propose():
    payload = request.get_json(silent=True) or {}
    return jsonify(desktop().propose(payload.get("action") or {}))


@console_bp.route("/api/console/desktop/approve", methods=["POST"])
def console_desktop_approve():
    if request.headers.get("X-Oxidus-Local-Action") != "approve":
        return jsonify({"ok": False, "error": "Missing local approval header."}), 403
    payload = request.get_json(silent=True) or {}
    return jsonify(desktop().approve(str(payload.get("id") or "")))


@console_bp.route("/api/console/desktop/reject", methods=["POST"])
def console_desktop_reject():
    payload = request.get_json(silent=True) or {}
    return jsonify(desktop().reject(str(payload.get("id") or "")))


@console_bp.route("/api/console/finetune", methods=["GET", "POST"])
def console_finetune():
    dest = root() / "data" / "training" / "lora_dataset.jsonl"
    if request.method == "POST":
        count = write_jsonl(build_dataset(root()), dest)
        return jsonify({"ok": True, "rows": count, "path": str(dest), "check": check_jsonl(dest)})
    return jsonify(check_jsonl(dest))


@console_bp.route("/api/console/logs", methods=["GET"])
def console_logs():
    name = Path(request.args.get("name") or "learning_log.jsonl").name
    candidates = [
        root() / "logs" / name,
        root() / "data" / "stores" / "questions" / name,
        root() / "data" / "stores" / "agent" / name,
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
            return jsonify({"name": name, "lines": lines})
    return jsonify({"name": name, "lines": []})


@console_bp.route("/api/console/skills", methods=["GET"])
def console_skills():
    skills = builtin_skills(library())
    return jsonify({"skills": [{"name": skill.name, "description": skill.description, "tier": skill.tier} for skill in skills]})


@console_bp.route("/api/console/models", methods=["GET", "POST"])
def console_models():
    path = root() / "data" / "stores" / "settings.json"
    current = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        if "backend" in payload:
            current["backend"] = str(payload.get("backend") or "auto")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return jsonify({
        "backend": current.get("backend") or "auto",
        "choices": ["auto", "retrieval", "lm_studio", "grok"],
        "grok": GrokBackend(root()).status(),
    })

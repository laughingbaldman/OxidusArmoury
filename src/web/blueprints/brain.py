"""Live 'Brain' page: Oxidus's knowledge graph as an animated network.

GET  /brain                 page (templates/brain.html, no CDN, plain canvas)
GET  /api/brain/graph       nodes + links (same graph as the Obsidian export)
GET  /api/brain/activity    recent retrieval hits, ?since=<seq> for polling
The chat route calls record_activity() after each answer so the page can pulse
the nodes Oxidus pulled from its shelf.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

ROOT = Path(__file__).resolve().parents[3]
bp = Blueprint("brain", __name__)

_graph = {"data": None, "building": False, "error": None}
_lock = threading.Lock()
_events: deque = deque(maxlen=50)
_seq = [0]


def build_graph(root: Path = ROOT) -> dict:
    from src.integrations.obsidian_brain_export import BrainExporter
    ex = BrainExporter(root, vault=Path(root) / "_unused_vault").build()
    nodes, links, seen = [], [], set()
    for n in ex.notes.values():
        nodes.append({"id": n.name, "kind": n.kind, "kinds": n.kinds, "folder": n.folder})
    names = {n["id"] for n in nodes}
    for n in ex.notes.values():
        for section, entries in n.links.items():
            for e in entries:
                tgt, _, label = e.partition("|")
                if tgt not in names:
                    continue
                key = tuple(sorted((n.name, tgt)))
                if key in seen:
                    continue
                seen.add(key)
                links.append({"source": n.name, "target": tgt, "rel": label or section})
    return {"nodes": nodes, "links": links, "built": time.time()}


def _build_bg():
    try:
        data = build_graph()
        with _lock:
            _graph.update(data=data, building=False, error=None)
    except Exception as exc:  # keep the page alive even if export code fails
        with _lock:
            _graph.update(building=False, error=str(exc))


def get_graph(refresh: bool = False):
    with _lock:
        if (_graph["data"] is None or refresh) and not _graph["building"]:
            _graph["building"] = True
            threading.Thread(target=_build_bg, daemon=True).start()
        return dict(_graph)


def _match_nodes(texts, limit=40):
    data = _graph.get("data")
    if not data:
        return []
    lookup = {n["id"].lower(): n["id"] for n in data["nodes"]}
    found = []
    for t in texts:
        t = str(t or "").strip()
        if not t:
            continue
        hit = lookup.get(t.lower())
        if hit and hit not in found:
            found.append(hit)
    joined = " ".join(str(t or "") for t in texts).lower()
    for low, name in lookup.items():
        if len(low) >= 5 and name not in found and re.search(r"\b" + re.escape(low) + r"\b", joined):
            found.append(name)
    return found[:limit]


def record_activity(query: str, hits=None, topics=None):
    """Called by chat after an answer. Never raises."""
    try:
        texts, labels = [], []
        for h in hits or []:
            if isinstance(h, dict):
                texts += [h.get("subtopic"), h.get("title"), h.get("topic")]
                labels.append(h.get("loc") or h.get("title") or "")
        texts += list(topics or []) + [query]
        get_graph()
        with _lock:
            _seq[0] += 1
            _events.append({"seq": _seq[0], "time": time.time(), "query": str(query)[:200],
                            "hits": [l for l in labels if l][:20], "nodes": _match_nodes(texts)})
    except Exception:
        pass


@bp.get("/brain")
def brain_page():
    return render_template("brain.html")


@bp.get("/api/brain/graph")
def graph_route():
    g = get_graph(refresh=request.args.get("refresh") == "1")
    if g["data"] is None:
        return jsonify({"building": True, "error": g["error"]}), 202
    return jsonify(g["data"])


@bp.get("/api/brain/activity")
def activity_route():
    since = int(request.args.get("since", 0) or 0)
    with _lock:
        ev = [e for e in _events if e["seq"] > since]
        return jsonify({"seq": _seq[0], "events": ev})


def register(app):
    app.register_blueprint(bp)

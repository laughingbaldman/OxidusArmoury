"""Build a chat dataset from questions Jonathon already answered. No GPU."""

from __future__ import annotations

from pathlib import Path
import json


def _rows_from_queue(path: Path) -> list:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = []
    for item in payload.get("questions") or []:
        if item.get("status") != "answered":
            continue
        question = (item.get("question") or "").strip()
        answer = (item.get("answer") or "").strip()
        if question and answer:
            rows.append({
                "messages": [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ]
            })
    return rows


def _rows_from_log(path: Path) -> list:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        question = (item.get("question") or "").strip()
        answer = (item.get("answer") or "").strip()
        if question and answer:
            rows.append({
                "messages": [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ]
            })
    return rows


def build_dataset(root: str | Path) -> list:
    root = Path(root)
    question_dir = root / "data" / "stores" / "questions"
    rows = _rows_from_queue(question_dir / "queue.json")
    rows.extend(_rows_from_log(question_dir / "learning_log.jsonl"))
    notes = root / "data" / "knowledge_base" / "notes" / "notes_index.json"
    if notes.exists():
        try:
            payload = json.loads(notes.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for note in payload.get("notes") or payload if isinstance(payload, list) else []:
            if not isinstance(note, dict):
                continue
            summary = (note.get("summary") or "").strip()
            topic = (note.get("topic") or "note").strip()
            if summary:
                rows.append({
                    "messages": [
                        {"role": "user", "content": topic},
                        {"role": "assistant", "content": summary},
                    ]
                })
    return rows


def write_jsonl(rows: list, dest: str | Path) -> int:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def check_jsonl(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"ok": False, "rows": 0, "error": "Dataset file is missing."}
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        messages = row.get("messages") or []
        if len(messages) < 2:
            return {"ok": False, "rows": count, "error": "A row is missing messages."}
        count += 1
    return {"ok": count > 0, "rows": count, "error": "" if count else "Dataset is empty."}

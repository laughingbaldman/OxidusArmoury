"""Ask the user when the shelf is thin, unsure, or in conflict.

Questions are stored on their own shelf. Answers become memories, not guesses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re
import hashlib

NEGATION = re.compile(
    r"\b(not|never|no|isn't|aren't|cannot|can't|won't|don't|doesn't|didn't)\b",
    re.IGNORECASE,
)
WORD = re.compile(r"[a-z']{3,}")
STOP = {
    "the", "and", "that", "with", "from", "this", "have", "was", "were",
    "are", "you", "your", "his", "her", "they", "them", "their", "not",
    "never", "but", "for", "into", "about",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _topic(query: str) -> str:
    text = re.sub(r"\s+", " ", (query or "").strip())
    return text[:80] or "this"


def _words(text: str) -> set:
    return {word for word in WORD.findall((text or "").lower()) if word not in STOP}


def contradicts(left: str, right: str) -> bool:
    a = _words(left)
    b = _words(right)
    if not a or not b:
        return False
    overlap = len(a & b) / float(min(len(a), len(b)))
    polarity_differs = bool(NEGATION.search(left)) != bool(NEGATION.search(right))
    return overlap >= 0.6 and polarity_differs


def _read(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


class QuestionLoop:
    def __init__(self, root: str | Path, library=None, weak_score: float = 0.35, low_confidence: float = 0.5):
        self.root = Path(root)
        self.library = library
        self.weak_score = weak_score
        self.low_confidence = low_confidence
        self.queue_path = self.root / "data" / "stores" / "questions" / "queue.json"
        self.log_path = self.root / "data" / "stores" / "questions" / "learning_log.jsonl"
        self.max_open = 12

    def _load(self) -> dict:
        payload = _read(self.queue_path, {"questions": []})
        if not isinstance(payload.get("questions"), list):
            payload["questions"] = []
        return payload

    def _save(self, payload: dict) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_open(self) -> list:
        return [item for item in self._load()["questions"] if item.get("status") == "open"]

    def list_all(self) -> list:
        return list(self._load()["questions"])

    def detect_gaps(self, query: str, hits=None, confidence=None, statements=None) -> list:
        hits = list(hits or [])
        scores = []
        for hit in hits:
            if isinstance(hit, dict):
                scores.append(float(hit.get("score") or 0))
            else:
                scores.append(float(getattr(hit, "score", 0) or 0))
        best = max(scores) if scores else 0.0
        topic = _topic(query)
        gaps = []
        if not hits or best < self.weak_score:
            gaps.append({
                "reason": "weak_hit",
                "topic": topic,
                "question": f"I don't have a solid source on {topic}. What should I treat as true?",
                "detail": f"Best match score was {best:.2f}.",
            })
        elif confidence is not None and float(confidence) < self.low_confidence:
            snippet = ""
            if isinstance(hits[0], dict):
                snippet = (hits[0].get("summary") or hits[0].get("text") or "")[:180]
            gaps.append({
                "reason": "low_confidence",
                "topic": topic,
                "question": f"I'm not confident about {topic}. Is this right: {snippet}".rstrip(),
                "detail": f"Confidence {float(confidence):.2f}.",
            })
        texts = [str(item) for item in (statements or [])]
        conflict = None
        for index, left in enumerate(texts):
            for right in texts[index + 1:]:
                if contradicts(left, right):
                    conflict = (left[:140], right[:140])
                    break
        if conflict is None:
            for index, left in enumerate(hits):
                if not isinstance(left, dict):
                    continue
                for right in hits[index + 1:]:
                    if isinstance(right, dict) and right.get("rel") == "contradicts":
                        conflict = (left.get("summary") or left.get("title"), right.get("summary") or right.get("title"))
                        break
        if conflict:
            gaps.append({
                "reason": "contradiction",
                "topic": topic,
                "question": f"These disagree about {topic}. Which should I keep?",
                "detail": f"A: {conflict[0]} | B: {conflict[1]}",
            })
        return gaps

    def enqueue(self, question: str, reason: str, topic: str, detail: str = "") -> dict | None:
        payload = self._load()
        open_items = [item for item in payload["questions"] if item.get("status") == "open"]
        key = re.sub(r"\s+", " ", question).strip().lower()
        for item in open_items:
            if item.get("question", "").strip().lower() == key:
                return item
        if len(open_items) >= self.max_open:
            return None
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        record = {
            "id": f"q_{digest}",
            "question": question.strip(),
            "reason": reason,
            "topic": topic,
            "detail": detail,
            "status": "open",
            "created": _now(),
            "answer": "",
            "learned_entry": "",
        }
        payload["questions"].append(record)
        self._save(payload)
        return record

    def observe(self, query: str, hits=None, confidence=None, statements=None) -> list:
        text = (query or "").strip()
        if len(text) < 8 and "?" not in text:
            return []
        queued = []
        for gap in self.detect_gaps(text, hits=hits, confidence=confidence, statements=statements):
            item = self.enqueue(gap["question"], gap["reason"], gap["topic"], gap["detail"])
            if item:
                queued.append(item)
        return queued

    def answer(self, question_id: str, answer: str, author: str = "user") -> dict:
        payload = self._load()
        target = None
        for item in payload["questions"]:
            if item.get("id") == question_id:
                target = item
                break
        if target is None:
            return {"ok": False, "error": "Question not found."}
        if target.get("status") == "answered":
            return {"ok": True, "question": target, "already": True}
        text = (answer or "").strip()
        if not text:
            return {"ok": False, "error": "Answer is empty."}
        learned_id = ""
        if self.library is not None:
            saved = self.library.add_entry(
                store="memories",
                topic=target.get("topic") or "learned",
                subtopic="from_user",
                title=target.get("question") or question_id,
                text=f"Q: {target.get('question')}\nA: {text}",
                summary=text[:280],
                source=author,
                when=_now()[:10],
            )
            learned_id = saved.get("id") or ""
        target["status"] = "answered"
        target["answer"] = text
        target["learned_entry"] = learned_id
        target["answered_at"] = _now()
        self._save(payload)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "at": target["answered_at"],
            "question_id": question_id,
            "question": target.get("question"),
            "answer": text,
            "learned_entry": learned_id,
            "author": author,
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
        return {"ok": True, "question": target}

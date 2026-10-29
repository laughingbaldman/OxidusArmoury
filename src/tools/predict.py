"""Guess the next useful action from logs and open questions. No network."""

from __future__ import annotations

from collections import Counter
import re


def forecast(events: list, open_questions: list | None = None) -> dict:
    texts = []
    for event in events or []:
        if isinstance(event, str):
            texts.append(event)
        elif isinstance(event, dict):
            texts.append(str(event.get("text") or event.get("detail") or event.get("content") or ""))
    words = []
    for text in texts:
        words.extend(re.findall(r"[a-z][a-z\-]{3,}", text.lower()))
    common = [word for word, _count in Counter(words).most_common(5)]
    actions = []
    if open_questions:
        actions.append({
            "action": "Answer the open question queue",
            "confidence": 0.8,
            "because": f"{len(open_questions)} question(s) are waiting.",
        })
    if common:
        actions.append({
            "action": f"Keep going on {common[0]}",
            "confidence": 0.55,
            "because": "That word shows up most in the recent log.",
        })
    if not actions:
        actions.append({
            "action": "Ask what to work on",
            "confidence": 0.4,
            "because": "The log is quiet.",
        })
    return {"next_actions": actions, "topics": common}

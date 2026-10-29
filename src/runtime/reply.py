"""Turn a user line into a reply, using the shelf first on Lite."""

from __future__ import annotations

from pathlib import Path

from src.core.question_loop import QuestionLoop
from src.library.shelf import ReferenceLibrary
from src.runtime.answering import answer_for_tier
from src.runtime.tiers import current_tier


def reply_to(root: str | Path, message: str, model_call=None) -> dict:
    root = Path(root)
    library = ReferenceLibrary(root)
    loop = QuestionLoop(root, library)
    tier = current_tier(root)
    caller = model_call if tier != "lite" else None
    answered = answer_for_tier(root, message, tier=tier, model_call=caller, library=library)
    hits = (answered.get("pack") or {}).get("hits") or []
    queued = loop.observe(message, hits=hits)
    return {
        "text": answered["text"],
        "tier": answered["tier"],
        "used_model": answered["used_model"],
        "questions": loop.list_open(),
        "queued": queued,
        "hits": hits,
    }

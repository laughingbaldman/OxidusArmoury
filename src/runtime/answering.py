"""Answer from the shelf first. Call a model only when the tier allows it."""

from __future__ import annotations

from pathlib import Path

from src.library.shelf import ReferenceLibrary
from src.runtime.tiers import current_tier


def format_cards(pack: dict) -> str:
    cards = pack.get("cards") or []
    if not cards:
        return (
            "I don't have a card on that yet. "
            "Tell me what to keep and I'll put it on the shelf."
        )
    lines = ["From the shelf:"]
    for card in cards:
        title = card.get("title") or "Note"
        summary = card.get("summary") or ""
        loc = card.get("loc") or ""
        suffix = f" ({loc})" if loc and loc not in title else ""
        lines.append(f"- {title}{suffix}: {summary}")
    return "\n".join(lines)


def answer_for_tier(root, query: str, tier: str | None = None, model_call=None, library=None) -> dict:
    root = Path(root)
    tier_name = tier or current_tier(root)
    lib = library or ReferenceLibrary(root)
    budget = 420 if tier_name == "lite" else 800
    pack = lib.build_context(query, budget_tokens=budget, full_text=False)
    shelf_text = format_cards(pack)
    model_text = None
    if tier_name != "lite" and model_call is not None:
        try:
            model_text = model_call(query, pack.get("context") or "")
        except Exception:
            model_text = None
    if model_text and str(model_text).strip():
        text = str(model_text).strip()
        used_model = True
    else:
        text = shelf_text
        used_model = False
    return {
        "text": text,
        "tier": tier_name,
        "used_model": used_model,
        "pack": pack,
    }

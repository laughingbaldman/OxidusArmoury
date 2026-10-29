"""Small local skill list.

The shape (a named skill the assistant can call) is the useful idea from
OpenJarvis. The skills themselves are Oxidus's. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Skill:
    name: str
    description: str
    tier: str
    runner: object

    def allows(self, tier: str) -> bool:
        order = {"lite": 0, "standard": 1, "full": 2}
        return order.get(tier, 0) >= order.get(self.tier, 0)

    def run(self, **kwargs):
        return self.runner(**kwargs)


def builtin_skills(library=None, voice=None) -> list:
    def shelf_search(query: str = ""):
        if library is None:
            return {"ok": False, "summary": "No shelf."}
        pack = library.build_context(query, budget_tokens=200)
        return {"ok": True, "summary": pack.get("context") or "No card."}

    def say(text: str = ""):
        if voice is None:
            return {"ok": False, "summary": "Voice is not attached."}
        return voice.speak(text)

    return [
        Skill("shelf_search", "Look up cards before spending a long answer.", "lite", shelf_search),
        Skill("say", "Speak a line if the mic path is on.", "lite", say),
    ]


def match_skill(skills: list, text: str):
    lowered = (text or "").lower()
    for skill in skills:
        if skill.name.replace("_", " ") in lowered or skill.name in lowered:
            return skill
    if lowered.startswith("define ") or lowered.startswith("what is "):
        for skill in skills:
            if skill.name == "shelf_search":
                return skill
    return None

"""Local skills. Named jobs the assistant can call."""

from .registry import Skill, builtin_skills, match_skill

__all__ = ["Skill", "builtin_skills", "match_skill"]

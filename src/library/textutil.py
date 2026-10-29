"""Small text helpers for the reference shelf."""

from __future__ import annotations

import hashlib
import re

TOKEN_CHARS = 4

STOPWORDS = {
    "the", "and", "for", "that", "with", "from", "this", "have", "were",
    "was", "are", "you", "your", "his", "her", "they", "them", "their",
    "shall", "shalt", "unto", "thee", "thou", "thy", "hath", "hast",
    "will", "would", "could", "should", "into", "over", "after", "before",
    "than", "then", "there", "here", "which", "who", "whom", "what",
    "when", "where", "how", "why", "all", "any", "nor", "its", "our",
    "but", "not", "did", "does", "don", "wasn", "isn", "aren", "upon",
    "said", "say", "ye", "him", "she", "her", "been", "being", "have",
    "has", "had", "a", "an", "of", "to", "in", "on", "or", "as", "at",
    "by", "be", "it", "he", "we", "me", "my", "so", "if", "no",
}

WORD_RE = re.compile(r"[a-z0-9']{3,}")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def normalize_key(text: str) -> str:
    return normalize_ws(text).lower()


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + TOKEN_CHARS - 1) // TOKEN_CHARS)


def trim_to_tokens(text: str, budget: int) -> str:
    if budget <= 0 or not text:
        return ""
    limit = max(1, budget) * TOKEN_CHARS
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip() + "..."


def summarize(text: str, limit: int = 280) -> str:
    clean = normalize_ws(text)
    if len(clean) <= limit:
        return clean
    cut = clean[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "..."


def key_points(text: str, limit: int = 3) -> list:
    clean = normalize_ws(text)
    parts = [part.strip() for part in SENTENCE_RE.split(clean) if part.strip()]
    if not parts:
        return [summarize(clean, 160)] if clean else []
    return [summarize(part, 160) for part in parts[:limit]]


def terms(text: str) -> list:
    found = []
    seen = set()
    for word in WORD_RE.findall((text or "").lower()):
        if word in STOPWORDS or word in seen:
            continue
        seen.add(word)
        found.append(word)
    return found


def stable_id(store: str, topic: str, subtopic: str, title: str) -> str:
    key = "|".join(normalize_key(part) for part in (store, topic, subtopic, title))
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"kb_{digest}"


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_ws(text).encode("utf-8")).hexdigest()

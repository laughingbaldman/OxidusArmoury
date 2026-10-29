"""Pick which reference book to open first."""

from __future__ import annotations

import re

CITE_RE = re.compile(
    r"\b((?:[1-3]\s+)?[A-Za-z]+(?:\s+[A-Za-z]+){0,3})\s+(\d+)\s*:\s*(\d+)\b"
)
YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")


def route(query: str, acronyms: dict | None = None) -> list:
    """Return index names in the order a librarian would consult them."""
    text = (query or "").strip()
    lowered = text.lower()
    acronyms = acronyms or {}
    known = {str(key).lower() for key in acronyms}

    if CITE_RE.search(text):
        return ["concordance", "cards", "topical"]

    if lowered.startswith(("define ", "what is ", "what does ", "glossary ")):
        return ["glossary", "aliases", "cards", "concordance"]

    tokens = re.findall(r"[A-Za-z0-9]+", text)
    if len(tokens) == 1 and tokens[0].isupper() and 2 <= len(tokens[0]) <= 6:
        return ["acronyms", "aliases", "glossary", "cards"]
    if tokens and tokens[0].lower() in known and len(tokens[0]) <= 6:
        return ["acronyms", "aliases", "glossary", "cards"]

    if any(phrase in lowered for phrase in ("see also", "related to", "contradict", "supports", "support for")):
        return ["graph", "cards", "topical"]

    if lowered.startswith("when ") or YEAR_RE.search(text):
        return ["chronological", "topical", "cards", "concordance"]

    if lowered.startswith(("source:", "from ")):
        return ["source", "cards", "topical"]

    return ["aliases", "topical", "cards", "glossary", "concordance"]


def parse_citation(query: str):
    match = CITE_RE.search(query or "")
    if not match:
        return None
    book = re.sub(r"\s+", " ", match.group(1)).strip()
    return book, int(match.group(2)), int(match.group(3))

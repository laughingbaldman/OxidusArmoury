"""Hierarchical reference shelf.

Separate stacks for knowledge, memories, and working notes.
The question queue lives next to them and is not treated as settled knowledge.
Indexes are persisted and updated one entry at a time.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import re

from .router import parse_citation, route
from .textutil import (
    content_hash,
    estimate_tokens,
    key_points,
    normalize_key,
    normalize_ws,
    stable_id,
    summarize,
    terms,
    trim_to_tokens,
)

MORAL_THESAURUS = {
    "mercy": ["compassion", "pity", "kindness", "forgiveness"],
    "justice": ["fairness", "judgment", "righteousness"],
    "honesty": ["truth", "truthfulness", "integrity"],
    "humility": ["meekness", "modest"],
    "love": ["charity", "kindness"],
    "forgiveness": ["pardon", "mercy"],
    "integrity": ["honesty", "uprightness"],
    "stewardship": ["faithfulness", "trust"],
    "golden rule": ["reciprocity", "do unto others"],
}

RELATIONS = {"related", "see_also", "contradicts", "supports"}
STORES = {"knowledge", "memories", "working"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text + "\n", encoding="utf-8")


class ReferenceLibrary:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.catalog = self.root / "data" / "stores" / "catalog"
        self.question_dir = self.root / "data" / "stores" / "questions"
        self.bible_dir = self.root / "data" / "knowledge_base" / "bible" / "kjv"
        self._loaded = False
        self._batch = 0
        self._dirty = set()
        self._post_keys = None

    def _entry_dir(self, store: str) -> Path:
        return self.root / "data" / "stores" / store / "entries"

    def _entry_path(self, store: str, entry_id: str) -> Path:
        return self._entry_dir(store) / f"{entry_id}.json"

    def _ensure(self) -> None:
        if self._loaded:
            return
        self.catalog.mkdir(parents=True, exist_ok=True)
        self.question_dir.mkdir(parents=True, exist_ok=True)
        self.toc = _read_json(self.catalog / "master_index.json", {"topics": {}})
        self.cards = _read_json(self.catalog / "cards.json", {})
        self.thesaurus = _read_json(self.catalog / "thesaurus.json", {"terms": {}})
        self.concordance = _read_json(self.catalog / "concordance.json", {"terms": {}, "by_entry": {}})
        self.glossary = _read_json(self.catalog / "glossary.json", {"terms": {}})
        self.graph = _read_json(self.catalog / "graph.json", {"edges": []})
        self.topical = _read_json(self.catalog / "topical.json", {"tree": {}, "terms": {}})
        self.chronological = _read_json(self.catalog / "chronological.json", {})
        self.sources = _read_json(self.catalog / "source_index.json", {})
        self.aliases = _read_json(self.catalog / "aliases.json", {})
        self.acronyms = _read_json(self.catalog / "acronyms.json", {})
        self.backlinks = _read_json(self.catalog / "backlinks.json", {})
        self.dedupe = _read_json(self.catalog / "dedupe.json", {})
        self.state = _read_json(self.catalog / "index_state.json", {})
        self._seed_language()
        self._loaded = True

    def _seed_language(self) -> None:
        if not self.thesaurus.get("terms"):
            self.thesaurus["terms"] = {key: list(value) for key, value in MORAL_THESAURUS.items()}
            synonym_path = self.root / "data" / "knowledge_base" / "metadata" / "topic_synonyms.json"
            raw = _read_json(synonym_path, {})
            for key, values in (raw.get("synonyms") or {}).items():
                if not isinstance(values, list):
                    continue
                bucket = self.thesaurus["terms"].setdefault(str(key).lower(), [])
                for value in values:
                    if value not in bucket:
                        bucket.append(value)
                if len(str(key)) <= 6 and str(key).isalpha():
                    expansion = values[0] if values else ""
                    if expansion:
                        self.acronyms.setdefault(str(key).upper(), {
                            "acronym": str(key).upper(),
                            "expansion": expansion,
                            "entry_id": "",
                        })
            self._dirty.add("thesaurus")
            if self.acronyms:
                self._dirty.add("acronyms")

    def flush(self) -> None:
        if self._batch or not self._loaded:
            return
        pretty = {"master_index.json", "glossary.json", "graph.json"}
        files = {
            "toc": ("master_index.json", self.toc),
            "cards": ("cards.json", self.cards),
            "thesaurus": ("thesaurus.json", self.thesaurus),
            "concordance": ("concordance.json", self.concordance),
            "glossary": ("glossary.json", self.glossary),
            "graph": ("graph.json", self.graph),
            "topical": ("topical.json", self.topical),
            "chronological": ("chronological.json", self.chronological),
            "sources": ("source_index.json", self.sources),
            "aliases": ("aliases.json", self.aliases),
            "acronyms": ("acronyms.json", self.acronyms),
            "backlinks": ("backlinks.json", self.backlinks),
            "dedupe": ("dedupe.json", self.dedupe),
            "state": ("index_state.json", self.state),
        }
        for name in list(self._dirty):
            filename, payload = files[name]
            _write_json(self.catalog / filename, payload, pretty=filename in pretty)
        self._dirty.clear()

    @contextmanager
    def batch(self):
        self._ensure()
        self._batch += 1
        try:
            yield self
        finally:
            self._batch -= 1
            if self._batch == 0:
                self.flush()

    def table_of_contents(self) -> dict:
        self._ensure()
        return self.toc

    def expand(self, query: str) -> list:
        self._ensure()
        words = terms(query)
        if not words and query:
            words = [normalize_key(query)]
        found = []
        seen = set()
        table = self.thesaurus.get("terms") or {}

        def add(value: str) -> None:
            key = normalize_key(value)
            if key and key not in seen:
                seen.add(key)
                found.append(key)

        for word in words:
            add(word)
        snapshot = list(found)
        for word in snapshot:
            if word in table:
                for synonym in table[word]:
                    add(synonym)
            for canon, synonyms in table.items():
                lowered = [normalize_key(item) for item in synonyms]
                if word == canon or word in lowered:
                    add(canon)
                    for synonym in lowered:
                        add(synonym)
        return found

    def set_synonyms(self, term: str, synonyms: list) -> dict:
        self._ensure()
        key = normalize_key(term)
        clean = []
        for item in synonyms:
            text = normalize_ws(str(item))
            if text and text.lower() not in clean:
                clean.append(text)
        self.thesaurus.setdefault("terms", {})[key] = clean
        self._dirty.add("thesaurus")
        self.flush()
        return {"term": key, "synonyms": clean}

    def remove_synonym_term(self, term: str) -> bool:
        self._ensure()
        key = normalize_key(term)
        if key not in self.thesaurus.get("terms", {}):
            return False
        del self.thesaurus["terms"][key]
        self._dirty.add("thesaurus")
        self.flush()
        return True

    def upsert_glossary(self, term: str, definition: str, entry_id: str = "", loc: str = "") -> dict:
        self._ensure()
        key = normalize_key(term)
        record = {
            "term": normalize_ws(term),
            "definition": normalize_ws(definition),
            "entry_id": entry_id or "",
            "loc": loc or "",
        }
        self.glossary.setdefault("terms", {})[key] = record
        self._dirty.add("glossary")
        self.flush()
        return record

    def delete_glossary(self, term: str) -> bool:
        self._ensure()
        key = normalize_key(term)
        if key not in self.glossary.get("terms", {}):
            return False
        del self.glossary["terms"][key]
        self._dirty.add("glossary")
        self.flush()
        return True

    def set_alias(self, alias: str, entry_id: str = "", loc: str = "", canonical: str = "") -> dict:
        self._ensure()
        key = normalize_key(alias)
        record = {"alias": normalize_ws(alias), "entry_id": entry_id or "", "loc": loc or "", "canonical": canonical or ""}
        self.aliases[key] = record
        self._dirty.add("aliases")
        self.flush()
        return record

    def delete_alias(self, alias: str) -> bool:
        self._ensure()
        key = normalize_key(alias)
        if key not in self.aliases:
            return False
        del self.aliases[key]
        self._dirty.add("aliases")
        self.flush()
        return True

    def set_acronym(self, acronym: str, expansion: str, entry_id: str = "") -> dict:
        self._ensure()
        key = acronym.strip().upper()
        record = {"acronym": key, "expansion": normalize_ws(expansion), "entry_id": entry_id or ""}
        self.acronyms[key] = record
        self._dirty.add("acronyms")
        self.flush()
        return record

    def delete_acronym(self, acronym: str) -> bool:
        self._ensure()
        key = acronym.strip().upper()
        if key not in self.acronyms:
            return False
        del self.acronyms[key]
        self._dirty.add("acronyms")
        self.flush()
        return True

    def link(self, source_id: str, target_id: str, rel: str = "related") -> dict:
        self._ensure()
        relation = rel if rel in RELATIONS else "related"
        edge = {"from": source_id, "to": target_id, "rel": relation}
        if edge not in self.graph["edges"]:
            self.graph["edges"].append(edge)
        if relation in {"related", "see_also", "contradicts"}:
            back = {"from": target_id, "to": source_id, "rel": relation}
            if back not in self.graph["edges"]:
                self.graph["edges"].append(back)
        self._rebuild_backlinks(source_id)
        self._rebuild_backlinks(target_id)
        self._dirty.add("graph")
        self._dirty.add("backlinks")
        self.flush()
        return edge

    def unlink(self, source_id: str, target_id: str, rel: str = "") -> int:
        self._ensure()
        kept = []
        removed = 0
        for edge in self.graph.get("edges", []):
            pair = {edge.get("from"), edge.get("to")} == {source_id, target_id}
            rel_ok = not rel or edge.get("rel") == rel
            if pair and rel_ok:
                removed += 1
                continue
            kept.append(edge)
        self.graph["edges"] = kept
        self._rebuild_backlinks(source_id)
        self._rebuild_backlinks(target_id)
        self._dirty.add("graph")
        self._dirty.add("backlinks")
        self.flush()
        return removed

    def _rebuild_backlinks(self, entry_id: str) -> None:
        incoming = []
        for edge in self.graph.get("edges", []):
            if edge.get("to") == entry_id:
                incoming.append({"from": edge.get("from"), "rel": edge.get("rel")})
        if incoming:
            self.backlinks[entry_id] = incoming
        else:
            self.backlinks.pop(entry_id, None)

    def add_entry(
        self,
        store: str,
        topic: str,
        subtopic: str,
        title: str,
        text: str = "",
        summary: str = "",
        source: str = "",
        when: str = "",
        loc: str = "",
        source_path: str = "",
        aliases: list | None = None,
        acronyms: dict | None = None,
        glossary: list | None = None,
        relations: list | None = None,
        text_ref: dict | None = None,
        extra_locs: list | None = None,
        force: bool = False,
    ) -> dict:
        self._ensure()
        store = store if store in STORES else "knowledge"
        topic = normalize_ws(topic) or "general"
        subtopic = normalize_ws(subtopic) or "general"
        title = normalize_ws(title) or "untitled"
        body = text or ""
        digest = content_hash(body or summary or title)
        entry_id = stable_id(store, topic, subtopic, title)

        if source_path and not force:
            previous = self.state.get(source_path)
            if previous and previous.get("hash") == digest and previous.get("id") == entry_id:
                return {"id": entry_id, "skipped": True, "duplicate": False}

        existing = self.dedupe.get(digest)
        if existing and existing != entry_id and body:
            if source:
                self._add_source(existing, source)
                self.flush()
            return {"id": existing, "skipped": False, "duplicate": True}

        if entry_id in self.cards and not force:
            if self.cards[entry_id].get("content_hash") == digest:
                return {"id": entry_id, "skipped": True, "duplicate": False}
            self._unindex(entry_id)

        summary_text = summary or summarize(body or title)
        card = {
            "id": entry_id,
            "store": store,
            "topic": topic,
            "subtopic": subtopic,
            "title": title,
            "summary": summary_text,
            "key_points": key_points(body or summary_text),
            "source": source or "",
            "when": when or "",
            "loc": loc or "",
            "content_hash": digest,
            "updated": _now(),
        }
        record = {
            "id": entry_id,
            "store": store,
            "topic": topic,
            "subtopic": subtopic,
            "title": title,
            "text": body,
            "text_ref": text_ref or {},
            "summary": summary_text,
            "source": source or "",
            "when": when or "",
            "loc": loc or "",
            "content_hash": digest,
            "updated": card["updated"],
        }
        path = self._entry_path(store, entry_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, record, pretty=True)

        self.cards[entry_id] = card
        self._place_toc(card)
        self._place_topical(card)
        self._place_chrono(card)
        self._add_source(entry_id, source)
        if body:
            self.dedupe[digest] = entry_id
            self._dirty.add("dedupe")
        self._index_words(entry_id, body, loc)
        for extra_loc, extra_text in extra_locs or []:
            self._index_words(entry_id, extra_text, extra_loc)
        for alias in aliases or []:
            self.aliases[normalize_key(alias)] = {
                "alias": alias,
                "entry_id": entry_id,
                "loc": loc or "",
                "canonical": title,
            }
            self._dirty.add("aliases")
        for acronym, expansion in (acronyms or {}).items():
            self.acronyms[str(acronym).upper()] = {
                "acronym": str(acronym).upper(),
                "expansion": expansion,
                "entry_id": entry_id,
            }
            self._dirty.add("acronyms")
        for item in glossary or []:
            self.upsert_glossary(
                item.get("term", ""),
                item.get("definition", ""),
                entry_id=entry_id,
                loc=item.get("loc") or loc or "",
            )
        for relation in relations or []:
            if relation.get("to"):
                self.link(entry_id, relation["to"], relation.get("rel") or "related")
        if source_path:
            self.state[source_path] = {"id": entry_id, "hash": digest, "updated": card["updated"]}
            self._dirty.add("state")
        self._dirty.update({"cards", "toc", "topical", "chronological", "concordance"})
        self.flush()
        return {"id": entry_id, "skipped": False, "duplicate": False, "card": card}

    def _place_toc(self, card: dict) -> None:
        topics = self.toc.setdefault("topics", {})
        topic = topics.setdefault(card["topic"], {"summary": "", "subtopics": {}})
        if not topic.get("summary"):
            topic["summary"] = card["summary"]
        bucket = topic.setdefault("subtopics", {}).setdefault(card["subtopic"], [])
        if card["id"] not in bucket:
            bucket.append(card["id"])

    def _place_topical(self, card: dict) -> None:
        tree = self.topical.setdefault("tree", {})
        topic = tree.setdefault(card["topic"].lower(), {})
        bucket = topic.setdefault(card["subtopic"].lower(), [])
        if card["id"] not in bucket:
            bucket.append(card["id"])
        term_map = self.topical.setdefault("terms", {})
        for word in terms(f"{card['topic']} {card['subtopic']} {card['title']}"):
            bucket = term_map.setdefault(word, [])
            if card["id"] not in bucket:
                bucket.append(card["id"])

    def _place_chrono(self, card: dict) -> None:
        year = (card.get("when") or "")[:4] or "unknown"
        if not re.fullmatch(r"\d{4}|unknown", year):
            year = "unknown"
        bucket = self.chronological.setdefault(year, [])
        if card["id"] not in bucket:
            bucket.append(card["id"])

    def _add_source(self, entry_id: str, source: str) -> None:
        if not source:
            return
        bucket = self.sources.setdefault(source, [])
        if entry_id not in bucket:
            bucket.append(entry_id)
            self._dirty.add("sources")

    def _index_words(self, entry_id: str, text: str, loc: str) -> None:
        if self._post_keys is None:
            self._post_keys = set()
            for word, posts in (self.concordance.get("terms") or {}).items():
                for post in posts:
                    if isinstance(post, list) and post:
                        self._post_keys.add((word, post[0], post[1] if len(post) > 1 else ""))
        owned = self.concordance.setdefault("by_entry", {}).setdefault(entry_id, [])
        for word in terms(text):
            key = (word, entry_id, loc or "")
            if key in self._post_keys:
                continue
            self._post_keys.add(key)
            self.concordance.setdefault("terms", {}).setdefault(word, []).append([entry_id, loc or ""])
            if word not in owned:
                owned.append(word)
        self._dirty.add("concordance")

    def _unindex(self, entry_id: str) -> None:
        card = self.cards.get(entry_id) or {}
        words = list((self.concordance.get("by_entry") or {}).get(entry_id) or [])
        for word in words:
            posts = (self.concordance.get("terms") or {}).get(word) or []
            kept = [post for post in posts if not post or post[0] != entry_id]
            if kept:
                self.concordance["terms"][word] = kept
            else:
                self.concordance["terms"].pop(word, None)
            if self._post_keys is not None:
                self._post_keys = {item for item in self._post_keys if item[1] != entry_id}
        self.concordance.get("by_entry", {}).pop(entry_id, None)

        topic_name = card.get("topic")
        subtopic = card.get("subtopic")
        if topic_name and self.toc.get("topics", {}).get(topic_name):
            bucket = self.toc["topics"][topic_name].get("subtopics", {}).get(subtopic, [])
            self.toc["topics"][topic_name]["subtopics"][subtopic] = [item for item in bucket if item != entry_id]
        tree = self.topical.get("tree", {})
        if topic_name and topic_name.lower() in tree and subtopic:
            bucket = tree[topic_name.lower()].get(subtopic.lower(), [])
            tree[topic_name.lower()][subtopic.lower()] = [item for item in bucket if item != entry_id]
        for word, ids in list((self.topical.get("terms") or {}).items()):
            self.topical["terms"][word] = [item for item in ids if item != entry_id]
        for year, ids in list(self.chronological.items()):
            self.chronological[year] = [item for item in ids if item != entry_id]
        for source, ids in list(self.sources.items()):
            self.sources[source] = [item for item in ids if item != entry_id]
        for key, record in list(self.glossary.get("terms", {}).items()):
            if record.get("entry_id") == entry_id:
                del self.glossary["terms"][key]
        for key, record in list(self.aliases.items()):
            if record.get("entry_id") == entry_id:
                del self.aliases[key]
        self.graph["edges"] = [
            edge for edge in self.graph.get("edges", [])
            if edge.get("from") != entry_id and edge.get("to") != entry_id
        ]
        self.backlinks.pop(entry_id, None)
        for other in list(self.backlinks):
            self.backlinks[other] = [item for item in self.backlinks[other] if item.get("from") != entry_id]
        for digest, owner in list(self.dedupe.items()):
            if owner == entry_id:
                del self.dedupe[digest]
        for path, info in list(self.state.items()):
            if info.get("id") == entry_id:
                del self.state[path]
        self.cards.pop(entry_id, None)
        self._dirty.update({
            "cards", "toc", "topical", "chronological", "sources", "concordance",
            "glossary", "aliases", "graph", "backlinks", "dedupe", "state",
        })

    def archive_entry(self, entry_id: str) -> dict:
        self._ensure()
        card = self.cards.get(entry_id)
        if not card:
            return {"ok": False, "error": "No entry with that id."}
        source = self._entry_path(card.get("store") or "knowledge", entry_id)
        dest_dir = self.root / "retired" / "stores" / (card.get("store") or "knowledge")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{entry_id}.json"
        if source.exists():
            dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            source.unlink()
        elif not dest.exists():
            _write_json(dest, {"id": entry_id, "card": card, "archived": _now()}, pretty=True)
        self._unindex(entry_id)
        self.flush()
        return {"ok": True, "archived": str(dest)}

    def get_entry(self, entry_id: str) -> dict | None:
        self._ensure()
        card = self.cards.get(entry_id)
        if not card:
            return None
        path = self._entry_path(card.get("store") or "knowledge", entry_id)
        record = _read_json(path, None)
        if not record:
            record = dict(card)
            record["text"] = ""
        return record

    def get_full(self, entry_id: str, loc: str = "") -> str:
        if loc:
            verse = self.verse_text(loc)
            if verse:
                return verse
        record = self.get_entry(entry_id) or {}
        if record.get("text"):
            return record["text"]
        ref = record.get("text_ref") or {}
        if ref.get("kind") == "bible":
            return self.chapter_text(ref.get("book"), ref.get("chapter"))
        return record.get("summary") or ""

    def verse_text(self, loc: str) -> str:
        parsed = parse_citation(loc) if loc else None
        if not parsed:
            return ""
        book, chapter, verse = parsed
        from .bible import read_verse

        return read_verse(self.bible_dir, book, chapter, verse)

    def chapter_text(self, book: str, chapter) -> str:
        from .bible import read_chapter

        verses = read_chapter(self.bible_dir, book, int(chapter))
        return " ".join(item["text"] for item in verses)

    def _card_hit(self, entry_id: str, score: float, index_name: str, loc: str = "", summary: str = "") -> dict | None:
        card = self.cards.get(entry_id)
        if not card and not summary:
            return None
        card = card or {"id": entry_id, "title": loc or entry_id, "summary": summary, "store": "knowledge", "topic": "", "subtopic": ""}
        hit = {
            "id": entry_id,
            "title": loc or card.get("title") or entry_id,
            "summary": summary or card.get("summary") or "",
            "score": round(float(score), 3),
            "store": card.get("store") or "knowledge",
            "topic": card.get("topic") or "",
            "subtopic": card.get("subtopic") or "",
            "index": index_name,
            "loc": loc or card.get("loc") or "",
            "source": card.get("source") or "",
        }
        return hit

    def _consult(self, index_name: str, query: str, expanded: list) -> list:
        hits = []
        lowered = normalize_key(query)
        if index_name == "aliases":
            for word in expanded:
                record = self.aliases.get(word)
                if record:
                    hit = self._card_hit(record.get("entry_id") or word, 0.97, "aliases", record.get("loc") or "")
                    if hit:
                        if record.get("loc") and not hit["summary"]:
                            hit["summary"] = self.verse_text(record["loc"]) or hit["summary"]
                        hits.append(hit)
        elif index_name == "acronyms":
            token = query.strip().split()[0].upper() if query.strip() else ""
            record = self.acronyms.get(token)
            if record:
                hit = self._card_hit(record.get("entry_id") or token, 0.95, "acronyms")
                if hit:
                    hit["summary"] = record.get("expansion") or hit["summary"]
                    hit["title"] = record.get("acronym") or hit["title"]
                    hits.append(hit)
        elif index_name == "glossary":
            for word in expanded:
                record = self.glossary.get("terms", {}).get(word)
                if record:
                    hit = self._card_hit(record.get("entry_id") or word, 0.9, "glossary", record.get("loc") or "")
                    if hit:
                        hit["title"] = record.get("term") or hit["title"]
                        hit["summary"] = record.get("definition") or hit["summary"]
                        hits.append(hit)
        elif index_name == "concordance":
            citation = parse_citation(query)
            if citation:
                book, chapter, verse = citation
                loc = f"{book} {chapter}:{verse}"
                text = self.verse_text(loc)
                if text:
                    hits.append({
                        "id": loc,
                        "title": loc,
                        "summary": text,
                        "score": 1.0,
                        "store": "knowledge",
                        "topic": "scripture",
                        "subtopic": book,
                        "index": "concordance",
                        "loc": loc,
                        "source": "KJV",
                    })
            for word in expanded:
                for post in (self.concordance.get("terms") or {}).get(word) or []:
                    entry_id = post[0]
                    loc = post[1] if len(post) > 1 else ""
                    summary = self.verse_text(loc) if loc else ""
                    hit = self._card_hit(entry_id, 0.62, "concordance", loc, summary)
                    if hit:
                        hits.append(hit)
                    if len(hits) >= 12:
                        break
        elif index_name == "topical":
            for word in expanded:
                for entry_id in (self.topical.get("terms") or {}).get(word) or []:
                    hit = self._card_hit(entry_id, 0.75, "topical")
                    if hit:
                        hits.append(hit)
        elif index_name == "cards":
            for entry_id, card in self.cards.items():
                blob = normalize_key(f"{card.get('title','')} {card.get('summary','')} {card.get('topic','')}")
                if any(word in blob for word in expanded) or (lowered and lowered in blob):
                    score = 0.8 if lowered and lowered in normalize_key(card.get("title") or "") else 0.55
                    hit = self._card_hit(entry_id, score, "cards")
                    if hit:
                        hits.append(hit)
        elif index_name == "chronological":
            match = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", query or "")
            year = match.group(1) if match else ""
            for entry_id in self.chronological.get(year, []):
                hit = self._card_hit(entry_id, 0.7, "chronological")
                if hit:
                    hits.append(hit)
        elif index_name == "source":
            label = query.split(":", 1)[-1].strip() if ":" in query else query
            label = label[5:].strip() if label.lower().startswith("from ") else label
            for source, ids in self.sources.items():
                if label.lower() in source.lower():
                    for entry_id in ids:
                        hit = self._card_hit(entry_id, 0.7, "source")
                        if hit:
                            hits.append(hit)
        elif index_name == "graph":
            wanted = "contradicts" if "contradict" in lowered else "supports" if "support" in lowered else "see_also"
            for edge in self.graph.get("edges", []):
                if wanted != "see_also" and edge.get("rel") != wanted:
                    continue
                hit = self._card_hit(edge.get("to"), 0.66, "graph")
                if hit:
                    hit["rel"] = edge.get("rel")
                    hits.append(hit)
        return hits

    def retrieve(self, query: str, limit: int = 5, full_text: bool = False) -> dict:
        self._ensure()
        order = route(query, self.acronyms)
        expanded = self.expand(query)
        hits = []
        seen = set()
        for index_name in order:
            for hit in self._consult(index_name, query, expanded):
                key = (hit.get("id"), hit.get("loc"), hit.get("index"))
                if key in seen:
                    continue
                seen.add(key)
                hits.append(hit)
            if len(hits) >= limit and hits[0].get("score", 0) >= 0.8:
                break
        for hit in hits:
            source = (hit.get("source") or "").lower()
            if "oxidus-working" in source or "provisional" in source:
                hit["score"] = round(float(hit.get("score") or 0) * 0.6, 3)
                hit["weight"] = "below established translations"
        hits.sort(key=lambda item: item.get("score", 0), reverse=True)
        hits = hits[:limit]
        if full_text:
            for hit in hits:
                hit["text"] = self.get_full(hit.get("id"), hit.get("loc") or "")
        return {"hits": hits, "cards": hits, "expanded": expanded, "indexes": order}

    def build_context(self, query: str, budget_tokens: int = 500, full_text: bool = False) -> dict:
        found = self.retrieve(query, limit=8, full_text=False)
        lines = []
        kept = []
        used = 0
        for hit in found["hits"]:
            line = f"{hit.get('title')}: {hit.get('summary')}"
            cost = estimate_tokens(line)
            if kept and used + cost > budget_tokens:
                break
            if not kept and cost > budget_tokens:
                line = trim_to_tokens(line, budget_tokens)
                cost = estimate_tokens(line)
            lines.append(line)
            used += cost
            kept.append(hit)
            if used >= budget_tokens:
                break
        if full_text and kept and used < budget_tokens:
            body = self.get_full(kept[0].get("id"), kept[0].get("loc") or "")
            remain = budget_tokens - used
            if body and remain > 16:
                snippet = trim_to_tokens(body, remain)
                if snippet and snippet not in (kept[0].get("summary") or ""):
                    lines.append(snippet)
                    used += estimate_tokens(snippet)
        return {
            "context": "\n".join(lines),
            "tokens": used,
            "cards": kept,
            "hits": kept,
            "expanded": found["expanded"],
            "indexes": found["indexes"],
        }

    def consolidate_thoughts(self, thoughts: list, keep_recent: int = 10) -> dict:
        self._ensure()
        if not thoughts or len(thoughts) <= keep_recent:
            return {"compressed": 0, "kept": len(thoughts or []), "id": "", "summary": ""}
        old = thoughts[:-keep_recent]
        lines = []
        for item in old:
            content = item if isinstance(item, str) else (item.get("content") or "")
            line = normalize_ws(content).split(". ")[0][:180]
            if line and line not in lines:
                lines.append(line)
        summary = " ".join(lines[:12])
        stamp = _now()
        saved = self.add_entry(
            store="working",
            topic="thoughts",
            subtopic="consolidated",
            title=f"Thoughts {stamp}",
            text=summary,
            summary=summarize(summary, 240),
            source="thought_stream",
            when=stamp[:10],
        )
        return {
            "compressed": len(old),
            "kept": keep_recent,
            "id": saved.get("id"),
            "summary": summary,
        }

    def reindex_file(self, path: Path, store: str = "knowledge") -> dict:
        path = Path(path)
        raw = _read_json(path, None)
        if not isinstance(raw, dict):
            text = path.read_text(encoding="utf-8", errors="replace")
            title = path.stem
            body = text
            topic = path.parent.name or "general"
        else:
            title = raw.get("title") or path.stem
            body = raw.get("content") or raw.get("text") or ""
            topic = raw.get("category") or path.parent.name or "general"
        if not body:
            return {"skipped": True, "reason": "empty"}
        return self.add_entry(
            store=store,
            topic=str(topic),
            subtopic="general",
            title=str(title),
            text=str(body),
            source=str(raw.get("author") if isinstance(raw, dict) else "") or path.name,
            when=str((raw or {}).get("publication_year") or "") if isinstance(raw, dict) else "",
            source_path=str(path),
        )

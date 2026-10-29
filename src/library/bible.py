"""King James Bible on disk, addressed by book, chapter, and verse.

The text is the public-domain King James Version. Theme tags are ours.
"""

from __future__ import annotations

from pathlib import Path
import json
import re

BOOKS = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua",
    "Judges", "Ruth", "1 Samuel", "2 Samuel", "1 Kings", "2 Kings",
    "1 Chronicles", "2 Chronicles", "Ezra", "Nehemiah", "Esther", "Job",
    "Psalms", "Proverbs", "Ecclesiastes", "Song of Solomon", "Isaiah",
    "Jeremiah", "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel",
    "Amos", "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk", "Zephaniah",
    "Haggai", "Zechariah", "Malachi", "Matthew", "Mark", "Luke", "John",
    "Acts", "Romans", "1 Corinthians", "2 Corinthians", "Galatians",
    "Ephesians", "Philippians", "Colossians", "1 Thessalonians",
    "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus", "Philemon",
    "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John", "3 John",
    "Jude", "Revelation",
]

ALIASES = {
    "gen": "Genesis", "ex": "Exodus", "exod": "Exodus", "lev": "Leviticus",
    "num": "Numbers", "deut": "Deuteronomy", "josh": "Joshua", "judg": "Judges",
    "ps": "Psalms", "psa": "Psalms", "psalm": "Psalms", "prov": "Proverbs",
    "eccl": "Ecclesiastes", "song": "Song of Solomon", "isa": "Isaiah",
    "jer": "Jeremiah", "lam": "Lamentations", "ezek": "Ezekiel", "dan": "Daniel",
    "hos": "Hosea", "obad": "Obadiah", "mic": "Micah", "nah": "Nahum",
    "hab": "Habakkuk", "zeph": "Zephaniah", "hag": "Haggai", "zech": "Zechariah",
    "mal": "Malachi", "matt": "Matthew", "mt": "Matthew", "mk": "Mark",
    "lk": "Luke", "jn": "John", "rom": "Romans", "1 cor": "1 Corinthians",
    "2 cor": "2 Corinthians", "gal": "Galatians", "eph": "Ephesians",
    "phil": "Philippians", "col": "Colossians", "1 thess": "1 Thessalonians",
    "2 thess": "2 Thessalonians", "1 tim": "1 Timothy", "2 tim": "2 Timothy",
    "heb": "Hebrews", "jas": "James", "1 pet": "1 Peter", "2 pet": "2 Peter",
    "1 jn": "1 John", "rev": "Revelation",
}

THEME_PATTERNS = {
    "justice": [r"\bjustice\b", r"\bjudgment\b", r"\bjudge\b"],
    "mercy": [r"\bmercy\b", r"\bmerciful\b", r"\bcompassion\b"],
    "honesty": [r"\bfalse witness\b", r"\btruth\b", r"\blie\b", r"\bdeceit\b"],
    "humility": [r"\bhumble\b", r"\bhumility\b", r"\bmeek\b"],
    "love": [r"\blove\b", r"\bcharity\b"],
    "forgiveness": [r"\bforgiv\w*\b"],
    "integrity": [r"\bintegrity\b", r"\bupright\b"],
    "kindness": [r"\bkindness\b", r"\blovingkindness\b"],
    "stewardship": [r"\bfaithful\b", r"\bsteward\b"],
}

ANCHORS = [
    ("Matthew", 7, 12, ["golden_rule", "right_wrong"]),
    ("Luke", 6, 31, ["golden_rule", "right_wrong"]),
    ("Micah", 6, 8, ["justice", "mercy", "humility"]),
    ("Exodus", 20, 16, ["honesty"]),
    ("Proverbs", 12, 22, ["honesty", "integrity"]),
    ("Matthew", 5, 7, ["mercy"]),
    ("Zechariah", 7, 9, ["justice", "mercy"]),
    ("1 Corinthians", 13, 4, ["love", "kindness"]),
    ("Matthew", 6, 14, ["forgiveness"]),
    ("Luke", 16, 10, ["stewardship", "integrity"]),
]


def file_slug(book: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", book)


def canonical_book(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    if cleaned in BOOKS:
        return cleaned
    key = cleaned.lower()
    if key in ALIASES:
        return ALIASES[key]
    for book in BOOKS:
        if book.lower() == key:
            return book
    return cleaned


def themes_for(text: str) -> list:
    found = []
    for name, patterns in THEME_PATTERNS.items():
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            found.append(name)
    return found


def book_path(bible_dir: Path, book: str) -> Path:
    return Path(bible_dir) / f"{file_slug(canonical_book(book))}.json"


def read_book(bible_dir: Path, book: str) -> dict:
    path = book_path(bible_dir, book)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_chapter(bible_dir: Path, book: str, chapter: int) -> list:
    data = read_book(bible_dir, book)
    verses = ((data.get("chapters") or {}).get(str(chapter)) or {})
    rows = []
    for number, payload in verses.items():
        if isinstance(payload, dict):
            rows.append({
                "verse": int(number),
                "text": payload.get("text") or "",
                "themes": payload.get("themes") or [],
            })
    rows.sort(key=lambda item: item["verse"])
    return rows


def read_verse(bible_dir: Path, book: str, chapter: int, verse: int) -> str:
    data = read_book(bible_dir, book)
    payload = ((data.get("chapters") or {}).get(str(chapter)) or {}).get(str(verse)) or {}
    if isinstance(payload, dict):
        return payload.get("text") or ""
    return ""


def list_books(bible_dir: Path) -> list:
    bible_dir = Path(bible_dir)
    if not bible_dir.exists():
        return []
    catalog_path = bible_dir / "catalog.json"
    if catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            names = catalog.get("books") or []
            if names:
                return [name for name in names if book_path(bible_dir, name).exists() or (bible_dir / f"{file_slug(name)}.json").exists()]
        except Exception:
            pass
    found = []
    for book in BOOKS:
        if book_path(bible_dir, book).exists():
            found.append(book)
    return found


def convert_source_book(raw: dict, testament: str) -> dict:
    chapters = {}
    for chapter in raw.get("chapters") or []:
        number = str(chapter.get("chapter"))
        verses = {}
        for verse in chapter.get("verses") or []:
            text = verse.get("text") or ""
            verses[str(verse.get("verse"))] = {"text": text, "themes": themes_for(text)}
        chapters[number] = verses
    book = raw.get("book") or ""
    for anchor_book, chapter, verse, extra in ANCHORS:
        if anchor_book != book:
            continue
        payload = chapters.get(str(chapter), {}).get(str(verse))
        if not payload:
            continue
        for theme in extra:
            if theme not in payload["themes"]:
                payload["themes"].append(theme)
    return {
        "translation": "KJV",
        "book": book,
        "testament": testament,
        "license": "Public domain King James Version.",
        "chapters": chapters,
    }


def ingest_sources(library, source_dir: Path, bible_dir: Path | None = None) -> dict:
    """Convert raw KJV JSON into the shelf and the book files."""
    source_dir = Path(source_dir)
    bible_dir = Path(bible_dir or library.bible_dir)
    bible_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with library.batch():
        for index, book in enumerate(BOOKS):
            raw_path = source_dir / f"{file_slug(book)}.json"
            if not raw_path.exists():
                continue
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            testament = "old" if index < 39 else "new"
            converted = convert_source_book(raw, testament)
            dest = bible_dir / f"{file_slug(book)}.json"
            dest.write_text(json.dumps(converted, ensure_ascii=False) + "\n", encoding="utf-8")
            written.append(book)
            for chapter, verses in converted["chapters"].items():
                ordered = sorted(verses.items(), key=lambda item: int(item[0]))
                if not ordered:
                    continue
                first = ordered[0][1].get("text") or ""
                extra = []
                for verse_no, payload in ordered:
                    extra.append((f"{book} {chapter}:{verse_no}", payload.get("text") or ""))
                library.add_entry(
                    store="knowledge",
                    topic="scripture",
                    subtopic=book,
                    title=f"{book} {chapter}",
                    text="",
                    summary=first[:280],
                    source="KJV",
                    loc=f"{book} {chapter}:1",
                    source_path=str(dest) + f"#chapter-{chapter}",
                    text_ref={"kind": "bible", "book": book, "chapter": int(chapter)},
                    extra_locs=extra,
                )
        _anchor_notes(library)
    catalog = {
        "translation": "KJV",
        "books": written,
        "count": len(written),
    }
    (bible_dir / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    return catalog


def _anchor_notes(library) -> None:
    notes = {
        "golden rule": (
            "Treat people the way you would want to be treated.",
            "Matthew 7:12",
        ),
        "mercy": (
            "Hold back the harm you could do, and do it without dropping justice.",
            "Micah 6:8",
        ),
        "justice": (
            "Give people what is right, and do not sell the judgment.",
            "Micah 6:8",
        ),
        "honesty": (
            "Do not lie about your neighbor. A false weight and a false word are the same kind of cheat.",
            "Exodus 20:16",
        ),
        "humility": (
            "Walk quietly. Strength that needs an audience has already gone wrong.",
            "Micah 6:8",
        ),
        "love": (
            "Love is patient and does not keep a score to use later.",
            "1 Corinthians 13:4",
        ),
        "forgiveness": (
            "If you want to be forgiven, you do not get to clutch the grudge.",
            "Matthew 6:14",
        ),
        "integrity": (
            "The small trust is the interview for the large one.",
            "Luke 16:10",
        ),
        "stewardship": (
            "What you are given is not a trophy. Keep it faithfully.",
            "Luke 16:10",
        ),
    }
    for term, (definition, loc) in notes.items():
        library.upsert_glossary(term, definition, loc=loc)
        library.set_alias(term, loc=loc, canonical=term)

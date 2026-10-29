#!/usr/bin/env python3
"""Add the wider public-domain canon around the 66-book KJV.

Protestant text already on disk is only stamped with tradition tags.
Verse wording of those books is not rewritten.
"""

from __future__ import annotations

from pathlib import Path
import json
import re
import time
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BIBLE_DIR = ROOT / "data" / "knowledge_base" / "bible" / "kjv"
KJV_ZIP = Path("/tmp/bible-src/kjv.zip")
WEB_XML = Path("/tmp/bible-src/web.xml")
MEQ_DIR = Path("/tmp/bible-src/meqepub/EPUB/text")

import sys
sys.path.insert(0, str(ROOT))
from src.library.bible import file_slug, themes_for

SHARED = ["protestant", "catholic", "orthodox", "ethiopian"]
DEUTERO = ["catholic", "orthodox", "ethiopian"]
ORTHODOX = ["orthodox"]

KJV_EXTRA = {
    "TOB": ("Tobit", DEUTERO, "old", "King James Version 1611 Apocrypha"),
    "JDT": ("Judith", DEUTERO, "old", "King James Version 1611 Apocrypha"),
    "ESG": ("Additions to Esther", DEUTERO, "old", "King James Version 1611 Apocrypha"),
    "WIS": ("Wisdom of Solomon", DEUTERO, "old", "King James Version 1611 Apocrypha"),
    "SIR": ("Sirach", DEUTERO, "old", "King James Version 1611 Apocrypha"),
    "BAR": ("Baruch", DEUTERO, "old", "King James Version 1611 Apocrypha"),
    "S3Y": ("Prayer of Azariah", DEUTERO, "old", "King James Version 1611 Apocrypha, Additions to Daniel"),
    "SUS": ("Susanna", DEUTERO, "old", "King James Version 1611 Apocrypha, Additions to Daniel"),
    "BEL": ("Bel and the Dragon", DEUTERO, "old", "King James Version 1611 Apocrypha, Additions to Daniel"),
    "1MA": ("1 Maccabees", DEUTERO, "old", "King James Version 1611 Apocrypha"),
    "2MA": ("2 Maccabees", DEUTERO, "old", "King James Version 1611 Apocrypha"),
    "1ES": ("1 Esdras", ORTHODOX, "old", "King James Version 1611 Apocrypha; Eastern canon, not the Roman Catholic list"),
    "MAN": ("Prayer of Manasseh", ORTHODOX, "old", "King James Version 1611 Apocrypha"),
    "2ES": ("2 Esdras", ["orthodox"], "old", "King James Version 1611 Apocrypha; Slavonic and KJV appendix, not the Roman Catholic list"),
}

WEB_EXTRA = {
    "3MA": ("3 Maccabees", ORTHODOX, "old", "World English Bible, public domain"),
    "4MA": ("4 Maccabees", ORTHODOX, "old", "World English Bible, public domain; Greek appendix"),
    "PS2": ("Psalm 151", ORTHODOX, "old", "World English Bible, public domain"),
}


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<f\b.*?</f>", " ", fragment, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("¶", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_usfx_book(xml: str, book_id: str) -> dict:
    match = re.search(rf'<book[^>]*\bid="{book_id}"\b.*?</book>', xml, flags=re.S)
    if not match:
        return {}
    block = match.group(0)
    chapters = {}
    parts = re.split(r'<c id="(\d+)"\s*/>', block)
    # parts[0] is preamble, then id, body, id, body...
    index = 1
    while index + 1 < len(parts):
        chapter = parts[index]
        body = parts[index + 1]
        verses = {}
        for verse_id, raw in re.findall(r'<v id="(\d+)"[^>]*/>\s*(.*?)<ve\s*/>', body, flags=re.S):
            text = strip_tags(raw)
            if text:
                verses[verse_id] = {"text": text, "themes": themes_for(text)}
        if verses:
            chapters[chapter] = verses
        index += 2
    return chapters


def book_record(name, traditions, testament, edition, translation, chapters, weight="established", status="public-domain"):
    return {
        "translation": translation,
        "translation_status": status,
        "weight": weight,
        "book": name,
        "testament": testament,
        "traditions": traditions,
        "source_edition": edition,
        "license": "Public domain." if status == "public-domain" else "CC0 1.0 public-domain dedication.",
        "chapters": chapters,
    }


def write_book(record: dict) -> None:
    path = BIBLE_DIR / f"{file_slug(record['book'])}.json"
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def stamp_protestant() -> int:
    count = 0
    for path in BIBLE_DIR.glob("*.json"):
        if path.name == "catalog.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("translation") != "KJV":
            continue
        if data.get("book") not in {
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
        }:
            continue
        before = json.dumps(data.get("chapters"), ensure_ascii=False)
        data["traditions"] = SHARED
        data["translation_status"] = "public-domain"
        data["weight"] = "established"
        data["source_edition"] = data.get("source_edition") or "King James Version, public domain"
        after = json.dumps(data.get("chapters"), ensure_ascii=False)
        if before != after:
            raise SystemExit(f"Refusing to rewrite verses in {path.name}")
        path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        count += 1
    return count


def load_kjv_xml() -> str:
    with zipfile.ZipFile(KJV_ZIP) as archive:
        return archive.read("eng-kjv_usfx.xml").decode("utf-8")


def add_usfx_books(xml: str, table: dict, translation: str) -> list:
    written = []
    for book_id, (name, traditions, testament, edition) in table.items():
        chapters = parse_usfx_book(xml, book_id)
        if book_id == "BAR" and "6" in chapters:
            letter = {"1": chapters.pop("6")}
            write_book(book_record(
                "Letter of Jeremiah",
                DEUTERO,
                "old",
                "King James Version 1611 Apocrypha (Baruch 6, the letter)",
                "KJV",
                letter,
            ))
            written.append("Letter of Jeremiah")
        if not chapters:
            print("missing", book_id)
            continue
        write_book(book_record(name, traditions, testament, edition, translation, chapters))
        written.append(name)
    return written


def html_lines(path: Path) -> list:
    raw = path.read_text(encoding="utf-8")
    raw = re.sub(r"</(p|div|h1|h2|h3|li)>", "\n", raw, flags=re.I)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", "", raw)
    lines = []
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def parse_meqabyan(lines: list) -> dict:
    chapters = {}
    current = None
    verses = {}
    expect = 1

    def flush():
        nonlocal verses
        if current and verses:
            chapters[str(current)] = verses
        verses = {}

    index = 0
    while index < len(lines):
        header = re.fullmatch(r"Chapter (\d+)", lines[index])
        if header:
            window = lines[index + 1:index + 8]
            if any(re.match(r"^1\s+[A-Z\"“]", item) for item in window):
                flush()
                current = int(header.group(1))
                expect = 1
            index += 1
            continue
        match = re.match(r"^(\d+)\s+(.+)$", lines[index])
        if current and match:
            number = int(match.group(1))
            text = match.group(2).strip()
            if number == 1 or number == expect:
                if len(text) > 40 and not text.lower().startswith("mq has"):
                    verses[str(number)] = {"text": text, "themes": themes_for(text)}
                    expect = number + 1
        index += 1
    flush()
    return chapters


def add_meqabyan() -> list:
    files = {
        "1 Meqabyan": MEQ_DIR / "ch002.xhtml",
        "2 Meqabyan": MEQ_DIR / "ch004.xhtml",
        "3 Meqabyan": MEQ_DIR / "ch006.xhtml",
    }
    edition = (
        "CC0 1.0 English translation from the modern Amharic EOTC text, "
        "Internet Archive item three-books-of-meqabyan-cc0-translation (2026). "
        "Not from Ge'ez. Provisional: weigh below KJV, WEB, and Charles."
    )
    written = []
    for name, path in files.items():
        chapters = parse_meqabyan(html_lines(path))
        record = book_record(
            name, ["ethiopian"], "old", edition, "CC0 English",
            chapters, weight="provisional", status="public-domain-dedication",
        )
        write_book(record)
        written.append(name)
        print(name, "chapters", len(chapters), "verses", sum(len(v) for v in chapters.values()))
    return written


def wiki_text(title: str) -> str:
    url = "https://en.wikisource.org/w/api.php?" + urllib.parse.urlencode({
        "action": "parse",
        "page": title,
        "prop": "wikitext",
        "format": "json",
    })
    request = urllib.request.Request(url, headers={"User-Agent": "OxidusCanon/1.0 (public-domain study)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["parse"]["wikitext"]["*"]


def clean_wiki(text: str) -> str:
    text = re.sub(r"\{\{[\s\S]*?\}\}", " ", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    text = text.replace("''", "")
    text = text.replace("⌈⌈", "").replace("⌉⌉", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def verses_from_wikitext(text: str) -> dict:
    body = clean_wiki(text)
    body = re.sub(r"=+[^=]+?=+", " ", body)
    body = re.sub(r"CHAPTER [IVXLC]+", " ", body)
    parts = re.split(r"(?:(?<=\s)|^)(\d{1,3})\.\s+", body)
    verses = {}
    # parts: preamble, num, text, num, text...
    index = 1
    while index + 1 < len(parts):
        number = parts[index]
        chunk = parts[index + 1].strip(" ;")
        if chunk:
            verses[number] = {"text": chunk, "themes": themes_for(chunk)}
        index += 2
    return verses


def add_enoch() -> str:
    index = wiki_text("The_Book_of_Enoch_(Charles)")
    numbers = re.findall(r"\[\[/Chapter (\d+)\]\]", index)
    chapters = {}
    for number in numbers:
        title = f"The_Book_of_Enoch_(Charles)/Chapter_{number}"
        try:
            raw = wiki_text(title)
        except Exception as exc:
            print("enoch miss", number, exc)
            continue
        verses = verses_from_wikitext(raw)
        if verses:
            chapters[str(int(number))] = verses
        time.sleep(0.12)
        if int(number) % 20 == 0:
            print("enoch", number, "verses", len(verses))
    write_book(book_record(
        "1 Enoch",
        ["ethiopian"],
        "old",
        "R.H. Charles, The Book of Enoch, 1917. Public-domain English, kept as Charles wrote it.",
        "Charles 1917",
        chapters,
    ))
    print("1 Enoch chapters", len(chapters))
    return "1 Enoch"


def main() -> int:
    BIBLE_DIR.mkdir(parents=True, exist_ok=True)
    stamped = stamp_protestant()
    print("stamped protestant books", stamped)
    kjv_xml = load_kjv_xml()
    web_xml = WEB_XML.read_text(encoding="utf-8")
    names = []
    names.extend(add_usfx_books(kjv_xml, KJV_EXTRA, "KJV"))
    names.extend(add_usfx_books(web_xml, WEB_EXTRA, "WEB"))
    names.extend(add_meqabyan())
    names.append(add_enoch())
    order = []
    seen = set()
    for path in sorted(BIBLE_DIR.glob("*.json")):
        if path.name == "catalog.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        book = data.get("book")
        if book and book not in seen:
            seen.add(book)
            order.append(book)
    # Prefer a readable order: existing catalog protestant order, then the new names.
    catalog = {
        "books": order,
        "count": len(order),
        "not_ingested": [
            {
                "book": "Jubilees",
                "reason": "R.H. Charles 1902 is public domain, so it was not retranslated. The copy on hand is a commentary whose OCR mixes notes into the translation, so it was not stored as scripture.",
            },
            {
                "book": "1-3 Meqabyan working translation",
                "reason": "No clean public-domain Ge'ez transcription was readable. A CC0 English translation from modern Amharic was kept instead of an Oxidus working translation.",
            },
        ],
        "working_translations": [],
    }
    (BIBLE_DIR / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print("total books", len(order))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

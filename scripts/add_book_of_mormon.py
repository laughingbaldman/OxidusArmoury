#!/usr/bin/env python3
"""Load the public-domain Book of Mormon from Project Gutenberg eBook 17.

The 1830 printing has chapters and no verse numbers. Gutenberg keeps the
later public-domain verse divisions, which is what we store.
"""

from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.library.bible import file_slug, themes_for

SOURCE = Path("/tmp/gutenberg-bom.txt")
OUT = ROOT / "data" / "knowledge_base" / "bible" / "book-of-mormon"
CHAPTER = re.compile(
    r"^(1 Nephi|2 Nephi|Jacob|Enos|Jarom|Omni|Words of Mormon|Mosiah|Alma|Helaman|3 Nephi|4 Nephi|Mormon|Ether|Moroni) Chapter (\d+)$"
)
TITLES = {
    "THE BOOK OF ENOS": ("Enos", "1"),
    "THE BOOK OF JAROM": ("Jarom", "1"),
    "THE BOOK OF OMNI": ("Omni", "1"),
    "THE WORDS OF MORMON": ("Words of Mormon", "1"),
    "FOURTH NEPHI": ("4 Nephi", "1"),
}
VERSE = re.compile(r"^(?:[A-Za-z0-9 ]+ )?(\d+):(\d+)\s+(.*)$")
EDITION = (
    "Project Gutenberg eBook #17, public domain. "
    "Verse numbers are the public-domain versification. "
    "The 1830 first edition has the same narrative in chapters without these verse numbers."
)


def download() -> Path:
    if SOURCE.exists() and SOURCE.stat().st_size > 100000:
        return SOURCE
    import urllib.request
    url = "https://www.gutenberg.org/files/17/17-0.txt"
    urllib.request.urlretrieve(url, SOURCE)
    return SOURCE


def parse(text: str) -> dict:
    books = {}
    book = None
    chapter = None
    verse = None
    buf = []

    def flush():
        nonlocal buf, verse
        if book and chapter and verse and buf:
            verse_text = re.sub(r"\s+", " ", " ".join(buf)).strip()
            pieces = re.split(rf"\s+{re.escape(chapter)}:(\d+)\s+", verse_text)
            numbered = [(verse, pieces[0].strip())]
            index = 1
            while index + 1 < len(pieces):
                numbered.append((pieces[index], pieces[index + 1].strip()))
                index += 2
            for number, chunk in numbered:
                if not chunk:
                    continue
                books.setdefault(book, {}).setdefault(chapter, {})[number] = {
                    "text": chunk,
                    "themes": themes_for(chunk),
                }
        buf = []

    for raw in text.splitlines():
        line = raw.strip()
        if "END OF THE PROJECT GUTENBERG" in line:
            break
        header = CHAPTER.match(line)
        if header:
            flush()
            book = header.group(1)
            chapter = header.group(2)
            verse = None
            continue
        titled = TITLES.get(line)
        if titled:
            flush()
            book, chapter = titled
            verse = None
            continue
        match = VERSE.match(line)
        if match and book and match.group(1) == chapter:
            flush()
            verse = match.group(2)
            buf = [match.group(3)]
            continue
        if verse and line and not line.startswith("***"):
            buf.append(line)
    flush()
    return books


def main() -> int:
    path = download()
    text = path.read_text(encoding="utf-8", errors="replace")
    books = parse(text)
    OUT.mkdir(parents=True, exist_ok=True)
    order = []
    for name, chapters in books.items():
        record = {
            "translation": "Project Gutenberg #17",
            "translation_status": "public-domain",
            "weight": "established",
            "book": name,
            "testament": "latter-day saint",
            "traditions": ["latter-day saint"],
            "source_edition": EDITION,
            "license": "Public domain. Project Gutenberg eBook #17.",
            "chapters": chapters,
        }
        (OUT / f"{file_slug(name)}.json").write_text(
            json.dumps(record, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        verses = sum(len(item) for item in chapters.values())
        print(f"{name}: {len(chapters)} chapters, {verses} verses")
        order.append(name)
    catalog_path = ROOT / "data" / "knowledge_base" / "bible" / "kjv" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {"books": []}
    catalog["book_of_mormon"] = order
    catalog["book_of_mormon_count"] = len(order)
    catalog["book_of_mormon_source"] = EDITION
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    return 0 if len(order) >= 15 else 1


if __name__ == "__main__":
    raise SystemExit(main())

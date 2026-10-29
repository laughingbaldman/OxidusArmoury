#!/usr/bin/env python3
"""Load the public-domain King James text onto the shelf."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.library.bible import ingest_sources
from src.library.shelf import ReferenceLibrary


def main() -> int:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/kjv")
    if not source.exists():
        print(f"No KJV source at {source}")
        return 1
    library = ReferenceLibrary(ROOT)
    catalog = ingest_sources(library, source)
    print(f"Wrote {catalog['count']} books.")
    return 0 if catalog["count"] == 66 else 1


if __name__ == "__main__":
    raise SystemExit(main())

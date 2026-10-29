"""The Book of Mormon is stored like the rest of the shelf, from Gutenberg."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOM = ROOT / "data" / "knowledge_base" / "bible" / "book-of-mormon"
KJV = ROOT / "data" / "knowledge_base" / "bible" / "kjv"


class BookOfMormonTests(unittest.TestCase):
    def test_fifteen_books_from_gutenberg(self):
        files = list(BOM.glob("*.json"))
        self.assertEqual(len(files), 15)
        nephi = json.loads((BOM / "1Nephi.json").read_text(encoding="utf-8"))
        self.assertEqual(nephi["traditions"], ["latter-day saint"])
        self.assertIn("Project Gutenberg", nephi["translation"])
        self.assertTrue(nephi["chapters"]["1"]["1"]["text"].startswith("I, Nephi, having been born of goodly parents"))
        moroni = json.loads((BOM / "Moroni.json").read_text(encoding="utf-8"))
        self.assertIn("ask God", moroni["chapters"]["10"]["4"]["text"])

    def test_king_james_verse_was_not_rewritten(self):
        matthew = json.loads((KJV / "Matthew.json").read_text(encoding="utf-8"))
        self.assertIn("protestant", matthew["traditions"])
        self.assertIn("whatsoever ye would", matthew["chapters"]["7"]["12"]["text"])
        tobit = json.loads((KJV / "Tobit.json").read_text(encoding="utf-8"))
        self.assertNotIn("protestant", tobit["traditions"])
        self.assertIn("catholic", tobit["traditions"])
        self.assertTrue(tobit["chapters"]["1"]["1"]["text"].startswith("The book of the words of Tobit"))


if __name__ == "__main__":
    unittest.main()

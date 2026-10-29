"""The Obsidian brain export links things together and never touches user notes."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from src.integrations.obsidian_brain_export import export_brain


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class BrainExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.vault = Path(self.tmp.name) / "vault"
        kb = self.root / "data" / "knowledge_base"
        _write(kb / "wiki_corpus" / "Abacus_1.md",
               '---\ntitle: "Abacus"\ndomain: "mathematics"\nurl: "u"\n---\nAn abacus is used in Arithmetic.\n')
        _write(kb / "wiki_corpus" / "Arithmetic_2.md",
               '---\ntitle: "Arithmetic"\ndomain: "mathematics"\n---\nArithmetic is old.\n')
        _write(kb / "bible" / "kjv" / "Ruth.json", json.dumps({
            "book": "Ruth", "testament": "old", "translation": "KJV", "traditions": ["protestant"],
            "chapters": {"1": {"1": {"text": "Now it came to pass", "themes": ["kindness"]}}}}))
        _write(self.vault / "Oxidus Brain" / "Welcome.md", "my note")
        _write(self.vault / "Oxidus Brain" / "Brain" / "Library" / "Wiki" / "Abacus.md", "user wrote this")
        (self.vault / "Oxidus Brain" / ".obsidian").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_links_and_safety(self):
        r = export_brain(root=self.root, vault=self.vault)
        brain = self.vault / "Oxidus Brain" / "Brain"
        self.assertGreater(r["notes"], 10)
        self.assertEqual(len(r["skipped_user_files"]), 1)
        self.assertEqual((brain / "Library" / "Wiki" / "Abacus.md").read_text(encoding="utf-8"), "user wrote this")
        self.assertEqual((self.vault / "Oxidus Brain" / "Welcome.md").read_text(encoding="utf-8"), "my note")
        ruth = (brain / "Library" / "Scripture" / "Ruth.md").read_text(encoding="utf-8")
        self.assertIn("generated_by: oxidus", ruth)
        self.assertIn("[[Kindness]]", ruth)
        self.assertIn("[[Old Testament]]", ruth)
        arith = (brain / "Library" / "Wiki" / "Arithmetic.md").read_text(encoding="utf-8")
        self.assertIn("[[Mathematics]]", arith)
        names = {p.stem for p in brain.rglob("*.md")}
        for p in brain.rglob("*.md"):
            for link in re.findall(r"\[\[([^\]|]+)", p.read_text(encoding="utf-8")):
                self.assertIn(link, names | {"Abacus"})
        self.assertTrue((self.vault / "Oxidus Brain" / ".obsidian" / "graph.json").exists())
        again = export_brain(root=self.root, vault=self.vault)
        self.assertEqual(again["created"] + again["updated"], 0)


if __name__ == "__main__":
    unittest.main()

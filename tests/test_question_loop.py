"""Open questions stay on their own list until someone answers them."""

import tempfile
import unittest
from pathlib import Path

from src.core.question_loop import QuestionLoop


class QuestionLoopTests(unittest.TestCase):
    def test_weak_hit_is_queued_and_the_answer_is_kept(self):
        tmp = tempfile.TemporaryDirectory()
        loop = QuestionLoop(tmp.name)
        queued = loop.observe("Where did the plates come from?", hits=[])
        self.assertTrue(queued)
        self.assertEqual(loop.list_open()[0]["reason"], "weak_hit")
        saved = loop.answer(queued[0]["id"], "Nephi says his father left Jerusalem.")
        self.assertTrue(saved["ok"])
        self.assertEqual(loop.list_open(), [])
        log = Path(tmp.name) / "data" / "stores" / "questions" / "learning_log.jsonl"
        self.assertIn("Jerusalem", log.read_text(encoding="utf-8"))
        tmp.cleanup()


class PageTests(unittest.TestCase):
    def test_question_panel_and_notice(self):
        root = Path(__file__).resolve().parents[1]
        chat = (root / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="questionQueue"', chat)
        workshop = (root / "templates" / "console.html").read_text(encoding="utf-8")
        self.assertIn("Admin", workshop)
        self.assertIn("/api/admin/health", workshop)
        notice = (root / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("Apache License 2.0", notice)
        self.assertIn("OpenJarvis", notice)
        self.assertIn("grok-1", notice)


if __name__ == "__main__":
    unittest.main()

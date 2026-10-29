"""The live Brain page builds a graph and records what a chat answer pulled."""

import unittest
from pathlib import Path

from src.web.blueprints import brain


class BrainPageTests(unittest.TestCase):
    def test_graph_and_activity(self):
        data = brain.build_graph()
        self.assertGreater(len(data["nodes"]), 10)
        self.assertGreater(len(data["links"]), 10)
        ids = {n["id"] for n in data["nodes"]}
        for link in data["links"][:200]:
            self.assertIn(link["source"], ids)
            self.assertIn(link["target"], ids)
        brain._graph["data"] = data
        brain.record_activity("Tell me about Genesis", hits=[{"subtopic": "Genesis", "title": "Genesis 1", "loc": "Genesis 1:1"}])
        last = list(brain._events)[-1]
        self.assertIn("Genesis", last["nodes"])

    def test_template_has_no_cdn(self):
        html = (Path(__file__).resolve().parents[1] / "templates" / "brain.html").read_text(encoding="utf-8")
        self.assertIn("/api/brain/activity", html)
        self.assertNotIn("https://", html)


if __name__ == "__main__":
    unittest.main()

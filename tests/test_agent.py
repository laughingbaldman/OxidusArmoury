"""The agent stays in the workspace and waits before it does something you cannot take back."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from src.agent.loop import AgentLoop, ScriptedMind
from src.agent.safety import AgentControl
from src.agent.schedule import Scheduler
from src.agent.tools import ToolKit
from src.web.agent_page import agent_bp


def _git(cwd, *args, env=None):
    subprocess.check_call(["git", *args], cwd=str(cwd), env=env)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.control = AgentControl(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_coding_pass_edits_tests_and_commits_on_a_branch(self):
        _git(self.root, "init", "-b", "work")
        (self.root / "sample.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.root / "test_sample.py").write_text(
            "import unittest\nfrom sample import add\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(1, 1), 2)\n",
            encoding="utf-8",
        )
        env = {
            "GIT_AUTHOR_NAME": "Jonathon Ward",
            "GIT_AUTHOR_EMAIL": "laughingbaldman@local",
            "GIT_COMMITTER_NAME": "Jonathon Ward",
            "GIT_COMMITTER_EMAIL": "laughingbaldman@local",
            "PATH": "/usr/bin:/bin",
        }
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-m", "start", env=env)
        mind = ScriptedMind([
            {"tool": "file_read", "args": {"path": "sample.py"}, "thought": "Read the function."},
            {"tool": "file_edit", "args": {"path": "sample.py", "old": "return a - b", "new": "return a + b"}, "thought": "Make it a sum."},
            {"tool": "run_tests", "args": {"target": "test_sample.py"}, "thought": "Run the test."},
            {"tool": "git_commit", "args": {"message": "Fix the sum", "paths": ["sample.py"]}, "thought": "Commit on this branch."},
            {"done": True, "reflection": "Fixed."},
        ])
        result = AgentLoop(self.root, mind=mind, control=self.control).run("Fix add and prove it.")
        self.assertIn("return a + b", (self.root / "sample.py").read_text(encoding="utf-8"))
        self.assertIn("Fixed.", str(result["observation"]))
        log = subprocess.check_output(["git", "log", "--oneline"], cwd=self.root, text=True)
        self.assertIn("Fix the sum", log)
        kinds = [row["kind"] for row in self.control.activity()]
        self.assertIn("plan", kinds)
        self.assertIn("act", kinds)
        self.assertIn("observe", kinds)
        self.assertIn("reflect", kinds)

    def test_commit_on_main_is_refused(self):
        _git(self.root, "init", "-b", "main")
        (self.root / "readme.txt").write_text("hi\n", encoding="utf-8")
        env = {
            "GIT_AUTHOR_NAME": "Jonathon Ward",
            "GIT_AUTHOR_EMAIL": "laughingbaldman@local",
            "GIT_COMMITTER_NAME": "Jonathon Ward",
            "GIT_COMMITTER_EMAIL": "laughingbaldman@local",
            "PATH": "/usr/bin:/bin",
        }
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-m", "start", env=env)
        outcome = ToolKit(self.control).git_commit({"message": "should not land"}, False)
        self.assertFalse(outcome["ok"])
        self.assertIn("main", outcome["summary"])

    def test_delete_waits_and_then_moves_to_retired(self):
        note = self.root / "notes.txt"
        note.write_text("keep me\n", encoding="utf-8")
        loop = AgentLoop(self.root, mind=ScriptedMind([
            {"tool": "delete", "args": {"path": "notes.txt"}, "thought": "Retire the note."},
        ]), control=self.control)
        paused = loop.run("clean up")
        self.assertTrue(note.exists())
        self.assertTrue(paused["pending"])
        loop.approve(paused["pending"]["id"], True)
        self.assertFalse(note.exists())
        held = list((self.root / "retired" / "agent_held").glob("notes.txt"))
        self.assertEqual(len(held), 1)
        self.assertIn("keep me", held[0].read_text(encoding="utf-8"))

    def test_write_outside_workspace_needs_approval(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        target = outside / "new.txt"
        work = self.root / "workspace_only"
        work.mkdir()
        loop = AgentLoop(work, mind=ScriptedMind([
            {"tool": "file_write", "args": {"path": str(target), "content": "nope"}, "thought": "Write outside."},
        ]))
        paused = loop.run("leak")
        self.assertFalse(target.exists())
        self.assertTrue(paused["pending"]["needs_approval"])
        loop.approve(paused["pending"]["id"], False)
        self.assertFalse(target.exists())

    def test_force_push_never_hits_main_and_waits_on_other_branches(self):
        pushed = []

        def runner(command):
            pushed.append(command)
            return {"ok": True, "summary": " ".join(command)}

        tools = ToolKit(self.control, {"git_push": runner})
        refused = tools.git_force_push({"branch": "main"}, approved=True)
        self.assertFalse(refused["ok"])
        self.assertEqual(pushed, [])
        waiting = tools.git_force_push({"branch": "work"}, approved=False)
        self.assertTrue(waiting["needs_approval"])
        self.assertEqual(pushed, [])
        self.control.take_pending(waiting["id"], True)
        done = tools.git_force_push({"branch": "work"}, approved=True)
        self.assertTrue(done["ok"])
        self.assertIn("work", done["summary"])
        self.assertNotIn("main", done["summary"].split())

    def test_network_stays_off_until_granted(self):
        tools = ToolKit(self.control, {"fetch": lambda url: {"ok": True, "summary": url}})
        blocked = tools.web_fetch({"url": "https://example.com"}, False)
        self.assertIn("Network is off", blocked["summary"])
        self.control.update_config({"network_granted": True})
        allowed = tools.web_fetch({"url": "https://example.com"}, False)
        self.assertEqual(allowed["summary"], "https://example.com")

    def test_kill_switch_stops_the_next_step(self):
        self.control.kill()
        loop = AgentLoop(self.root, mind=ScriptedMind([
            {"tool": "file_write", "args": {"path": "nope.txt", "content": "x"}, "thought": "write"},
        ]), control=self.control)
        result = loop.run("do not do this")
        self.assertTrue(result["killed"])
        self.assertFalse((self.root / "nope.txt").exists())

    def test_message_and_purchase_do_nothing_until_approved(self):
        sent = []
        loop = AgentLoop(self.root, mind=ScriptedMind([
            {"tool": "send_message", "args": {"to": "sam", "body": "hi"}, "thought": "send"},
        ]), control=self.control, backends={"send_message": lambda args: sent.append(args) or {"ok": True, "summary": "sent"}})
        paused = loop.run("tell sam")
        self.assertEqual(sent, [])
        loop.approve(paused["pending"]["id"], True)
        self.assertEqual(sent[0]["to"], "sam")
        bought = ToolKit(self.control).purchase({"item": "a book"}, approved=True)
        self.assertIn("Nothing was bought", bought["summary"])

    def test_schedule_and_subtask_depth(self):
        clock = {"now": 1000.0}
        scheduler = Scheduler(self.control, clock=lambda: clock["now"])
        scheduler.add("look", 50, "check the shelf")
        self.assertEqual(len(scheduler.due()), 1)
        self.assertEqual(scheduler.due(), [])
        clock["now"] = 1060
        self.assertEqual(scheduler.due()[0]["name"], "look")
        self.control.kill()
        self.assertEqual(scheduler.due(), [])
        self.control.resume()
        self.control.config_path.write_text('{"max_depth": 0, "enabled_tools": ["subtask"]}\n', encoding="utf-8")
        loop = AgentLoop(self.root, mind=ScriptedMind([
            {"tool": "subtask", "args": {"goal": "deeper"}, "thought": "spawn"},
        ]), control=self.control)
        loop.run("nest")
        details = " ".join(row["detail"] for row in self.control.activity())
        self.assertIn("depth", details)


class AgentPageTests(unittest.TestCase):
    def test_kill_switch_and_settings_round_trip(self):
        tmp = tempfile.TemporaryDirectory()
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        app.config["OXIDUS_ROOT"] = tmp.name
        app.register_blueprint(agent_bp)
        client = app.test_client()
        page = client.get("/agent")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Kill switch", page.data)
        self.assertIn(b"Activity", page.data)
        killed = client.post("/api/agent/kill")
        self.assertTrue(killed.get_json()["killed"])
        self.assertTrue((Path(tmp.name) / "data" / "stores" / "agent" / "KILL").exists())
        saved = client.post("/api/agent/config", json={"network_granted": True, "max_steps": 3})
        body = saved.get_json()
        self.assertTrue(body["network_granted"])
        self.assertEqual(body["max_steps"], 3)
        status = client.get("/api/agent/status").get_json()
        self.assertTrue(status["activity"])
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()

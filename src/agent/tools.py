"""Tools the local agent can call. Dangerous ones stop and wait."""

from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess

from .safety import env_for_subprocess

RISKY_SHELL = re.compile(
    r"\b(rm\s+-|git\s+push\s+.*(-f|--force)|git\s+reset\s+--hard|git\s+clean\b|mkfs|shutdown|reboot|dd\s+)\b",
    re.IGNORECASE,
)


class ToolKit:
    def __init__(self, control, backends: dict | None = None):
        self.control = control
        self.backends = backends or {}
        self.handlers = {
            "shell": self.shell,
            "file_read": self.file_read,
            "file_write": self.file_write,
            "file_edit": self.file_edit,
            "code_search": self.code_search,
            "run_tests": self.run_tests,
            "git_status": self.git_status,
            "git_commit": self.git_commit,
            "git_force_push": self.git_force_push,
            "web_fetch": self.web_fetch,
            "web_search": self.web_search,
            "desktop": self.desktop,
            "kb_search": self.kb_search,
            "kb_add": self.kb_add,
            "image": self.image,
            "video": self.video,
            "schedule": self.schedule,
            "subtask": self.subtask,
            "send_message": self.send_message,
            "purchase": self.purchase,
            "delete": self.delete,
        }

    def invoke(self, name: str, args: dict | None = None, approved: bool = False) -> dict:
        if self.control.killed():
            return {"ok": False, "summary": "Kill switch is on."}
        args = dict(args or {})
        if name not in self.handlers:
            return {"ok": False, "summary": f"Unknown tool {name}."}
        if not self.control.tool_enabled(name):
            return {"ok": False, "summary": f"{name} is turned off in settings."}
        return self.handlers[name](args, approved)

    def shell(self, args: dict, approved: bool) -> dict:
        command = str(args.get("command") or "").strip()
        if not command:
            return {"ok": False, "summary": "No command."}
        workspace = self.control.workspace()
        if not approved and (RISKY_SHELL.search(command) or self._shell_escapes(command, workspace)):
            return self.control.request_approval("shell", args, f"Shell wants a destructive or out-of-workspace command: {command}")
        runner = self.backends.get("shell")
        if runner:
            result = runner(command, workspace, approved)
            self.control.record("act", f"shell: {command}")
            return result
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=int(args.get("timeout") or 20),
                env=env_for_subprocess(),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "summary": "Command timed out."}
        text = ((completed.stdout or "") + (completed.stderr or ""))[-4000:]
        return {"ok": completed.returncode == 0, "summary": text or f"exit {completed.returncode}", "code": completed.returncode}

    def _shell_escapes(self, command: str, workspace: Path) -> bool:
        if re.search(r"(^|[\s=])\.\.([/\s]|$)", command):
            return True
        for match in re.findall(r"(?:^|\s)(/[^\s;&|]+)", command):
            try:
                Path(match).resolve().relative_to(workspace)
            except ValueError:
                return True
        return False

    def file_read(self, args: dict, approved: bool) -> dict:
        path, inside = self.control.resolve_inside(str(args.get("path") or ""))
        if not inside and not approved:
            return self.control.request_approval("file_read", args, f"Read outside the workspace: {path}")
        if not path.exists() or not path.is_file():
            return {"ok": False, "summary": f"No file at {path}."}
        text = path.read_text(encoding="utf-8", errors="replace")
        limit = int(args.get("limit") or 8000)
        return {"ok": True, "summary": text[:limit], "path": str(path)}

    def file_write(self, args: dict, approved: bool) -> dict:
        path, inside = self.control.resolve_inside(str(args.get("path") or ""))
        if not inside and not approved:
            return self.control.request_approval("file_write", args, f"Write outside the workspace: {path}")
        if path.exists() and not inside and not approved:
            return self.control.request_approval("file_write", args, f"Overwrite outside the workspace: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args.get("content") or ""), encoding="utf-8")
        return {"ok": True, "summary": f"Wrote {path.name}.", "path": str(path)}

    def file_edit(self, args: dict, approved: bool) -> dict:
        path, inside = self.control.resolve_inside(str(args.get("path") or ""))
        if not inside and not approved:
            return self.control.request_approval("file_edit", args, f"Edit outside the workspace: {path}")
        if not path.exists():
            return {"ok": False, "summary": "File is not there."}
        original = path.read_text(encoding="utf-8")
        old = str(args.get("old") or "")
        new = str(args.get("new") if args.get("new") is not None else "")
        if not old or old not in original:
            return {"ok": False, "summary": "That text was not in the file."}
        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        return {"ok": True, "summary": f"Edited {path.name}."}

    def code_search(self, args: dict, approved: bool) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "summary": "No query."}
        hits = []
        root = self.control.workspace()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in {".git", "node_modules", "__pycache__", ".venv"}]
            for name in filenames:
                path = Path(dirpath) / name
                if path.stat().st_size > 1_000_000:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if query in text:
                    line_no = text[: text.index(query)].count("\n") + 1
                    hits.append(f"{path.relative_to(root)}:{line_no}")
                if len(hits) >= 20:
                    break
            if len(hits) >= 20:
                break
        return {"ok": True, "summary": "\n".join(hits) or "No matches.", "hits": hits}

    def run_tests(self, args: dict, approved: bool) -> dict:
        target = str(args.get("target") or "tests")
        path, inside = self.control.resolve_inside(target)
        if not inside:
            return {"ok": False, "summary": "Tests stay inside the workspace."}
        command = ["python3", "-m", "unittest", target]
        completed = subprocess.run(
            command,
            cwd=str(self.control.workspace()),
            capture_output=True,
            text=True,
            timeout=int(args.get("timeout") or 60),
            env=env_for_subprocess(),
        )
        tail = ((completed.stdout or "") + (completed.stderr or ""))[-4000:]
        return {"ok": completed.returncode == 0, "summary": tail or f"exit {completed.returncode}", "code": completed.returncode}

    def _branch(self) -> str:
        completed = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(self.control.workspace()),
            capture_output=True,
            text=True,
        )
        return (completed.stdout or "").strip()

    def git_status(self, args: dict, approved: bool) -> dict:
        completed = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=str(self.control.workspace()),
            capture_output=True,
            text=True,
        )
        return {"ok": completed.returncode == 0, "summary": (completed.stdout or completed.stderr or "").strip()}

    def git_commit(self, args: dict, approved: bool) -> dict:
        branch = self._branch()
        if branch in self.control.protected_branches():
            return {"ok": False, "summary": f"Refusing to commit on {branch}. Use another branch."}
        message = str(args.get("message") or "").strip()
        if not message:
            return {"ok": False, "summary": "Commit message is empty."}
        paths = args.get("paths") or ["."]
        add = subprocess.run(
            ["git", "add", "--", *paths],
            cwd=str(self.control.workspace()),
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            return {"ok": False, "summary": add.stderr or "git add failed"}
        env = env_for_subprocess()
        env["GIT_AUTHOR_NAME"] = "Jonathon Ward"
        env["GIT_AUTHOR_EMAIL"] = "laughingbaldman@local"
        env["GIT_COMMITTER_NAME"] = "Jonathon Ward"
        env["GIT_COMMITTER_EMAIL"] = "laughingbaldman@local"
        commit = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(self.control.workspace()),
            capture_output=True,
            text=True,
            env=env,
        )
        text = (commit.stdout or "") + (commit.stderr or "")
        return {"ok": commit.returncode == 0, "summary": text.strip() or "Nothing to commit."}

    def git_force_push(self, args: dict, approved: bool) -> dict:
        branch = str(args.get("branch") or self._branch())
        if branch in self.control.protected_branches():
            return {"ok": False, "summary": f"Force-push to {branch} is refused."}
        if not approved:
            return self.control.request_approval(
                "git_force_push",
                args,
                f"Force-push {branch}? This rewrites remote history.",
            )
        runner = self.backends.get("git_push")
        command = ["git", "push", "--force-with-lease", "origin", branch]
        if runner:
            return runner(command)
        return {"ok": False, "summary": "Force-push was approved but no git remote runner is configured."}

    def web_fetch(self, args: dict, approved: bool) -> dict:
        if not self.control.network_granted():
            return {"ok": False, "summary": "Network is off. Turn it on in agent settings first."}
        url = str(args.get("url") or "")
        fetcher = self.backends.get("fetch")
        if fetcher:
            return fetcher(url)
        return {"ok": False, "summary": "Fetch backend is not configured."}

    def web_search(self, args: dict, approved: bool) -> dict:
        if not self.control.network_granted():
            return {"ok": False, "summary": "Network is off. Turn it on in agent settings first."}
        searcher = self.backends.get("search")
        if searcher:
            return searcher(str(args.get("query") or ""))
        return {"ok": False, "summary": "Search backend is not configured."}

    def desktop(self, args: dict, approved: bool) -> dict:
        if not approved:
            return self.control.request_approval("desktop", args, f"Desktop action: {args.get('summary') or args}")
        actor = self.backends.get("desktop")
        if actor:
            return actor(args)
        return {"ok": True, "summary": "Approved. No desktop backend is loaded, so nothing moved."}

    def kb_search(self, args: dict, approved: bool) -> dict:
        finder = self.backends.get("kb_search")
        if finder:
            return finder(str(args.get("query") or ""))
        try:
            from src.library.shelf import ReferenceLibrary
            pack = ReferenceLibrary(self.control.root).build_context(str(args.get("query") or ""), budget_tokens=240)
            return {"ok": True, "summary": pack.get("context") or "No cards."}
        except Exception as exc:
            return {"ok": False, "summary": f"Shelf lookup failed: {exc}"}

    def kb_add(self, args: dict, approved: bool) -> dict:
        try:
            from src.library.shelf import ReferenceLibrary
            saved = ReferenceLibrary(self.control.root).add_entry(
                store=str(args.get("store") or "knowledge"),
                topic=str(args.get("topic") or "notes"),
                subtopic=str(args.get("subtopic") or "agent"),
                title=str(args.get("title") or "note"),
                text=str(args.get("text") or ""),
                source="agent",
            )
            return {"ok": True, "summary": f"Shelved {saved.get('id')}."}
        except Exception as exc:
            return {"ok": False, "summary": f"Could not shelve that: {exc}"}

    def image(self, args: dict, approved: bool) -> dict:
        maker = self.backends.get("image")
        if not maker:
            return {"ok": False, "summary": "Image generator is not loaded."}
        return maker(str(args.get("prompt") or ""))

    def video(self, args: dict, approved: bool) -> dict:
        maker = self.backends.get("video")
        if not maker:
            return {"ok": False, "summary": "Video generator is not loaded."}
        return maker(str(args.get("prompt") or ""))

    def schedule(self, args: dict, approved: bool) -> dict:
        from .schedule import Scheduler
        task = Scheduler(self.control).add(
            str(args.get("name") or "task"),
            int(args.get("every_seconds") or 3600),
            str(args.get("goal") or ""),
        )
        return {"ok": True, "summary": f"Scheduled {task['name']} every {task['every_seconds']}s.", "task": task}

    def subtask(self, args: dict, approved: bool) -> dict:
        runner = self.backends.get("subtask")
        if not runner:
            return {"ok": False, "summary": "Sub-task runner is not configured."}
        return runner(str(args.get("goal") or ""))

    def send_message(self, args: dict, approved: bool) -> dict:
        if not approved:
            return self.control.request_approval("send_message", args, f"Send a message to {args.get('to') or 'someone'}?")
        sender = self.backends.get("send_message")
        if not sender:
            return {"ok": False, "summary": "Approved, but no messenger is configured. Nothing was sent."}
        return sender(args)

    def purchase(self, args: dict, approved: bool) -> dict:
        if not approved:
            return self.control.request_approval("purchase", args, f"Purchase {args.get('item') or 'something'}?")
        buyer = self.backends.get("purchase")
        if not buyer:
            return {"ok": False, "summary": "Approved, but no payment backend is configured. Nothing was bought."}
        return buyer(args)

    def delete(self, args: dict, approved: bool) -> dict:
        return self._retire(str(args.get("path") or ""), approved)

    def _retire(self, raw: str, approved: bool) -> dict:
        path, inside = self.control.resolve_inside(raw)
        if not approved:
            return self.control.request_approval("delete", {"path": raw}, f"Move {path} to retired/? It will not be destroyed.")
        if not inside:
            return {"ok": False, "summary": "Refusing to delete outside the workspace."}
        if not path.exists():
            return {"ok": False, "summary": "Nothing there to retire."}
        if path.resolve() == self.control.workspace().resolve():
            return {"ok": False, "summary": "Refusing to retire the workspace itself."}
        held = self.control.root / "retired" / "agent_held"
        held.mkdir(parents=True, exist_ok=True)
        dest = held / path.name
        if dest.exists():
            dest = held / f"{path.stem}_{path.stat().st_mtime_ns}{path.suffix}"
        shutil.move(str(path), str(dest))
        return {"ok": True, "summary": f"Moved to {dest}.", "path": str(dest)}

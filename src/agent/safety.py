"""Sandbox, approvals, and the kill switch.

Reads and edits inside the workspace are normal work.
Delete, leaving the workspace, force-push, sending a message, or spending money wait for a yes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os

PROTECTED_BRANCHES = {"main", "master"}

APPROVAL_TOOLS = {
    "delete",
    "force_push",
    "send_message",
    "purchase",
    "desktop",
    "write_outside",
    "shell_risky",
}

DEFAULT_TOOLS = [
    "shell",
    "file_read",
    "file_write",
    "file_edit",
    "code_search",
    "run_tests",
    "git_status",
    "git_commit",
    "git_force_push",
    "web_fetch",
    "web_search",
    "desktop",
    "kb_search",
    "kb_add",
    "image",
    "video",
    "schedule",
    "subtask",
    "send_message",
    "purchase",
    "delete",
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AgentControl:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.dir = self.root / "data" / "stores" / "agent"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.log_path = self.dir / "activity.jsonl"
        self.kill_path = self.dir / "KILL"
        self._pending = None

    def config(self) -> dict:
        default = {
            "workspace": str(self.root),
            "network_granted": False,
            "max_steps": 8,
            "max_depth": 2,
            "enabled_tools": list(DEFAULT_TOOLS),
            "protected_branches": sorted(PROTECTED_BRANCHES),
        }
        if not self.config_path.exists():
            return default
        try:
            stored = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return default
        if not isinstance(stored, dict):
            return default
        default.update(stored)
        return default

    def update_config(self, updates: dict) -> dict:
        current = self.config()
        allowed = {
            "workspace",
            "network_granted",
            "max_steps",
            "max_depth",
            "enabled_tools",
            "protected_branches",
        }
        for key, value in (updates or {}).items():
            if key not in allowed:
                continue
            if key == "workspace":
                path = Path(str(value)).expanduser()
                if not path.is_absolute():
                    path = (self.root / path).resolve()
                else:
                    path = path.resolve()
                if not path.exists() or not path.is_dir():
                    continue
                current["workspace"] = str(path)
            elif key == "network_granted":
                current["network_granted"] = bool(value)
            elif key in {"max_steps", "max_depth"}:
                current[key] = max(1, int(value))
            elif key == "enabled_tools" and isinstance(value, list):
                current["enabled_tools"] = [str(item) for item in value if str(item) in DEFAULT_TOOLS]
            elif key == "protected_branches" and isinstance(value, list):
                names = {str(item).strip() for item in value if str(item).strip()}
                names.update(PROTECTED_BRANCHES)
                current["protected_branches"] = sorted(names)
        self.config_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        self.record("config", "Settings saved.")
        return current

    def workspace(self) -> Path:
        raw = self.config().get("workspace") or str(self.root)
        path = Path(raw).resolve()
        if not path.exists():
            return self.root
        return path

    def tool_enabled(self, name: str) -> bool:
        return name in set(self.config().get("enabled_tools") or [])

    def network_granted(self) -> bool:
        return bool(self.config().get("network_granted"))

    def protected_branches(self) -> set:
        return {str(item) for item in self.config().get("protected_branches") or PROTECTED_BRANCHES}

    def killed(self) -> bool:
        return self.kill_path.exists()

    def kill(self) -> None:
        self.kill_path.write_text(_now() + "\n", encoding="utf-8")
        self.record("kill", "Kill switch is on. The loop will not take another step.")

    def resume(self) -> None:
        if self.kill_path.exists():
            self.kill_path.unlink()
        self.record("kill", "Kill switch is off.")

    def record(self, kind: str, detail: str) -> dict:
        entry = {"at": _now(), "kind": kind, "detail": str(detail)[:2000]}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return entry

    def activity(self, limit: int = 80) -> list:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        rows = []
        for line in lines[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows

    def resolve_inside(self, raw: str) -> tuple:
        workspace = self.workspace()
        path = Path(raw)
        if not path.is_absolute():
            path = workspace / path
        full = path.resolve()
        try:
            full.relative_to(workspace)
            return full, True
        except ValueError:
            return full, False

    def request_approval(self, tool: str, args: dict, summary: str) -> dict:
        digest = hashlib.sha1(f"{tool}:{summary}:{_now()}".encode("utf-8")).hexdigest()[:10]
        self._pending = {
            "id": f"apr_{digest}",
            "tool": tool,
            "args": args,
            "summary": summary,
            "needs_approval": True,
        }
        self.record("approval", summary)
        return dict(self._pending)

    def pending(self) -> dict | None:
        return dict(self._pending) if self._pending else None

    def take_pending(self, approval_id: str, approved: bool) -> dict | None:
        current = self._pending
        if not current or current.get("id") != approval_id:
            return None
        self._pending = None
        self.record("approval", f"{'Approved' if approved else 'Denied'}: {current.get('summary')}")
        if not approved:
            return {"ok": False, "summary": "Denied.", "denied": True}
        return current


def env_for_subprocess() -> dict:
    kept = {}
    for key in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE"):
        if os.environ.get(key):
            kept[key] = os.environ[key]
    kept["PYTHONDONTWRITEBYTECODE"] = "1"
    return kept

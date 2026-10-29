"""Recurring chores. The kill switch stops them too."""

from __future__ import annotations

import json
import time


class Scheduler:
    def __init__(self, control, clock=None):
        self.control = control
        self.clock = clock or time.time
        self.path = control.dir / "schedule.json"

    def _load(self) -> list:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def _save(self, tasks: list) -> None:
        self.path.write_text(json.dumps(tasks, indent=2) + "\n", encoding="utf-8")

    def list_tasks(self) -> list:
        return self._load()

    def add(self, name: str, every_seconds: int, goal: str) -> dict:
        tasks = self._load()
        task = {
            "name": name.strip() or "task",
            "every_seconds": max(30, int(every_seconds)),
            "goal": goal.strip(),
            "last_run": 0,
            "enabled": True,
        }
        tasks = [item for item in tasks if item.get("name") != task["name"]]
        tasks.append(task)
        self._save(tasks)
        self.control.record("schedule", f"Every {task['every_seconds']}s: {task['name']}")
        return task

    def set_enabled(self, name: str, enabled: bool) -> dict:
        tasks = self._load()
        found = None
        for item in tasks:
            if item.get("name") == name:
                item["enabled"] = bool(enabled)
                found = item
        self._save(tasks)
        return found or {"ok": False}

    def due(self) -> list:
        if self.control.killed():
            return []
        now = float(self.clock())
        ready = []
        tasks = self._load()
        changed = False
        for item in tasks:
            if not item.get("enabled"):
                continue
            last = float(item.get("last_run") or 0)
            if now - last >= int(item.get("every_seconds") or 3600):
                item["last_run"] = now
                ready.append(dict(item))
                changed = True
        if changed:
            self._save(tasks)
        return ready

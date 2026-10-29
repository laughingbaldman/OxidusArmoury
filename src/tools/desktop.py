"""Desktop control. Nothing happens until the action is approved, and the indicator is on."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DesktopControl:
    def __init__(self, root: str | Path, actor=None, indicator=None):
        self.root = Path(root)
        self.actor = actor
        self.indicator = indicator or []
        self.pending = {}
        self.log_path = self.root / "data" / "stores" / "desktop" / "actions.jsonl"
        self.visible = False

    def status(self) -> dict:
        return {
            "visible": self.visible,
            "indicator": "OXIDUS DESKTOP CONTROL" if self.visible else "Desktop control idle",
            "pending": list(self.pending.values()),
        }

    def propose(self, action: dict) -> dict:
        digest = hashlib.sha1(json.dumps(action, sort_keys=True).encode("utf-8")).hexdigest()[:10]
        record = {"id": f"desk_{digest}", "action": action, "summary": action.get("summary") or action.get("type") or "desktop"}
        self.pending[record["id"]] = record
        return {"ok": True, "needs_approval": True, **record, **self.status()}

    def approve(self, action_id: str) -> dict:
        record = self.pending.get(action_id)
        if not record:
            return {"ok": False, "error": "No such action."}
        self.visible = True
        self.indicator.append("show")
        if not self.visible:
            return {"ok": False, "error": "Indicator did not show. Refusing the action."}
        result = {"ok": True, "ran": False}
        if self.actor:
            result = self.actor(record["action"])
        self.indicator.append("hide")
        self.visible = False
        self.pending.pop(action_id, None)
        self._log({"at": _now(), "action": record, "result": result})
        return {"ok": True, "result": result, **self.status()}

    def reject(self, action_id: str) -> dict:
        self.pending.pop(action_id, None)
        return {"ok": True, **self.status()}

    def _log(self, row: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

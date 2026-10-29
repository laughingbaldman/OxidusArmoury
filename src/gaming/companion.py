"""Companion mode. The screen is watched only while the switch is on."""

from __future__ import annotations

from pathlib import Path
import json

from .safety import GameSafety


class GameCompanion:
    def __init__(self, root: str | Path, safety: GameSafety | None = None, voice=None, library=None):
        self.root = Path(root)
        self.safety = safety or GameSafety(root)
        self.voice = voice
        self.library = library
        self.enabled = False
        self.appid = ""
        self.log_path = self.root / "data" / "stores" / "games" / "companion_log.jsonl"

    def status(self) -> dict:
        watching = bool(self.enabled and self.appid)
        return {
            "enabled": self.enabled,
            "appid": self.appid,
            "watching": watching,
            "indicator": "Oxidus is watching the game" if watching else "Game companion is off",
        }

    def set_enabled(self, enabled: bool, game: dict | None = None) -> dict:
        if not enabled:
            self.enabled = False
            self.appid = ""
            return {"ok": True, **self.status()}
        if not game:
            return {"ok": False, "error": "Pick a game first.", **self.status()}
        report = self.safety.assess(game)
        if not report["input_allowed"]:
            turned = self.safety.set_input(str(game.get("appid")), True, game)
            if not turned.get("ok"):
                self.enabled = False
                return {
                    "ok": False,
                    "needs_warning": turned.get("needs_warning", False),
                    "error": turned.get("warning") or "Play-together is off for this game.",
                    "safety": report,
                    **self.status(),
                }
            report = turned.get("safety") or report
        self.enabled = True
        self.appid = str(game.get("appid"))
        return {"ok": True, "safety": report, **self.status()}

    def coach(self, game: dict, frame_text: str = "") -> dict:
        """frame_text is a description or OCR snippet from a capture the user allowed."""
        if not self.enabled or str(game.get("appid")) != str(self.appid):
            return {"ok": False, "error": "Companion is off.", **self.status()}
        report = self.safety.assess(game)
        if not report["input_allowed"]:
            self.enabled = False
            return {
                "ok": False,
                "needs_warning": report.get("needs_warning", False),
                "error": report.get("warning") or "Play-together is off for this game.",
                "safety": report,
                **self.status(),
            }
        note = (frame_text or "").strip()
        if self.library is not None and note:
            pack = self.library.build_context(note, budget_tokens=180)
            detail = pack.get("context") or ""
        else:
            detail = ""
        if note and detail:
            line = f"I see {note[:180]}. From the shelf: {detail[:240]}"
        elif note:
            line = f"I see {note[:180]}. Take the simple next step and tell me what changed."
        else:
            line = "I'm watching. Tell me the board, or leave a capture note, and I'll coach the next move."
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"appid": self.appid, "note": note[:240], "line": line}) + "\n")
        if self.voice is not None:
            try:
                self.voice.speak(line)
            except Exception:
                pass
        return {"ok": True, "line": line, "indicator": self.status()["indicator"], "safety": report}

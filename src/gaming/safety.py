"""Per-game play-together switch.

Online titles start off. Jonathon hosts private rooms, so he can turn a
game on himself. The first time he does, Oxidus shows one warning:
some anti-cheat systems may flag input automation. That warning is not a lock.
"""

from __future__ import annotations

from pathlib import Path
import json
import os

ANTICHEAT_MARKERS = (
    "easyanticheat",
    "eac",
    "battleye",
    "beservice",
    "vanguard",
    "vgk",
    "nprotect",
    "xigncode",
    "mhyprot",
)

# Used only to label a title as online so the switch starts off.
ONLINE_APPIDS = {
    "730",
    "570",
    "440",
    "252490",
    "1172470",
    "1599340",
    "1966720",
    "271590",
}

WARNING_TEXT = (
    "Some games' anti-cheat may flag input automation. "
    "This notice shows once. Private rooms are your call: leave the switch off "
    "unless you want Oxidus in that room."
)


class GameSafety:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "data" / "stores" / "games" / "modes.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"modes": {}, "input": {}, "warning_acknowledged": False}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("modes", {})
        data.setdefault("input", {})
        data.setdefault("warning_acknowledged", False)
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def set_mode(self, appid: str, mode: str) -> dict:
        mode = (mode or "").strip().lower()
        if mode not in {"single_player", "local_coop", "online", "unset"}:
            return {"ok": False, "error": "Mode must be single_player, local_coop, or online."}
        data = self._load()
        data["modes"][str(appid)] = mode
        if mode == "online" and str(appid) not in data["input"]:
            data["input"][str(appid)] = False
        self._save(data)
        return {"ok": True, "appid": str(appid), "mode": mode}

    def mode_for(self, appid: str) -> str:
        return self._load()["modes"].get(str(appid), "unset")

    def warning_acknowledged(self) -> bool:
        return bool(self._load().get("warning_acknowledged"))

    def acknowledge_warning(self) -> dict:
        data = self._load()
        data["warning_acknowledged"] = True
        self._save(data)
        return {"ok": True, "warning_acknowledged": True, "warning": WARNING_TEXT}

    def is_online(self, game: dict) -> bool:
        appid = str(game.get("appid") or "")
        mode = self.mode_for(appid)
        if mode == "online":
            return True
        if mode in {"single_player", "local_coop"}:
            return False
        if appid in ONLINE_APPIDS:
            return True
        name = (game.get("name") or "").lower()
        return any(token in name for token in ("multiplayer", "online", "battle royale"))

    def input_enabled(self, appid: str, game: dict | None = None) -> bool:
        data = self._load()
        key = str(appid)
        if key in data["input"]:
            return bool(data["input"][key])
        if game is not None and self.is_online(game):
            return False
        if key in ONLINE_APPIDS:
            return False
        return False

    def set_input(self, appid: str, enabled: bool, game: dict | None = None) -> dict:
        """Turn play-together on or off for one game. Online titles default off."""
        data = self._load()
        key = str(appid)
        if enabled and not data.get("warning_acknowledged"):
            return {
                "ok": False,
                "needs_warning": True,
                "warning": WARNING_TEXT,
                "enabled": False,
                "appid": key,
            }
        data["input"][key] = bool(enabled)
        if enabled and game is not None and self.is_online(game):
            data["modes"][key] = data["modes"].get(key) or "online"
        self._save(data)
        report = self.assess(game or {"appid": key, "name": key})
        return {"ok": True, "enabled": bool(enabled), "safety": report}

    def anticheat_hits(self, installed_path: str) -> list:
        path = Path(installed_path or "")
        if not path.exists() or not path.is_dir():
            return []
        hits = []
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                names = [name.lower() for name in dirnames + filenames]
                for marker in ANTICHEAT_MARKERS:
                    if any(marker in name for name in names):
                        hits.append(marker)
                if dirpath.count(os.sep) - str(path).count(os.sep) >= 2:
                    dirnames[:] = []
        except Exception:
            return hits
        return sorted(set(hits))

    def assess(self, game: dict) -> dict:
        appid = str(game.get("appid") or "")
        name = game.get("name") or appid
        hits = self.anticheat_hits(game.get("installed_path") or "")
        online = self.is_online(game)
        enabled = self.input_enabled(appid, game)
        acked = self.warning_acknowledged()
        note = ""
        if hits and not acked:
            note = WARNING_TEXT
        elif online and not enabled:
            note = "Online title. Play-together is off until you turn it on for this private room."
        elif hits:
            note = "Anti-cheat files are present. You already saw the one-time warning."
        return {
            "appid": appid,
            "name": name,
            "mode": self.mode_for(appid),
            "online": online,
            "blocked": False,
            "input_enabled": enabled,
            "input_allowed": enabled and acked,
            "anticheat": hits,
            "warning_acknowledged": acked,
            "warning": WARNING_TEXT if not acked else note,
            "needs_warning": not acked,
        }

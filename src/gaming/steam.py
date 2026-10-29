"""Read a local Steam library. The Web API key stays in the environment."""

from __future__ import annotations

from pathlib import Path
import os
import re


def parse_vdf(text: str) -> dict:
    """Parse the small subset of Valve's text format that Steam actually writes."""
    tokens = re.findall(r'"([^"]*)"|([{}])', text or "")
    stream = [brace if brace in ("{", "}") else quoted for quoted, brace in tokens]
    index = 0

    def parse_block():
        nonlocal index
        node = {}
        while index < len(stream):
            token = stream[index]
            if token == "}":
                index += 1
                break
            if token == "{":
                index += 1
                continue
            index += 1
            if index >= len(stream):
                break
            nxt = stream[index]
            if nxt == "{":
                index += 1
                node[token] = parse_block()
            else:
                index += 1
                node[token] = nxt
        return node

    if not stream:
        return {}
    if stream[0] != "{":
        key = stream[0]
        while index < len(stream) and stream[index] != "{":
            index += 1
        if index < len(stream) and stream[index] == "{":
            index += 1
            return {key: parse_block()}
        return {}
    index = 1
    return parse_block()


def default_steam_roots() -> list:
    home = Path.home()
    candidates = [
        home / ".steam" / "steam",
        home / ".local" / "share" / "Steam",
        home / ".steam" / "root",
        Path(os.environ.get("STEAM_ROOT", "")),
        Path("C:/Program Files (x86)/Steam"),
        Path("C:/Program Files/Steam"),
    ]
    found = []
    for path in candidates:
        if path and str(path) not in {".", ""} and path not in found:
            found.append(path)
    return found


def _folders_from_library(vdf_path: Path) -> list:
    data = parse_vdf(vdf_path.read_text(encoding="utf-8", errors="replace"))
    folders = data.get("libraryfolders") or data
    paths = []
    if isinstance(folders, dict):
        for value in folders.values():
            if isinstance(value, dict) and value.get("path"):
                paths.append(Path(value["path"]))
            elif isinstance(value, str) and value and "path" not in value:
                # Older libraryfolders.vdf used numbered path strings.
                maybe = Path(value)
                if maybe.exists():
                    paths.append(maybe)
    return paths


def _manifest_games(steamapps: Path) -> list:
    games = []
    if not steamapps.exists():
        return games
    for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
        data = parse_vdf(manifest.read_text(encoding="utf-8", errors="replace"))
        state = data.get("AppState") or data
        appid = str(state.get("appid") or manifest.stem.split("_")[-1])
        name = state.get("name") or f"App {appid}"
        installdir = state.get("installdir") or ""
        installed = steamapps.parent / "steamapps" / "common" / installdir if False else steamapps / "common" / installdir
        games.append({
            "appid": appid,
            "name": name,
            "installdir": installdir,
            "installed_path": str(installed),
            "manifest": str(manifest),
            "launch_url": f"steam://rungameid/{appid}",
        })
    return games


class SteamLibrary:
    def __init__(self, roots: list | None = None, env: dict | None = None):
        self.roots = [Path(path) for path in (roots or default_steam_roots())]
        self.env = env if env is not None else os.environ

    def api_key(self) -> str:
        return (
            self.env.get("OXIDUS_STEAM_API_KEY", "")
            or self.env.get("STEAM_WEB_API_KEY", "")
        ).strip()

    def library_paths(self) -> list:
        paths = []
        for root in self.roots:
            vdf = root / "steamapps" / "libraryfolders.vdf"
            if vdf.exists():
                for folder in _folders_from_library(vdf):
                    if folder not in paths:
                        paths.append(folder)
            elif root.exists() and root not in paths:
                paths.append(root)
        return paths

    def installed_games(self) -> list:
        games = []
        seen = set()
        for folder in self.library_paths():
            steamapps = folder / "steamapps"
            if not steamapps.exists() and (folder / "appmanifest_0.acf").exists():
                steamapps = folder
            for game in _manifest_games(steamapps):
                if game["appid"] in seen:
                    continue
                seen.add(game["appid"])
                games.append(game)
        games.sort(key=lambda item: item["name"].lower())
        return games

    def owned_games(self, fetcher=None) -> dict:
        """Optional. Uses a key from the environment and never writes it down."""
        key = self.api_key()
        steam_id = (self.env.get("OXIDUS_STEAM_ID") or self.env.get("STEAM_ID") or "").strip()
        if not key or not steam_id:
            return {"ok": False, "games": [], "reason": "Set OXIDUS_STEAM_API_KEY and OXIDUS_STEAM_ID to ask Steam what you own."}
        if fetcher is None:
            return {"ok": False, "games": [], "reason": "Web lookup is available only when a fetcher is configured."}
        payload = fetcher(key, steam_id)
        games = payload.get("games") if isinstance(payload, dict) else payload
        return {"ok": True, "games": games or [], "reason": ""}

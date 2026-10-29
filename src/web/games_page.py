"""Play-together controls. Online titles stay off until Jonathon flips that game."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from src.gaming.boards import BoardTable
from src.gaming.companion import GameCompanion
from src.gaming.player2 import PlayerTwo
from src.gaming.safety import GameSafety
from src.gaming.steam import SteamLibrary

ROOT = Path(__file__).resolve().parents[2]

games_bp = Blueprint("games", __name__)
_safety = None
_player = None
_companion = None
_boards = BoardTable()


def _root() -> Path:
    return ROOT


def safety() -> GameSafety:
    global _safety
    if _safety is None:
        _safety = GameSafety(_root())
    return _safety


def player() -> PlayerTwo:
    global _player
    if _player is None:
        _player = PlayerTwo(_root(), safety=safety())
    return _player


def companion() -> GameCompanion:
    global _companion
    if _companion is None:
        _companion = GameCompanion(_root(), safety=safety())
    return _companion


def _library() -> SteamLibrary:
    return SteamLibrary()


def _find(appid: str) -> dict:
    for game in _library().installed_games():
        if str(game.get("appid")) == str(appid):
            return game
    return {"appid": str(appid), "name": f"App {appid}", "installed_path": "", "launch_url": f"steam://rungameid/{appid}"}


@games_bp.route("/games")
def games_page():
    return render_template("games.html")


@games_bp.route("/api/games/library", methods=["GET"])
def games_library():
    rows = []
    for game in _library().installed_games():
        report = safety().assess(game)
        rows.append({**game, "safety": report})
    return jsonify({
        "games": rows,
        "warning_acknowledged": safety().warning_acknowledged(),
        "player2": player().status(),
        "companion": companion().status(),
    })


@games_bp.route("/api/games/warning", methods=["POST"])
def games_warning():
    return jsonify(safety().acknowledge_warning())


@games_bp.route("/api/games/toggle", methods=["POST"])
def games_toggle():
    payload = request.get_json(silent=True) or {}
    appid = str(payload.get("appid") or "")
    if not appid:
        return jsonify({"ok": False, "error": "Pick a game."}), 400
    game = _find(appid)
    if payload.get("mode"):
        safety().set_mode(appid, payload.get("mode"))
    result = safety().set_input(appid, bool(payload.get("enabled")), game)
    status = 200 if result.get("ok") else 409
    return jsonify(result), status


@games_bp.route("/api/games/launch", methods=["POST"])
def games_launch():
    payload = request.get_json(silent=True) or {}
    game = _find(str(payload.get("appid") or ""))
    return jsonify({"ok": True, "launch_url": game.get("launch_url"), "name": game.get("name")})


@games_bp.route("/api/games/companion", methods=["POST"])
def games_companion():
    payload = request.get_json(silent=True) or {}
    game = _find(str(payload.get("appid") or "")) if payload.get("enabled") else None
    result = companion().set_enabled(bool(payload.get("enabled")), game)
    code = 200 if result.get("ok") else 409
    return jsonify(result), code


@games_bp.route("/api/games/coach", methods=["POST"])
def games_coach():
    payload = request.get_json(silent=True) or {}
    game = _find(str(payload.get("appid") or companion().appid))
    result = companion().coach(game, payload.get("note") or "")
    code = 200 if result.get("ok") else 409
    return jsonify(result), code


@games_bp.route("/api/games/player2", methods=["POST"])
def games_player2():
    payload = request.get_json(silent=True) or {}
    game = _find(str(payload.get("appid") or "")) if payload.get("enabled") else None
    result = player().set_enabled(bool(payload.get("enabled")), game, backend=payload.get("backend") or "fake")
    code = 200 if result.get("ok") else 409
    return jsonify(result), code


@games_bp.route("/api/games/player2/act", methods=["POST"])
def games_player2_act():
    payload = request.get_json(silent=True) or {}
    game = _find(str(player().appid or payload.get("appid") or ""))
    result = player().act(game, payload.get("action") or {})
    code = 200 if result.get("ok") else 409
    return jsonify(result), code


@games_bp.route("/api/games/board", methods=["GET", "POST"])
def games_board():
    if request.method == "GET":
        return jsonify({"word": _boards.word_state(), "tic": _boards.tic_state(), "trivia": _boards.trivia()})
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    if kind == "word":
        return jsonify(_boards.guess_letter(payload.get("letter") or ""))
    if kind == "new-word":
        return jsonify(_boards.new_word())
    if kind == "trivia":
        return jsonify(_boards.answer_trivia(payload.get("answer") or ""))
    if kind == "tic":
        try:
            cell = int(payload.get("cell"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Pick a square."}), 400
        return jsonify(_boards.tic_move(cell))
    return jsonify({"ok": False, "error": "Unknown board action."}), 400

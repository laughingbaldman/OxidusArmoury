"""Agent controls: settings, the running log, approvals, and the kill switch."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request

from src.agent.loop import AgentLoop, ScriptedMind
from src.agent.safety import DEFAULT_TOOLS
from src.agent.schedule import Scheduler

agent_bp = Blueprint("agent", __name__)
_loops = {}


def _root() -> Path:
    configured = current_app.config.get("OXIDUS_ROOT")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2]


def _loop() -> AgentLoop:
    root = _root().resolve()
    key = str(root)
    loop = _loops.get(key)
    if loop is None or loop.control.root != root:
        loop = AgentLoop(root, mind=ScriptedMind())
        _loops[key] = loop
    return loop


@agent_bp.route("/agent")
def agent_page():
    return render_template("agent.html", tools=DEFAULT_TOOLS)


@agent_bp.route("/api/agent/status", methods=["GET"])
def agent_status():
    loop = _loop()
    return jsonify({
        "config": loop.control.config(),
        "killed": loop.control.killed(),
        "pending": loop.control.pending(),
        "activity": loop.control.activity(),
        "schedule": Scheduler(loop.control).list_tasks(),
        "tools": DEFAULT_TOOLS,
    })


@agent_bp.route("/api/agent/config", methods=["POST"])
def agent_config():
    payload = request.get_json(silent=True) or {}
    return jsonify(_loop().control.update_config(payload))


@agent_bp.route("/api/agent/run", methods=["POST"])
def agent_run():
    payload = request.get_json(silent=True) or {}
    goal = str(payload.get("goal") or "").strip()
    steps = payload.get("steps") or []
    loop = _loop()
    if steps:
        loop.mind = ScriptedMind(steps)
        loop.tools.backends = loop._child_backends()
    if not goal:
        return jsonify({"ok": False, "error": "Give the agent a goal."}), 400
    return jsonify(loop.run(goal))


@agent_bp.route("/api/agent/approve", methods=["POST"])
def agent_approve():
    payload = request.get_json(silent=True) or {}
    result = _loop().approve(str(payload.get("id") or ""), bool(payload.get("yes")))
    return jsonify(result)


@agent_bp.route("/api/agent/kill", methods=["POST"])
def agent_kill():
    _loop().control.kill()
    return jsonify({"ok": True, "killed": True})


@agent_bp.route("/api/agent/resume", methods=["POST"])
def agent_resume():
    _loop().control.resume()
    return jsonify({"ok": True, "killed": False})


@agent_bp.route("/api/agent/schedule", methods=["POST"])
def agent_schedule():
    payload = request.get_json(silent=True) or {}
    task = Scheduler(_loop().control).add(
        str(payload.get("name") or "task"),
        int(payload.get("every_seconds") or 3600),
        str(payload.get("goal") or ""),
    )
    return jsonify(task)

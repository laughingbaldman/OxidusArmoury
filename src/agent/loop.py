"""Plan, act, observe, reflect. One step at a time so a kill switch can land."""

from __future__ import annotations

from pathlib import Path

from .safety import AgentControl
from .tools import ToolKit


class ScriptedMind:
    """Stand-in for a local model. Tests hand it a list of tool calls."""

    def __init__(self, steps: list | None = None):
        self.steps = list(steps or [])

    def decide(self, goal: str, observation: str, trace: list) -> dict:
        if not self.steps:
            return {"done": True, "reflection": "Nothing else to do."}
        step = self.steps.pop(0)
        step.setdefault("thought", step.get("tool") or "think")
        return step

    def reflect(self, observation: dict) -> str:
        if observation.get("ok"):
            return "That landed."
        return "That did not land. Adjust and try again."


class AgentLoop:
    def __init__(self, root: str | Path, mind=None, backends: dict | None = None, control: AgentControl | None = None, depth: int = 0):
        self.control = control or AgentControl(root)
        self.mind = mind or ScriptedMind()
        self.backends = dict(backends or {})
        self.depth = depth
        self.tools = ToolKit(self.control, self._child_backends())
        self.goal = ""
        self.observation = ""
        self.trace = []

    def _child_backends(self) -> dict:
        backends = dict(self.backends)
        backends["subtask"] = self._run_subtask
        return backends

    def snapshot(self) -> dict:
        return {
            "goal": self.goal,
            "killed": self.control.killed(),
            "pending": self.control.pending(),
            "observation": self.observation,
            "activity": self.control.activity(),
            "depth": self.depth,
        }

    def run(self, goal: str, max_steps: int | None = None) -> dict:
        self.goal = goal
        self.control.record("plan", goal)
        limit = int(max_steps or self.control.config().get("max_steps") or 8)
        for _ in range(limit):
            if self.control.killed():
                self.control.record("kill", "Stopped before the next step.")
                return self.snapshot()
            if self.control.pending():
                return self.snapshot()
            decision = self.mind.decide(self.goal, str(self.observation), self.trace)
            if decision.get("done"):
                note = decision.get("reflection") or "Done."
                self.control.record("reflect", note)
                self.observation = note
                return self.snapshot()
            thought = decision.get("thought") or ""
            self.control.record("plan", thought)
            outcome = self._act(decision.get("tool") or "", decision.get("args") or {}, approved=False)
            if outcome.get("needs_approval"):
                return self.snapshot()
            self.observation = outcome.get("summary") or ""
            self.trace.append({"tool": decision.get("tool"), "summary": self.observation})
            self.control.record("observe", self.observation)
            self.control.record("reflect", self.mind.reflect(outcome))
        self.control.record("reflect", "Hit the step limit.")
        return self.snapshot()

    def approve(self, approval_id: str, yes: bool) -> dict:
        if self.control.killed():
            return self.snapshot()
        pending = self.control.take_pending(approval_id, yes)
        if pending is None:
            return {"ok": False, "summary": "No matching approval.", **self.snapshot()}
        if pending.get("denied"):
            self.observation = "Denied."
            return self.snapshot()
        outcome = self._act(pending["tool"], pending.get("args") or {}, approved=True)
        self.observation = outcome.get("summary") or ""
        self.control.record("observe", self.observation)
        self.control.record("reflect", self.mind.reflect(outcome))
        return self.snapshot()

    def _act(self, tool: str, args: dict, approved: bool) -> dict:
        self.control.record("act", f"{tool} {args}".strip())
        return self.tools.invoke(tool, args, approved=approved)

    def _run_subtask(self, goal: str) -> dict:
        max_depth = int(self.control.config().get("max_depth", 2))
        if self.depth + 1 > max_depth:
            return {"ok": False, "summary": "Sub-task depth limit reached."}
        if self.control.killed():
            return {"ok": False, "summary": "Kill switch is on."}
        child = AgentLoop(
            self.control.root,
            mind=self.mind,
            backends=self.backends,
            control=self.control,
            depth=self.depth + 1,
        )
        child.tools = ToolKit(self.control, child._child_backends())
        result = child.run(goal, max_steps=3)
        return {"ok": not result.get("killed"), "summary": str(result.get("observation") or "sub-task returned")}

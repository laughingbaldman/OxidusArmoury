"""Player 2 controls. Real devices are optional. The fake pad is the default in tests.

Windows can use vgamepad (ViGEm). Linux can use uinput. Neither is required to load.
"""

from __future__ import annotations

from pathlib import Path
import json

from .safety import GameSafety


class FakePad:
    def __init__(self):
        self.events = []
        self.connected = False

    def connect(self):
        self.connected = True
        self.events.append({"op": "connect"})

    def disconnect(self):
        self.connected = False
        self.events.append({"op": "disconnect"})

    def button(self, name: str, pressed: bool):
        self.events.append({"op": "button", "name": name, "pressed": bool(pressed)})

    def move(self, axis: str, value: float):
        self.events.append({"op": "axis", "name": axis, "value": float(value)})

    def key(self, name: str):
        self.events.append({"op": "key", "name": name})


def _try_vgamepad():
    import vgamepad  # type: ignore

    class _Pad:
        def __init__(self):
            self.pad = vgamepad.VX360Gamepad()
            self.connected = True

        def connect(self):
            self.connected = True

        def disconnect(self):
            self.pad.reset()
            self.pad.update()
            self.connected = False

        def button(self, name, pressed):
            mapping = {
                "a": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_A,
                "b": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_B,
                "x": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_X,
                "y": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_Y,
            }
            button = mapping.get(name.lower())
            if button is None:
                raise ValueError(f"Unknown button {name}")
            if pressed:
                self.pad.press_button(button)
            else:
                self.pad.release_button(button)
            self.pad.update()

        def move(self, axis, value):
            amount = int(max(-1.0, min(1.0, value)) * 32767)
            if axis == "lx":
                self.pad.left_joystick(x_value=amount, y_value=0)
            elif axis == "ly":
                self.pad.left_joystick(x_value=0, y_value=amount)
            self.pad.update()

        def key(self, name):
            raise RuntimeError("This pad does not type. Use a button.")

    return _Pad()


def _try_uinput():
    import uinput  # type: ignore

    class _Pad:
        def __init__(self):
            self.device = uinput.Device([uinput.KEY_A, uinput.KEY_B, uinput.KEY_UP, uinput.KEY_DOWN])
            self.connected = True

        def connect(self):
            self.connected = True

        def disconnect(self):
            self.connected = False

        def button(self, name, pressed):
            key = {"a": uinput.KEY_A, "b": uinput.KEY_B}.get(name.lower())
            if key is None:
                raise ValueError(f"Unknown button {name}")
            self.device.emit(key, 1 if pressed else 0)

        def move(self, axis, value):
            key = uinput.KEY_UP if value < 0 else uinput.KEY_DOWN
            self.device.emit(key, 1)
            self.device.emit(key, 0)

        def key(self, name):
            self.button("a" if name.lower() == "a" else "b", True)
            self.button("a" if name.lower() == "a" else "b", False)

    return _Pad()


class PlayerTwo:
    def __init__(self, root: str | Path, pad=None, safety: GameSafety | None = None):
        self.root = Path(root)
        self.safety = safety or GameSafety(root)
        self.pad = pad if pad is not None else FakePad()
        self.enabled = False
        self.appid = ""
        self.backend = "fake"
        self.log_path = self.root / "data" / "stores" / "games" / "player2_log.jsonl"

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "appid": self.appid,
            "backend": self.backend,
            "indicator": "PLAYER 2 CONTROLS ON" if self.enabled else "Player 2 is off",
        }

    def _log(self, record: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def set_enabled(self, enabled: bool, game: dict | None = None, backend: str = "fake") -> dict:
        if not enabled:
            self.enabled = False
            self.appid = ""
            try:
                self.pad.disconnect()
            except Exception:
                pass
            return {"ok": True, **self.status()}
        if not game:
            return {"ok": False, "error": "Pick a game first.", **self.status()}
        report = self.safety.assess(game)
        if report.get("needs_warning") and not report.get("input_allowed"):
            self.enabled = False
            return {"ok": False, "needs_warning": True, "error": report["warning"], "safety": report, **self.status()}
        if not report["input_allowed"]:
            turned = self.safety.set_input(str(game.get("appid")), True, game)
            if not turned.get("ok"):
                self.enabled = False
                return {"ok": False, "needs_warning": turned.get("needs_warning", False), "error": turned.get("warning") or "Play-together is off for this game.", "safety": report, **self.status()}
            report = turned.get("safety") or report
        if backend == "vgamepad":
            self.pad = _try_vgamepad()
        elif backend == "uinput":
            self.pad = _try_uinput()
        elif backend == "fake":
            if self.pad is None:
                self.pad = FakePad()
        else:
            return {"ok": False, "error": "Backend must be fake, vgamepad, or uinput."}
        self.pad.connect()
        self.enabled = True
        self.appid = str(game.get("appid"))
        self.backend = backend
        self._log({"op": "enable", "appid": self.appid, "backend": backend})
        return {"ok": True, "safety": report, **self.status()}

    def act(self, game: dict, action: dict) -> dict:
        if not self.enabled or str(game.get("appid")) != self.appid:
            return {"ok": False, "error": "Player 2 is off for this game.", **self.status()}
        report = self.safety.assess(game)
        if not report["input_allowed"]:
            self.enabled = False
            try:
                self.pad.disconnect()
            except Exception:
                pass
            return {
                "ok": False,
                "needs_warning": report.get("needs_warning", False),
                "error": report.get("warning") or "Play-together is off for this game.",
                "safety": report,
                **self.status(),
            }
        kind = (action.get("type") or "").lower()
        if kind == "button":
            self.pad.button(action.get("name") or "a", bool(action.get("pressed", True)))
        elif kind == "axis":
            self.pad.move(action.get("name") or "lx", float(action.get("value") or 0))
        elif kind == "key":
            self.pad.key(action.get("name") or "a")
        else:
            return {"ok": False, "error": "Action must be button, axis, or key."}
        self._log({"op": "act", "appid": self.appid, "action": action})
        return {"ok": True, "indicator": self.status()["indicator"], "safety": report}

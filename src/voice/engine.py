"""Jarvis-style voice. The microphone stays off until Jonathon turns it on."""

from __future__ import annotations

from pathlib import Path
import array
import json


def rms_int16(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    usable = pcm[: len(pcm) // 2 * 2]
    if not usable:
        return 0.0
    samples = array.array("h")
    samples.frombytes(usable)
    if not samples:
        return 0.0
    total = sum(sample * sample for sample in samples) / float(len(samples))
    return min(1.0, (total ** 0.5) / 32768.0)


class VoiceEngine:
    def __init__(self, root: str | Path, stt=None, tts=None, wake=None, devices=None):
        self.root = Path(root)
        self.settings_path = self.root / "data" / "stores" / "voice" / "settings.json"
        self.stt = stt
        self.tts = tts
        self.wake = wake
        self._devices = devices or [{"id": "default", "name": "Default input"}]
        self.mic_enabled = False
        self.muted = False
        self.listening = False
        self.speaking = False
        self.conversation = False
        self.ptt_down = False
        self.device_id = "default"
        self.level = 0.0
        self.voice_id = "en_US-ryan-medium"
        self.hotkey = "ctrl+space"
        self.wake_word = "oxidus"
        self.interrupted = False
        self._load()

    def _load(self) -> None:
        if not self.settings_path.exists():
            return
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.mic_enabled = bool(data.get("mic_enabled", False))
        self.muted = bool(data.get("muted", False))
        self.device_id = data.get("device_id") or "default"
        self.voice_id = data.get("voice_id") or self.voice_id
        self.hotkey = data.get("hotkey") or self.hotkey
        self.conversation = bool(data.get("conversation", False))

    def save(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps({
            "mic_enabled": self.mic_enabled,
            "muted": self.muted,
            "device_id": self.device_id,
            "voice_id": self.voice_id,
            "hotkey": self.hotkey,
            "conversation": self.conversation,
            "wake_word": self.wake_word,
        }, indent=2) + "\n", encoding="utf-8")

    def indicator(self) -> str:
        if not self.mic_enabled:
            return "off"
        if self.muted:
            return "muted"
        if self.speaking:
            return "speaking"
        if self.listening or self.ptt_down:
            return "listening"
        if self.conversation:
            return "conversation"
        return "idle"

    def status(self) -> dict:
        return {
            "mic_enabled": self.mic_enabled,
            "muted": self.muted,
            "listening": self.listening,
            "speaking": self.speaking,
            "conversation": self.conversation,
            "device_id": self.device_id,
            "devices": self.list_devices(),
            "level": 0.0 if not self.mic_enabled else self.level,
            "voice_id": self.voice_id,
            "hotkey": self.hotkey,
            "wake_word": self.wake_word,
            "indicator": self.indicator(),
        }

    def list_devices(self) -> list:
        if callable(self._devices):
            return list(self._devices())
        return list(self._devices)

    def set_device(self, device_id: str) -> dict:
        self.device_id = device_id or "default"
        self.save()
        return self.status()

    def enable_mic(self, on: bool) -> dict:
        self.mic_enabled = bool(on)
        if not self.mic_enabled:
            self.listening = False
            self.ptt_down = False
            self.level = 0.0
        self.save()
        return self.status()

    def set_muted(self, muted: bool) -> dict:
        self.muted = bool(muted)
        if self.muted:
            self.listening = False
            self.ptt_down = False
        self.save()
        return self.status()

    def set_conversation(self, on: bool) -> dict:
        self.conversation = bool(on) and self.mic_enabled and not self.muted
        self.listening = self.conversation
        self.save()
        return self.status()

    def _gated(self) -> str:
        if not self.mic_enabled:
            return "mic off"
        if self.muted:
            return "muted"
        return ""

    def push_to_talk(self, down: bool, pcm: bytes | None = None) -> dict:
        reason = self._gated()
        if reason:
            return {"ok": False, "reason": reason, **self.status()}
        if down and self.speaking:
            self.barge_in()
        self.ptt_down = bool(down)
        self.listening = bool(down)
        transcript = ""
        if not down and pcm:
            transcript = self._transcribe(pcm)
            self.listening = self.conversation
        return {"ok": True, "transcript": transcript, **self.status()}

    def feed(self, pcm: bytes, wake_score: float | None = None) -> dict:
        reason = self._gated()
        if reason:
            self.level = 0.0
            return {"ignored": True, "reason": reason, **self.status()}
        self.level = rms_int16(pcm)
        heard = False
        if wake_score is not None:
            heard = wake_score >= 0.5
        elif self.wake is not None:
            heard = bool(self.wake.heard(pcm))
        if heard:
            if self.speaking:
                self.barge_in()
            self.conversation = True
            self.listening = True
        return {"wake": heard, **self.status()}

    def speak(self, text: str) -> dict:
        self.interrupted = False
        self.speaking = True
        self.listening = False
        if self.tts is not None:
            self.tts.speak(text, voice=self.voice_id)
        if self.interrupted:
            self.speaking = False
        else:
            self.speaking = False
        if self.conversation and self.mic_enabled and not self.muted:
            self.listening = True
        return {"ok": True, "interrupted": self.interrupted, **self.status()}

    def barge_in(self) -> dict:
        if self.speaking and self.tts is not None:
            self.tts.stop()
        self.interrupted = True
        self.speaking = False
        if self.mic_enabled and not self.muted:
            self.listening = True
        return {"ok": True, **self.status()}

    def _transcribe(self, pcm: bytes) -> str:
        if self.stt is None:
            return ""
        return self.stt.transcribe(pcm) or ""

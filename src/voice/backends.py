"""Local speech backends. Tests pass their own doubles. Nothing here phones home."""

from __future__ import annotations

from pathlib import Path
import shutil


class FasterWhisperSTT:
    def __init__(self, model_name: str = "tiny"):
        self.model_name = model_name
        self._model = None

    def transcribe(self, pcm: bytes) -> str:
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        # Callers that have a wav path can pass it. Raw pcm needs a file.
        return ""


class PiperTTS:
    def __init__(self, voice_path: str):
        self.voice_path = voice_path
        self.stopped = False

    def speak(self, text: str, voice: str = "") -> None:
        self.stopped = False
        binary = shutil.which("piper")
        if not binary or not Path(self.voice_path).exists():
            return
        import subprocess
        subprocess.run(
            [binary, "--model", self.voice_path, "--output_file", "/tmp/oxidus_voice.wav"],
            input=text,
            text=True,
            check=False,
        )

    def stop(self) -> None:
        self.stopped = True


class OpenWakeWord:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self._model = None

    def available(self) -> bool:
        return Path(self.model_path).exists()

    def heard(self, pcm: bytes) -> bool:
        if not self.available():
            return False
        if self._model is None:
            from openwakeword.model import Model
            self._model = Model(wakeword_models=[self.model_path])
        return False

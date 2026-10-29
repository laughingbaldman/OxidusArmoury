"""Local Grok-1. Weights are never stored in git.

Official checkpoint is the JAX release from xAI, Apache-2.0.
A GGUF file can be used through llama.cpp when you already have one.
"""

from __future__ import annotations

from pathlib import Path
import os
import shutil


WEIGHTS_NOTE = (
    "Grok-1 is a 314B mixture-of-experts model. The checkpoint is about 300 GB. "
    "The example code expects a machine that can hold it across several GPUs "
    "(the upstream runner uses an 8-device mesh). CPU GGUF is slower and only works "
    "if you point OXIDUS_GROK_GGUF at a file you already have."
)


class GrokBackend:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or Path(__file__).resolve().parents[2])

    def checkpoint_dir(self) -> Path | None:
        env = os.environ.get("OXIDUS_GROK_CKPT", "").strip()
        candidates = []
        if env:
            candidates.append(Path(env))
        candidates.extend([
            self.root / "third_party" / "grok-1" / "checkpoints" / "ckpt-0",
            self.root / "data" / "models" / "grok-1" / "ckpt-0",
        ])
        for path in candidates:
            if path.exists():
                return path
        return None

    def gguf_path(self) -> Path | None:
        raw = os.environ.get("OXIDUS_GROK_GGUF", "").strip()
        if raw and Path(raw).exists():
            return Path(raw)
        return None

    def status(self) -> dict:
        gguf = self.gguf_path()
        ckpt = self.checkpoint_dir()
        mode = "missing"
        if gguf:
            mode = "gguf"
        elif ckpt:
            mode = "jax"
        return {
            "available": mode != "missing",
            "mode": mode,
            "checkpoint": str(ckpt) if ckpt else "",
            "gguf": str(gguf) if gguf else "",
            "llama": bool(shutil.which("llama-cli") or shutil.which("llama-completion")),
            "note": WEIGHTS_NOTE,
        }

    def generate(self, prompt: str, max_tokens: int = 64) -> str:
        info = self.status()
        if info["mode"] == "gguf":
            binary = shutil.which("llama-cli") or shutil.which("llama-completion")
            if not binary:
                raise RuntimeError("GGUF is set but llama.cpp is not on PATH.")
            import subprocess
            completed = subprocess.run(
                [binary, "-m", info["gguf"], "-p", prompt, "-n", str(max_tokens)],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr or "llama.cpp failed")
            return completed.stdout
        if info["mode"] == "jax":
            raise RuntimeError(
                "JAX Grok-1 checkpoint is present. Run third_party/grok-1/run.py on a machine with the GPU mesh. "
                "This process will not load 300 GB on import."
            )
        raise RuntimeError("Grok-1 weights are not installed. See scripts/download_grok_weights.py")

    def review(self, question: str, draft: str) -> str | None:
        if not self.status()["available"]:
            return None
        return self.generate(f"Review this draft.\nQuestion: {question}\nDraft: {draft}\n", max_tokens=120)

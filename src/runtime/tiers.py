"""Hardware tiers.

Lite is the floor: a 4-core machine with 8-16 GB and no GPU still gets
useful answers from the card catalog. Heavy work stays optional.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import shutil

TIERS = ("lite", "standard", "full")

TIER_NOTES = {
    "lite": "Retrieval from the shelf, Piper, and Whisper tiny. No image, video, or Grok.",
    "standard": "Local LM Studio model plus the shelf. Voice can use a larger Whisper model.",
    "full": "Grok-1 and image/video generation, only when you ask and the machine can hold them.",
}


@dataclass(frozen=True)
class Hardware:
    cores: int
    ram_gb: float
    gpu: bool
    gpu_count: int = 0

    def as_dict(self) -> dict:
        return {
            "cores": self.cores,
            "ram_gb": self.ram_gb,
            "gpu": self.gpu,
            "gpu_count": self.gpu_count,
        }


def ram_gb() -> float:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024.0 / 1024.0, 1)
    except Exception:
        return 0.0
    return 0.0


def gpu_count() -> int:
    if not shutil.which("nvidia-smi"):
        return 0
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return 0
    if result.returncode != 0:
        return 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return len(lines)


def detect_hardware() -> Hardware:
    cores = os.cpu_count() or 1
    count = gpu_count()
    return Hardware(cores=cores, ram_gb=ram_gb(), gpu=count > 0, gpu_count=count)


def choose_tier(hardware: Hardware, override: str | None = None, weights_ready: bool = False) -> str:
    if override in TIERS:
        return override
    # Full is not a surprise. The checkpoint is about 300 GB.
    if weights_ready and (hardware.ram_gb >= 192 or hardware.gpu_count >= 8):
        return "full"
    if hardware.gpu and hardware.ram_gb > 16 and hardware.cores > 4:
        return "standard"
    if hardware.ram_gb > 16 and hardware.cores >= 8:
        return "standard"
    return "lite"


def settings_path(root: Path) -> Path:
    return Path(root) / "data" / "stores" / "settings.json"


def load_settings(root: Path) -> dict:
    path = settings_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(root: Path, updates: dict) -> dict:
    current = load_settings(root)
    current.update(updates)
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return current


def grok_weights_present(root: Path) -> bool:
    candidates = [
        Path(root) / "third_party" / "grok-1" / "checkpoints" / "ckpt-0",
        Path(root) / "data" / "models" / "grok-1" / "ckpt-0",
    ]
    env = os.environ.get("OXIDUS_GROK_CKPT", "").strip()
    if env:
        candidates.append(Path(env))
    gguf = os.environ.get("OXIDUS_GROK_GGUF", "").strip()
    if gguf and Path(gguf).exists():
        return True
    return any(path.exists() for path in candidates)


def current_tier(root: Path, hardware: Hardware | None = None) -> str:
    stored = load_settings(root).get("tier")
    env = os.environ.get("OXIDUS_TIER", "").strip().lower()
    override = stored or env or None
    hw = hardware or detect_hardware()
    return choose_tier(hw, override=override, weights_ready=grok_weights_present(root))


def whisper_model_for(tier: str) -> str:
    if tier == "full":
        return "small"
    if tier == "standard":
        return "base"
    return "tiny"


def allows(tier: str, feature: str) -> bool:
    feature = feature.lower()
    if feature in {"image", "video", "grok"}:
        return tier == "full"
    if feature in {"lm_studio", "lora"}:
        return tier in {"standard", "full"}
    return True


class TierError(RuntimeError):
    pass


class OptionalModule:
    """Heavy imports happen on first use, and only on a tier that asked for them."""

    def __init__(self, tier: str, allowed: set, loader, name: str):
        self.tier = tier
        self.allowed = allowed
        self.loader = loader
        self.name = name
        self._value = None
        self.loaded = False

    def get(self):
        if self.tier not in self.allowed:
            raise TierError(f"{self.name} is not loaded on the {self.tier} tier.")
        if not self.loaded:
            self._value = self.loader()
            self.loaded = True
        return self._value

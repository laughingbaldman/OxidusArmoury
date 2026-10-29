"""Image and short video. Heavy backends load only when asked, and only on Full."""

from __future__ import annotations

from src.runtime.tiers import TierError, allows


class ImageTool:
    def __init__(self, tier: str = "lite", backend=None):
        self.tier = tier
        self.backend = backend
        self.loaded = False

    def generate(self, prompt: str) -> dict:
        if not allows(self.tier, "image") and self.backend is None:
            return {"ok": False, "error": "Image generation is a Full-tier tool."}
        if self.backend is None:
            return {"ok": False, "error": "No local image backend is loaded."}
        self.loaded = True
        return self.backend(prompt)


class VideoTool:
    def __init__(self, tier: str = "lite", backend=None):
        self.tier = tier
        self.backend = backend
        self.loaded = False

    def generate(self, prompt: str) -> dict:
        if not allows(self.tier, "video") and self.backend is None:
            return {"ok": False, "error": "Video generation is a Full-tier tool."}
        if self.backend is None:
            return {"ok": False, "error": "No local video backend is loaded."}
        self.loaded = True
        return self.backend(prompt)


def diffusers_image(prompt: str, model_id: str):
    import torch
    from diffusers import StableDiffusionPipeline
    pipe = StableDiffusionPipeline.from_pretrained(model_id, local_files_only=True, torch_dtype=torch.float32)
    image = pipe(prompt).images[0]
    return {"ok": True, "image": image}


def unused_tier_error():
    return TierError

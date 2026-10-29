"""Admin secret loading.

The secret comes from OXIDUS_ADMIN_TOKEN or config/admin_token.txt.
There is no built-in default.
"""

from __future__ import annotations

from pathlib import Path
import os


def load_admin_token(token_file: str | Path | None = None) -> str:
    env = os.environ.get("OXIDUS_ADMIN_TOKEN", "").strip()
    if env:
        return env
    if token_file is None:
        return ""
    path = Path(token_file)
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""

"""Musiqa modullari uchun .env (environs + os.getenv)."""
from __future__ import annotations

import os


def music_env_str(name: str, default: str = "") -> str:
    try:
        from src.app.core.config import env

        return (env.str(name, default) or default).strip()
    except Exception:
        return (os.getenv(name) or default).strip()


def music_env_bool(name: str, *, default: bool) -> bool:
    raw = music_env_str(name, "")
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")

"""Telegram Mini App ochish uchun /index URL."""
from __future__ import annotations

from src.app.core.config import Settings, load_config


def miniapp_index_url_from_base(public_base_url: str) -> str | None:
    base = (public_base_url or "").strip().rstrip("/")
    if not base.startswith("https://"):
        return None
    return f"{base}/index"


def miniapp_index_url(settings: Settings | None = None) -> str | None:
    cfg = settings or load_config()
    return miniapp_index_url_from_base(cfg.telegram_webapp_url)

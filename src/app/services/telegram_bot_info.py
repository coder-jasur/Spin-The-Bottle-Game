"""BOT_TOKEN bo'yicha Telegram bot @username (getMe) — referral havolalar uchun."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

log = logging.getLogger("spinbottle.telegram_bot")

_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_TTL_SEC = 3600.0
_lock = asyncio.Lock()


async def _fetch_username(bot_token: str) -> Optional[str]:
    token = (bot_token or "").strip()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            r.raise_for_status()
            data = r.json()
        if not data.get("ok"):
            log.warning("getMe not ok: %s", (data.get("description") or "")[:120])
            return None
        result = data.get("result") or {}
        username = (result.get("username") or "").strip().lstrip("@")
        return username or None
    except Exception as e:
        log.warning("getMe failed: %s", e)
        return None


async def get_bot_username(
    bot_token: str,
    *,
    fallback: str | None = None,
) -> str:
    """Joriy BOT_TOKEN uchun @username; xato bo'lsa fallback (TELEGRAM_MINIAPP_BOT)."""
    token = (bot_token or "").strip()
    fb = (fallback or "SpinbottleTgBot").strip().lstrip("@")
    if not token:
        return fb

    now = time.monotonic()
    hit = _CACHE.get(token)
    if hit and (now - hit[1]) < _CACHE_TTL_SEC:
        return hit[0]

    async with _lock:
        hit = _CACHE.get(token)
        if hit and (now - hit[1]) < _CACHE_TTL_SEC:
            return hit[0]
        username = await _fetch_username(token)
        if username:
            _CACHE[token] = (username, time.monotonic())
            return username
        _CACHE.pop(token, None)

    return fb


async def warm_bot_username_cache(app) -> str:
    """Startup: token → username ni app.state ga yozish."""
    settings = getattr(app.state, "settings", None)
    if not settings:
        return ""
    username = await get_bot_username(
        settings.bot_token,
        fallback=settings.telegram_miniapp_bot,
    )
    app.state.telegram_bot_username = username
    app.state.telegram_bot_username_token = settings.bot_token
    log.info("Telegram bot username: @%s (from BOT_TOKEN)", username)
    return username


async def ensure_app_bot_username(app) -> str:
    """Token almashtirilsa qayta getMe."""
    settings = getattr(app.state, "settings", None)
    if not settings:
        return "SpinbottleTgBot"
    token = settings.bot_token
    cached = getattr(app.state, "telegram_bot_username", None)
    cached_token = getattr(app.state, "telegram_bot_username_token", None)
    if cached and cached_token == token:
        return str(cached)
    return await warm_bot_username_cache(app)

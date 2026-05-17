"""Telegram bot webhook (polling o'rniga production uchun)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger("spinbottle.tg_webhook")
router = APIRouter(tags=["Telegram Webhook"])


@router.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    settings = request.app.state.settings
    secret = getattr(settings, "telegram_webhook_secret", "") or ""
    if secret:
        hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if hdr != secret:
            raise HTTPException(status_code=403, detail="Forbidden")

    dp = getattr(request.app.state, "dp", None)
    bot = getattr(request.app.state, "bot", None)
    if not dp or not bot:
        raise HTTPException(status_code=503, detail="Bot not configured")

    from aiogram.types import Update

    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

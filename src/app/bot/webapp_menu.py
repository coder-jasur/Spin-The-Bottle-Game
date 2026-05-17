"""Telegram Mini App: menyu tugmasi to'g'ridan-to'g'ri /index ga."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import MenuButtonWebApp, WebAppInfo

from src.app.bot.i18n import translate
from src.app.bot.miniapp_url import miniapp_index_url_from_base
from src.app.core.language import DEFAULT_LANG

log = logging.getLogger("spinbottle.bot.menu")

_MENU_PLAY = "🎮 Play"


async def configure_miniapp_menu(bot: Bot, public_base_url: str, *, lang: str | None = None) -> None:
    """Bot menyusidagi Web App havolasini /index ga o'rnatadi."""
    url = miniapp_index_url_from_base(public_base_url)
    if not url:
        log.warning("TELEGRAM_WEBAPP_URL noto'g'ri (https kerak): %s", public_base_url)
        return
    label = translate(lang or DEFAULT_LANG, _MENU_PLAY)
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text=label, web_app=WebAppInfo(url=url)),
    )
    log.info("Mini App menu: %s (%s)", url, label)
    print(f"[OK] Telegram Mini App menu → {url}", flush=True)

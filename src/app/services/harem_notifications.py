"""Uxajorlik o'zgarganda — eski uxajorga Telegram bot xabari."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from src.app.api.auth.user_payload import game_display_name
from src.app.bot.i18n import translate
from src.app.bot.miniapp_url import miniapp_index_url
from src.app.bot.telegram_safe import is_bot_blocked_by_user, log_bot_blocked
from src.app.core.config import load_config
from src.app.core.language import bot_lang_from_db_user
from src.app.services.telegram_payments import get_telegram_bot

log = logging.getLogger("spinbottle.harem_notify")

_HAREM_TAKEN_MSGID = (
    "Hello %(victim)s! %(new_owner)s took %(target)s away from your court."
)
_PLAY_BTN_MSGID = "🎮 PLAY"


def _play_keyboard(webapp_url: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=translate(lang, _PLAY_BTN_MSGID),
                    web_app=WebAppInfo(url=webapp_url),
                )
            ]
        ]
    )


async def notify_harem_court_taken(
    displaced_db_id: int,
    target_db_id: int,
    new_owner_db_id: int,
) -> None:
    """Eski uxajorga: yangi odam nishonni olib ketdi (tg_id bo'lsa)."""
    if not displaced_db_id or displaced_db_id == new_owner_db_id:
        return

    bot = get_telegram_bot()
    if not bot:
        return

    settings = load_config()
    webapp_url = miniapp_index_url(settings)
    if not webapp_url:
        log.debug("harem notify: TELEGRAM_WEBAPP_URL yo'q")
        return

    from src.app.api.ws.game_manager import manager

    try:
        async with manager._db() as repo:
            displaced = await repo.get_user_with_wallet(displaced_db_id)
            if not displaced or not displaced.tg_id:
                return
            target_name = "?"
            if target_db_id:
                target_u = await repo.get_user_with_wallet(target_db_id)
                if target_u:
                    target_name = game_display_name(target_u)
            new_owner_name = "?"
            if new_owner_db_id:
                new_u = await repo.get_user_with_wallet(new_owner_db_id)
                if new_u:
                    new_owner_name = game_display_name(new_u)
            lang = bot_lang_from_db_user(displaced)
            chat_id = int(displaced.tg_id)
    except Exception as e:
        log.warning("harem notify DB: %s", e)
        return

    victim_name = game_display_name(displaced)
    text = translate(
        lang,
        _HAREM_TAKEN_MSGID,
        victim=victim_name,
        new_owner=new_owner_name,
        target=target_name,
    )
    kb = _play_keyboard(webapp_url, lang)

    try:
        await bot.send_message(chat_id, text, reply_markup=kb)
        log.info(
            "harem notify sent: displaced=%s lang=%s (db=%r) target=%s new_owner=%s",
            displaced_db_id,
            lang,
            getattr(displaced, "language_code", None),
            target_db_id,
            new_owner_db_id,
        )
    except TelegramForbiddenError:
        log_bot_blocked(chat_id, context="harem_taken")
    except Exception as e:
        if is_bot_blocked_by_user(e):
            log_bot_blocked(chat_id, context="harem_taken")
            return
        log.warning("harem notify send failed chat=%s: %s", chat_id, e)

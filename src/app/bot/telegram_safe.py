"""Telegram API xatolari — bot bloklanganida polling yiqilmasin."""
from __future__ import annotations

import logging
from typing import TypeVar

from aiogram.exceptions import TelegramForbiddenError

log = logging.getLogger("spinbottle.bot.tg_safe")

T = TypeVar("T")


def is_bot_blocked_by_user(exc: BaseException) -> bool:
    if isinstance(exc, TelegramForbiddenError):
        return True
    msg = str(exc).lower()
    return "bot was blocked" in msg or "blocked by the user" in msg


def log_bot_blocked(chat_id: int | str | None = None, *, context: str = "") -> None:
    suffix = f" ({context})" if context else ""
    log.info("TG: foydalanuvchi botni bloklagan chat=%s%s", chat_id, suffix)

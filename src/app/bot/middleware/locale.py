"""Foydalanuvchi Telegram tilini handler kontekstiga ulash."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject

from src.app.bot.i18n import set_locale
from src.app.core.language import resolve_user_lang


class LocaleMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_lang: str | None = None
        user = data.get("event_from_user")
        if user is not None:
            tg_lang = getattr(user, "language_code", None)
        elif isinstance(event, Message) and event.from_user:
            tg_lang = event.from_user.language_code
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_lang = event.from_user.language_code
        elif isinstance(event, PreCheckoutQuery) and event.from_user:
            tg_lang = event.from_user.language_code

        lang = resolve_user_lang(telegram_language_code=tg_lang)
        set_locale(lang)
        data["locale"] = lang
        return await handler(event, data)

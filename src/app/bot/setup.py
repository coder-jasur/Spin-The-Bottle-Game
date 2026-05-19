"""Telegram bot va dispatcher sozlash."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.app.bot.commands import register_bot_commands
from src.app.bot.telegram_safe import log_bot_blocked
from src.app.bot.handlers.admin_panel import router as admin_panel_router
from src.app.bot.handlers.admin_referral import router as admin_referral_router
from src.app.bot.handlers.payments import router as payments_router
from src.app.bot.handlers.start import router as start_router
from src.app.bot.handlers.store import router as store_router
from src.app.bot.middleware import register_middleware
from src.app.bot.middleware.database_pool import DatabaseMiddleware
from src.app.core.config import Settings

log = logging.getLogger("spinbottle.bot")


def create_bot_and_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker,
) -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=MemoryStorage())
    _register_error_handlers(dp)
    register_middleware(dp, session_factory)
    db_mw = DatabaseMiddleware(session_factory)
    dp.pre_checkout_query.outer_middleware(db_mw)
    dp.include_router(store_router)
    dp.include_router(payments_router)
    dp.include_router(admin_panel_router)
    dp.include_router(admin_referral_router)
    dp.include_router(start_router)
    return bot, dp


def _register_error_handlers(dp: Dispatcher) -> None:
    @dp.errors()
    async def _on_bot_blocked(event: ErrorEvent) -> bool:
        if not isinstance(event.exception, TelegramForbiddenError):
            return False
        chat_id = None
        upd = event.update
        if upd.message:
            chat_id = upd.message.chat.id
        elif upd.callback_query and upd.callback_query.message:
            chat_id = upd.callback_query.message.chat.id
        log_bot_blocked(chat_id, context="handler")
        return True

    @dp.errors()
    async def _on_network_error(event: ErrorEvent) -> bool:
        if not isinstance(event.exception, TelegramNetworkError):
            return False
        log.warning("TG tarmoq (handler): %s", event.exception)
        return True


__all__ = ["create_bot_and_dispatcher", "register_bot_commands"]

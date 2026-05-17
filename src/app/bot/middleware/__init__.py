from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.app.bot.middleware.database_pool import DatabaseMiddleware
from src.app.bot.middleware.locale import LocaleMiddleware


def register_middleware(dp: Dispatcher, session_pool: async_sessionmaker):
    locale_mw = LocaleMiddleware()
    db_mw = DatabaseMiddleware(session_pool)

    for mw in (locale_mw, db_mw):
        dp.message.outer_middleware(mw)
        dp.callback_query.outer_middleware(mw)
        dp.pre_checkout_query.outer_middleware(mw)
    dp.chat_member.outer_middleware(db_mw)

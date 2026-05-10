from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.app.bot.middleware.database_pool import DatabaseMiddleware


def register_middleware(dp: Dispatcher, session_pool: async_sessionmaker):
    middleware = DatabaseMiddleware(session_pool)

    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.chat_member.outer_middleware(middleware)

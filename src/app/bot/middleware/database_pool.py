from typing import Callable, Dict, Any, Awaitable
from sqlalchemy.ext.asyncio import async_sessionmaker
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery, Message

class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, session_pool: async_sessionmaker):
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        async with self.session_pool() as session:
            data["session"] = session
            return await handler(event, data)

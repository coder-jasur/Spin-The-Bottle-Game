"""Admin handlerlar uchun umumiy yordamchilar."""
from __future__ import annotations

from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.bot.admin_access import get_user_by_telegram_id, is_telegram_admin
from src.app.bot.i18n import _, set_locale
from src.app.core.language import bot_lang_from_db_user

ACCESS_DENIED_MSGID = "⛔ Access denied."


async def apply_admin_locale(session: AsyncSession, tg_id: int) -> None:
    user = await get_user_by_telegram_id(session, tg_id)
    set_locale(bot_lang_from_db_user(user))


async def deny_if_not_admin(
    event: Message | CallbackQuery, session: AsyncSession
) -> bool:
    tg_user = event.from_user
    if not tg_user or not await is_telegram_admin(session, tg_user.id):
        text = _(ACCESS_DENIED_MSGID)
        if isinstance(event, Message):
            await event.answer(text)
        else:
            await event.answer(text, show_alert=True)
        return True
    return False

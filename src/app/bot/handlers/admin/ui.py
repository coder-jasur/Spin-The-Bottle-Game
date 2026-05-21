"""Admin inline UI — edit_message yordamchilari."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

log = logging.getLogger("spinbottle.bot.admin.ui")


async def edit_screen(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return
        log.debug("admin edit_message: %s", e)
        await message.answer(text, reply_markup=reply_markup)


async def save_ui_message(state: FSMContext, message: Message) -> None:
    await state.update_data(
        ui_chat_id=message.chat.id,
        ui_message_id=message.message_id,
    )


async def edit_ui_from_state(
    bot: Bot,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    data = await state.get_data()
    chat_id = data.get("ui_chat_id")
    message_id = data.get("ui_message_id")
    if not chat_id or not message_id:
        return False
    try:
        await bot.edit_message_text(
            chat_id=int(chat_id),
            message_id=int(message_id),
            text=text,
            reply_markup=reply_markup,
        )
        return True
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return True
        log.debug("admin edit_ui_from_state: %s", e)
        return False

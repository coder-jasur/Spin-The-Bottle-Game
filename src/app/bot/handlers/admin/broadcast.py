"""Admin panel: broadcast — barcha Telegram userlarga xabar."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.bot.handlers.admin.common import apply_admin_locale, deny_if_not_admin
from src.app.bot.handlers.admin.panel import PANEL_TITLE_MSGID, panel_keyboard
from src.app.bot.handlers.admin.ui import (
    edit_screen,
    edit_ui_from_state,
    save_ui_message,
)
from src.app.bot.i18n import _
from src.app.services.broadcaster import (
    USER_NAME_PLACEHOLDER,
    _ALT_PLACEHOLDER,
    run_broadcast_task,
    template_has_placeholder,
)

router = Router(name="admin_broadcast")
log = logging.getLogger("spinbottle.bot.admin_broadcast")

_CB_OPEN = "adm:broadcast"
_CB_NEW = "adm:bc:new"
_CB_HELP = "adm:bc:help"
_CB_BACK_MENU = "adm:bc:back"
_CB_BACK_PANEL = "adm:bc:panel"
_CB_CONFIRM = "adm:bc:confirm"
_CB_CANCEL = "adm:bc:cancel"

_MENU_TITLE_MSGID = "📣 <b>Broadcast</b>\n\nChoose:"
_BTN_NEW_MSGID = "✉️ New broadcast"
_BTN_HELP_MSGID = "ℹ️ Personalization help"
_BTN_BACK_MENU_MSGID = "◀️ Back"
_BTN_BACK_PANEL_MSGID = "🛠 Admin panel"
_BTN_CONFIRM_MSGID = "✅ Send"
_BTN_CANCEL_MSGID = "❌ Cancel"

_HELP_MSGID = (
    "ℹ️ <b>Personalization</b>\n\n"
    f"Matnda <code>{USER_NAME_PLACEHOLDER}</code> yoki "
    f"<code>{_ALT_PLACEHOLDER}</code> — har bir oluvchining "
    "Telegram <b>familiyasi</b> (yo'q bo'lsa ismi) qo'yiladi.\n\n"
    "Masalan:\n"
    f"<code>Salom {USER_NAME_PLACEHOLDER}, yaxshimisiz?</code>"
)
_PROMPT_MSGID = (
    "✉️ <b>New broadcast</b>\n\n"
    "Xabarni yuboring (matn, rasm, video va h.k.)."
)
_PREVIEW_MSGID = (
    "📋 <b>Confirm</b>\n\n"
    "This message will be sent to all bot users.\n"
    "Personalization: <b>%(personalize)s</b>"
)
_STARTED_MSGID = (
    "✅ Broadcast started in the background. "
    "Progress updates will appear here."
)


class AdminBroadcastState(StatesGroup):
    waiting_message = State()
    confirm = State()


def _broadcast_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_(_BTN_NEW_MSGID), callback_data=_CB_NEW
                )
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_HELP_MSGID), callback_data=_CB_HELP
                )
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_BACK_PANEL_MSGID), callback_data=_CB_BACK_PANEL
                )
            ],
        ]
    )


def _back_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_(_BTN_BACK_MENU_MSGID), callback_data=_CB_BACK_MENU
                )
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_CANCEL_MSGID), callback_data=_CB_CANCEL
                )
            ],
        ]
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_(_BTN_CONFIRM_MSGID), callback_data=_CB_CONFIRM
                )
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_CANCEL_MSGID), callback_data=_CB_CANCEL
                )
            ],
        ]
    )


def _help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_(_BTN_BACK_MENU_MSGID),
                    callback_data=_CB_BACK_MENU,
                )
            ]
        ]
    )


async def _show_broadcast_menu(message: Message) -> None:
    await edit_screen(
        message,
        _(_MENU_TITLE_MSGID),
        reply_markup=_broadcast_menu_keyboard(),
    )


def _extract_broadcast_payload(message: Message) -> dict[str, Any]:
    if message.text:
        template = (message.text or "").strip()
    elif message.caption:
        template = (message.caption or "").strip()
    else:
        template = ""
    payload: dict[str, Any] = {
        "from_chat_id": message.chat.id,
        "message_id": message.message_id,
        "content_type": message.content_type,
        "text_template": template,
        "reply_markup": (
            message.reply_markup.model_dump() if message.reply_markup else None
        ),
    }
    if message.photo:
        payload["photo_id"] = message.photo[-1].file_id
    if message.video:
        payload["video_id"] = message.video.file_id
    if message.animation:
        payload["animation_id"] = message.animation.file_id
    if message.document:
        payload["document_id"] = message.document.file_id
    return payload


@router.callback_query(F.data == _CB_OPEN)
async def open_broadcast_menu(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await apply_admin_locale(session, cb.from_user.id)
    await state.clear()
    await cb.answer()
    await save_ui_message(state, cb.message)
    await _show_broadcast_menu(cb.message)


@router.callback_query(F.data == _CB_BACK_MENU)
async def back_broadcast_menu(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await apply_admin_locale(session, cb.from_user.id)
    await state.clear()
    await cb.answer()
    await save_ui_message(state, cb.message)
    await _show_broadcast_menu(cb.message)


@router.callback_query(F.data == _CB_BACK_PANEL)
async def back_admin_panel(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await apply_admin_locale(session, cb.from_user.id)
    await state.clear()
    await cb.answer()
    await edit_screen(
        cb.message,
        _(PANEL_TITLE_MSGID),
        reply_markup=panel_keyboard(),
    )


@router.callback_query(F.data == _CB_HELP)
async def on_broadcast_help(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await apply_admin_locale(session, cb.from_user.id)
    await cb.answer()
    await edit_screen(
        cb.message,
        _(_HELP_MSGID),
        reply_markup=_help_keyboard(),
    )


@router.callback_query(F.data == _CB_NEW)
async def on_broadcast_compose_start(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await apply_admin_locale(session, cb.from_user.id)
    await save_ui_message(state, cb.message)
    await state.set_state(AdminBroadcastState.waiting_message)
    await cb.answer()
    await edit_screen(
        cb.message,
        _(_PROMPT_MSGID),
        reply_markup=_back_cancel_keyboard(),
    )


@router.message(StateFilter(AdminBroadcastState.waiting_message))
async def on_broadcast_message(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not message.from_user or await deny_if_not_admin(message, session):
        return
    await apply_admin_locale(session, message.from_user.id)

    if (
        not message.text
        and not message.caption
        and not message.photo
        and not message.video
        and not message.animation
        and not message.document
    ):
        edited = await edit_ui_from_state(
            message.bot,
            state,
            _(_PROMPT_MSGID),
            reply_markup=_back_cancel_keyboard(),
        )
        if not edited:
            await message.answer(
                _(_PROMPT_MSGID),
                reply_markup=_back_cancel_keyboard(),
            )
        return

    payload = _extract_broadcast_payload(message)
    await state.update_data(broadcast=payload)
    await state.set_state(AdminBroadcastState.confirm)

    personalize = (
        "ha" if template_has_placeholder(payload.get("text_template")) else "yo'q"
    )
    preview = _(_PREVIEW_MSGID, personalize=personalize)
    if payload.get("photo_id"):
        preview += "\n\n📷 <i>Rasm</i>"
    elif payload.get("video_id"):
        preview += "\n\n🎬 <i>Video</i>"
    elif payload.get("animation_id"):
        preview += "\n\n🎞 <i>GIF</i>"
    elif payload.get("document_id"):
        preview += "\n\n📎 <i>Hujjat</i>"

    edited = await edit_ui_from_state(
        message.bot,
        state,
        preview,
        reply_markup=_confirm_keyboard(),
    )
    if not edited:
        await message.answer(preview, reply_markup=_confirm_keyboard())


@router.callback_query(
    StateFilter(AdminBroadcastState.confirm), F.data == _CB_CONFIRM
)
async def on_broadcast_confirm(
    cb: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    session_pool: async_sessionmaker,
) -> None:
    if await deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user or not cb.message.bot:
        return
    await apply_admin_locale(session, cb.from_user.id)
    data = await state.get_data()
    payload = data.get("broadcast")
    if not payload:
        await cb.answer("Message not found", show_alert=True)
        return

    await cb.answer()
    await edit_screen(cb.message, _(_STARTED_MSGID))

    asyncio.create_task(
        run_broadcast_task(
            cb.message.bot,
            session_pool,
            int(cb.from_user.id),
            payload,
        )
    )
    await state.clear()
    await save_ui_message(state, cb.message)
    await _show_broadcast_menu(cb.message)


@router.callback_query(
    StateFilter(AdminBroadcastState.confirm, AdminBroadcastState.waiting_message),
    F.data == _CB_CANCEL,
)
async def on_broadcast_cancel(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await apply_admin_locale(session, cb.from_user.id)
    await state.clear()
    await cb.answer()
    await save_ui_message(state, cb.message)
    await _show_broadcast_menu(cb.message)

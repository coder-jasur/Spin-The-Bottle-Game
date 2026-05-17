"""Admin: partnerlar va global referal bonus sozlamalari."""
from __future__ import annotations

import logging
import re

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
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.bot.handlers.admin_panel import (
    _apply_admin_locale,
    _deny_if_not_admin,
    _panel_keyboard,
)
from src.app.bot.i18n import _
from src.app.database.repositories.referral import ReferralRepository
from src.app.database.repositories.user import UserRepository

log = logging.getLogger("spinbottle.bot.admin_referral")
router = Router(name="admin_referral")

_CB_OPEN = "adm:referral"
_CB_LIST = "admref:list"
_CB_ADD = "admref:add"
_CB_SETTINGS = "admref:settings"
_CB_BACK_REF = "admref:back"
_CB_BACK_PANEL = "admref:panel"
_CB_CANCEL = "admref:cancel"

_TITLE_MSGID = "🤝 <b>Referral & partners</b>\n\nChoose:"
_BTN_LIST_MSGID = "📋 Partner list"
_BTN_ADD_MSGID = "➕ Add partner"
_BTN_SETTINGS_MSGID = "⚙️ Default referral bonus"
_BTN_BACK_MSGID = "◀️ Back"
_BTN_BACK_PANEL_MSGID = "🛠 Admin panel"
_BTN_CANCEL_MSGID = "❌ Cancel"

_LIST_EMPTY_MSGID = "No partners yet."
_LIST_HEADER_MSGID = "<b>Partners</b> (%(count)s):"
_PARTNER_LINE_MSGID = (
    "• <code>%(code)s</code> — %(name)s\n"
    "  bonus: <b>%(bonus)s</b> | limit/day: <b>%(limit)s</b>\n"
    "  guests: <b>%(guests)s</b> | today: <b>%(today)s</b>/%(limit)s"
)
_PARTNER_DETAIL_MSGID = (
    "<b>Partner</b> #%(id)s\n"
    "User: <b>%(user_id)s</b> %(name)s\n"
    "Code: <code>%(code)s</code>\n"
    "Per invite: <b>%(bonus)s</b> hearts\n"
    "Daily limit: <b>%(limit)s</b>\n"
    "Invited guests: <b>%(guests)s</b>\n"
    "Earned today: <b>%(today)s</b>\n"
    "Status: %(status)s"
)
_SETTINGS_MSGID = (
    "<b>Default referral</b> (regular users)\n"
    "Per invite: <b>%(bonus)s</b> hearts\n"
    "Daily limit: <b>%(limit)s</b>"
)
_BTN_EDIT_BONUS_MSGID = "✏️ Per-invite bonus"
_BTN_EDIT_LIMIT_MSGID = "✏️ Daily limit"
_BTN_TOGGLE_MSGID = "🔛 Toggle active"
_SAVED_MSGID = "✅ Saved."
_ERR_MSGID = "❌ Error: %(error)s"
_PROMPT_USER_MSGID = (
    "Send partner user id, Telegram id, or login/username:"
)
_PROMPT_CODE_MSGID = (
    "Send referral <b>partner code</b> (or <code>-</code> to use user's code):"
)
_PROMPT_BONUS_MSGID = "Send hearts per invite (integer):"
_PROMPT_LIMIT_MSGID = "Send daily hearts limit (integer, 0 = unlimited):"
_USER_NOT_FOUND_MSGID = "User not found."


class AdminReferralState(StatesGroup):
    add_user = State()
    add_code = State()
    add_bonus = State()
    add_limit = State()
    edit_bonus = State()
    edit_limit = State()
    settings_bonus = State()
    settings_limit = State()


def _referral_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_(_BTN_LIST_MSGID), callback_data=_CB_LIST)],
            [InlineKeyboardButton(text=_(_BTN_ADD_MSGID), callback_data=_CB_ADD)],
            [
                InlineKeyboardButton(
                    text=_(_BTN_SETTINGS_MSGID), callback_data=_CB_SETTINGS
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
                    text=_(_BTN_CANCEL_MSGID), callback_data=_CB_CANCEL
                )
            ],
        ]
    )


def _partner_detail_keyboard(partner_pk: int, *, active: bool) -> InlineKeyboardMarkup:
    status_btn = _("Deactivate") if active else _("Activate")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_(_BTN_EDIT_BONUS_MSGID),
                    callback_data=f"admref:pb:{partner_pk}",
                ),
                InlineKeyboardButton(
                    text=_(_BTN_EDIT_LIMIT_MSGID),
                    callback_data=f"admref:pl:{partner_pk}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=status_btn,
                    callback_data=f"admref:pt:{partner_pk}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_BACK_MSGID), callback_data=_CB_BACK_REF
                )
            ],
        ]
    )


def _settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_(_BTN_EDIT_BONUS_MSGID), callback_data="admref:sb"
                ),
                InlineKeyboardButton(
                    text=_(_BTN_EDIT_LIMIT_MSGID), callback_data="admref:sl"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_BACK_MSGID), callback_data=_CB_BACK_REF
                )
            ],
        ]
    )


async def _show_referral_menu(message: Message) -> None:
    await message.answer(_(_TITLE_MSGID), reply_markup=_referral_menu_keyboard())


def _partner_display_name(partner) -> str:
    u = partner.user
    if not u:
        return f"id={partner.user_id}"
    return (u.display_name or u.username or u.login or f"id={u.id}").strip()


@router.callback_query(F.data == _CB_OPEN)
async def open_referral_menu(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await state.clear()
    await cb.answer()
    await _show_referral_menu(cb.message)


@router.callback_query(F.data == _CB_BACK_REF)
async def back_referral_menu(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await state.clear()
    await cb.answer()
    await _show_referral_menu(cb.message)


@router.callback_query(F.data == _CB_BACK_PANEL)
async def back_admin_panel(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await state.clear()
    await cb.answer()
    from src.app.bot.handlers.admin_panel import _PANEL_TITLE_MSGID

    await cb.message.answer(_(_PANEL_TITLE_MSGID), reply_markup=_panel_keyboard())


@router.callback_query(F.data == _CB_CANCEL)
async def on_cancel(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await state.clear()
    await cb.answer()
    await _show_referral_menu(cb.message)


@router.callback_query(F.data == _CB_LIST)
async def list_partners(cb: CallbackQuery, session: AsyncSession) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await cb.answer()
    repo = ReferralRepository(session)
    partners = await repo.list_partners()
    if not partners:
        await cb.message.answer(_(_LIST_EMPTY_MSGID))
        return
    lines = [_(_LIST_HEADER_MSGID, count=len(partners))]
    buttons: list[list[InlineKeyboardButton]] = []
    for p in partners:
        today = await repo.get_daily_earned(p.user_id)
        lines.append(
            _(
                _PARTNER_LINE_MSGID,
                code=p.partner_id,
                name=_partner_display_name(p),
                bonus=p.invited_bonus,
                limit=p.bonus_limit,
                guests=p.invited_guests,
                today=today,
            )
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"#{p.id} {p.partner_id[:12]}",
                    callback_data=f"admref:p:{p.id}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text=_(_BTN_BACK_MSGID), callback_data=_CB_BACK_REF)]
    )
    await cb.message.answer(
        "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("admref:p:"))
async def partner_detail(cb: CallbackQuery, session: AsyncSession) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await cb.answer()
    pk = int(cb.data.split(":")[-1])
    repo = ReferralRepository(session)
    partners = {p.id: p for p in await repo.list_partners()}
    p = partners.get(pk)
    if not p:
        await cb.message.answer(_(_USER_NOT_FOUND_MSGID))
        return
    today = await repo.get_daily_earned(p.user_id)
    status = _("Active") if p.is_active else _("Inactive")
    await cb.message.answer(
        _(
            _PARTNER_DETAIL_MSGID,
            id=p.id,
            user_id=p.user_id,
            name=_partner_display_name(p),
            code=p.partner_id,
            bonus=p.invited_bonus,
            limit=p.bonus_limit,
            guests=p.invited_guests,
            today=today,
            status=status,
        ),
        reply_markup=_partner_detail_keyboard(p.id, active=bool(p.is_active)),
    )


@router.callback_query(F.data == _CB_SETTINGS)
async def show_settings(cb: CallbackQuery, session: AsyncSession) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await cb.answer()
    repo = ReferralRepository(session)
    s = await repo.get_default_settings()
    await cb.message.answer(
        _(_SETTINGS_MSGID, bonus=s.bonus_hearts, limit=s.bonus_limit),
        reply_markup=_settings_keyboard(),
    )


@router.callback_query(F.data == _CB_ADD)
async def add_partner_start(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await state.set_state(AdminReferralState.add_user)
    await cb.answer()
    await cb.message.answer(
        _(_PROMPT_USER_MSGID), reply_markup=_back_cancel_keyboard()
    )


@router.message(StateFilter(AdminReferralState.add_user))
async def add_partner_user(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    if not message.from_user or await _deny_if_not_admin(message, session):
        return
    await _apply_admin_locale(session, message.from_user.id)
    raw = (message.text or "").strip()
    user_repo = UserRepository(session)
    user = None
    if raw.isdigit():
        tid = int(raw)
        user = await user_repo.get_user(tid)
        if not user:
            user = await user_repo.get_user_by_id(tid)
    if not user:
        user = await user_repo.get_user_by_login_or_id(raw)
    if not user:
        await message.answer(_(_USER_NOT_FOUND_MSGID))
        return
    await state.update_data(user_id=user.id, default_code=user.referral_id or "")
    await state.set_state(AdminReferralState.add_code)
    await message.answer(_(_PROMPT_CODE_MSGID))


@router.message(StateFilter(AdminReferralState.add_code))
async def add_partner_code(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    if not message.from_user or await _deny_if_not_admin(message, session):
        return
    await _apply_admin_locale(session, message.from_user.id)
    data = await state.get_data()
    raw = (message.text or "").strip()
    code = data.get("default_code") or ""
    if raw and raw != "-":
        code = raw
    if not code:
        await message.answer(_(_ERR_MSGID, error="code_required"))
        return
    await state.update_data(partner_code=code)
    await state.set_state(AdminReferralState.add_bonus)
    await message.answer(_(_PROMPT_BONUS_MSGID))


@router.message(StateFilter(AdminReferralState.add_bonus))
async def add_partner_bonus(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    if not message.from_user or await _deny_if_not_admin(message, session):
        return
    await _apply_admin_locale(session, message.from_user.id)
    try:
        bonus = int(re.sub(r"\s+", "", message.text or ""))
    except ValueError:
        await message.answer(_(_PROMPT_BONUS_MSGID))
        return
    await state.update_data(invited_bonus=bonus)
    await state.set_state(AdminReferralState.add_limit)
    await message.answer(_(_PROMPT_LIMIT_MSGID))


@router.message(StateFilter(AdminReferralState.add_limit))
async def add_partner_limit(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    if not message.from_user or await _deny_if_not_admin(message, session):
        return
    await _apply_admin_locale(session, message.from_user.id)
    try:
        limit = int(re.sub(r"\s+", "", message.text or ""))
    except ValueError:
        await message.answer(_(_PROMPT_LIMIT_MSGID))
        return
    data = await state.get_data()
    repo = ReferralRepository(session)
    try:
        p = await repo.create_partner(
            user_id=int(data["user_id"]),
            partner_id=data.get("partner_code"),
            invited_bonus=int(data.get("invited_bonus", 50)),
            bonus_limit=limit,
        )
        await session.commit()
        await state.clear()
        await message.answer(_(_SAVED_MSGID))
        today = await repo.get_daily_earned(p.user_id)
        await message.answer(
            _(
                _PARTNER_DETAIL_MSGID,
                id=p.id,
                user_id=p.user_id,
                name=_partner_display_name(p),
                code=p.partner_id,
                bonus=p.invited_bonus,
                limit=p.bonus_limit,
                guests=p.invited_guests,
                today=today,
                status=_("Active"),
            ),
            reply_markup=_partner_detail_keyboard(p.id, active=True),
        )
    except ValueError as e:
        await message.answer(_(_ERR_MSGID, error=str(e)))


@router.callback_query(F.data.startswith("admref:pb:"))
async def edit_partner_bonus_start(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    pk = int(cb.data.split(":")[-1])
    await state.set_state(AdminReferralState.edit_bonus)
    await state.update_data(partner_pk=pk)
    await cb.answer()
    await cb.message.answer(_(_PROMPT_BONUS_MSGID))


@router.callback_query(F.data.startswith("admref:pl:"))
async def edit_partner_limit_start(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    pk = int(cb.data.split(":")[-1])
    await state.set_state(AdminReferralState.edit_limit)
    await state.update_data(partner_pk=pk)
    await cb.answer()
    await cb.message.answer(_(_PROMPT_LIMIT_MSGID))


@router.callback_query(F.data.startswith("admref:pt:"))
async def toggle_partner(
    cb: CallbackQuery, session: AsyncSession
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    pk = int(cb.data.split(":")[-1])
    repo = ReferralRepository(session)
    partners = {p.id: p for p in await repo.list_partners()}
    p = partners.get(pk)
    if not p:
        await cb.answer(_(_USER_NOT_FOUND_MSGID), show_alert=True)
        return
    new_active = not bool(p.is_active)
    await repo.update_partner(pk, is_active=new_active)
    await session.commit()
    await cb.answer(_(_SAVED_MSGID))
    p = (await repo.list_partners())
    p = next((x for x in p if x.id == pk), None)
    if p:
        today = await repo.get_daily_earned(p.user_id)
        status = _("Active") if p.is_active else _("Inactive")
        await cb.message.answer(
            _(
                _PARTNER_DETAIL_MSGID,
                id=p.id,
                user_id=p.user_id,
                name=_partner_display_name(p),
                code=p.partner_id,
                bonus=p.invited_bonus,
                limit=p.bonus_limit,
                guests=p.invited_guests,
                today=today,
                status=status,
            ),
            reply_markup=_partner_detail_keyboard(p.id, active=bool(p.is_active)),
        )


@router.message(StateFilter(AdminReferralState.edit_bonus))
async def edit_partner_bonus_save(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    if not message.from_user or await _deny_if_not_admin(message, session):
        return
    await _apply_admin_locale(session, message.from_user.id)
    data = await state.get_data()
    try:
        bonus = int(re.sub(r"\s+", "", message.text or ""))
    except ValueError:
        await message.answer(_(_PROMPT_BONUS_MSGID))
        return
    repo = ReferralRepository(session)
    await repo.update_partner(int(data["partner_pk"]), invited_bonus=bonus)
    await session.commit()
    await state.clear()
    await message.answer(_(_SAVED_MSGID))


@router.message(StateFilter(AdminReferralState.edit_limit))
async def edit_partner_limit_save(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    if not message.from_user or await _deny_if_not_admin(message, session):
        return
    await _apply_admin_locale(session, message.from_user.id)
    data = await state.get_data()
    try:
        limit = int(re.sub(r"\s+", "", message.text or ""))
    except ValueError:
        await message.answer(_(_PROMPT_LIMIT_MSGID))
        return
    repo = ReferralRepository(session)
    await repo.update_partner(int(data["partner_pk"]), bonus_limit=limit)
    await session.commit()
    await state.clear()
    await message.answer(_(_SAVED_MSGID))


@router.callback_query(F.data == "admref:sb")
async def settings_bonus_start(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await state.set_state(AdminReferralState.settings_bonus)
    await cb.answer()
    await cb.message.answer(_(_PROMPT_BONUS_MSGID))


@router.callback_query(F.data == "admref:sl")
async def settings_limit_start(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await state.set_state(AdminReferralState.settings_limit)
    await cb.answer()
    await cb.message.answer(_(_PROMPT_LIMIT_MSGID))


@router.message(StateFilter(AdminReferralState.settings_bonus))
async def settings_bonus_save(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    if not message.from_user or await _deny_if_not_admin(message, session):
        return
    await _apply_admin_locale(session, message.from_user.id)
    try:
        bonus = int(re.sub(r"\s+", "", message.text or ""))
    except ValueError:
        await message.answer(_(_PROMPT_BONUS_MSGID))
        return
    repo = ReferralRepository(session)
    await repo.update_default_settings(bonus_hearts=bonus)
    await session.commit()
    await state.clear()
    await message.answer(_(_SAVED_MSGID))


@router.message(StateFilter(AdminReferralState.settings_limit))
async def settings_limit_save(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    if not message.from_user or await _deny_if_not_admin(message, session):
        return
    await _apply_admin_locale(session, message.from_user.id)
    try:
        limit = int(re.sub(r"\s+", "", message.text or ""))
    except ValueError:
        await message.answer(_(_PROMPT_LIMIT_MSGID))
        return
    repo = ReferralRepository(session)
    await repo.update_default_settings(bonus_limit=limit)
    await session.commit()
    await state.clear()
    await message.answer(_(_SAVED_MSGID))

"""Bot /store — Telegram Stars to'ldirish (chek + balans)."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.ws.constants import HEARTS_PACKAGES, hearts_for_stars_price
from src.app.bot.i18n import _, get_locale
from src.app.bot.miniapp_url import miniapp_index_url
from src.app.core.config import load_config
from src.app.database.repositories.user import UserRepository
from src.app.services.telegram_payments import (
    MAX_TOPUP_STARS,
    MIN_TOPUP_STARS,
    send_stars_invoice_to_chat,
)

log = logging.getLogger("spinbottle.bot.store")
router = Router(name="store")

_STORE_PREFIX = "store_custom"
_STORE_BUY_PREFIX = "store_buy:"

_STORE_TEXT_MSGID = (
    "⭐ <b>Stars store</b>\n\n"
    "• <b>Packages</b> — pay with Telegram Stars, get ❤️ hearts (same as in the game).\n"
    "• <b>Custom amount</b> — top up your ★ Stars balance 1:1 for in-game purchases.\n\n"
    "Select a package or custom amount — Telegram invoice will arrive."
)


class StoreCustomState(StatesGroup):
    waiting_amount = State()


def _store_keyboard() -> InlineKeyboardMarkup:
    amounts = sorted(int(k) for k in HEARTS_PACKAGES.keys())
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for stars in amounts:
        hearts = hearts_for_stars_price(stars) or 0
        row.append(
            InlineKeyboardButton(
                text=f"⭐{stars} → ❤️{hearts}",
                callback_data=f"{_STORE_BUY_PREFIX}{stars}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text=_("✏️ Custom amount"),
                callback_data=_STORE_PREFIX,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _parse_stars_amount(text: str) -> int | None:
    raw = (text or "").strip().replace(" ", "").replace(",", "")
    if not raw.isdigit():
        return None
    return int(raw)


def _amount_error_message() -> str:
    return _(
        "Amount must be a whole number from %(min)s to %(max)s.",
        min=MIN_TOPUP_STARS,
        max=f"{MAX_TOPUP_STARS:,}",
    )


async def _send_invoice(
    chat_id: int,
    db_user_id: int,
    stars: int,
    *,
    hearts: int | None = None,
    lang: str | None = None,
) -> bool:
    if stars < MIN_TOPUP_STARS or stars > MAX_TOPUP_STARS:
        return False
    return await send_stars_invoice_to_chat(
        chat_id,
        db_user_id,
        stars,
        hearts=hearts,
        lang=lang or get_locale(),
    )


async def _send_store_menu(message: Message, session: AsyncSession, tg_id: int) -> None:
    settings = load_config()
    repo = UserRepository(session)
    user = await repo.get_user(tg_id)
    if not user:
        webapp = miniapp_index_url(settings)
        hint = _("Open the game first: /start → PLAY, then send /store again.")
        if webapp:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=_("🎮 PLAY"),
                            web_app=WebAppInfo(url=webapp),
                        )
                    ]
                ]
            )
            await message.answer(hint, reply_markup=kb)
        else:
            await message.answer(hint)
        return

    wallet = user.wallet
    sc = int(wallet.stars_coin or 0) if wallet else 0
    gt = int(wallet.gift_tokens or 0) if wallet else 0
    lines = [_(_STORE_TEXT_MSGID) + "\n", _("Current balance: <b>%(sc)s</b> ★ Stars", sc=sc)]
    if gt:
        lines.append(_("Gift tokens: <b>%(gt)s</b>", gt=gt))
    await message.answer("\n".join(lines), reply_markup=_store_keyboard())


@router.message(Command("store"))
async def cmd_store(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    tg_id = message.from_user.id if message.from_user else 0
    if not tg_id:
        return
    await _send_store_menu(message, session, tg_id)


@router.callback_query(F.data == "store_open")
async def on_store_open(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if not query.from_user or not query.message:
        return
    await state.clear()
    await query.answer()
    await _send_store_menu(query.message, session, int(query.from_user.id))


@router.callback_query(F.data == _STORE_PREFIX)
async def on_store_custom_start(query: CallbackQuery, state: FSMContext) -> None:
    if not query.message:
        return
    await state.set_state(StoreCustomState.waiting_amount)
    await query.answer()
    await query.message.answer(
        _(
            "How many ★ Stars do you want?\n"
            "Send a number (e.g. <code>150</code>).\n"
            "Range: %(min)s — %(max)s\n\n"
            "Cancel: /store",
            min=MIN_TOPUP_STARS,
            max=f"{MAX_TOPUP_STARS:,}",
        )
    )


@router.message(StateFilter(StoreCustomState.waiting_amount))
async def on_store_custom_amount(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not message.from_user:
        return

    stars = _parse_stars_amount(message.text or "")
    if stars is None:
        await message.answer(
            _("Send a whole number only (e.g. 100).\n%(err)s", err=_amount_error_message())
        )
        return
    if stars < MIN_TOPUP_STARS or stars > MAX_TOPUP_STARS:
        await message.answer(_amount_error_message())
        return

    tg_id = int(message.from_user.id)
    repo = UserRepository(session)
    user = await repo.get_user(tg_id)
    if not user:
        await state.clear()
        await message.answer(_("Open the game first: /start → PLAY"))
        return

    await state.clear()
    ok = await _send_invoice(message.chat.id, int(user.id), stars)
    if ok:
        await message.answer(
            _("✅ Invoice for %(stars)s ★ sent — confirm payment.", stars=stars)
        )
    else:
        await message.answer(
            _(
                "Invoice was not sent. Check that you have not blocked the bot, "
                "or try again later."
            )
        )


@router.callback_query(F.data.startswith(_STORE_BUY_PREFIX))
async def on_store_buy(query: CallbackQuery, session: AsyncSession) -> None:
    if not query.from_user or not query.message:
        return

    raw = (query.data or "")[len(_STORE_BUY_PREFIX) :].strip()
    try:
        stars = int(raw)
    except ValueError:
        await query.answer(_("Invalid package"), show_alert=True)
        return

    if stars not in HEARTS_PACKAGES:
        await query.answer(_("This package is not available"), show_alert=True)
        return

    tg_id = int(query.from_user.id)
    repo = UserRepository(session)
    user = await repo.get_user(tg_id)
    if not user:
        await query.answer(
            _("Open the game first (/start → PLAY)"),
            show_alert=True,
        )
        return

    hearts = hearts_for_stars_price(stars)
    ok = await _send_invoice(
        query.message.chat.id,
        int(user.id),
        stars,
        hearts=hearts,
    )
    if ok:
        await query.answer(
            _("Invoice sent — confirm payment"),
        )
    else:
        await query.answer(
            _(
                "Invoice was not sent. Make sure the bot is not blocked in Telegram, "
                "or try /start again."
            ),
            show_alert=True,
        )

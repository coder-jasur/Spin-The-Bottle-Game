"""Telegram Stars to'lov handlerlari (pre_checkout, successful_payment)."""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.bot.i18n import _
from src.app.bot.telegram_safe import is_bot_blocked_by_user, log_bot_blocked
from src.app.services.telegram_payments import (
    apply_successful_stars_payment,
    notify_player_topup,
    parse_invoice_payload,
)

log = logging.getLogger("spinbottle.tg_pay")
router = Router(name="payments")


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    parsed = parse_invoice_payload(query.invoice_payload or "")
    ok = parsed is not None
    if ok and query.currency != "XTR":
        ok = False
    await query.answer(ok=ok, error_message=_("Invalid order") if not ok else None)


@router.message(lambda m: m.successful_payment is not None)
async def on_successful_payment(message: Message, session: AsyncSession) -> None:
    pay = message.successful_payment
    if not pay:
        return

    parsed = parse_invoice_payload(pay.invoice_payload or "")
    if not parsed:
        log.warning("TG pay: payload noto'g'ri %r", pay.invoice_payload)
        return

    user_id, expected_stars, hearts_product = parsed
    paid = int(pay.total_amount or 0)
    if paid < expected_stars:
        log.warning(
            "TG pay: summa mos emas user=%s expected=%s paid=%s",
            user_id,
            expected_stars,
            paid,
        )
        return

    charge_id = pay.telegram_payment_charge_id or pay.provider_payment_charge_id or ""
    if not charge_id:
        charge_id = f"{user_id}:{pay.invoice_payload}"

    if hearts_product and hearts_product > 0:
        from src.app.database.repositories.game import GameRepository

        repo = GameRepository(session)
        ok, sc, gt, hearts = await repo.apply_tg_hearts_product_payment(
            user_id,
            hearts_product,
            paid,
            charge_id,
        )
    else:
        ok, sc, gt, hearts = await apply_successful_stars_payment(
            session,
            user_id=user_id,
            stars=paid,
            charge_id=charge_id,
            telegram_payment_charge_id=pay.telegram_payment_charge_id,
        )
    if not ok:
        log.info("TG pay: allaqachon qayta ishlangan charge=%s", charge_id)
        return

    log.info("TG pay OK user=%s +%s stars_coin=%s", user_id, paid, sc)
    await notify_player_topup(user_id, paid, sc, gt, hearts)

    try:
        await message.answer(
            _(
                "✅ Payment received: +%(paid)s ★\n"
                "Current balance: %(sc)s ★ Stars\n"
                "You can return to the game and continue shopping.",
                paid=paid,
                sc=sc,
            )
        )
    except TelegramForbiddenError:
        log_bot_blocked(message.chat.id if message.chat else None, context="payment_ok")
    except Exception as e:
        if is_bot_blocked_by_user(e):
            log_bot_blocked(message.chat.id if message.chat else None, context="payment_ok")

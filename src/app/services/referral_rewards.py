"""Yangi ro'yxatdan o'tishda referal mukofotini berish."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.database.repositories.referral import ReferralGrantResult, ReferralRepository

log = logging.getLogger("spinbottle.referral")


async def process_referral_signup(
    session: AsyncSession,
    ref_code: str | None,
    *,
    referee_label: str,
    new_user_id: int | None = None,
) -> ReferralGrantResult:
    ref_code = (ref_code or "").strip()
    if not ref_code:
        return ReferralGrantResult(0, None, False, 0, 0, "not_found")

    repo = ReferralRepository(session)
    await repo.ensure_default_settings()
    result = await repo.grant_signup_bonus(
        ref_code,
        referee_label=referee_label,
        skip_if_self=new_user_id,
    )
    if result.reason == "ok":
        log.info(
            "Referal +%s hearts user_id=%s partner=%s daily=%s/%s",
            result.granted,
            result.referrer_user_id,
            result.is_partner,
            result.daily_total,
            result.daily_limit,
        )
    elif result.reason == "daily_limit":
        log.info(
            "Referal limit reached referrer=%s daily=%s/%s",
            result.referrer_user_id,
            result.daily_total,
            result.daily_limit,
        )
    return result

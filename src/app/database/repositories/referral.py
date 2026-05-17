"""Referal bonuslari: partner va oddiy foydalanuvchilar, kunlik limit."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.database.models.partner import Partner
from src.app.database.models.referral_bonus import ReferralBonusSettings, ReferralDailyEarnings
from src.app.database.models.user import User
from src.app.database.repositories.game import GameRepository


@dataclass
class ReferralGrantResult:
    granted: int
    referrer_user_id: int | None
    is_partner: bool
    daily_total: int
    daily_limit: int
    reason: str  # ok | daily_limit | not_found | inactive | zero_bonus


class ReferralRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_default_settings(
        self, *, bonus_hearts: int = 50, bonus_limit: int = 10000
    ) -> ReferralBonusSettings:
        stmt = select(ReferralBonusSettings).where(ReferralBonusSettings.id == 1)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row:
            return row
        row = ReferralBonusSettings(
            id=1, bonus_hearts=bonus_hearts, bonus_limit=bonus_limit
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_default_settings(self) -> ReferralBonusSettings:
        row = await self.ensure_default_settings()
        return row

    async def update_default_settings(
        self, *, bonus_hearts: int | None = None, bonus_limit: int | None = None
    ) -> ReferralBonusSettings:
        row = await self.ensure_default_settings()
        if bonus_hearts is not None:
            row.bonus_hearts = max(0, int(bonus_hearts))
        if bonus_limit is not None:
            row.bonus_limit = max(0, int(bonus_limit))
        row.updated_at = datetime.now()
        await self.session.flush()
        return row

    async def get_partner_by_code(self, code: str) -> Partner | None:
        code = (code or "").strip()
        if not code:
            return None
        stmt = (
            select(Partner)
            .where(Partner.partner_id == code, Partner.is_active.is_(True))
            .options(selectinload(Partner.user))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_partner_by_user_id(self, user_id: int) -> Partner | None:
        stmt = select(Partner).where(Partner.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_partners(self) -> list[Partner]:
        stmt = (
            select(Partner)
            .options(selectinload(Partner.user))
            .order_by(Partner.id.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_partner(
        self,
        *,
        user_id: int,
        partner_id: str | None = None,
        invited_bonus: int = 50,
        bonus_limit: int = 10000,
    ) -> Partner:
        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("user_not_found")
        pid = (partner_id or user.referral_id or "").strip()
        if not pid:
            raise ValueError("partner_id_required")
        existing = await self.get_partner_by_code(pid)
        if existing and existing.user_id != user_id:
            raise ValueError("partner_id_taken")
        row = await self.get_partner_by_user_id(user_id)
        if row:
            row.partner_id = pid
            row.invited_bonus = max(0, int(invited_bonus))
            row.bonus_limit = max(0, int(bonus_limit))
            row.is_active = True
            row.updated_at = datetime.now()
        else:
            row = Partner(
                user_id=user_id,
                partner_id=pid,
                invited_bonus=max(0, int(invited_bonus)),
                bonus_limit=max(0, int(bonus_limit)),
                invited_guests=0,
                is_active=True,
            )
            self.session.add(row)
        await self.session.flush()
        return row

    async def update_partner(
        self,
        partner_pk: int,
        *,
        invited_bonus: int | None = None,
        bonus_limit: int | None = None,
        is_active: bool | None = None,
    ) -> Partner | None:
        row = await self.session.get(Partner, partner_pk)
        if not row:
            return None
        if invited_bonus is not None:
            row.invited_bonus = max(0, int(invited_bonus))
        if bonus_limit is not None:
            row.bonus_limit = max(0, int(bonus_limit))
        if is_active is not None:
            row.is_active = bool(is_active)
        row.updated_at = datetime.now()
        await self.session.flush()
        return row

    async def _get_daily_row(self, user_id: int, day: date) -> ReferralDailyEarnings:
        stmt = select(ReferralDailyEarnings).where(
            ReferralDailyEarnings.user_id == user_id,
            ReferralDailyEarnings.earned_date == day,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row:
            return row
        row = ReferralDailyEarnings(user_id=user_id, earned_date=day, hearts_earned=0)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_daily_earned(self, user_id: int, day: date | None = None) -> int:
        day = day or date.today()
        row = await self._get_daily_row(user_id, day)
        return int(row.hearts_earned or 0)

    async def grant_signup_bonus(
        self,
        ref_code: str,
        *,
        referee_label: str,
        skip_if_self: int | None = None,
    ) -> ReferralGrantResult:
        ref_code = (ref_code or "").strip()
        if not ref_code:
            return ReferralGrantResult(
                0, None, False, 0, 0, "not_found"
            )

        partner = await self.get_partner_by_code(ref_code)
        if partner:
            referrer_id = partner.user_id
            invited_bonus = int(partner.invited_bonus)
            bonus_limit = int(partner.bonus_limit)
            is_partner = True
            if skip_if_self and skip_if_self == referrer_id:
                return ReferralGrantResult(
                    0, referrer_id, True, 0, bonus_limit, "self"
                )
        else:
            stmt = select(User).where(User.referral_id == ref_code)
            referrer = (await self.session.execute(stmt)).scalar_one_or_none()
            if not referrer:
                return ReferralGrantResult(
                    0, None, False, 0, 0, "not_found"
                )
            referrer_id = referrer.id
            settings = await self.get_default_settings()
            invited_bonus = int(settings.bonus_hearts)
            bonus_limit = int(settings.bonus_limit)
            is_partner = False
            if skip_if_self and skip_if_self == referrer_id:
                return ReferralGrantResult(
                    0, referrer_id, False, 0, bonus_limit, "self"
                )

        if invited_bonus <= 0:
            return ReferralGrantResult(
                0, referrer_id, is_partner, 0, bonus_limit, "zero_bonus"
            )

        today = date.today()
        daily = await self._get_daily_row(referrer_id, today)
        earned = int(daily.hearts_earned or 0)
        if bonus_limit > 0 and earned >= bonus_limit:
            return ReferralGrantResult(
                0,
                referrer_id,
                is_partner,
                earned,
                bonus_limit,
                "daily_limit",
            )

        remaining = (
            invited_bonus
            if bonus_limit <= 0
            else min(invited_bonus, max(0, bonus_limit - earned))
        )
        if remaining <= 0:
            return ReferralGrantResult(
                0,
                referrer_id,
                is_partner,
                earned,
                bonus_limit,
                "daily_limit",
            )

        game = GameRepository(self.session)
        await game.ensure_wallet(referrer_id)
        tx_type = "partner_referral_bonus" if is_partner else "referral_bonus"
        await game.add_hearts(
            referrer_id,
            remaining,
            tx_type=tx_type,
            description=f"Referal: {referee_label}"[:200],
        )

        daily.hearts_earned = earned + remaining
        await self.session.flush()

        return ReferralGrantResult(
            remaining,
            referrer_id,
            is_partner,
            int(daily.hearts_earned),
            bonus_limit,
            "ok",
        )

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database.base import Base


class ReferralBonusSettings(Base):
    """Oddiy foydalanuvchilar uchun global referal sozlamalari (bitta qator)."""

    __tablename__ = "referral_bonus_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bonus_hearts: Mapped[int] = mapped_column(
        Integer, server_default="50", nullable=False
    )
    bonus_limit: Mapped[int] = mapped_column(
        Integer, server_default="10000", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ReferralDailyEarnings(Base):
    """Referrer uchun kunlik hearts daromadi (limit nazorati)."""

    __tablename__ = "referral_daily_earnings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    earned_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    hearts_earned: Mapped[int] = mapped_column(
        BigInteger, server_default="0", nullable=False
    )

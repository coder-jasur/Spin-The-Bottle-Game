from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    referral_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    referred_by_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)

    login: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)

    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_code: Mapped[str | None] = mapped_column(Text, nullable=True)

    xp: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    level: Mapped[int] = mapped_column(BigInteger, server_default="1", nullable=False)
    age: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    birth_date: Mapped[str | None] = mapped_column(Text, nullable=True)

    gender: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(Text, default="active", nullable=False)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    vip_status: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vip_payment_history: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vip_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    invited_guests: Mapped[int] = mapped_column(
        BigInteger, server_default="0", nullable=False
    )

    username_change_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )

    league_name: Mapped[str] = mapped_column(
        Text, server_default="none", nullable=False
    )

    music_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False
    )
    sound_volume: Mapped[int] = mapped_column(
        Integer, server_default="100", nullable=False
    )
    friends_privacy: Mapped[str] = mapped_column(
        Text, server_default="everyone", nullable=False
    )

    frame: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    stone: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    # Sotib olingan ramka/toshlar: {"ruby": 1, "emerald": 1, ...}
    owned_decor_items: Mapped[dict] = mapped_column(
        JSON, server_default="{}", nullable=False
    )
    status_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    zodiac_sign: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_verified: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )

    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_viewed_daily_message: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    is_banned: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    number_of_complaints: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    ban_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    kisses: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    dj: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    expense: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    importance: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    emotion: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)

    daily_streak: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    last_bonus_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    harem_owner_id: Mapped[int | None] = mapped_column(
        BigInteger, server_default="0", nullable=True
    )
    harem_price: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
    harem_courts_received: Mapped[int] = mapped_column(
        BigInteger, server_default="0", nullable=False
    )
    harem_owner_paid_price: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )

    kickout_streak_count: Mapped[int] = mapped_column(
        BigInteger, server_default="0", nullable=False
    )
    kickout_last_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    gift_love_stock: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    wallet = relationship("Wallet", back_populates="user", uselist=False)
    boosters = relationship("UserBooster", back_populates="user", uselist=False)
    stats = relationship("UserStats", back_populates="user")
    achievements = relationship("UserAchievement", back_populates="user")
    relations = relationship(
        "UserRelation", foreign_keys="UserRelation.user_id", back_populates="user"
    )

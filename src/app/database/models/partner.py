from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database.base import Base


class Partner(Base):
    """Maxsus referal hamkor: har taklif va kunlik limit admin tomondan."""

    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    partner_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    invited_bonus: Mapped[int] = mapped_column(
        Integer, server_default="50", nullable=False
    )
    bonus_limit: Mapped[int] = mapped_column(
        Integer, server_default="10000", nullable=False
    )
    invited_guests: Mapped[int] = mapped_column(
        BigInteger, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", foreign_keys=[user_id])

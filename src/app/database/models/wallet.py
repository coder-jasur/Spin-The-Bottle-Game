from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey,
    Integer, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.app.database.base import Base


# ═══════════════════════════════════════════════════════════════════════════
# WALLET
# ═══════════════════════════════════════════════════════════════════════════
class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    hearts: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    stars_coin: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    gift_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    user = relationship("User", back_populates="wallet")
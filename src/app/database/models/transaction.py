from datetime import datetime
from sqlalchemy import BigInteger, ForeignKey, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database.base import Base

class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, server_default="stars", nullable=False)  # stars, hearts, vip
    type: Mapped[str] = mapped_column(Text, nullable=False)  # earn, spend, buy, reward, vip_buy
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False
    )

from datetime import datetime
from sqlalchemy import BigInteger, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database.base import Base

class UserBooster(Base):
    __tablename__ = "user_boosters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    # ⚡️ Choice Boosters (Sanoqli)
    passionate_kiss_count: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    slap_count: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)

    # 🏅 League Boosters
    x2_booster_count: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    x2_booster_active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)

    limit_increase_count: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    bonus_points_count: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)

    # Relationship
    user = relationship("User", back_populates="boosters")

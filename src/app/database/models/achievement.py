from sqlalchemy import BigInteger, ForeignKey, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database.base import Base

class Achievement(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text, unique=True, nullable=False) # masalan: 'first_kiss'
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

class UserAchievement(Base):
    __tablename__ = "user_achievements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    achievement_id: Mapped[int] = mapped_column(ForeignKey("achievements.id", ondelete="CASCADE"), nullable=False)

    status: Mapped[str] = mapped_column(Text, default="locked", nullable=False) # locked, completed
    level: Mapped[int] = mapped_column(Integer, default=0, nullable=False) # 1-5 stars
    # Qaysi darajagacha mukofot (gold) olingan — qayta claim oldini olish
    bonus_claimed_level: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    # Relationship
    user = relationship("User", back_populates="achievements")
    achievement = relationship("Achievement")

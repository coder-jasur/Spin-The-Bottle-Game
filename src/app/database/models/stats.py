from sqlalchemy import BigInteger, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database.base import Base

class UserStats(Base):
    __tablename__ = "user_stats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Kategoriya: kisses, dj, expense, importance, emotion
    category: Mapped[str] = mapped_column(Text, nullable=False)

    # Ballar
    daily_value: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    weekly_value: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    monthly_value: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    total_value: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)

    # Bir foydalanuvchi bir kategoriyada faqat bitta qatorga ega bo'lishi kerak
    __table_args__ = (UniqueConstraint('user_id', 'category', name='_user_category_uc'),)

    # Relationship
    user = relationship("User", back_populates="stats")

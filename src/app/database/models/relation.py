from datetime import datetime
from sqlalchemy import ForeignKey, Text, DateTime, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database.base import Base

class UserRelation(Base):
    __tablename__ = "user_relations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # turi: friend, admirer
    type: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False
    )

    __table_args__ = (UniqueConstraint('user_id', 'target_id', 'type', name='_user_relation_uc'),)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="relations")
    target = relationship("User", foreign_keys=[target_id])

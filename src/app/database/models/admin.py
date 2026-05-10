from sqlalchemy import ForeignKey, Integer, Text, DateTime, func, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from src.app.database.base import Base

class Admins(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(Text, default="superadmin", nullable=False) # Masalan: superadmin, moderator

    # Relationship to User
    user = relationship("User")

class AdminActionLog(Base):
    __tablename__ = "admin_action_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False) # Masalan: "add_stars", "add_hearts", "broadcast", "ban"
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class BroadcastMessage(Base):
    __tablename__ = "broadcast_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationship to User
    admin = relationship("User")

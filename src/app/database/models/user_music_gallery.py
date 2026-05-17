"""Foydalanuvchi profil musiqa galereyasi (add-mp3 / user-gallery)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database.base import Base


class UserMusicGalleryItem(Base):
    __tablename__ = "user_music_gallery"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    video_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    artist: Mapped[str] = mapped_column(Text, nullable=False, server_default="YouTube")
    duration: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

"""Musiqa katalogi — qidiruv va get_by_ids uchun cache (provider + video_id)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database.base import Base


class MusicTrack(Base):
    """
    Global trek katalogi.
    Manba: upstream API, music_popular.json import, foydalanuvchi harakatlari.
    """

    __tablename__ = "music_tracks"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_music_provider_ext"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    track_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="song"
    )  # song | movie | video
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    artist: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    duration: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    search_blob: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

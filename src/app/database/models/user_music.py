"""Foydalanuvchi musiqa papkalari (sevimlilar, tarix) — WebSocket sync."""
from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database.base import Base

# Klient papkalari (get_favorite_songs / mark_song_favorite)
MUSIC_FOLDER_FAV_SONGS = "fav_songs"
MUSIC_FOLDER_FAV_VIDEOS = "fav_videos"
MUSIC_FOLDER_HISTORY_SONGS = "history_songs"
MUSIC_FOLDER_HISTORY_VIDEOS = "history_videos"


class UserMusicFolder(Base):
    """
    Foydalanuvchi papkasi: faqat trek ID ro'yxati (klient keyin get_by_ids chaqiradi).
    folder: fav_songs | fav_videos | history_songs | history_videos
    provider: mv | yt | ok | cz | ...
    """

    __tablename__ = "user_music_folders"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    folder: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, primary_key=True, server_default="mv")
    song_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

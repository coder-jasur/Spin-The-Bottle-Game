"""Musiqa sevimlilar / tarix — DB."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.database.models.user_music import UserMusicFolder

FOLDER_LIMITS: dict[str, int] = {
    "fav_songs": 30,
    "fav_videos": 30,
    "history_songs": 100,
    "history_videos": 100,
}
DEFAULT_FOLDER_LIMIT = 30


def folder_limit(folder: str) -> int:
    return FOLDER_LIMITS.get(folder, DEFAULT_FOLDER_LIMIT)


class MusicFavoritesRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_song_ids(
        self, user_id: int, folder: str, provider: str
    ) -> list[str]:
        stmt = select(UserMusicFolder).where(
            UserMusicFolder.user_id == int(user_id),
            UserMusicFolder.folder == folder,
            UserMusicFolder.provider == provider,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if not row or not row.song_ids:
            return []
        return [str(x) for x in row.song_ids if x]

    async def set_song_ids(
        self, user_id: int, folder: str, provider: str, song_ids: list[str]
    ) -> list[str]:
        limit = folder_limit(folder)
        clean = []
        seen: set[str] = set()
        for sid in song_ids:
            s = str(sid).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            clean.append(s)
            if len(clean) >= limit:
                break

        stmt = select(UserMusicFolder).where(
            UserMusicFolder.user_id == int(user_id),
            UserMusicFolder.folder == folder,
            UserMusicFolder.provider == provider,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row:
            row.song_ids = clean
        else:
            self.session.add(
                UserMusicFolder(
                    user_id=int(user_id),
                    folder=folder,
                    provider=provider,
                    song_ids=clean,
                )
            )
        await self.session.flush()
        return clean

    async def mark_song(
        self,
        user_id: int,
        folder: str,
        provider: str,
        song_id: str,
        *,
        favorite: bool,
    ) -> list[str]:
        sid = str(song_id).strip()
        if not sid:
            return await self.get_song_ids(user_id, folder, provider)

        current = await self.get_song_ids(user_id, folder, provider)
        rest = [x for x in current if x != sid]
        if favorite:
            rest.insert(0, sid)
        return await self.set_song_ids(user_id, folder, provider, rest)

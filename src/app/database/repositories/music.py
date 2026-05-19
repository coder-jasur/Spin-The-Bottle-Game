"""Musiqa ma'lumotlar bazasi — katalog va foydalanuvchi papkalari."""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.database.models.music_track import MusicTrack
from src.app.database.models.user_music import (
    MUSIC_FOLDER_HISTORY_SONGS,
    MUSIC_FOLDER_HISTORY_VIDEOS,
    UserMusicFolder,
)

FOLDER_LIMITS: dict[str, int] = {
    "fav_songs": 30,
    "fav_videos": 30,
    "history_songs": 100,
    "history_videos": 100,
}
DEFAULT_FOLDER_LIMIT = 30


def folder_limit(folder: str) -> int:
    return FOLDER_LIMITS.get(folder, DEFAULT_FOLDER_LIMIT)


def _safe_duration_field(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        try:
            return max(0, int(raw))
        except (ValueError, OverflowError):
            return 0
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return 0
        try:
            return max(0, int(float(s)))
        except (ValueError, TypeError):
            return 0
    return 0


def _row_to_api(row: dict[str, Any]) -> dict[str, Any]:
    vid = str(row.get("id") or row.get("video_id") or row.get("external_id") or "")
    thumb = row.get("thumbnail") or row.get("icon") or (
        f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else ""
    )
    duration = _safe_duration_field(row.get("duration"))
    return {
        "id": vid,
        "video_id": vid,
        "title": str(row.get("title") or ""),
        "artist": str(row.get("artist") or row.get("channel") or ""),
        "channel": str(row.get("channel") or row.get("artist") or ""),
        "duration": duration,
        "thumbnail": str(thumb),
        "icon": str(row.get("icon") or thumb),
        "provider": str(row.get("provider") or "yt"),
        "type": str(row.get("type") or row.get("track_type") or "song"),
    }


def _search_blob(title: str, artist: str, ext_id: str) -> str:
    return f"{title} {artist} {ext_id}".lower()


class MusicCatalogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_track(self, row: dict[str, Any], *, provider: str = "yt") -> None:
        vid = str(row.get("id") or row.get("video_id") or "").strip()
        if not vid:
            return
        prov = str(row.get("provider") or provider).strip() or "yt"
        title = str(row.get("title") or "")
        artist = str(row.get("artist") or row.get("channel") or "")
        stmt = select(MusicTrack).where(
            MusicTrack.provider == prov,
            MusicTrack.external_id == vid,
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        thumb = row.get("thumbnail") or row.get("icon")
        payload = {
            "title": title,
            "artist": artist,
            "duration": _safe_duration_field(row.get("duration")),
            "thumbnail": str(thumb) if thumb else None,
            "icon": str(row.get("icon") or thumb or "") or None,
            "url": row.get("url"),
            "track_type": str(row.get("type") or row.get("track_type") or "song"),
            "meta": row,
            "search_blob": _search_blob(title, artist, vid),
        }
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            self.session.add(
                MusicTrack(provider=prov, external_id=vid, **payload)
            )

    async def upsert_many(self, rows: list[dict[str, Any]], *, provider: str = "yt") -> None:
        for row in rows:
            await self.upsert_track(row, provider=provider)

    async def get_by_ids(
        self,
        ids: list[str],
        *,
        provider: str | None = None,
        track_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if not ids:
            return []
        stmt = select(MusicTrack).where(MusicTrack.external_id.in_(ids))
        if provider:
            stmt = stmt.where(MusicTrack.provider == provider)
        if track_type:
            stmt = stmt.where(MusicTrack.track_type == track_type)
        rows = (await self.session.execute(stmt)).scalars().all()
        by_id = {r.external_id: r for r in rows}
        out: list[dict[str, Any]] = []
        for i in ids:
            r = by_id.get(i)
            if r:
                out.append(
                    _row_to_api(
                        {
                            "id": r.external_id,
                            "title": r.title,
                            "artist": r.artist,
                            "duration": r.duration,
                            "thumbnail": r.thumbnail,
                            "icon": r.icon,
                            "provider": r.provider,
                            "type": r.track_type,
                        }
                    )
                )
        return out

    async def search(
        self,
        query: str,
        *,
        limit: int = 48,
        provider: str | None = None,
        track_type: str | None = None,
    ) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        stmt = select(MusicTrack)
        if provider:
            stmt = stmt.where(MusicTrack.provider == provider)
        if track_type:
            stmt = stmt.where(MusicTrack.track_type == track_type)
        if q:
            stmt = stmt.where(
                or_(
                    MusicTrack.search_blob.contains(q),
                    MusicTrack.title.ilike(f"%{q}%"),
                    MusicTrack.artist.ilike(f"%{q}%"),
                )
            )
        stmt = stmt.limit(min(limit, 200))
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            _row_to_api(
                {
                    "id": r.external_id,
                    "title": r.title,
                    "artist": r.artist,
                    "duration": r.duration,
                    "thumbnail": r.thumbnail,
                    "icon": r.icon,
                    "provider": r.provider,
                    "type": r.track_type,
                }
            )
            for r in rows
        ]

    async def list_popular(
        self,
        *,
        limit: int = 48,
        provider: str | None = None,
        track_type: str | None = None,
    ) -> list[dict[str, Any]]:
        stmt = select(MusicTrack).order_by(MusicTrack.updated_at.desc().nullslast())
        if provider:
            stmt = stmt.where(MusicTrack.provider == provider)
        if track_type:
            stmt = stmt.where(MusicTrack.track_type == track_type)
        stmt = stmt.limit(min(limit, 200))
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            _row_to_api(
                {
                    "id": r.external_id,
                    "title": r.title,
                    "artist": r.artist,
                    "duration": r.duration,
                    "thumbnail": r.thumbnail,
                    "icon": r.icon,
                    "provider": r.provider,
                    "type": r.track_type,
                }
            )
            for r in rows
        ]


class MusicFavoritesRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_song_ids(
        self, user_id: int, folder: str, provider: str
    ) -> list[str]:
        return await self.list_folder_song_ids(
            int(user_id), folder, preferred_provider=provider
        )

    async def list_folder_song_ids(
        self,
        user_id: int,
        folder: str,
        *,
        preferred_provider: str | None = None,
    ) -> list[str]:
        folders: tuple[str, ...] = (folder,)
        if folder == MUSIC_FOLDER_HISTORY_VIDEOS:
            folders = (MUSIC_FOLDER_HISTORY_VIDEOS, MUSIC_FOLDER_HISTORY_SONGS)

        merged: list[str] = []
        seen: set[str] = set()

        def _add(ids: list[str]) -> None:
            for sid in ids:
                s = str(sid).strip()
                if s and s not in seen:
                    seen.add(s)
                    merged.append(s)

        if preferred_provider:
            for fname in folders:
                stmt = select(UserMusicFolder).where(
                    UserMusicFolder.user_id == user_id,
                    UserMusicFolder.folder == fname,
                    UserMusicFolder.provider == preferred_provider,
                )
                row = (await self.session.execute(stmt)).scalar_one_or_none()
                if row and row.song_ids:
                    _add([str(x) for x in row.song_ids if x])

        for fname in folders:
            stmt = select(UserMusicFolder).where(
                UserMusicFolder.user_id == user_id,
                UserMusicFolder.folder == fname,
            )
            rows = (await self.session.execute(stmt)).scalars().all()
            for row in rows:
                if (
                    fname == MUSIC_FOLDER_HISTORY_SONGS
                    and folder == MUSIC_FOLDER_HISTORY_VIDEOS
                    and str(row.provider or "") not in ("mv", "ok", "vk", "vv")
                ):
                    continue
                if row.song_ids:
                    _add([str(x) for x in row.song_ids if x])

        return merged[: folder_limit(folder)]

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

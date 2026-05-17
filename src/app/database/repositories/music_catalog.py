"""Musiqa katalogi: qidiruv, ID bo'yicha, cache."""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.database.models.music_track import MusicTrack


def _row_to_api(row: dict[str, Any]) -> dict[str, Any]:
    vid = str(row.get("id") or row.get("video_id") or row.get("external_id") or "")
    thumb = row.get("thumbnail") or row.get("icon") or (
        f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else ""
    )
    return {
        "id": vid,
        "video_id": vid,
        "title": str(row.get("title") or ""),
        "artist": str(row.get("artist") or row.get("channel") or ""),
        "channel": str(row.get("channel") or row.get("artist") or ""),
        "duration": int(row.get("duration") or 0),
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
            "duration": int(row.get("duration") or 0),
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

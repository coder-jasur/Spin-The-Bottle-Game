"""
Musiqa API — `api_music/popular`, `api_music/search`, `get_by_ids*`.

Default (audio): RapidAPI YT MP3 — RAPIDAPI_KEY + MUSIC_USE_RAPIDAPI_YT=1.
Qidiruv fallback: MUSIC_USE_YTDLP=1.

Mahalliy katalog JSON (MUSIC_USE_LOCAL_JSON=1):
  - site/data/music_popular.json
  - site/data/music_get_by_ids_and_popular.json

O‘z upstream musiqa serveringiz bo‘lsa:
  - MUSIC_API_BASE=https://example.com  → .../api_music/popular|search|...
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

log = logging.getLogger("spinbottle.music")
router = APIRouter(tags=["Music"])

_SITE_DIR = Path(__file__).resolve().parents[2] / "site"
_LOCAL_POPULAR = _SITE_DIR / "data" / "music_popular.json"
_LOCAL_GET_BY_IDS = _SITE_DIR / "data" / "music_get_by_ids_and_popular.json"

_FALLBACK_POPULAR: list[dict[str, Any]] = [
    {
        "id": "jfKfPfyJRdk",
        "video_id": "jfKfPfyJRdk",
        "title": "Lofi hip hop radio - beats to relax/study to",
        "artist": "Lofi Girl",
        "duration": 3600,
        "provider": "mv",
        "type": "movie",
        "icon": "https://i.ytimg.com/vi/jfKfPfyJRdk/mqdefault.jpg",
        "thumbnail": "https://i.ytimg.com/vi/jfKfPfyJRdk/mqdefault.jpg",
        "url": "https://www.youtube.com/watch?v=jfKfPfyJRdk",
    },
    {
        "id": "dQw4w9WgXcQ",
        "video_id": "dQw4w9WgXcQ",
        "title": "Rick Astley - Never Gonna Give You Up",
        "artist": "Rick Astley",
        "duration": 213,
        "provider": "mv",
        "type": "movie",
        "icon": "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    },
]
_FALLBACK_BY_IDS: list[dict[str, Any]] = [
    {
        "id": "jfKfPfyJRdk",
        "video_id": "jfKfPfyJRdk",
        "title": "Lofi hip hop radio",
        "artist": "Lofi Girl",
        "duration": 3600,
        "provider": "yt",
        "type": "song",
        "url": "https://www.youtube.com/watch?v=jfKfPfyJRdk",
        "icon": "https://i.ytimg.com/vi/jfKfPfyJRdk/mqdefault.jpg",
        "thumbnail": "https://i.ytimg.com/vi/jfKfPfyJRdk/mqdefault.jpg",
    },
]

# Upstream muvaffaqiyatli javob — Redis cache (tunnel sekin bo‘lsa ham tez javob)
_POPULAR_CACHE_TTL_SEC = 300.0


async def _popular_cache_get(cache_key: str) -> list[dict[str, Any]] | None:
    from src.app.api.music.service import cache_get_json, popular_key

    data = await cache_get_json(popular_key(cache_key))
    if isinstance(data, list) and data:
        return [x for x in data if isinstance(x, dict)]
    return None


async def _popular_cache_set(cache_key: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    from src.app.api.music.service import cache_set_json, popular_key

    await cache_set_json(
        popular_key(cache_key),
        rows,
        ttl_sec=int(_POPULAR_CACHE_TTL_SEC),
    )


async def _popular_cache_clear() -> None:
    from src.app.api.music.service import cache_delete_prefix

    await cache_delete_prefix("music:popular:")


def _api_base() -> str:
    """Bo'sh = default upstream yo'q (eski bottle API ishlatilmaydi)."""
    return os.getenv("MUSIC_API_BASE", "").strip().rstrip("/")


def _upstream_popular() -> str:
    explicit = os.getenv("MUSIC_POPULAR_URL", "").strip()
    if explicit:
        return explicit
    base = _api_base()
    return f"{base}/api_music/popular" if base else ""


def _upstream_get_by_ids_and_popular() -> str:
    explicit = os.getenv("MUSIC_GET_BY_IDS_AND_POPULAR_URL", "").strip()
    if explicit:
        return explicit
    base = _api_base()
    return f"{base}/api_music/get_by_ids_and_popular" if base else ""


def _upstream_check() -> str:
    explicit = os.getenv("MUSIC_CHECK_URL", "").strip()
    if explicit:
        return explicit
    base = _api_base()
    return f"{base}/api_music/check" if base else ""


def _upstream_search() -> str:
    explicit = os.getenv("MUSIC_SEARCH_URL", "").strip()
    if explicit:
        return explicit
    base = _api_base()
    return f"{base}/api_music/search" if base else ""


def _upstream_get_by_ids_simple() -> str:
    explicit = os.getenv("MUSIC_GET_BY_IDS_SIMPLE_URL", "").strip()
    if explicit:
        return explicit
    base = _api_base()
    return f"{base}/api_music/get_by_ids" if base else ""


def _filter_rows_text(rows: list[dict[str, Any]], needle: str) -> list[dict[str, Any]]:
    q = (needle or "").strip().lower()
    if not q:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        blob = " ".join(
            str(row.get(k) or "")
            for k in ("title", "artist", "channel", "id", "video_id")
        ).lower()
        if q in blob:
            out.append(row)
    return out


def _bootstrap_catalog_rows() -> list[dict[str, Any]]:
    """JSON katalog + fallback — qidiruv uchun mahalliy manba."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in (_LOCAL_POPULAR, _LOCAL_GET_BY_IDS):
        if not path.is_file() or path.stat().st_size < 3:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            vid = str(item.get("id") or item.get("video_id") or "")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            rows.append(item)
    if not rows:
        rows = list(_FALLBACK_POPULAR) + list(_FALLBACK_BY_IDS)
    return rows


async def _cache_rows_to_db(request: Request, rows: list[dict[str, Any]]) -> None:
    db = getattr(request.app.state, "db", None)
    if not db or not rows:
        return
    try:
        from src.app.database.repositories.music import MusicCatalogRepository

        async with db.session_factory() as session:
            repo = MusicCatalogRepository(session)
            await repo.upsert_many(rows)
            await session.commit()
    except Exception:
        pass


def _song_video_id(song: dict[str, Any]) -> str:
    return str(
        song.get("id") or song.get("video_id") or song.get("song_id") or ""
    ).strip()


def _local_check_response(song: dict[str, Any]) -> dict[str, Any]:
    vid = _song_video_id(song)
    return {
        "success": True,
        "ok": True,
        "id": vid,
        "video_id": vid,
        "song_data": song,
    }


def _query_pairs(request: Request) -> list[tuple[str, str]]:
    return [(str(k), str(v)) for k, v in request.query_params.multi_items()]


def _parse_int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _count_from_pairs(pairs: list[tuple[str, str]], default: int = 48) -> int:
    for k, v in pairs:
        if k == "count":
            return _parse_int(v, default, 1, 200)
    return default


def _merge_body_into_query(
    pairs: list[tuple[str, str]],
    body: Any,
) -> list[tuple[str, str]]:
    """POST maydonlarini query ustiga yozadi (bir xil kalit — body ustun)."""
    if not isinstance(body, dict):
        return pairs
    keys_from_body = {str(k) for k in body.keys()}
    out = [(a, b) for a, b in pairs if a not in keys_from_body]
    for key, val in body.items():
        ks = str(key)
        if isinstance(val, (list, tuple)):
            for item in val:
                out.append((ks, str(item)))
        elif val is None:
            continue
        elif isinstance(val, bool):
            out.append((ks, "true" if val else "false"))
        elif isinstance(val, (dict,)):
            out.append((ks, json.dumps(val, ensure_ascii=False)))
        else:
            out.append((ks, str(val)))
    return out


def _local_json_catalog_enabled() -> bool:
    """MUSIC_USE_LOCAL_JSON=1 bo'lsa faqat site/data/*.json katalog."""
    return os.getenv("MUSIC_USE_LOCAL_JSON", "").strip().lower() in ("1", "true", "yes", "on")


def _rapidapi_yt_catalog_active() -> bool:
    try:
        from src.app.api.music.service import rapidapi_yt_enabled
    except ImportError:
        return False
    return rapidapi_yt_enabled()


def _api_over_local_catalog() -> bool:
    """API katalog mahalliy music_popular.json dan ustun."""
    if _local_json_catalog_enabled():
        return False
    if not _rapidapi_yt_catalog_active():
        return False
    raw = os.getenv("MUSIC_API_OVER_LOCAL") or os.getenv("MUSIC_DEEZER_OVER_LOCAL", "1")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _persist_catalog_json(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        _LOCAL_POPULAR.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(rows, ensure_ascii=False)
        _LOCAL_POPULAR.write_text(text, encoding="utf-8")
        _LOCAL_GET_BY_IDS.write_text(text, encoding="utf-8")
    except OSError as e:
        log.warning("music catalog json write: %s", e)


def _cache_has_youtube_ids(rows: list[dict[str, Any]]) -> bool:
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        vid = str(row.get("id") or row.get("song_id") or "")
        if _looks_like_youtube_id(vid):
            return True
    return False


def _load_local_list(path: Path) -> list[dict[str, Any]] | None:
    if not _local_json_catalog_enabled():
        return None
    if not path.is_file() or path.stat().st_size < 3:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(raw, list) and raw:
        return [x for x in raw if isinstance(x, dict) and (x.get("id") or x.get("video_id"))]
    return None


def _track_type_from_pairs(pairs: list[tuple[str, str]]) -> str | None:
    for k, v in pairs:
        if k == "type" and v:
            return str(v).strip()
    return None


def _duration_seconds(row: dict[str, Any]) -> int:
    """Katalog / upstream qatorlaridagi duration — int, float, 'mm:ss', 'H:MM:SS' yoki noto'g'ri qator."""
    v = row.get("duration")
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        try:
            return max(0, int(v))
        except (ValueError, OverflowError):
            return 0
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return 0
        if s.startswith("PT") and "M" in s:
            try:
                m = re.search(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
                if m:
                    h, mi, se = m.groups()
                    return (
                        int(h or 0) * 3600
                        + int(mi or 0) * 60
                        + int(se or 0)
                    )
            except (ValueError, TypeError):
                return 0
        if ":" in s:
            parts = s.split(":")
            try:
                seg = [int(float(p)) for p in parts if p.strip() != ""]
                if len(seg) == 3:
                    return seg[0] * 3600 + seg[1] * 60 + seg[2]
                if len(seg) == 2:
                    return seg[0] * 60 + seg[1]
                if len(seg) == 1:
                    return seg[0]
            except (ValueError, TypeError):
                return 0
        try:
            return max(0, int(float(s)))
        except (ValueError, TypeError):
            return 0
    return 0


def _filter_rows_by_type(
    rows: list[dict[str, Any]], track_type: str | None
) -> list[dict[str, Any]]:
    if not track_type:
        return rows
    if track_type == "movie":
        return [
            r
            for r in rows
            if str(r.get("type") or "") == "movie"
            or _duration_seconds(r) >= 120
        ]
    return [r for r in rows if str(r.get("type") or "song") == track_type]


def _response_body_bytes(resp: Response) -> bytes:
    raw = getattr(resp, "body", None)
    if raw is None:
        return b""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, memoryview):
        return raw.tobytes()
    try:
        return bytes(raw)
    except TypeError:
        return b""


def _popular_cache_key(pairs: list[tuple[str, str]]) -> str:
    return json.dumps(sorted(pairs), ensure_ascii=False)


async def _popular_rows_from_db(
    request: Request, *, count: int, track_type: str | None
) -> list[dict[str, Any]]:
    db = getattr(request.app.state, "db", None)
    if not db:
        return []
    try:
        from src.app.database.repositories.music import MusicCatalogRepository

        async with db.session_factory() as session:
            repo = MusicCatalogRepository(session)
            rows = await repo.list_popular(limit=count, track_type=track_type)
    except Exception:
        return []
    if _rapidapi_yt_catalog_active() and track_type != "movie":
        from src.app.api.music.service import normalize_youtube_id

        rows = [
            r
            for r in rows
            if normalize_youtube_id(str(r.get("id") or r.get("video_id") or ""))
        ]
    return rows


async def _popular_fallback_rows(
    request: Request, *, count: int, pairs: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    track_type = _track_type_from_pairs(pairs)
    if _rapidapi_yt_catalog_active() and track_type != "movie":
        from src.app.api.music.service import rapidapi_yt_search_async

        ra = await rapidapi_yt_search_async("", count, track_type)
        if ra:
            return ra[:count]
    rows = await _popular_rows_from_db(request, count=count, track_type=track_type)
    if not rows and not _api_over_local_catalog():
        rows = _filter_rows_by_type(_bootstrap_catalog_rows(), track_type)
    if not rows:
        rows = list(_FALLBACK_POPULAR)
    return rows[:count]


async def _try_proxy_upstream(
    url: str,
    *,
    method: str,
    query: list[tuple[str, str]],
    json_body: Any | None,
) -> Response | None:
    if not (url or "").strip():
        return None
    try:
        proxied = await _proxy_upstream(url, method=method, query=query, json_body=json_body)
    except Exception:
        return None
    if proxied.status_code < 200 or proxied.status_code >= 400:
        return None
    body = _response_body_bytes(proxied)
    if not body:
        return None
    return proxied


def _extract_ids(data: dict[str, Any] | None, pairs: list[tuple[str, str]]) -> list[str]:
    ids: list[str] = []
    if isinstance(data, dict):
        raw = data.get("ids") or data.get("video_ids") or data.get("id_list")
        if isinstance(raw, str) and raw.strip():
            ids.extend([x.strip() for x in raw.split(",") if x.strip()])
        elif isinstance(raw, list):
            ids.extend([str(x) for x in raw if x is not None])
    for k, v in pairs:
        if k in ("ids", "video_ids", "id") and v:
            if k == "id":
                ids.append(v)
            else:
                ids.extend([x.strip() for x in v.split(",") if x.strip()])
    seen: set[str] = set()
    uniq: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq


def _local_get_by_ids(
    items: list[dict[str, Any]],
    ids: list[str],
    count: int,
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """strict=True: faqat so'ralgan id lar (history/fav) — katalogdan qo'shimcha video qo'shilmaydi."""
    by_id: dict[str, dict[str, Any]] = {}
    for it in items:
        iid = str(it.get("id") or it.get("video_id") or "")
        if iid:
            by_id[iid] = it
    out: list[dict[str, Any]] = []
    for vid in ids:
        if vid in by_id:
            out.append(by_id[vid])
    if strict:
        return out[:count] if count else out
    have = {str(x.get("id") or x.get("video_id") or "") for x in out}
    for it in items:
        if len(out) >= count:
            break
        iid = str(it.get("id") or it.get("video_id") or "")
        if iid and iid not in have:
            out.append(it)
            have.add(iid)
    return out[:count]


def _rows_by_id_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        vid = str(row.get("id") or row.get("video_id") or row.get("song_id") or "")
        if vid:
            out[vid] = row
    return out


def _merge_rows_preserve_order(
    ids: list[str], *sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for src in sources:
        by_id.update(_rows_by_id_map(src))
    return [by_id[vid] for vid in ids if vid in by_id]


async def _resolve_rows_by_ids(
    request: Request,
    ids: list[str],
    *,
    count: int,
    track_type: str | None,
) -> list[dict[str, Any]]:
    """History / favourites: faqat berilgan video id lar, tartib saqlanadi."""
    if not ids:
        return []

    rows: list[dict[str, Any]] = []
    db = getattr(request.app.state, "db", None)
    if db:
        try:
            from src.app.database.repositories.music import MusicCatalogRepository

            async with db.session_factory() as session:
                repo = MusicCatalogRepository(session)
                rows = await repo.get_by_ids(ids, track_type=track_type)
        except Exception:
            rows = []

    have = {str(r.get("id") or r.get("video_id") or "") for r in rows}
    missing = [i for i in ids if i not in have]

    if missing:
        yt_ids = [i for i in missing if _looks_like_youtube_id(i)]
        if yt_ids and _rapidapi_yt_catalog_active():
            from src.app.api.music.service import rapidapi_yt_videos_by_ids_async

            ra_rows = await rapidapi_yt_videos_by_ids_async(yt_ids, track_type)
            rows = _merge_rows_preserve_order(ids, rows, ra_rows)
            have = {str(r.get("id") or r.get("video_id") or "") for r in rows}
            missing = [i for i in ids if i not in have]

    if missing:
        from src.app.api.music.service import (
            ytdlp_available,
            ytdlp_videos_by_ids_async,
        )

        if ytdlp_available():
            yt_rows = await ytdlp_videos_by_ids_async(missing, track_type)
            rows = _merge_rows_preserve_order(ids, rows, yt_rows)
            have = {str(r.get("id") or r.get("video_id") or "") for r in rows}
            missing = [i for i in ids if i not in have]

    if missing:
        catalog = _bootstrap_catalog_rows()
        cat_rows = _local_get_by_ids(catalog, missing, len(missing), strict=True)
        rows = _merge_rows_preserve_order(ids, rows, cat_rows)

    ordered = _merge_rows_preserve_order(ids, rows)
    if count > 0:
        return ordered[:count]
    return ordered


def _normalize_music_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Klient `song_id` va to'liq `duration` kutadi (A3 / YouTube player)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        vid = str(
            row.get("song_id") or row.get("video_id") or row.get("id") or ""
        ).strip()
        if not vid:
            continue
        dur = _duration_seconds(row)
        item = dict(row)
        item["id"] = vid
        item["video_id"] = vid
        item["song_id"] = vid
        item["duration"] = dur
        item["watch_url"] = _watch_url(vid)
        raw_url = str(item.get("url") or "").strip()
        is_movie = str(item.get("type") or "") == "movie"
        if is_movie:
            if not raw_url or "/api_music/play/" in raw_url:
                item["url"] = item["watch_url"]
        elif _is_direct_media_url(raw_url):
            item["url"] = raw_url
        else:
            item["url"] = _play_stream_path(vid)
            item["stream_url"] = item["url"]
            item["provider"] = "cz"
        thumb = item.get("thumbnail") or item.get("icon") or _thumb_url(vid)
        item["icon"] = item.get("icon") or thumb
        item["thumbnail"] = thumb
        item.setdefault("artist", str(item.get("channel") or item.get("artist") or ""))
        item.setdefault("channel", item["artist"])
        if item.get("type") == "movie":
            item.setdefault("provider", "mv")
        else:
            # Audio: server stream (HTML5), YouTube iframe emas — bot-blokirovka yo'q
            item.setdefault("provider", item.get("provider") or "cz")
        out.append(item)
    return out


def _thumb_url(vid: str) -> str:
    return f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"


def _looks_like_youtube_id(raw: str) -> bool:
    vid = (raw or "").strip()
    return bool(re.match(r"^[\w-]{11}$", vid))


def _play_503_youtube_blocked(vid: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            f"YouTube audio bloklandi (id={vid}). "
            "yt-dlp: Sign in to confirm you're not a bot. "
            "Yechim: Video (mv) rejimi, cookies.txt (/api_music/setup), "
            "yoki RAPIDAPI_KEY / cookies.txt sozlang."
        ),
    )


def _is_direct_media_url(url: str | None) -> bool:
    u = (url or "").strip().lower()
    if not u:
        return False
    if "youtube.com" in u or "youtu.be" in u:
        return False
    return (
        u.endswith((".mp3", ".m4a", ".ogg", ".wav", ".webm"))
        or ".m3u8" in u
        or "googlevideo.com" in u
        or "dzcdn.net" in u
        or "/api_music/play/" in u
    )


def _play_stream_path(vid: str) -> str:
    return f"/api_music/play/{vid}"


def _watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _audio_url_and_provider(vid: str, *, is_movie: bool) -> tuple[str, str]:
    """Movie → YouTube iframe. Qo'shiq → faqat audio stream (cz), video emas."""
    if is_movie:
        return _watch_url(vid), "mv"
    return _play_stream_path(vid), "cz"


def _proxied_list_response(
    proxied: Response, response: Response, *, catalog: str
) -> Response | None:
    try:
        data = json.loads(_response_body_bytes(proxied))
        if isinstance(data, list) and data:
            return _json_response(data, response, catalog=catalog)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        pass
    return None


def _json_response(data: Any, response: Response, *, catalog: str | None = None) -> Response:
    if isinstance(data, list):
        data = _normalize_music_rows(data)
    if catalog:
        response.headers["X-Music-Catalog"] = catalog
    return Response(
        content=json.dumps(data, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
    )


_UPSTREAM_TIMEOUT_SEC = float(os.getenv("MUSIC_UPSTREAM_TIMEOUT_SEC", "5"))


async def warm_music_catalog_cache() -> int:
    """Ishga tushganda: RapidAPI YT / upstream — music_popular.json."""
    from src.app.api.music.service import (
        rapidapi_yt_enabled,
        rapidapi_yt_search_async,
    )
    from src.app.api.music.service import ytdlp_available, ytdlp_search_async

    pairs = [("count", "48"), ("platform", "bottle_fb"), ("user_country", "UZ")]
    key = _popular_cache_key(pairs)

    if rapidapi_yt_enabled():
        await _popular_cache_clear()

    cached_rows = await _popular_cache_get(key)
    if cached_rows:
        if not rapidapi_yt_enabled() or _cache_has_youtube_ids(cached_rows):
            return len(cached_rows)

    upstream = _upstream_get_by_ids_and_popular()
    if (upstream or "").strip():
        try:
            async with httpx.AsyncClient(
                timeout=_UPSTREAM_TIMEOUT_SEC, follow_redirects=True
            ) as client:
                r = await client.get(upstream, params=pairs)
            if r.status_code < 200 or r.status_code >= 400 or not r.content:
                data = None
            else:
                data = r.json()
            if isinstance(data, list) and data:
                await _popular_cache_set(key, data)
                try:
                    _LOCAL_POPULAR.parent.mkdir(parents=True, exist_ok=True)
                    text = json.dumps(data, ensure_ascii=False)
                    _LOCAL_POPULAR.write_text(text, encoding="utf-8")
                    _LOCAL_GET_BY_IDS.write_text(text, encoding="utf-8")
                except OSError as e:
                    log.warning("music catalog write: %s", e)
                log.info("music catalog warmed (upstream): %s tracks", len(data))
                return len(data)
        except Exception as e:
            log.warning("music catalog warm (upstream) failed: %s", e)

    if rapidapi_yt_enabled():
        try:
            data = await rapidapi_yt_search_async("", 48, None)
            if isinstance(data, list) and data:
                await _popular_cache_set(key, data)
                try:
                    _LOCAL_POPULAR.parent.mkdir(parents=True, exist_ok=True)
                    text = json.dumps(data, ensure_ascii=False)
                    _LOCAL_POPULAR.write_text(text, encoding="utf-8")
                    _LOCAL_GET_BY_IDS.write_text(text, encoding="utf-8")
                except OSError as e:
                    log.warning("music catalog write: %s", e)
                log.info("music catalog warmed (rapidapi-yt): %s tracks", len(data))
                return len(data)
        except Exception as e:
            log.warning("music catalog warm (rapidapi-yt) failed: %s", e)

    if not ytdlp_available():
        return 0
    try:
        data = await ytdlp_search_async("", 48, None)
        if not isinstance(data, list) or not data:
            return 0
        await _popular_cache_set(key, data)
        try:
            _LOCAL_POPULAR.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(data, ensure_ascii=False)
            _LOCAL_POPULAR.write_text(text, encoding="utf-8")
            _LOCAL_GET_BY_IDS.write_text(text, encoding="utf-8")
        except OSError as e:
            log.warning("music catalog write: %s", e)
        log.info("music catalog warmed (yt-dlp): %s tracks", len(data))
        return len(data)
    except Exception as e:
        log.warning("music catalog warm (yt-dlp) failed: %s", e)
        return 0


async def _proxy_upstream(
    url: str,
    *,
    method: str,
    query: list[tuple[str, str]],
    json_body: Any | None,
) -> Response:
    async with httpx.AsyncClient(
        timeout=_UPSTREAM_TIMEOUT_SEC, follow_redirects=True
    ) as client:
        if method.upper() == "POST" and json_body is not None:
            r = await client.post(url, params=query, json=json_body)
        else:
            r = await client.get(url, params=query)
    ct = r.headers.get("content-type", "application/json; charset=utf-8")
    return Response(content=r.content, status_code=r.status_code, media_type=ct)


# ── audio stream (oddiy musiqa / cz pleer) ─────────────────────────────────


@router.get("/api_music/rapidapi_yt_status")
async def api_music_rapidapi_yt_status():
    from src.app.api.music.service import (
        rapidapi_yt_ping_async,
        rapidapi_yt_runtime_status,
    )

    st = await rapidapi_yt_runtime_status()
    if st.get("enabled"):
        st["api_ok"] = await rapidapi_yt_ping_async()
    else:
        st["api_ok"] = False
    return st


@router.get("/api_music/ytdlp_status")
async def api_music_ytdlp_status():
    """yt-dlp / YouTube cookies diagnostika."""
    from src.app.api.music.service import (
        _ensure_youtube_cookies_file,
        ytdlp_runtime_status,
    )

    _ensure_youtube_cookies_file()
    return ytdlp_runtime_status()


@router.get("/api_music/setup", response_class=HTMLResponse)
async def api_music_setup_page():
    """Brauzerdan cookies.txt yuklash (503 dan keyin bir marta)."""
    from src.app.api.music.service import ytdlp_runtime_status

    st = ytdlp_runtime_status()
    ok = st.get("cookies_ok")
    path = st.get("cookies_file") or "(yo'q)"
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="uz"><head><meta charset="utf-8"><title>YouTube cookies</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem;line-height:1.5}}
.ok{{color:#0a0}}.bad{{color:#c00}}code{{background:#f4f4f4;padding:.2rem .4rem;border-radius:4px}}
form{{margin-top:1.5rem;padding:1rem;border:1px solid #ddd;border-radius:8px}}
</style></head><body>
<h1>YouTube audio sozlash</h1>
<p>Holat: <strong class="{'ok' if ok else 'bad'}">{"cookies OK" if ok else "cookies yo'q (503)"}</strong></p>
<p>Fayl: <code>{path}</code></p>
<ol>
<li>Chrome/Edge da <a href="https://www.youtube.com" target="_blank">youtube.com</a> ga kiring.</li>
<li>Kengaytma: <strong>Get cookies.txt LOCALLY</strong> (Chrome Web Store).</li>
<li>YouTube sahifasida export → <code>cookies.txt</code>.</li>
<li>Quyida yuklang (server qayta ishga tushirish shart emas).</li>
</ol>
<form action="/api_music/setup/cookies" method="post" enctype="multipart/form-data">
<label>cookies.txt: <input type="file" name="file" accept=".txt" required></label>
<button type="submit">Yuklash</button>
</form>
<p><a href="/api_music/ytdlp_status">JSON status</a> · sinov: <code>/api_music/play/VIDEO_ID</code></p>
</body></html>"""
    )


@router.post("/api_music/setup/cookies")
async def api_music_setup_cookies(file: UploadFile = File(...)):
    """YouTube cookies.txt (Netscape) — Get cookies.txt LOCALLY kengaytmasidan."""
    from src.app.api.music.service import save_youtube_cookies_file, ytdlp_runtime_status

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Fayl bo'sh")
    try:
        path = save_youtube_cookies_file(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    st = ytdlp_runtime_status()
    return HTMLResponse(
        f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>OK</title></head>
<body style="font-family:system-ui;max-width:640px;margin:2rem auto">
<h1 style="color:#0a0">cookies.txt saqlandi</h1>
<p><code>{path}</code></p>
<p>cookies_ok: <strong>{st.get('cookies_ok')}</strong></p>
<p><a href="/api_music/setup">Orqaga</a> · <a href="/api_music/ytdlp_status">Status</a></p>
<p>Endi <code>/api_music/play/...</code> ishlashi kerak.</p>
</body></html>"""
    )


def _youtube_play_503_detail(err: str = "") -> str:
    base = (
        "YouTube audio olinmadi. Tekshiring: yt-dlp -f bestaudio --get-url VIDEO_URL. "
        "Agar CLI ishlasa, serverni qayta ishga tushiring. "
        "Agar CLI ham xato bersa: /api_music/setup (cookies.txt). "
        "Vaqtincha Video (mv) rejimi."
    )
    if err:
        return f"{base} Xato: {err[:280]}"
    return base


def _audio_stream_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "Accept-Ranges": "bytes"}


def _audio_streaming_response(
    stream_url: str,
    *,
    request: Request | None = None,
    media_type: str = "audio/mpeg",
) -> StreamingResponse:
    from src.app.api.music.service import guess_audio_media_type, iter_proxy_audio_stream

    mt = media_type or guess_audio_media_type(stream_url)
    range_h = request.headers.get("range") if request else None
    return StreamingResponse(
        iter_proxy_audio_stream(stream_url, range_header=range_h),
        media_type=mt,
        headers=_audio_stream_headers(),
    )


@router.head("/api_music/play/{video_id}")
async def api_music_play_head(video_id: str):
    vid = str(video_id or "").strip()
    if not vid:
        raise HTTPException(status_code=503)

    from src.app.api.music.service import (
        rapidapi_yt_enabled,
        rapidapi_yt_mp3_url_async,
        normalize_youtube_id,
    )

    if rapidapi_yt_enabled() and normalize_youtube_id(vid):
        if await rapidapi_yt_mp3_url_async(vid):
            return Response(status_code=200)
        raise HTTPException(status_code=503, detail="RapidAPI MP3 tayyor emas")

    from src.app.api.music.service import (
        ytdlp_audio_stream_url_async,
        ytdlp_available,
    )

    if not ytdlp_available():
        raise HTTPException(status_code=503)
    if await ytdlp_audio_stream_url_async(vid):
        return Response(status_code=200)
    raise _play_503_youtube_blocked(vid)


@router.get("/api_music/play/{video_id}")
async def api_music_play(video_id: str, request: Request):
    """Audio stream: RapidAPI YT (to'liq MP3) yoki yt-dlp."""
    from src.app.api.music.service import (
        ytdlp_audio_stream_url_async,
        ytdlp_available,
    )

    vid = str(video_id or "").strip()
    if not vid:
        raise HTTPException(status_code=400, detail="video_id kerak")

    from src.app.api.music.service import (
        rapidapi_yt_enabled,
        rapidapi_yt_mp3_url_async,
        normalize_youtube_id,
    )

    if rapidapi_yt_enabled():
        ytid = normalize_youtube_id(vid)
        if ytid:
            stream_url = await rapidapi_yt_mp3_url_async(ytid)
            if stream_url:
                return _audio_streaming_response(stream_url, request=request)
            raise HTTPException(
                status_code=503,
                detail=(
                    "RapidAPI MP3 olinmadi (processing yoki limit). "
                    "RAPIDAPI_KEY va Pro reja tekshiring."
                ),
            )

    if not ytdlp_available():
        raise HTTPException(status_code=503, detail="yt-dlp o'rnatilmagan")

    stream_url = await ytdlp_audio_stream_url_async(vid)
    if stream_url:
        return _audio_streaming_response(stream_url, request=request)

    raise _play_503_youtube_blocked(vid)


# ── check (musiqa preview oldin tekshiruv) ─────────────────────────────────


@router.api_route("/api_music/check", methods=["GET", "POST"])
async def api_music_check(request: Request, response: Response):
    """Klient MusicPreviewDialog: POST { song_data: {...} } → { success: true }."""
    body: dict[str, Any] = {}
    if request.method.upper() == "POST":
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                body = raw
        except Exception:
            body = {}

    song = body.get("song_data")
    if not isinstance(song, dict):
        song = body if _song_video_id(body) else None

    if not isinstance(song, dict) or not _song_video_id(song):
        return Response(
            status_code=400,
            content=json.dumps(
                {"success": False, "error": "song_data yoki video id kerak"},
                ensure_ascii=False,
            ),
            media_type="application/json; charset=utf-8",
        )

    if _rapidapi_yt_catalog_active():
        from src.app.api.music.service import (
            normalize_youtube_id,
            rapidapi_yt_mp3_url_async,
        )

        vid = _song_video_id(song)
        if normalize_youtube_id(vid):
            ok = bool(await rapidapi_yt_mp3_url_async(vid))
            payload = {
                "success": ok,
                "ok": ok,
                "id": vid,
                "error": None if ok else "RapidAPI MP3 mavjud emas",
                "song_data": song,
            }
            tag = "rapidapi-yt-check" if ok else "rapidapi-yt-check-fail"
            return _json_response(payload, response, catalog=tag)
        return _json_response(
            {
                "success": False,
                "ok": False,
                "error": "YouTube video id kerak",
                "id": vid,
            },
            response,
            catalog="rapidapi-yt-check-fail",
        )

    return _json_response(_local_check_response(song), response, catalog="local")


# ── search ────────────────────────────────────────────────────────────────


@router.api_route("/api_music/search", methods=["GET", "POST"])
async def api_music_search(request: Request, response: Response):
    """
    Klient: GET/POST ?q=...&count=48&user_vip=0&type=movie|song&platform=...
    Javob: JSON massiv (trek obyektlari).
    """
    pairs = _query_pairs(request)
    body: dict[str, Any] | None = None
    if request.method.upper() == "POST":
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                body = raw
        except Exception:
            body = None
        pairs = _merge_body_into_query(pairs, body)

    q = ""
    track_type: str | None = None
    count = 48
    for k, v in pairs:
        if k in ("q", "query", "name"):
            q = v
        elif k == "count":
            count = _parse_int(v, 48, 1, 200)
        elif k == "type":
            track_type = v or None

    proxied = await _try_proxy_upstream(
        _upstream_search(), method="GET", query=pairs, json_body=None
    )
    if proxied is not None:
        parsed = _proxied_list_response(proxied, response, catalog="upstream-search")
        if parsed is not None:
            return parsed

    from src.app.api.music.service import (
        rapidapi_yt_enabled,
        rapidapi_yt_search_async,
    )
    from src.app.api.music.service import ytdlp_available, ytdlp_search_async

    if rapidapi_yt_enabled() and track_type != "movie":
        ra_rows = await rapidapi_yt_search_async(q, count, track_type)
        if ra_rows:
            await _cache_rows_to_db(request, ra_rows)
            tag = "rapidapi-yt-search" if q else "rapidapi-yt-popular"
            return _json_response(ra_rows, response, catalog=tag)
        if ytdlp_available():
            yt_rows = await ytdlp_search_async(q, count, track_type)
            yt_rows = _filter_rows_by_type(yt_rows, track_type)[:count]
            if yt_rows:
                await _cache_rows_to_db(request, yt_rows)
                return _json_response(yt_rows, response, catalog="ytdlp-search")
        return _json_response([], response, catalog="rapidapi-yt-empty")

    if ytdlp_available():
        yt_rows = await ytdlp_search_async(q, count, track_type)
        yt_rows = _filter_rows_by_type(yt_rows, track_type)[:count]
        if yt_rows:
            await _cache_rows_to_db(request, yt_rows)
            return _json_response(yt_rows, response, catalog="ytdlp-search")

    rows = _filter_rows_text(_bootstrap_catalog_rows(), q)[:count]
    if track_type:
        rows = [
            r
            for r in rows
            if str(r.get("type") or "song") == track_type
            or (track_type == "movie" and _duration_seconds(r) > 600)
        ][:count]
    await _cache_rows_to_db(request, rows)
    return _json_response(rows, response, catalog="local-search")


# ── get_by_ids ────────────────────────────────────────────────────────────


@router.api_route("/api_music/get_by_ids", methods=["GET", "POST"])
async def api_music_get_by_ids_only(request: Request, response: Response):
    """Klient: ?id=abc,def yoki POST { id: \"a,b\" }."""
    pairs = _query_pairs(request)
    body: dict[str, Any] | None = None
    if request.method.upper() == "POST":
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                body = raw
        except Exception:
            body = None
        pairs = _merge_body_into_query(pairs, body)

    ids = _extract_ids(body, pairs)
    count = _count_from_pairs(pairs, 48)
    track_type = _track_type_from_pairs(pairs)

    if ids:
        proxied = await _try_proxy_upstream(
            _upstream_get_by_ids_simple(), method="GET", query=pairs, json_body=None
        )
        if proxied is not None:
            parsed = _proxied_list_response(proxied, response, catalog="upstream-by-ids")
            if parsed is not None:
                return parsed
        data = await _resolve_rows_by_ids(
            request, ids, count=count or len(ids), track_type=track_type
        )
        await _cache_rows_to_db(request, data)
        return _json_response(data, response, catalog="by-ids")

    proxied = await _try_proxy_upstream(
        _upstream_get_by_ids_simple(), method="GET", query=pairs, json_body=None
    )
    if proxied is not None:
        parsed = _proxied_list_response(proxied, response, catalog="upstream-by-ids")
        if parsed is not None:
            return parsed

    return _json_response([], response, catalog="by-ids-empty")


# ── popular ───────────────────────────────────────────────────────────────


@router.api_route("/api_music/popular", methods=["GET", "POST"])
async def api_music_popular(request: Request, response: Response):
    pairs = _query_pairs(request)
    count = _count_from_pairs(pairs, 48)

    body: Any | None = None
    if request.method.upper() == "POST":
        try:
            body = await request.json()
        except Exception:
            body = None
        pairs = _merge_body_into_query(pairs, body)
    if not any(k == "count" for k, _ in pairs):
        pairs = list(pairs) + [("count", str(count))]

    track_type = _track_type_from_pairs(pairs)
    cache_key = _popular_cache_key(pairs)

    from src.app.api.music.service import (
        rapidapi_yt_enabled,
        rapidapi_yt_search_async,
    )
    from src.app.api.music.service import ytdlp_available, ytdlp_search_async

    if _rapidapi_yt_catalog_active() and track_type != "movie":
        ra_rows = await rapidapi_yt_search_async("", count, track_type)
        if ra_rows:
            await _popular_cache_set(cache_key, ra_rows)
            _persist_catalog_json(ra_rows)
            await _cache_rows_to_db(request, ra_rows)
            return _json_response(ra_rows, response, catalog="rapidapi-yt-popular")
        if ytdlp_available():
            yrows = await ytdlp_search_async("", count, track_type)
            yrows = _filter_rows_by_type(yrows, track_type)[:count]
            if yrows:
                await _popular_cache_set(cache_key, yrows)
                _persist_catalog_json(yrows)
                await _cache_rows_to_db(request, yrows)
                return _json_response(yrows, response, catalog="ytdlp-popular")

    local = None if _api_over_local_catalog() else _load_local_list(_LOCAL_POPULAR)
    if local is not None:
        rows = _filter_rows_by_type(local, track_type)[:count]
        await _cache_rows_to_db(request, rows)
        return _json_response(rows, response, catalog="local")

    cached_rows = await _popular_cache_get(cache_key)
    if cached_rows:
        if not _rapidapi_yt_catalog_active() or _cache_has_youtube_ids(cached_rows):
            return _json_response(cached_rows[:count], response, catalog="cache")

    proxied = await _try_proxy_upstream(
        _upstream_popular(), method="GET", query=pairs, json_body=None
    )
    if proxied is not None:
        try:
            data = json.loads(_response_body_bytes(proxied))
            if isinstance(data, list) and data:
                await _popular_cache_set(cache_key, data)
                await _cache_rows_to_db(request, data)
                return _json_response(data, response, catalog="upstream")
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            pass

    if ytdlp_available():
        yrows = await ytdlp_search_async("", count, track_type)
        yrows = _filter_rows_by_type(yrows, track_type)[:count]
        if yrows:
            await _popular_cache_set(cache_key, yrows)
            await _cache_rows_to_db(request, yrows)
            return _json_response(yrows, response, catalog="ytdlp")

    rows = await _popular_fallback_rows(request, count=count, pairs=pairs)
    await _cache_rows_to_db(request, rows)
    return _json_response(rows, response, catalog="fallback")


# ── get_by_ids_and_popular ────────────────────────────────────────────────


@router.api_route("/api_music/get_by_ids_and_popular", methods=["GET", "POST"])
async def api_music_get_by_ids_and_popular(request: Request, response: Response):
    pairs = _query_pairs(request)
    count = _count_from_pairs(pairs, 48)

    body: Any | None = None
    if request.method.upper() == "POST":
        try:
            body = await request.json()
        except Exception:
            body = None

    if not any(k == "count" for k, _ in pairs):
        pairs = list(pairs) + [("count", str(count))]

    cache_key = "mix:" + _popular_cache_key(pairs)
    tt = _track_type_from_pairs(pairs)
    ids = _extract_ids(body if isinstance(body, dict) else None, pairs)

    if ids:
        data = await _resolve_rows_by_ids(
            request, ids, count=count or len(ids), track_type=tt
        )
        await _cache_rows_to_db(request, data)
        return _json_response(data, response, catalog="mix-by-ids")

    if not ids and _rapidapi_yt_catalog_active() and tt != "movie":
        from src.app.api.music.service import rapidapi_yt_search_async

        ra_rows = await rapidapi_yt_search_async("", count, tt)
        if ra_rows:
            await _popular_cache_set(cache_key, ra_rows)
            _persist_catalog_json(ra_rows)
            await _cache_rows_to_db(request, ra_rows)
            return _json_response(ra_rows, response, catalog="rapidapi-yt-popular")

    local = None if _api_over_local_catalog() else _load_local_list(_LOCAL_GET_BY_IDS)
    if local is not None:
        if ids:
            data = _local_get_by_ids(local, ids, count)
        else:
            data = local[:count]
        return _json_response(data, response, catalog="local")

    cached_rows = await _popular_cache_get(cache_key)
    if cached_rows:
        if not _rapidapi_yt_catalog_active() or _cache_has_youtube_ids(cached_rows):
            return _json_response(cached_rows[:count], response, catalog="cache")

    proxied: Response | None = None
    mix_upstream = _upstream_get_by_ids_and_popular()
    if (mix_upstream or "").strip():
        if request.method.upper() == "POST":
            post_json: Any = body if body is not None else {}
            proxied = await _try_proxy_upstream(
                mix_upstream,
                method="POST",
                query=pairs,
                json_body=post_json,
            )
        else:
            proxied = await _try_proxy_upstream(
                mix_upstream,
                method="GET",
                query=pairs,
                json_body=None,
            )
    if proxied is not None:
        try:
            data = json.loads(_response_body_bytes(proxied))
            if isinstance(data, list) and data:
                await _popular_cache_set(cache_key, data)
                await _cache_rows_to_db(request, data)
                return _json_response(data, response, catalog="upstream-mix")
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            pass

    from src.app.api.music.service import (
        ytdlp_available,
        ytdlp_search_async,
        ytdlp_videos_by_ids_async,
    )

    if ytdlp_available():
        ymix = await ytdlp_search_async("", count, tt)
        ymix = _filter_rows_by_type(ymix, tt)[:count]
        if ymix:
            await _cache_rows_to_db(request, ymix)
            return _json_response(ymix, response, catalog="ytdlp-mix-popular")

    data = await _popular_fallback_rows(request, count=count, pairs=pairs)
    await _cache_rows_to_db(request, data)
    return _json_response(data, response, catalog="fallback")

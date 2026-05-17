"""
Musiqa API — `api_music/popular` va `api_music/get_by_ids_and_popular`.
GET: barcha query parametrlar upstreamga uzatiladi (ixtiyoriy nomlar).
POST: JSON body upstreamga uzatiladi; `popular` uchun qo‘shimcha ravishda
body maydonlari GET query bilan birlashtirilishi mumkin (upstream faqat GET qabul qilsa).

Mahalliy katalog (faqat MUSIC_USE_LOCAL_JSON=1 bo‘lsa):
  - site/data/music_popular.json
  - site/data/music_get_by_ids_and_popular.json

Upstream URL (ixtiyoriy, to‘liq path):
  - MUSIC_POPULAR_URL
  - MUSIC_GET_BY_IDS_URL
Asos (ixtiyoriy): MUSIC_API_BASE — default https://bottle.tgspinbotlle.com (asl o‘yin bilan bir xil)
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response

log = logging.getLogger("spinbottle.music")
router = APIRouter(tags=["Music"])

_DEFAULT_BASE = "https://bottle.tgspinbotlle.com"
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

# Upstream muvaffaqiyatli javob — qisqa TTL cache (tunnel sekin bo‘lsa ham tez javob)
_POPULAR_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_POPULAR_CACHE_TTL_SEC = 300.0


def _api_base() -> str:
    return os.getenv("MUSIC_API_BASE", _DEFAULT_BASE).rstrip("/")


def _upstream_popular() -> str:
    return os.getenv(
        "MUSIC_POPULAR_URL",
        f"{_api_base()}/api_music/popular",
    ).strip()


def _upstream_get_by_ids_and_popular() -> str:
    return os.getenv(
        "MUSIC_GET_BY_IDS_AND_POPULAR_URL",
        f"{_api_base()}/api_music/get_by_ids_and_popular",
    ).strip()


def _upstream_check() -> str:
    return os.getenv("MUSIC_CHECK_URL", f"{_api_base()}/api_music/check").strip()


def _upstream_search() -> str:
    return os.getenv("MUSIC_SEARCH_URL", f"{_api_base()}/api_music/search").strip()


def _upstream_get_by_ids_simple() -> str:
    return os.getenv(
        "MUSIC_GET_BY_IDS_SIMPLE_URL",
        f"{_api_base()}/api_music/get_by_ids",
    ).strip()


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
        from src.app.database.repositories.music_catalog import MusicCatalogRepository

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
    """Odatda o‘chiq — upstream (asl bottle API) ishlatiladi. Offline: MUSIC_USE_LOCAL_JSON=1."""
    return os.getenv("MUSIC_USE_LOCAL_JSON", "").strip().lower() in ("1", "true", "yes", "on")


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
            or int(r.get("duration") or 0) >= 120
        ]
    return [r for r in rows if str(r.get("type") or "song") == track_type]


def _popular_cache_key(pairs: list[tuple[str, str]]) -> str:
    return json.dumps(sorted(pairs), ensure_ascii=False)


async def _popular_rows_from_db(
    request: Request, *, count: int, track_type: str | None
) -> list[dict[str, Any]]:
    db = getattr(request.app.state, "db", None)
    if not db:
        return []
    try:
        from src.app.database.repositories.music_catalog import MusicCatalogRepository

        async with db.session_factory() as session:
            repo = MusicCatalogRepository(session)
            return await repo.list_popular(limit=count, track_type=track_type)
    except Exception:
        return []


async def _popular_fallback_rows(
    request: Request, *, count: int, pairs: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    track_type = _track_type_from_pairs(pairs)
    rows = await _popular_rows_from_db(request, count=count, track_type=track_type)
    if not rows:
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
    try:
        proxied = await _proxy_upstream(url, method=method, query=query, json_body=json_body)
    except Exception:
        return None
    if proxied.status_code < 200 or proxied.status_code >= 400:
        return None
    if not proxied.body:
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
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for it in items:
        iid = str(it.get("id") or it.get("video_id") or "")
        if iid:
            by_id[iid] = it
    out: list[dict[str, Any]] = []
    for vid in ids:
        if vid in by_id:
            out.append(by_id[vid])
    have = {str(x.get("id") or x.get("video_id") or "") for x in out}
    for it in items:
        if len(out) >= count:
            break
        iid = str(it.get("id") or it.get("video_id") or "")
        if iid and iid not in have:
            out.append(it)
            have.add(iid)
    return out[:count]


def _json_response(data: Any, response: Response, *, catalog: str | None = None) -> Response:
    if catalog:
        response.headers["X-Music-Catalog"] = catalog
    return Response(
        content=json.dumps(data, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
    )


_UPSTREAM_TIMEOUT_SEC = float(os.getenv("MUSIC_UPSTREAM_TIMEOUT_SEC", "5"))


async def warm_music_catalog_cache() -> int:
    """Ishga tushganda upstream dan katalog yuklab JSON + xotiraga saqlaydi."""
    pairs = [("count", "48"), ("platform", "bottle_fb"), ("user_country", "UZ")]
    key = _popular_cache_key(pairs)
    if key in _POPULAR_CACHE:
        return len(_POPULAR_CACHE[key][1])
    try:
        async with httpx.AsyncClient(
            timeout=_UPSTREAM_TIMEOUT_SEC, follow_redirects=True
        ) as client:
            r = await client.get(_upstream_get_by_ids_and_popular(), params=pairs)
        if r.status_code < 200 or r.status_code >= 400 or not r.content:
            return 0
        data = r.json()
        if not isinstance(data, list) or not data:
            return 0
        _POPULAR_CACHE[key] = (time.time(), data)
        try:
            _SITE_DIR.mkdir(parents=True, exist_ok=True)
            text = json.dumps(data, ensure_ascii=False)
            _LOCAL_POPULAR.write_text(text, encoding="utf-8")
            _LOCAL_GET_BY_IDS.write_text(text, encoding="utf-8")
        except OSError as e:
            log.warning("music catalog write: %s", e)
        log.info("music catalog warmed: %s tracks", len(data))
        return len(data)
    except Exception as e:
        log.warning("music catalog warm failed: %s", e)
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

    try:
        proxied = await _proxy_upstream(
            _upstream_search(), method="GET", query=pairs, json_body=None
        )
        if proxied.status_code < 400:
            return proxied
    except Exception:
        pass

    rows = _filter_rows_text(_bootstrap_catalog_rows(), q)[:count]
    if track_type:
        rows = [
            r
            for r in rows
            if str(r.get("type") or "song") == track_type
            or (track_type == "movie" and r.get("duration", 0) > 600)
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

    try:
        proxied = await _proxy_upstream(
            _upstream_get_by_ids_simple(), method="GET", query=pairs, json_body=None
        )
        if proxied.status_code < 400:
            return proxied
    except Exception:
        pass

    catalog = _bootstrap_catalog_rows()
    local = _local_get_by_ids(catalog, ids, count) if ids else catalog[:count]
    await _cache_rows_to_db(request, local)
    return _json_response(local, response, catalog="local-by-ids")


# ── popular ───────────────────────────────────────────────────────────────


@router.api_route("/api_music/popular", methods=["GET", "POST"])
async def api_music_popular(request: Request, response: Response):
    local = _load_local_list(_LOCAL_POPULAR)
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

    if local is not None:
        track_type = _track_type_from_pairs(pairs)
        rows = _filter_rows_by_type(local, track_type)[:count]
        await _cache_rows_to_db(request, rows)
        return _json_response(rows, response, catalog="local")

    cache_key = _popular_cache_key(pairs)
    cached = _POPULAR_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _POPULAR_CACHE_TTL_SEC:
        return _json_response(cached[1][:count], response, catalog="cache")

    proxied = await _try_proxy_upstream(
        _upstream_popular(), method="GET", query=pairs, json_body=None
    )
    if proxied is not None:
        try:
            data = json.loads(proxied.body)
            if isinstance(data, list) and data:
                _POPULAR_CACHE[cache_key] = (time.time(), data)
                await _cache_rows_to_db(request, data)
                return proxied
        except (json.JSONDecodeError, TypeError):
            pass

    rows = await _popular_fallback_rows(request, count=count, pairs=pairs)
    await _cache_rows_to_db(request, rows)
    return _json_response(rows, response, catalog="fallback")


# ── get_by_ids_and_popular ────────────────────────────────────────────────


@router.api_route("/api_music/get_by_ids_and_popular", methods=["GET", "POST"])
async def api_music_get_by_ids_and_popular(request: Request, response: Response):
    local = _load_local_list(_LOCAL_GET_BY_IDS)
    pairs = _query_pairs(request)
    count = _count_from_pairs(pairs, 48)

    body: Any | None = None
    if request.method.upper() == "POST":
        try:
            body = await request.json()
        except Exception:
            body = None

    if local is not None:
        ids = _extract_ids(body if isinstance(body, dict) else None, pairs)
        if ids:
            data = _local_get_by_ids(local, ids, count)
        else:
            data = local[:count]
        return _json_response(data, response, catalog="local")

    if not any(k == "count" for k, _ in pairs):
        pairs = list(pairs) + [("count", str(count))]

    cache_key = "mix:" + _popular_cache_key(pairs)
    cached = _POPULAR_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _POPULAR_CACHE_TTL_SEC:
        return _json_response(cached[1][:count], response, catalog="cache")

    # Diskdagi katalog (startup warm) — upstream kutmasdan
    disk_rows = _bootstrap_catalog_rows()
    if len(disk_rows) >= min(count, 8):
        ids = _extract_ids(body if isinstance(body, dict) else None, pairs)
        if ids:
            data = _local_get_by_ids(disk_rows, ids, count)
        else:
            data = _filter_rows_by_type(disk_rows, _track_type_from_pairs(pairs))[:count]
        await _cache_rows_to_db(request, data)
        return _json_response(data, response, catalog="disk")

    proxied: Response | None = None
    if request.method.upper() == "POST":
        post_json: Any = body if body is not None else {}
        proxied = await _try_proxy_upstream(
            _upstream_get_by_ids_and_popular(),
            method="POST",
            query=pairs,
            json_body=post_json,
        )
    else:
        proxied = await _try_proxy_upstream(
            _upstream_get_by_ids_and_popular(),
            method="GET",
            query=pairs,
            json_body=None,
        )
    if proxied is not None:
        try:
            data = json.loads(proxied.body)
            if isinstance(data, list) and data:
                _POPULAR_CACHE[cache_key] = (time.time(), data)
                await _cache_rows_to_db(request, data)
                return proxied
        except (json.JSONDecodeError, TypeError):
            pass

    ids = _extract_ids(body if isinstance(body, dict) else None, pairs)
    if ids:
        data = _local_get_by_ids(disk_rows, ids, count)
    else:
        data = await _popular_fallback_rows(request, count=count, pairs=pairs)
    await _cache_rows_to_db(request, data)
    return _json_response(data, response, catalog="fallback")

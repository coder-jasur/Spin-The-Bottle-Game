"""
Musiqa API — `api_music/popular` va `api_music/get_by_ids_and_popular`.
GET: barcha query parametrlar upstreamga uzatiladi (ixtiyoriy nomlar).
POST: JSON body upstreamga uzatiladi; `popular` uchun qo‘shimcha ravishda
body maydonlari GET query bilan birlashtirilishi mumkin (upstream faqat GET qabul qilsa).

Mahalliy katalog (ustuvor):
  - site/data/music_popular.json
  - site/data/music_get_by_ids_and_popular.json

Upstream URL (ixtiyoriy, to‘liq path):
  - MUSIC_POPULAR_URL
  - MUSIC_GET_BY_IDS_URL
Asos (ixtiyoriy): MUSIC_API_BASE — default https://tgspinbotlle.com
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["Music"])

_DEFAULT_BASE = "https://tgspinbotlle.com"
_SITE_DIR = Path(__file__).resolve().parents[2] / "site"
_LOCAL_POPULAR = _SITE_DIR / "data" / "music_popular.json"
_LOCAL_GET_BY_IDS = _SITE_DIR / "data" / "music_get_by_ids_and_popular.json"

_FALLBACK_POPULAR: list[dict[str, Any]] = [
    {
        "id": "jfKfPfyJRdk",
        "title": "Lofi hip hop radio - beats to relax/study to",
        "view_count": 0,
        "duration": 0,
        "icon": "https://i.ytimg.com/vi/jfKfPfyJRdk/mqdefault.jpg",
    },
]
_FALLBACK_BY_IDS: list[dict[str, Any]] = [
    {
        "id": "jfKfPfyJRdk",
        "title": "Lofi hip hop radio",
        "artist": "",
        "duration": 0,
        "url": "https://i.ytimg.com/vi/jfKfPfyJRdk/mqdefault.jpg",
    },
]


def _api_base() -> str:
    return os.getenv("MUSIC_API_BASE", _DEFAULT_BASE).rstrip("/")


def _upstream_popular() -> str:
    return os.getenv(
        "MUSIC_POPULAR_URL",
        f"{_api_base()}/api_music/popular",
    ).strip()


def _upstream_get_by_ids() -> str:
    return os.getenv(
        "MUSIC_GET_BY_IDS_URL",
        f"{_api_base()}/api_music/get_by_ids_and_popular",
    ).strip()


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


def _load_local_list(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(raw, list) and raw:
        return [x for x in raw if isinstance(x, dict) and (x.get("id") or x.get("video_id"))]
    return None


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


async def _proxy_upstream(
    url: str,
    *,
    method: str,
    query: list[tuple[str, str]],
    json_body: Any | None,
) -> Response:
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        if method.upper() == "POST" and json_body is not None:
            r = await client.post(url, params=query, json=json_body)
        else:
            r = await client.get(url, params=query)
    ct = r.headers.get("content-type", "application/json; charset=utf-8")
    return Response(content=r.content, status_code=r.status_code, media_type=ct)


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
        # Mahalliy: faqat ro‘yxatni qisqartiramiz; qatorlar o‘zgartirilmaydi
        return _json_response(local[:count], response, catalog="local")

    try:
        # Upstream odatda GET — POST bo‘lsa ham query birlashtirilgan GET yuboriladi
        return await _proxy_upstream(_upstream_popular(), method="GET", query=pairs, json_body=None)
    except Exception:
        fb = _FALLBACK_POPULAR[: min(count, len(_FALLBACK_POPULAR))]
        return _json_response(fb, response, catalog="fallback")


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

    try:
        if request.method.upper() == "POST":
            if not any(k == "count" for k, _ in pairs):
                pairs = list(pairs) + [("count", str(count))]
            post_json: Any = body if body is not None else {}
            return await _proxy_upstream(
                _upstream_get_by_ids(),
                method="POST",
                query=pairs,
                json_body=post_json,
            )
        if not any(k == "count" for k, _ in pairs):
            pairs = list(pairs) + [("count", str(count))]
        return await _proxy_upstream(
            _upstream_get_by_ids(),
            method="GET",
            query=pairs,
            json_body=None,
        )
    except Exception:
        fb = _FALLBACK_BY_IDS[: min(count, len(_FALLBACK_BY_IDS))]
        return _json_response(fb, response, catalog="fallback")

"""Musiqa servislari — Redis cache, RapidAPI YouTube MP3, yt-dlp."""
from __future__ import annotations

# ── Redis cache ──────────────────────────────────────────────────────────

import hashlib
import json
import logging
import time
from typing import Any

log = logging.getLogger("spinbottle.music.redis")

_PREFIX = "music"
_client: Any = None
_client_checked = False
_mem: dict[str, tuple[float, str]] = {}


def _enabled() -> bool:
    from src.app.api.music.settings import music_env_bool

    return music_env_bool("MUSIC_USE_REDIS_CACHE", default=True)


async def _redis() -> Any | None:
    global _client, _client_checked
    if not _enabled():
        return None
    if _client_checked:
        return _client
    _client_checked = True
    try:
        import redis.asyncio as aioredis

        from src.app.core.config import load_config

        url = (load_config().redis_url or "").strip()
        if not url:
            log.warning("REDIS_URL bo'sh — musiqa cache RAM da")
            return None
        _client = aioredis.from_url(url, decode_responses=True)
        await _client.ping()
        log.info("Musiqa cache: Redis ulandi")
    except Exception as e:
        log.warning("Musiqa cache: Redis ulanmadi (%s), RAM fallback", e)
        _client = None
    return _client


def _mem_get(key: str) -> str | None:
    row = _mem.get(key)
    if not row:
        return None
    exp, val = row
    if exp > 0 and time.time() > exp:
        _mem.pop(key, None)
        return None
    return val


def _mem_set(key: str, value: str, ttl_sec: int) -> None:
    exp = time.time() + ttl_sec if ttl_sec > 0 else 0.0
    _mem[key] = (exp, value)


async def cache_get_str(key: str) -> str | None:
    r = await _redis()
    if r is not None:
        try:
            val = await r.get(key)
            if val is not None:
                return str(val)
        except Exception as e:
            log.debug("redis get %s: %s", key, e)
    return _mem_get(key)


async def cache_set_str(key: str, value: str, *, ttl_sec: int) -> None:
    if ttl_sec > 0:
        _mem_set(key, value, ttl_sec)
    r = await _redis()
    if r is None:
        return
    try:
        await r.set(key, value, ex=max(1, int(ttl_sec)))
    except Exception as e:
        log.debug("redis set %s: %s", key, e)


async def cache_get_json(key: str) -> Any | None:
    raw = await cache_get_str(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def cache_set_json(key: str, value: Any, *, ttl_sec: int) -> None:
    await cache_set_str(
        key, json.dumps(value, ensure_ascii=False), ttl_sec=ttl_sec
    )


async def cache_delete_prefix(prefix: str) -> int:
    """music:popular:* kabi kalitlarni tozalash."""
    n = 0
    for k in list(_mem.keys()):
        if k.startswith(prefix):
            _mem.pop(k, None)
            n += 1
    r = await _redis()
    if r is None:
        return n
    try:
        async for key in r.scan_iter(match=f"{prefix}*", count=100):
            await r.delete(key)
            n += 1
    except Exception as e:
        log.debug("redis delete prefix %s: %s", prefix, e)
    return n


def mp3_key(video_id: str) -> str:
    return f"{_PREFIX}:mp3:{video_id}"


def popular_key(cache_key: str) -> str:
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:32]
    return f"{_PREFIX}:popular:{digest}"


async def redis_cache_status() -> dict[str, Any]:
    r = await _redis()
    return {
        "enabled": _enabled(),
        "redis_connected": r is not None,
        "memory_fallback_entries": len(_mem),
    }

# ── RapidAPI YouTube MP3 ─────────────────────────────────────────────────

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

import httpx

log = logging.getLogger("spinbottle.music.rapidapi_yt")

_DEFAULT_HOST = "yt-search-and-download-mp3.p.rapidapi.com"
_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|^)([\w-]{11})(?:\?|&|$)")

def _rapidapi_key() -> str:
    from src.app.api.music.settings import music_env_str

    return (
        music_env_str("RAPIDAPI_KEY") or music_env_str("RAPIDAPI_KEY_MUSIC") or ""
    ).strip()


def rapidapi_yt_enabled() -> bool:
    if not _rapidapi_key():
        return False
    from src.app.api.music.settings import music_env_bool

    return music_env_bool("MUSIC_USE_RAPIDAPI_YT", default=True)


def _host() -> str:
    from src.app.api.music.settings import music_env_str

    return (music_env_str("YT_RAPIDAPI_HOST") or _DEFAULT_HOST).strip()


def _headers() -> dict[str, str]:
    host = _host()
    return {
        "x-rapidapi-key": _rapidapi_key(),
        "x-rapidapi-host": host,
    }


def _base_url() -> str:
    return f"https://{_host()}"


def _timeout() -> float:
    from src.app.api.music.settings import music_env_str

    try:
        return float(music_env_str("YT_RAPIDAPI_TIMEOUT_SEC", "45"))
    except ValueError:
        return 45.0


def _mp3_cache_ttl() -> float:
    from src.app.api.music.settings import music_env_str

    try:
        return float(music_env_str("YT_RAPIDAPI_MP3_CACHE_TTL_SEC", "21600"))
    except ValueError:
        return 21600.0


def _mp3_max_retries() -> int:
    from src.app.api.music.settings import music_env_str

    try:
        return max(1, min(int(music_env_str("YT_RAPIDAPI_MP3_MAX_RETRIES", "12")), 30))
    except ValueError:
        return 12


def _mp3_retry_sec() -> float:
    from src.app.api.music.settings import music_env_str

    try:
        return float(music_env_str("YT_RAPIDAPI_MP3_RETRY_SEC", "3"))
    except ValueError:
        return 3.0


def _popular_query() -> str:
    from src.app.api.music.settings import music_env_str

    return (music_env_str("YT_RAPIDAPI_POPULAR_QUERY") or "music").strip() or "music"


def normalize_youtube_id(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    m = _YT_ID.search(s)
    if m:
        return m.group(1)
    if len(s) == 11 and re.match(r"^[\w-]{11}$", s):
        return s
    return ""


def youtube_watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _thumb(vid: str) -> str:
    return f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"


def _parse_duration(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        try:
            return max(0, int(raw))
        except (ValueError, OverflowError):
            return 0
    s = str(raw).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    if ":" in s:
        parts = [p for p in s.split(":") if p.isdigit()]
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return 0
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
    try:
        return max(0, int(float(s)))
    except ValueError:
        return 0


def _extract_download_url(data: Any) -> str | None:
    if isinstance(data, str) and data.startswith("http"):
        return data.strip()
    if not isinstance(data, dict):
        return None
    for key in ("download", "download_url", "url", "link", "mp3", "audio"):
        val = data.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val.strip()
    return None


def _video_to_row(item: dict[str, Any], *, track_type: str | None) -> dict[str, Any] | None:
    vid = normalize_youtube_id(
        str(
            item.get("videoId")
            or item.get("video_id")
            or item.get("id")
            or item.get("url")
            or ""
        )
    )
    if not vid:
        return None
    title = str(item.get("title") or item.get("name") or vid).strip()
    artist = str(
        item.get("artist")
        or item.get("author")
        or item.get("channel")
        or item.get("uploader")
        or ""
    ).strip()
    dur = _parse_duration(
        item.get("duration") or item.get("lengthSeconds") or item.get("lengthText")
    )
    if track_type == "movie":
        row_type = "movie"
        provider = "mv"
    elif dur >= 600:
        row_type = "movie"
        provider = "mv"
    else:
        row_type = "song"
        provider = "cz"
    return {
        "id": vid,
        "video_id": vid,
        "song_id": vid,
        "title": title,
        "artist": artist,
        "channel": artist,
        "duration": dur,
        "provider": provider,
        "type": row_type,
        "source": "rapidapi-yt",
        "icon": str(item.get("thumbnail") or item.get("thumb") or _thumb(vid)),
        "thumbnail": str(item.get("thumbnail") or item.get("thumb") or _thumb(vid)),
        "url": f"/api_music/play/{vid}",
    }


def _rows_from_search_payload(data: Any, *, limit: int, track_type: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return out

    candidates: list[Any] = []
    for key in ("data", "results", "items", "contents", "videos"):
        val = data.get(key)
        if isinstance(val, list):
            candidates = val
            break

    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        item = entry.get("video") if isinstance(entry.get("video"), dict) else entry
        if isinstance(item, dict):
            row = _video_to_row(item, track_type=track_type)
            if row:
                out.append(row)
        if len(out) >= limit:
            break
    return out[:limit]


async def _get_json(path: str, *, params: dict[str, Any] | None = None) -> Any:
    url = f"{_base_url()}{path}"
    async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=True) as client:
        r = await client.get(url, params=params or {}, headers=_headers())
        r.raise_for_status()
        return r.json()


async def _try_search_paths(query: str, limit: int) -> Any | None:
    q = (query or "").strip()
    n = max(1, min(limit, 50))
    paths_params: list[tuple[str, dict[str, Any]]] = [
        ("/search", {"q": q, "limit": n}),
        ("/search", {"query": q, "limit": n}),
        ("/search", {"q": q}),
        ("/ytsearch", {"q": q, "limit": n}),
    ]
    for path, params in paths_params:
        try:
            return await _get_json(path, params=params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 405):
                continue
            log.debug("rapidapi yt search %s: %s", path, e)
            continue
        except Exception as e:
            log.debug("rapidapi yt search %s: %s", path, e)
            continue
    return None


async def rapidapi_yt_search_async(
    query: str, limit: int, track_type: str | None
) -> list[dict[str, Any]]:
    if not rapidapi_yt_enabled():
        return []
    q = (query or "").strip() or _popular_query()
    n = max(1, min(limit, 50))

    try:
        data = await _try_search_paths(q, n)
        if isinstance(data, list):
            rows: list[dict[str, Any]] = []
            for entry in data:
                if isinstance(entry, dict):
                    row = _video_to_row(entry, track_type=track_type)
                    if row:
                        rows.append(row)
                if len(rows) >= n:
                    break
            if rows:
                return rows
        if isinstance(data, dict):
            rows = _rows_from_search_payload(data, limit=n, track_type=track_type)
            if rows:
                return rows
    except Exception as e:
        log.warning("rapidapi yt search q=%r: %s", q, e)

    if ytdlp_available():
        yt_rows = await ytdlp_search_async(q, n, track_type)
        for row in yt_rows:
            row["source"] = "rapidapi-yt+ytdlp-meta"
        return yt_rows
    return []


async def _fetch_mp3_json(watch_url: str, video_id: str) -> dict[str, Any] | None:
    vid = normalize_youtube_id(video_id)
    param_sets: list[dict[str, str]] = [
        {"url": watch_url},
        {"url": watch_url, "format": "mp3"},
    ]
    if vid:
        param_sets.extend(
            [
                {"id": vid},
                {"videoId": vid},
                {"v": vid},
                {"url": f"https://youtu.be/{vid}"},
            ]
        )
    seen: set[str] = set()
    for params in param_sets:
        key = json.dumps(params, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        try:
            data = await _get_json("/mp3", params=params)
        except Exception as e:
            log.debug("rapidapi yt mp3 params=%s: %s", params, e)
            continue
        if isinstance(data, dict):
            return data
    log.warning("rapidapi yt mp3 failed for %s", watch_url)
    return None


async def rapidapi_yt_mp3_url_async(video_id: str) -> str | None:
    """To'liq MP3 URL (cache + processing retry)."""
    if not rapidapi_yt_enabled():
        return None
    vid = normalize_youtube_id(video_id)
    if not vid:
        return None

    cached_url = await cache_get_str(mp3_key(vid))
    if cached_url:
        return cached_url

    watch = youtube_watch_url(vid)
    retries = _mp3_max_retries()
    delay = _mp3_retry_sec()

    for attempt in range(retries):
        data = await _fetch_mp3_json(watch, vid)
        if not data:
            if attempt < retries - 1:
                await asyncio.sleep(delay)
            continue

        if data.get("success") is True:
            url = _extract_download_url(data)
            if url:
                await cache_set_str(
                    mp3_key(vid), url, ttl_sec=int(_mp3_cache_ttl())
                )
                return url
            err = data.get("error") or "download URL yo'q"
            log.warning("rapidapi yt mp3 %s: %s", vid, err)
            return None

        status = str(data.get("status") or "").lower()
        if status == "processing":
            log.debug("rapidapi yt mp3 %s processing (%s/%s)", vid, attempt + 1, retries)
            await asyncio.sleep(delay)
            continue

        err = data.get("error") or data.get("message") or "mp3 xato"
        log.warning("rapidapi yt mp3 %s: %s", vid, err)
        return None

    log.warning("rapidapi yt mp3 %s: timeout (processing)", vid)
    return None


async def rapidapi_yt_videos_by_ids_async(
    ids: list[str], track_type: str | None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in ids:
        vid = normalize_youtube_id(raw)
        if not vid:
            continue
        out.append(
            {
                "id": vid,
                "video_id": vid,
                "song_id": vid,
                "title": vid,
                "artist": "",
                "duration": 0,
                "provider": "cz",
                "type": "song",
                "source": "rapidapi-yt",
                "icon": _thumb(vid),
                "thumbnail": _thumb(vid),
                "url": f"/api_music/play/{vid}",
            }
        )
    if track_type == "movie":
        out = [r for r in out if str(r.get("type") or "") == "movie"]
    elif track_type:
        out = [r for r in out if str(r.get("type") or "song") == track_type]
    return out


async def rapidapi_yt_runtime_status() -> dict[str, Any]:
    st = {
        "enabled": rapidapi_yt_enabled(),
        "host": _host(),
        "has_key": bool(_rapidapi_key()),
        "mp3_cache_ttl_sec": _mp3_cache_ttl(),
        "note": (
            "Pro $10/oy — 300k so'rov. /mp3 = 1 so'rov/trek; Redis cache. "
            "Qidiruv: /search yoki yt-dlp metadata fallback."
        ),
        "pricing": "https://rapidapi.com/zayviusdigital/api/yt-search-and-download-mp3/pricing",
    }
    st.update(await redis_cache_status())
    return st


async def rapidapi_yt_ping_async() -> bool:
    if not rapidapi_yt_enabled():
        return False
    from src.app.api.music.settings import music_env_str

    vid = music_env_str("YT_RAPIDAPI_PING_VIDEO_ID", "dQw4w9WgXcQ")
    if not normalize_youtube_id(vid):
        return False
    try:
        data = await _fetch_mp3_json(youtube_watch_url(vid), vid)
        if not data:
            return False
        if data.get("success") is True and _extract_download_url(data):
            return True
        return str(data.get("status") or "").lower() == "processing"
    except Exception:
        return False

# ── yt-dlp ───────────────────────────────────────────────────────────────

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path
from typing import Any

log = logging.getLogger("spinbottle.music.ytdlp")

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None  # type: ignore[assignment, misc]

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_COOKIES_CACHE_PATH = _PROJECT_ROOT / "cookies.txt"
_COOKIES_EXPORT_TRIED = False

_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|^)([\w-]{11})(?:\?|&|$)")
_LIVE_TITLE_HINTS = re.compile(
    r"\b(live\s*stream|24/7|24\/7|lofi\s+(hip\s*hop\s+)?radio|live\s+radio)\b",
    re.I,
)


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def ytdlp_available() -> bool:
    if yt_dlp is None:
        return False
    from src.app.api.music.settings import music_env_bool, music_env_str

    explicit = music_env_str("MUSIC_USE_YTDLP", "")
    if explicit:
        return music_env_bool("MUSIC_USE_YTDLP", default=False)
    if rapidapi_yt_enabled():
        return True
    return music_env_bool("MUSIC_USE_YTDLP", default=True)


def _timeout_sec() -> float:
    try:
        return float(os.getenv("YTDLP_TIMEOUT_SEC", "45"))
    except ValueError:
        return 45.0


def _popular_query() -> str:
    return (os.getenv("YTDLP_POPULAR_QUERY") or "music").strip() or "music"


def _movie_query() -> str:
    return (
        os.getenv("YTDLP_MOVIE_QUERY") or "official music video"
    ).strip() or "official music video"


def _search_query(query: str, track_type: str | None) -> str:
    q = (query or "").strip()
    if q:
        return q
    if track_type == "movie":
        return _movie_query()
    return _popular_query()


def _extract_flat() -> bool:
    return _env_bool("YTDLP_EXTRACT_FLAT", default=True)


def _node_executable() -> str | None:
    explicit = (os.getenv("YTDLP_NODE_PATH") or "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    for name in ("node", "node.exe"):
        found = shutil.which(name)
        if found and "cursor" not in found.lower().replace("\\", "/"):
            return found
    return shutil.which("node") or shutil.which("node.exe")


def _find_existing_cookies_file() -> str | None:
    explicit = (os.getenv("YTDLP_COOKIES_FILE") or "").strip()
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p.resolve())
        log.warning("YTDLP_COOKIES_FILE yo'q: %s", explicit)
    for candidate in (
        _COOKIES_CACHE_PATH,
        _PROJECT_ROOT / "config" / "cookies.txt",
        Path.home() / "cookies.txt",
    ):
        if candidate.is_file() and candidate.stat().st_size > 64:
            return str(candidate.resolve())
    return None


def _export_cookies_from_installed_browsers() -> str | None:
    """Edge ochiq bo'lsa ham browser-cookie3 orqali cookies.txt (yt-dlp uchun)."""
    if _env_bool("YTDLP_DISABLE_BROWSER_COOKIE3", default=False):
        return None
    try:
        import browser_cookie3
    except ImportError:
        return None
    names = (
        ("edge", "chrome", "firefox", "brave", "chromium")
        if sys.platform == "win32"
        else ("chrome", "firefox", "chromium", "brave")
    )
    for name in names:
        loader = getattr(browser_cookie3, name, None)
        if loader is None:
            continue
        try:
            cj = loader(domain_name=".youtube.com")
            if not list(cj):
                continue
            cj.save(str(_COOKIES_CACHE_PATH), ignore_discard=True, ignore_expires=True)
            if _COOKIES_CACHE_PATH.is_file() and _COOKIES_CACHE_PATH.stat().st_size > 64:
                log.info("YouTube cookies.txt: %s brauzerdan yozildi", name)
                return str(_COOKIES_CACHE_PATH.resolve())
        except Exception as e:
            log.debug("browser_cookie3 %s: %s", name, e)
    return None


def reset_youtube_cookies_cache() -> None:
    global _COOKIES_EXPORT_TRIED
    _COOKIES_EXPORT_TRIED = False


def _ensure_youtube_cookies_file() -> str | None:
    global _COOKIES_EXPORT_TRIED
    found = _find_existing_cookies_file()
    if found:
        return found
    if _COOKIES_EXPORT_TRIED:
        return (
            str(_COOKIES_CACHE_PATH.resolve())
            if _COOKIES_CACHE_PATH.is_file()
            else None
        )
    _COOKIES_EXPORT_TRIED = True
    found = _export_cookies_from_installed_browsers()
    if found:
        return found
    return _export_cookies_via_ytdlp_cli()


def save_youtube_cookies_file(content: bytes) -> str:
    """Qo'lda yuklangan cookies.txt (Netscape format)."""
    data = content.strip()
    if len(data) < 64:
        raise ValueError("cookies.txt juda qisqa")
    _COOKIES_CACHE_PATH.write_bytes(data + (b"\n" if not data.endswith(b"\n") else b""))
    reset_youtube_cookies_cache()
    return str(_COOKIES_CACHE_PATH.resolve())


def ytdlp_cookies_ok() -> bool:
    return bool(_resolve_cookies_file())


def _resolve_cookies_file() -> str | None:
    return _ensure_youtube_cookies_file()


def _export_cookies_via_ytdlp_cli() -> str | None:
    """Edge yopiq bo'lsa cookies.txt — yt-dlp CLI (browser_cookie3 dan keyin)."""
    browser = _resolve_cookies_browser()
    if not browser:
        return None
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--cookies-from-browser",
        browser,
        "--cookies",
        str(_COOKIES_CACHE_PATH),
        "--skip-download",
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    ]
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            timeout=int(min(_timeout_sec(), 50)),
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.debug("yt-dlp cookies export: %s", e)
        return None
    if _COOKIES_CACHE_PATH.is_file() and _COOKIES_CACHE_PATH.stat().st_size > 64:
        log.info("YouTube cookies.txt: yt-dlp --cookies-from-browser %s", browser)
        return str(_COOKIES_CACHE_PATH.resolve())
    return None


def _resolve_cookies_browser() -> str | None:
    explicit = (os.getenv("YTDLP_COOKIES_FROM_BROWSER") or "").strip()
    if explicit:
        return explicit
    if _env_bool("YTDLP_AUTO_BROWSER_COOKIES", default=(sys.platform == "win32")):
        if sys.platform == "win32":
            return "edge"
        return "chrome"
    return None


def ytdlp_runtime_status() -> dict[str, Any]:
    """Diagnostika: /api_music/ytdlp_status va log uchun."""
    cf = _resolve_cookies_file()
    browser = _resolve_cookies_browser() if not cf else None
    node = _node_executable()
    return {
        "ytdlp_installed": yt_dlp is not None,
        "enabled": ytdlp_available(),
        "cookies_file": cf,
        "cookies_from_browser": browser,
        "node": node,
        "cookies_ok": bool(cf),
        "hint": (
            "YouTube ga Edge/Chrome da kiring, serverni qayta ishga tushiring "
            "(cookies.txt avtomatik). Qo'lda: scripts\\ytdlp_export_cookies.ps1"
        ),
    }


def _apply_ytdlp_runtime_opts(
    opts: dict[str, Any],
    *,
    allow_browser_cookies: bool = False,
    use_cookies: bool = True,
    player_clients: list[str] | None = None,
) -> None:
    """YouTube: JS runtime + cookies.

    Qidiruv/metadata: faqat cookies.txt — Windows da Edge DB qulflanganda
    ``cookiesfrombrowser`` butun ytsearch ni buzadi (javob []).
    Audio play: avval cookiesiz android/ios; keyin cookiefile / brauzer.
    """
    node = _node_executable()
    if node:
        opts["js_runtimes"] = {"node": {"path": node}}
        opts["remote_components"] = ["ejs:github"]
    if use_cookies:
        cf = _resolve_cookies_file()
        if cf:
            opts["cookiefile"] = cf
        elif allow_browser_cookies:
            browser = _resolve_cookies_browser()
            if browser:
                opts["cookiesfrombrowser"] = (browser,)
    clients = player_clients or ["android", "ios", "web", "mweb", "tv"]
    opts["extractor_args"] = {"youtube": {"player_client": clients}}


def _ydl_opts(
    *,
    playlistend: int | None = None,
    noplaylist: bool = True,
    allow_browser_cookies: bool = False,
    use_cookies: bool = True,
    player_clients: list[str] | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": int(os.getenv("YTDLP_SOCKET_TIMEOUT", "20")),
    }
    if noplaylist:
        opts["noplaylist"] = True
    if playlistend is not None:
        opts["playlistend"] = playlistend

    # Cookie HECH QACHON ishlatmaymiz — Chrome DB lock muammosi
    # player_client ni oddiy qoldiramiz — CLI da ishladi
    clients = player_clients or ["android", "ios", "web"]
    opts["extractor_args"] = {"youtube": {"player_client": clients}}
    return opts


def _ytdlp_pipe_cmd(vid: str, *, use_cookies: bool = False) -> list[str]:
    vid = _normalize_video_id(vid)
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-f",
        "bestaudio/best",
        "--no-playlist",
        "-o",
        "-",
        "--quiet",
        "--no-warnings",
        "--extractor-args",
        "youtube:player_client=android_vr,android",  # cookiesiz (android_embedded mavjud emas)
    ]
    if use_cookies:
        cf = _resolve_cookies_file()
        if cf:
            cmd.extend(["--cookies", cf])
    _append_ytdlp_node_args(cmd)
    cmd.append(f"https://www.youtube.com/watch?v={vid}")
    return cmd


def _normalize_video_id(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    m = _YT_ID.search(s)
    if m:
        return m.group(1)
    if len(s) == 11 and re.match(r"^[\w-]{11}$", s):
        return s
    return ""


def _thumb(vid: str) -> str:
    return f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"


def _duration_from_entry(entry: dict[str, Any]) -> int:
    raw = entry.get("duration")
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        try:
            return max(0, int(raw))
        except (ValueError, OverflowError):
            return 0
    if isinstance(raw, str) and raw.strip():
        try:
            return max(0, int(float(raw.strip())))
        except (ValueError, TypeError):
            return 0
    return 0


def _should_skip_entry(entry: dict[str, Any]) -> bool:
    """Live / radio — klientda YouTube player duration=0 → darhol xato."""
    if entry.get("is_live"):
        return True
    live_status = str(entry.get("live_status") or "").lower()
    if live_status in ("is_live", "is_upcoming"):
        return True
    dur = _duration_from_entry(entry)
    title = str(entry.get("title") or "")
    if dur <= 0 and _LIVE_TITLE_HINTS.search(title):
        return True
    if _extract_flat() and dur <= 0:
        return True
    return False


def _entry_to_row(
    entry: dict[str, Any],
    *,
    track_type: str | None,
) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    vid = _normalize_video_id(
        str(entry.get("id") or entry.get("url") or entry.get("webpage_url") or "")
    )
    if not vid:
        return None
    title = str(entry.get("title") or "").strip() or vid
    artist = str(
        entry.get("artist") or entry.get("uploader") or entry.get("channel") or ""
    ).strip()
    dur = _duration_from_entry(entry)
    if track_type == "movie":
        row_type = "movie"
        provider = "mv"
    elif dur >= 600:
        row_type = "movie"
        provider = "mv"
    else:
        row_type = "song"
        provider = "cz"
    return {
        "id": vid,
        "video_id": vid,
        "song_id": vid,
        "title": title,
        "artist": artist,
        "channel": artist,
        "duration": dur,
        "provider": provider,
        "type": row_type,
        "icon": _thumb(vid),
        "thumbnail": _thumb(vid),
        "url": f"https://www.youtube.com/watch?v={vid}",
    }


def _entries_from_info(info: Any) -> list[dict[str, Any]]:
    if not isinstance(info, dict):
        return []
    ent = info.get("entries")
    if isinstance(ent, list):
        return [x for x in ent if isinstance(x, dict)]
    return [info]


def _search_sync(query: str, count: int, track_type: str | None) -> list[dict[str, Any]]:
    assert yt_dlp is not None
    n = max(1, min(count, 50))
    q = _search_query(query, track_type)
    fetch_n = min(50, max(n, n * 3 if _extract_flat() else n))
    url = f"ytsearch{fetch_n}:{q}"

    def _run_search(*, extract_flat: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        opts = _ydl_opts(playlistend=fetch_n, noplaylist=False)
        opts["extract_flat"] = extract_flat
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        for e in _entries_from_info(info):
            if _should_skip_entry(e):
                continue
            row = _entry_to_row(e, track_type=track_type)
            if row:
                out.append(row)
            if len(out) >= n:
                break
        return out[:n]

    # Qidiruv: avval extract_flat (tez, ko‘pincha cookiesiz ishlaydi)
    try:
        rows = _run_search(extract_flat=True)
        if rows:
            return rows
    except Exception as e:
        log.warning("ytdlp ytsearch %r (flat): %s", q, e)

    # To‘liq extract faqat cookies.txt bo‘lsa (aks holda bot xatosi)
    if _resolve_cookies_file():
        try:
            rows = _run_search(extract_flat=False)
            if rows:
                return rows
        except Exception as e:
            log.warning("ytdlp ytsearch %r (full): %s", q, e)
    return []


def _videos_by_ids_sync(ids: list[str], track_type: str | None) -> list[dict[str, Any]]:
    assert yt_dlp is not None
    out: list[dict[str, Any]] = []
    opts = _ydl_opts(noplaylist=True, allow_browser_cookies=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        for raw in ids:
            vid = _normalize_video_id(raw)
            if not vid:
                continue
            try:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={vid}",
                    download=False,
                )
            except Exception as e:
                log.debug("ytdlp get %s: %s", vid, e)
                continue
            if isinstance(info, dict):
                if _should_skip_entry(info):
                    continue
                row = _entry_to_row(info, track_type=track_type)
                if row:
                    out.append(row)
    return out


async def ytdlp_search_async(
    query: str, count: int, track_type: str | None
) -> list[dict[str, Any]]:
    if not ytdlp_available():
        return []
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                None, partial(_search_sync, query, count, track_type)
            ),
            timeout=_timeout_sec(),
        )
    except TimeoutError:
        log.warning("ytdlp search timeout q=%r", query)
    except Exception as e:
        log.warning("ytdlp search: %s", e)
    return []


_AUDIO_URL_CACHE: dict[str, tuple[float, str]] = {}
_AUDIO_URL_TTL_SEC = 1800.0


def _pick_audio_url(info: dict[str, Any]) -> str | None:
    direct = info.get("url")
    if isinstance(direct, str) and direct.startswith("http"):
        return direct
    manifest = info.get("manifest_url")
    if isinstance(manifest, str) and manifest.startswith("http"):
        return manifest
    requested = info.get("requested_formats")
    if isinstance(requested, list):
        for fmt in requested:
            if isinstance(fmt, dict):
                u = fmt.get("url")
                if isinstance(u, str) and u.startswith("http"):
                    return u
    formats = info.get("formats")
    if not isinstance(formats, list):
        return None
    m3u8: list[tuple[int, str]] = []
    direct_audio: list[tuple[int, str]] = []
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        url = fmt.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        if fmt.get("acodec") in (None, "none"):
            continue
        abr = int(fmt.get("abr") or fmt.get("tbr") or 0)
        proto = str(fmt.get("protocol") or "")
        ext = str(fmt.get("ext") or "")
        if ext == "m3u8" or "m3u8" in proto:
            m3u8.append((abr, url))
        else:
            direct_audio.append((abr, url))
    if m3u8:
        m3u8.sort(key=lambda x: x[0], reverse=True)
        return m3u8[0][1]
    if not direct_audio:
        return None
    direct_audio.sort(key=lambda x: x[0], reverse=True)
    return direct_audio[0][1]


# Cookiesiz ishlash uchun valid client kombinatsiyalari (yt-dlp 2026.03.17)
# android_embedded va tv_embedded mavjud emas — skip qilinadi
# android_vr: stdout ga URL yozadi (exit code 1 bo'lsa ham) — asosiy fix!
_COOKIELESS_CLIENT_SETS = [
    "android_vr,android",  # 1: android_vr — bot blokini o'tadi, URL qaytaradi
    "ios,android",         # 2: iOS fallback
    "mweb",                # 3: mobil web fallback
]

_GET_URL_FORMATS = (
    "bestaudio",
    "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
)


def guess_audio_media_type(url: str) -> str:
    u = (url or "").lower()
    if "mime=audio%2Fwebm" in u or "audio/webm" in u:
        return "audio/webm"
    if "mime=audio%2Fmp4" in u or "audio/mp4" in u or "mime=audio%2Fm4a" in u:
        return "audio/mp4"
    path = u.split("?", 1)[0]
    if path.endswith(".webm"):
        return "audio/webm"
    if path.endswith((".m4a", ".mp4")):
        return "audio/mp4"
    return "audio/webm"


def _append_ytdlp_node_args(cmd: list[str]) -> None:
    node = _node_executable()
    if node:
        cmd.extend(["--js-runtimes", f"node:{node}"])
        cmd.extend(["--remote-components", "ejs:github"])


def _get_url_cli_sync(vid: str, *, use_cookies: bool = False) -> str | None:
    """yt-dlp CLI orqali audio URL — cookiesiz, bir nechta client urinish.

    Muhim: yt-dlp ba'zan 429 WARNING bilan NON-ZERO exit code qaytaradi,
    lekin stdout ga hali ham to'g'ri URL yozadi. Shuning uchun stdout
    avval tekshiriladi, keyin return code ga qaraladi.
    """
    vid = _normalize_video_id(vid)
    if not vid:
        return None

    url_str = f"https://www.youtube.com/watch?v={vid}"
    timeout = int(min(_timeout_sec(), 45))
    client_sets: list[str | None] = [None, *_COOKIELESS_CLIENT_SETS]

    for fmt in _GET_URL_FORMATS:
        for clients in client_sets:
            cmd = [
                sys.executable,
                "-m",
                "yt_dlp",
                "-f",
                fmt,
                "--no-playlist",
                "--no-warnings",
                "--get-url",
            ]
            if use_cookies:
                cf = _resolve_cookies_file()
                if cf:
                    cmd.extend(["--cookies", cf])
            if clients:
                cmd.extend(
                    ["--extractor-args", f"youtube:player_client={clients}"]
                )
            _append_ytdlp_node_args(cmd)
            cmd.append(url_str)
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                log.debug(
                    "ytdlp --get-url timeout vid=%s fmt=%s clients=%s",
                    vid,
                    fmt,
                    clients,
                )
                continue

            # stdout avval — 429 warning bilan ham URL bo'lishi mumkin
            raw = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
            line = raw.splitlines()[0].strip() if raw else ""
            if line.startswith("http"):
                log.info(
                    "ytdlp stream url %s fmt=%s clients=%s cookies=%s",
                    vid,
                    fmt,
                    clients or "default",
                    use_cookies,
                )
                return line

            err = (completed.stderr or b"").decode("utf-8", errors="replace")[:300]
            log.debug(
                "ytdlp --get-url %s fmt=%s clients=%s rc=%s: %s",
                vid,
                fmt,
                clients,
                completed.returncode,
                err,
            )

    return None


def _extract_audio_url_once(
    vid: str,
    *,
    use_cookies: bool,
    allow_browser_cookies: bool,
    player_clients: list[str] | None,
) -> str | None:
    assert yt_dlp is not None
    opts = _ydl_opts(
        noplaylist=True,
        allow_browser_cookies=allow_browser_cookies,
        use_cookies=use_cookies,
        player_clients=player_clients,
    )
    opts["format"] = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={vid}",
            download=False,
        )
    if not isinstance(info, dict):
        return None
    return _pick_audio_url(info)


def _audio_stream_url_sync(vid: str) -> str | None:
    assert yt_dlp is not None
    vid = _normalize_video_id(vid)
    if not vid:
        return None
    cached = _AUDIO_URL_CACHE.get(vid)
    if cached and (time.time() - cached[0]) < _AUDIO_URL_TTL_SEC:
        return cached[1]

    url = _get_url_cli_sync(vid, use_cookies=False)
    if not url:
        url = _get_url_cli_sync(vid, use_cookies=True)
    if url:
        _AUDIO_URL_CACHE[vid] = (time.time(), url)
        return url

    log.warning("ytdlp stream url %s: URL olinmadi (cookiesiz va cookies bilan)", vid)
    return None


async def ytdlp_audio_stream_url_async(vid: str) -> str | None:
    if not ytdlp_available():
        return None
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, partial(_audio_stream_url_sync, vid)),
            timeout=_timeout_sec(),
        )
    except TimeoutError:
        log.warning("ytdlp audio url timeout vid=%s", vid)
    except Exception as e:
        log.warning("ytdlp audio url: %s", e)
    return None


async def ytdlp_audio_playable_async(vid: str) -> tuple[bool, str]:
    """yt-dlp audio olish mumkinligini tekshiradi (bot/cookies)."""
    if not ytdlp_available():
        return False, "yt-dlp o'rnatilmagan"
    loop = asyncio.get_running_loop()

    def _probe() -> tuple[bool, str]:
        last_err = ""
        for use_cookies in (False, True):
            cmd = _ytdlp_pipe_cmd(vid, use_cookies=use_cookies) + [
                "--simulate",
                "--print",
                "id",
            ]
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=int(min(_timeout_sec(), 35)),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return False, "yt-dlp timeout"
            err = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
            if completed.returncode == 0:
                return True, ""
            last_err = err[:400] or f"exit {completed.returncode}"
        return False, last_err

    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _probe),
            timeout=_timeout_sec() + 5,
        )
    except TimeoutError:
        return False, "probe timeout"


def _proxy_stream_request_headers(url: str, *, range_header: str | None = None) -> dict[str, str]:
    """CDN ga mos Referer — Deezer preview YouTube header bilan ishlamaydi."""
    u = (url or "").lower()
    headers: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    if "googlevideo.com" in u or "youtube.com" in u or "ytimg.com" in u:
        headers["Referer"] = "https://www.youtube.com/"
        headers["Origin"] = "https://www.youtube.com"
    elif "dzcdn.net" in u or "deezer.com" in u:
        headers["Referer"] = "https://www.deezer.com/"
    if range_header:
        headers["Range"] = range_header
    return headers


async def iter_proxy_audio_stream(
    url: str, *, range_header: str | None = None
) -> AsyncIterator[bytes]:
    """Tashqi audio URL → same-origin /api_music/play proxy."""
    import httpx

    headers = _proxy_stream_request_headers(url, range_header=range_header)
    timeout = httpx.Timeout(60.0, read=120.0)
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout
    ) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(64 * 1024):
                yield chunk


async def iter_ytdlp_audio_stream(vid: str) -> AsyncIterator[bytes]:
    """yt-dlp stdout → brauzer (redirect ishlamasa proxy stream)."""
    vid = _normalize_video_id(vid)
    if not vid or not ytdlp_available():
        return
    cmd = _ytdlp_pipe_cmd(vid)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    try:
        while True:
            chunk = await proc.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except TimeoutError:
                proc.kill()
        err = b""
        if proc.stderr:
            try:
                err = await proc.stderr.read()
            except Exception:
                pass
        if proc.returncode not in (0, None) and proc.returncode != 0:
            log.warning(
                "ytdlp pipe exit %s vid=%s: %s",
                proc.returncode,
                vid,
                err.decode("utf-8", errors="replace")[:300],
            )


def ytdlp_log_startup_config() -> None:
    if not ytdlp_available():
        log.info("yt-dlp: o'chirilgan (MUSIC_USE_YTDLP=0)")
        return
    _ensure_youtube_cookies_file()
    st = ytdlp_runtime_status()
    if not st["cookies_ok"]:
        log.warning(
            "yt-dlp: YouTube cookies yo'q — audio ko'pincha ishlamaydi (YouTube bot). "
            "cookies.txt → loyiha ildiziga yoki /api_music/setup. "
            "Skript: scripts/ytdlp_export_cookies.ps1 (Edge yopiq)"
        )
    else:
        log.info(
            "yt-dlp: cookies=%s browser=%s node=%s",
            st.get("cookies_file"),
            st.get("cookies_from_browser"),
            st.get("node"),
        )


async def ytdlp_videos_by_ids_async(
    ids: list[str], track_type: str | None
) -> list[dict[str, Any]]:
    if not ytdlp_available() or not ids:
        return []
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                None, partial(_videos_by_ids_sync, ids[:50], track_type)
            ),
            timeout=_timeout_sec(),
        )
    except TimeoutError:
        log.warning("ytdlp get_by_ids timeout n=%s", len(ids))
    except Exception as e:
        log.warning("ytdlp get_by_ids: %s", e)
    return []

"""Telegram profil rasmini Bot API orqali olish va /photos/ ga saqlash."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger("spinbottle.tg_profile")

_PHOTOS_DIR = (
    Path(__file__).resolve().parents[1] / "site" / "media" / "photos"
)
NO_IMG = "/photos/no_img.png"
_TG_PHOTO_PROXY_PREFIX = "https://proxy-msk.ciliz.com/tgphoto/"
_LOCAL_TG_PHOTO_PREFIX = "/api/proxy/tgphoto/"


def is_remote_telegram_avatar(url: str | None) -> bool:
    """t.me SVG yoki Telegram URL — DB da saqlanmasligi kerak."""
    raw = (url or "").strip().lower()
    if not raw:
        return False
    if raw.startswith("/photos/"):
        return False
    return (
        "t.me/" in raw
        or "telegram.org" in raw
        or "tgphoto" in raw
        or raw.endswith(".svg")
    )


def public_avatar_url(avatar_url: str | None) -> str:
    """Klientga faqat lokal /photos/ yoki no_img (SVG/t.me yuborilmaydi)."""
    raw = (avatar_url or "").strip()
    if not raw:
        return NO_IMG
    if raw.startswith("/photos/"):
        if raw.endswith("no_img.png"):
            return NO_IMG
        return raw if local_avatar_is_valid(raw) else NO_IMG
    if is_remote_telegram_avatar(raw):
        return NO_IMG
    return raw


def _is_valid_image_bytes(data: bytes) -> bool:
    if not data or len(data) < 200:
        return False
    head = data[:32].lstrip()
    if head.startswith(b"<") or head.startswith(b"{"):
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:4] == b"RIFF" and b"WEBP" in data[:16]:
        return True
    return False


def _is_svg_or_html_url(url: str) -> bool:
    low = url.lower().split("?", 1)[0]
    return low.endswith(".svg") or "/userpic/" in low and "t.me/i/" in low


def _client_photo_download_url(client_photo_url: str | None) -> str | None:
    """WebApp photo_url — t.me ko'pincha SVG; proxy orqali JPEG/PNG bo'lishi mumkin."""
    raw = (client_photo_url or "").strip()
    if not raw:
        return None
    if raw.startswith("https://t.me/"):
        if _is_svg_or_html_url(raw):
            return f"{_TG_PHOTO_PROXY_PREFIX}{raw[len('https://t.me/'):]}"
        return raw
    if raw.startswith("http://t.me/"):
        path = raw[len("http://t.me/") :]
        return f"{_TG_PHOTO_PROXY_PREFIX}{path}"
    if _is_svg_or_html_url(raw):
        return None
    return raw


def _local_avatar_path(avatar_url: str | None) -> Path | None:
    if not avatar_url or not str(avatar_url).startswith("/photos/"):
        return None
    name = str(avatar_url).split("/")[-1].strip()
    if not name or name == "no_img.png":
        return None
    return _PHOTOS_DIR / name


def local_avatar_is_valid(avatar_url: str | None) -> bool:
    path = _local_avatar_path(avatar_url)
    if not path or not path.is_file():
        return False
    try:
        return _is_valid_image_bytes(path.read_bytes())
    except OSError:
        return False


_REMOTE_NO_IMG = "https://bottle.tgspinbotlle.com/photos/no_img.png"


async def ensure_no_img_placeholder() -> None:
    if _NO_IMG.is_file() and _is_valid_image_bytes(_NO_IMG.read_bytes()):
        return
    try:
        _PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(_REMOTE_NO_IMG, timeout=20.0)
            r.raise_for_status()
            if _is_valid_image_bytes(r.content):
                _NO_IMG.write_bytes(r.content)
                log.info("no_img.png yuklandi (%s bytes)", len(r.content))
    except Exception as e:
        log.warning("no_img.png yuklab bo'lmadi: %s", e)


def purge_invalid_local_avatars() -> int:
    """Diskdagi noto'g'ri user_* rasmlarini o'chiradi."""
    if not _PHOTOS_DIR.is_dir():
        return 0
    removed = 0
    for path in _PHOTOS_DIR.glob("user_*"):
        if not path.is_file():
            continue
        try:
            if not _is_valid_image_bytes(path.read_bytes()):
                path.unlink(missing_ok=True)
                removed += 1
                log.info("Removed invalid avatar file: %s", path.name)
        except OSError as e:
            log.warning("Could not check avatar %s: %s", path.name, e)
    return removed


async def _tg_api_get(client: httpx.AsyncClient, bot_token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    r = await client.get(url, params=params, timeout=20.0)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        log.warning("Telegram API %s: %s", method, data.get("description"))
        return {}
    return data.get("result") or {}


async def _file_url_from_file_id(
    client: httpx.AsyncClient, bot_token: str, file_id: str | None
) -> str | None:
    if not file_id:
        return None
    finfo = await _tg_api_get(client, bot_token, "getFile", file_id=file_id)
    file_path = finfo.get("file_path")
    if not file_path:
        return None
    return f"https://api.telegram.org/file/bot{bot_token}/{file_path}"


async def fetch_telegram_photo_file_url(tg_id: int, bot_token: str) -> str | None:
    """Bot API: profil rasmlari, keyin getChat.photo (Mini App foydalanuvchilari uchun)."""
    if not bot_token or not tg_id:
        return None
    try:
        async with httpx.AsyncClient() as client:
            photos = await _tg_api_get(
                client, bot_token, "getUserProfilePhotos", user_id=tg_id, limit=1
            )
            sets = photos.get("photos") or []
            if sets and sets[0]:
                for size in reversed(sets[0]):
                    url = await _file_url_from_file_id(
                        client, bot_token, size.get("file_id")
                    )
                    if url:
                        return url

            chat = await _tg_api_get(client, bot_token, "getChat", chat_id=tg_id)
            photo = chat.get("photo") or {}
            for key in ("big_file_id", "small_file_id"):
                url = await _file_url_from_file_id(client, bot_token, photo.get(key))
                if url:
                    log.debug("TG avatar getChat.%s tg_id=%s", key, tg_id)
                    return url
    except Exception as e:
        log.warning("TG avatar URL (tg_id=%s): %s", tg_id, e)
        return None
    log.debug("TG avatar: Bot API da rasm yo'q (tg_id=%s)", tg_id)
    return None


async def download_avatar_to_photos(source_url: str, user_db_id: int) -> str | None:
    if not source_url:
        return None
    try:
        _PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        headers = {
            "User-Agent": "SpinBottleBot/1.0",
            "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8,*/*;q=0.5",
        }
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(source_url, timeout=30.0, headers=headers)
            r.raise_for_status()
            raw = r.content
            if not _is_valid_image_bytes(raw):
                ct = (r.headers.get("content-type") or "").lower()
                is_placeholder = (
                    "svg" in ct
                    or _is_svg_or_html_url(source_url)
                    or raw[:32].lstrip().startswith(b"<")
                )
                if is_placeholder:
                    log.debug(
                        "Avatar placeholder skipped (user=%s, %s bytes, url=%s)",
                        user_db_id,
                        len(raw),
                        source_url[:100],
                    )
                else:
                    log.warning(
                        "Avatar not an image (user=%s, %s bytes, ct=%s, url=%s)",
                        user_db_id,
                        len(raw),
                        r.headers.get("content-type"),
                        source_url[:120],
                    )
                return None
            ext = ".jpg"
            if raw[:8] == b"\x89PNG\r\n\x1a\n":
                ext = ".png"
            elif raw[:4] == b"RIFF" and b"WEBP" in raw[:16]:
                ext = ".webp"
            file_name = f"user_{user_db_id}_{int(time.time())}{ext}"
            path = _PHOTOS_DIR / file_name
            path.write_bytes(raw)
            log.info("Avatar saved: %s (%s bytes)", file_name, len(raw))
            return f"/photos/{file_name}"
    except Exception as e:
        log.warning("Avatar yuklab olish (user=%s): %s", user_db_id, e)
        return None


def avatar_needs_telegram_sync(avatar_url: str | None) -> bool:
    if not avatar_url or avatar_url.strip() in ("", NO_IMG):
        return True
    if is_remote_telegram_avatar(avatar_url):
        return True
    if str(avatar_url).startswith("/photos/user_"):
        if not local_avatar_is_valid(avatar_url):
            bad = _local_avatar_path(avatar_url)
            if bad and bad.is_file():
                try:
                    bad.unlink(missing_ok=True)
                except OSError:
                    pass
            return True
    return False


async def _avatar_download_sources(
    *,
    tg_id: int,
    bot_token: str,
    client_photo_url: str | None,
) -> list[str]:
    """Bot API birinchi; keyin proxy (t.me userpic SVG emas, haqiqiy rasm)."""
    sources: list[str] = []
    seen: set[str] = set()

    def _add(url: str | None) -> None:
        if url and url not in seen:
            seen.add(url)
            sources.append(url)

    _add(await fetch_telegram_photo_file_url(tg_id, bot_token))
    _add(_client_photo_download_url(client_photo_url))
    return sources


async def resolve_telegram_avatar(
    *,
    tg_id: int,
    bot_token: str,
    user_db_id: int,
    current_avatar: str | None,
    client_photo_url: str | None = None,
    force_refresh: bool = False,
) -> str | None:
    if not force_refresh and not avatar_needs_telegram_sync(current_avatar):
        return current_avatar

    for source in await _avatar_download_sources(
        tg_id=tg_id,
        bot_token=bot_token,
        client_photo_url=client_photo_url,
    ):
        local = await download_avatar_to_photos(source, user_db_id)
        if local:
            return local

    if avatar_needs_telegram_sync(current_avatar):
        return NO_IMG
    return current_avatar or NO_IMG


async def sync_telegram_user_avatar(
    *,
    tg_id: int,
    bot_token: str,
    user_db_id: int,
    client_photo_url: str | None = None,
    current_avatar: str | None = None,
    timeout: float = 12.0,
) -> str:
    """TG kirish: Bot API → /photos/user_*.jpg; muvaffaqiyatsiz → no_img."""
    try:
        resolved = await asyncio.wait_for(
            resolve_telegram_avatar(
                tg_id=tg_id,
                bot_token=bot_token,
                user_db_id=user_db_id,
                current_avatar=current_avatar,
                client_photo_url=client_photo_url,
                force_refresh=True,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.warning("TG avatar sync timeout user=%s tg_id=%s", user_db_id, tg_id)
        resolved = None
    if resolved and not is_remote_telegram_avatar(resolved):
        if resolved.startswith("/photos/") and local_avatar_is_valid(resolved):
            return resolved
        if resolved == NO_IMG:
            return NO_IMG
    return NO_IMG

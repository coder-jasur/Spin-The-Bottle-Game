import httpx
from fastapi import APIRouter, Response
from fastapi.responses import FileResponse
from pathlib import Path

from src.app.services.telegram_profile import local_avatar_is_valid

router = APIRouter(tags=["Proxy"])

_TG_HEADERS = {
    "User-Agent": "SpinBottle/1.0",
    "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8,*/*;q=0.5",
}

_PHOTOS_DIR = (
    Path(__file__).resolve().parents[2] / "site" / "media" / "photos"
)
_NO_IMG = _PHOTOS_DIR / "no_img.png"


def _no_img_response() -> FileResponse | Response:
    if _NO_IMG.is_file():
        return FileResponse(_NO_IMG)
    return Response(status_code=404)


async def proxy_request(url: str):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, follow_redirects=True)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type"),
            )
        except Exception as e:
            return Response(content=str(e), status_code=500)


@router.get("/photos/{path:path}")
async def proxy_photos(path: str):
    """Avval lokal (TG yuklangan), keyin production proxy."""
    safe = path.replace("\\", "/").lstrip("/")
    if safe:
        local = (_PHOTOS_DIR / safe).resolve()
        try:
            local.relative_to(_PHOTOS_DIR.resolve())
        except ValueError:
            return Response(status_code=404)
        if local.is_file():
            if safe.startswith("user_") and not local_avatar_is_valid(f"/photos/{safe}"):
                return _no_img_response()
            return FileResponse(local)
    if safe == "no_img.png":
        return _no_img_response()
    return Response(status_code=404)


@router.get("/api/proxy/tgphoto/{path:path}")
async def proxy_tg_photo(path: str):
    """Telegram userpic (t.me) — localhost/TMA CORS uchun mahalliy proxy."""
    safe = path.replace("\\", "/").lstrip("/")
    if not safe:
        return Response(status_code=400)

    candidates = [
        f"https://t.me/{safe}",
    ]
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        for url in candidates:
            try:
                resp = await client.get(url, headers=_TG_HEADERS)
                if resp.status_code != 200 or not resp.content:
                    continue
                head = resp.content[:32].lstrip()
                if head.startswith(b"<") or head.startswith(b"{"):
                    continue
                ct = resp.headers.get("content-type") or "image/jpeg"
                return Response(
                    content=resp.content,
                    media_type=ct,
                    headers={
                        "Cache-Control": "public, max-age=86400",
                        "Access-Control-Allow-Origin": "*",
                    },
                )
            except Exception:
                continue
    return _no_img_response()

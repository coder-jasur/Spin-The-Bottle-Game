import json
import pathlib
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from src.app.api.deps import get_db

router = APIRouter(tags=["Web"])

base_dir = pathlib.Path(__file__).resolve().parents[2]
site_dir = base_dir / "site"


def _auth_payload_from_request(request: Request) -> Optional[dict]:
    """JWT, legacy `["id"]` cookie, yoki `user_id` query’dagi o‘yin sessiya tokeni.

    `/index` URL’dagi `user_id` (game_sessions) brauzerda cookie yo‘q yoki
    JWT eskirganda ham ishlaydi — `/exit-game` ham shu bilan mos bo‘lishi kerak.
    """
    from src.app.api.game_session import game_sessions
    from src.app.core.jwt import verify_access_token

    raw_list: list[str] = []
    q = request.query_params.get("user_id")
    if q and str(q).strip():
        q = str(q).strip()
        raw_list.append(q)
        # Ba'zan `back` orqali kelganda token bir-ikki marta encode bo'ladi
        for _ in range(2):
            try:
                dec = urllib.parse.unquote(q)
            except Exception:
                break
            if dec == q or not dec:
                break
            q = dec
            if dec not in raw_list:
                raw_list.append(dec)
    for ck in ("device_user_ids", "accessToken"):
        v = request.cookies.get(ck)
        if v and str(v).strip():
            s = str(v).strip()
            if s not in raw_list:
                raw_list.append(s)

    for raw in raw_list:
        p = verify_access_token(raw)
        if p:
            return p
        try:
            decoded = urllib.parse.unquote(raw)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if isinstance(ids, list) and ids:
                    return {"id": int(ids[0])}
        except Exception:
            pass
        uid_game = game_sessions.verify(raw)
        if uid_game is not None:
            return {"id": int(uid_game)}
    return None


def _exit_back_url(request: Request) -> str:
    from src.app.api.config.server_json import public_base_url

    settings = getattr(request.app.state, "settings", None)
    return f"{public_base_url(request, settings)}/exit-game"

@router.get("/banned")
async def get_banned(request: Request):
    return FileResponse(site_dir / "banned.html")


@router.get("/stars-support")
async def get_stars_support():
    """Sayt foydalanuvchilari: Stars yetmasa @SpinTheBottleSupport ga yo'naltirish."""
    return FileResponse(site_dir / "stars_support.html")

@router.get("/")
async def get_login(request: Request, session: AsyncSession = Depends(get_db)):
    payload = _auth_payload_from_request(request)
    token = request.cookies.get("device_user_ids")
    print(f">>> DEBUG: get_login token={token[:10] if token else None}, payload={payload is not None}", flush=True)
    
    if payload:
        from src.app.database.repositories.user import UserRepository
        user_repo = UserRepository(session)
        user = await user_repo.get_user_by_id(payload.get("id"))
        if user:
            return RedirectResponse(url="/index")
        else:
            # Agar bazada yo'q bo'lsa, barcha cookielarni tozalaymiz (Agressiv Logout)
            response = FileResponse(site_dir / "login.html")
            for cookie in ["device_user_ids", "accessToken", "refreshToken", "language", "user_id"]:
                response.delete_cookie(key=cookie, path="/")
            return response
        
    return FileResponse(site_dir / "login.html")


@router.get("/login")
@router.get("/login.html")
async def get_login_alias(request: Request, session: AsyncSession = Depends(get_db)):
    return await get_login(request, session)


@router.get("/index")
@router.get("/bottle-iframe")
@router.get("/index.html")
@router.get("/android_bottle_mobile.html")
@router.get("/ios_bottle_mobile.html")
@router.get("/bottle_mobile.html")
async def get_index(request: Request, session: AsyncSession = Depends(get_db)):
    user_id_param = request.query_params.get("user_id")
    payload = _auth_payload_from_request(request)
    if not payload:
        # Mini App: index.html ichida Telegram auth (tg_auto_auth.js)
        return FileResponse(site_dir / "index.html")

    # Bazada bormi?
    from src.app.database.repositories.user import UserRepository
    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(payload.get("id"))
    if not user:
        print(f">>> AUTH ERROR: User {payload.get('id')} not found in DB. Aggressive Logout.", flush=True)
        response = RedirectResponse(url="/")
        for cookie in ["device_user_ids", "accessToken", "refreshToken", "language", "user_id"]:
            response.delete_cookie(key=cookie, path="/")
        return response
    
    
    # 3. Agar hamma narsa joyida bo'lsa, lekin URLda parametrlar bo'lmasa, ularni qo'shib redirect qilamiz
    # (Bu o'yin yuklanishi uchun zarur)
    if not user_id_param:
        from src.app.api.game_entry import build_game_index_path

        path = build_game_index_path(
            request, user.id, language_code=getattr(user, "language_code", None)
        )
        print(f">>> GAME SESSION redirect: user_id={user.id} -> {path[:80]}...", flush=True)
        return RedirectResponse(url=path)

    return FileResponse(site_dir / "index.html")

@router.get("/welcome")
async def get_welcome(request: Request, session: AsyncSession = Depends(get_db)):
    payload = _auth_payload_from_request(request)
    token = request.cookies.get("device_user_ids")
    print(f">>> DEBUG: get_welcome payload={payload is not None}", flush=True)
    
    if not payload:
        return RedirectResponse(url="/")

    # Bazada bormi?
    from src.app.database.repositories.user import UserRepository
    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(payload.get("id"))
    if not user:
        print(f">>> AUTH ERROR: User {payload.get('id')} not found in DB. Aggressive Logout.", flush=True)
        response = RedirectResponse(url="/")
        for cookie in ["device_user_ids", "accessToken", "refreshToken", "language", "user_id"]:
            response.delete_cookie(key=cookie, path="/")
        return response

    # BAN TEKSHIRISH
    if user.is_banned:
        from datetime import datetime
        now = datetime.now()
        if user.ban_expires_at and now > user.ban_expires_at:
            user.is_banned = False
            user.ban_expires_at = None
            user.number_of_complaints = 0
            await session.commit()
        else:
            from src.app.core.language import resolve_user_lang
            from src.app.core.stars_support import build_banned_path

            ban_time = user.ban_expires_at.strftime("%Y-%m-%d %H:%M") if user.ban_expires_at else "umrbod"
            lang = resolve_user_lang(
                cookie_lang=request.cookies.get("language"),
                db_language_code=getattr(user, "language_code", None),
            )
            settings = getattr(request.app.state, "settings", None)
            support_user = (
                getattr(settings, "telegram_support_username", None) if settings else None
            )
            return RedirectResponse(
                url=build_banned_path(
                    expires_at=ban_time, lang=lang, support_user=support_user
                )
            )
        
    return FileResponse(site_dir / "welcome.html")

@router.get("/exit-game")
async def exit_game(request: Request):
    """O'yindan chiqish — `back` parametridagi sessiya tokeni bilan /welcome."""
    payload = _auth_payload_from_request(request)

    if not payload:
        return RedirectResponse(url="/", status_code=302)

    return RedirectResponse(url="/welcome", status_code=302)

@router.get("/site.webmanifest")
async def get_manifest():
    return FileResponse(site_dir / "site.webmanifest")
@router.get("/api/tables")
async def get_tables(country: str = "UZBEKISTAN", session: AsyncSession = Depends(get_db)):
    """Stol ro'yxati UI uchun — davlat + global, qadam-baqadam ochilish."""
    from src.app.database.repositories.game import GameRepository
    from src.app.api.ws.game_manager import manager

    repo = GameRepository(session)
    await repo.seed_country_tables(country)
    await repo.seed_global_tables()

    rows = await manager.http_tables_list_payload(country)
    return [
        {
            "room_id": r["room_id"],
            "name": r["name"],
            "online": r["online"],
            "capacity": r["capacity"],
            "is_vip": r["is_vip"],
            "country": r["country"],
            "scope": r["scope"],
        }
        for r in rows
    ]

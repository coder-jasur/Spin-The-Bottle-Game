import pathlib
from fastapi import APIRouter, Request, Depends
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from src.app.api.deps import get_db

router = APIRouter(tags=["Web"])

base_dir = pathlib.Path(__file__).resolve().parents[2]
site_dir = base_dir / "site"

@router.get("/banned")
async def get_banned(request: Request):
    return FileResponse(site_dir / "banned.html")

@router.get("/")
async def get_login(request: Request, session: AsyncSession = Depends(get_db)):
    from src.app.core.jwt import verify_access_token
    token = request.cookies.get("device_user_ids")
    payload = verify_access_token(token) if token else None
    
    # FALLBACK: ["user_id"] formatini tekshirish
    if not payload and token:
        try:
            import urllib.parse
            import json
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if isinstance(ids, list) and len(ids) > 0:
                    try:
                        uid = int(ids[0])
                        payload = {"id": uid}
                    except (ValueError, TypeError):
                        pass
        except: pass
    print(f">>> DEBUG: get_login token={token[:10] if token else None}, payload={payload is not None}", flush=True)
    
    if payload:
        from src.app.database.repositories.user import UserRepository
        user_repo = UserRepository(session)
        user = await user_repo.get_user_by_id(payload.get("id"))
        if user:
            return RedirectResponse(url="/welcome")
        else:
            # Agar bazada yo'q bo'lsa, barcha cookielarni tozalaymiz (Agressiv Logout)
            response = FileResponse(site_dir / "login.html")
            for cookie in ["device_user_ids", "accessToken", "refreshToken", "language", "user_id"]:
                response.delete_cookie(key=cookie, path="/")
            return response
        
    return FileResponse(site_dir / "login.html")

@router.get("/index")
@router.get("/bottle-iframe")
@router.get("/index.html")
@router.get("/android_bottle_mobile.html")
@router.get("/ios_bottle_mobile.html")
@router.get("/bottle_mobile.html")
async def get_index(request: Request, session: AsyncSession = Depends(get_db)):
    from src.app.core.jwt import verify_access_token
    import urllib.parse

    # 1. Tokenni olish (Cookie yoki Parametrdan)
    user_id_param = request.query_params.get("user_id")
    access_token_cookie = request.cookies.get("device_user_ids")
    
    token = access_token_cookie or user_id_param
    
    # 2. Tokenni tekshirish
    payload = verify_access_token(token) if token else None
    
    # FALLBACK: ["user_id"] formatini tekshirish
    if not payload and token:
        try:
            import json
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if isinstance(ids, list) and len(ids) > 0:
                    try:
                        uid = int(ids[0])
                        payload = {"id": uid}
                    except (ValueError, TypeError):
                        pass
        except: pass
    
    if not payload:
        print(">>> AUTH ERROR: No valid token found. Redirecting to login.", flush=True)
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
    
    
    # 3. Agar hamma narsa joyida bo'lsa, lekin URLda parametrlar bo'lmasa, ularni qo'shib redirect qilamiz
    # (Bu o'yin yuklanishi uchun zarur)
    if not user_id_param:
        # ── Sessiya tokeni yaratish ────────────────────────────────────────
        # DB dan tasdiqlangan real user asosida xavfsiz token yaratiladi.
        # Har safar yangi token, eskisi avtomatik bekor bo'ladi (30 daqiqa).
        from src.app.api.game_session import game_sessions
        session_token = game_sessions.create(user.id)
        print(f">>> GAME SESSION: user_id={user.id}, token={session_token[:12]}...", flush=True)

        # Brauzerdagi tilni aniqlash (Cookie'dan)
        client_lang = request.cookies.get("language", "ru")

        # O'yin tushunadigan formatga o'tkazish
        LOCALE_MAP = {
            "uz": "uz_UZ",
            "kz": "kz_KZ",
            "tj": "tj_TJ",
            "en": "en_US",
            "az": "az_AZ",
            "tr": "tr_TR",
            "ru": "ru_RU"
        }
        game_locale = LOCALE_MAP.get(client_lang, "ru_RU")

        params = {
            "signed_request": "fb",
            "query": "",
            "user_id": session_token,   # ← JWT emas, xavfsiz sessiya token
            "locale": game_locale,
            "back": "http://localhost:8000/exit-game"
        }
        query_string = urllib.parse.urlencode(params)
        return RedirectResponse(url=f"/index?{query_string}")

    return FileResponse(site_dir / "index.html")

@router.get("/welcome")
async def get_welcome(request: Request, session: AsyncSession = Depends(get_db)):
    from src.app.core.jwt import verify_access_token
    token = request.cookies.get("device_user_ids")
    payload = verify_access_token(token) if token else None
    
    # FALLBACK: ["user_id"] formatini tekshirish
    if not payload and token:
        try:
            import urllib.parse
            import json
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if isinstance(ids, list) and len(ids) > 0:
                    try:
                        uid = int(ids[0])
                        payload = {"id": uid}
                    except (ValueError, TypeError):
                        pass
        except: pass
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
            ban_time = user.ban_expires_at.strftime("%Y-%m-%d %H:%M") if user.ban_expires_at else "umrbod"
            import urllib.parse
            return RedirectResponse(url=f"/banned?expires_at={urllib.parse.quote(ban_time)}")
        
    return FileResponse(site_dir / "welcome.html")

@router.get("/exit-game")
async def exit_game(request: Request):
    from src.app.core.jwt import verify_access_token
    token = request.cookies.get("device_user_ids")
    payload = verify_access_token(token) if token else None
    
    # FALLBACK: ["user_id"] formatini tekshirish
    if not payload and token:
        try:
            import urllib.parse
            import json
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if isinstance(ids, list) and len(ids) > 0:
                    payload = {"id": ids[0]}
        except: pass
    
    if not payload:
        return RedirectResponse(url="/")
        
    return RedirectResponse(url="/welcome")

@router.get("/site.webmanifest")
async def get_manifest():
    return FileResponse(site_dir / "site.webmanifest")
@router.get("/api/tables")
async def get_tables(country: str = "UZBEKISTAN", session: AsyncSession = Depends(get_db)):
    """Stol ro'yxati UI uchun (Diagramma 6 & Request 2)."""
    from src.app.database.repositories.game import GameRepository
    from src.app.api.ws.game_manager import manager
    
    repo = GameRepository(session)
    rooms = await repo.get_rooms_by_country(country)
    
    result = []
    for r in rooms:
        table_obj = manager.tables.get(str(r.room_id))
        online_count = len(table_obj.players) if table_obj else 0
        
        result.append({
            "room_id":  r.room_id,
            "name":     r.name,
            "online":   online_count,
            "capacity": 12,
            "is_vip":   r.is_vip
        })
    return result

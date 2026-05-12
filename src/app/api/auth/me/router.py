import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.ws.player import parse_birth_date_ms
from src.app.core.jwt import verify_access_token
from src.app.database.repositories.user import UserRepository

router = APIRouter(tags=["Me"])


async def get_db(request: Request) -> AsyncSession:
    db = getattr(request.app.state, "db", None)
    if not db:
        from src.app.core.config import load_config
        from src.app.database.base import Database

        settings = load_config()
        dsn = settings.construct_postgresql_url()
        db = Database(dsn)
        request.app.state.db = db

    async with db.session_factory() as session:
        yield session


@router.get("/api/auth/me")
@router.get("/auth/me")
@router.get("/api/auth/profile")
@router.get("/auth/profile")
async def get_me(
    request: Request,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_db),
):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")
    if not token:
        token = request.cookies.get("device_user_ids")

    if not token:
        raise HTTPException(status_code=401, detail="No authorization found")

    try:
        payload = verify_access_token(token) if token else None

        # FALLBACK: Legacy cookie format [user_id]
        if not payload and token:
            try:
                import json
                import urllib.parse
                decoded = urllib.parse.unquote(token)
                if decoded.startswith("[") and decoded.endswith("]"):
                    ids = json.loads(decoded)
                    if isinstance(ids, list) and len(ids) > 0:
                        payload = {"id": int(ids[0])}
            except:
                pass

        if not payload:
            raise HTTPException(status_code=401, detail="Invalid token")

        user_id = payload.get("id")
        user_repo = UserRepository(session)
        user = await user_repo.get_user_by_id(user_id)

        if not user:
            # Barcha cookielarni o'chiramiz (Agressiv Logout)
            from fastapi import Response
            response = Response(status_code=404, content="User not found")
            for cookie in ["device_user_ids", "accessToken", "refreshToken", "language", "user_id"]:
                response.delete_cookie(key=cookie, path="/")
            return response

        # BAN TEKSHIRISH
        if user.is_banned:
            from datetime import datetime
            now = datetime.now()
            if user.ban_expires_at and now > user.ban_expires_at:
                # Ban muddati tugagan - avtomatik ochamiz
                user.is_banned = False
                user.ban_expires_at = None
                user.number_of_complaints = 0
                await session.commit()
            else:
                # Hali ham bandda
                ban_time_str = "umrbod"
                if user.ban_expires_at:
                    ban_time_str = user.ban_expires_at.strftime("%Y-%m-%d %H:%M")
                
                return {
                    "is_banned": True,
                    "message": f"Siz admin tomonidan {ban_time_str} ga qadar ban qilindingiz",
                    "ban_expires_at": ban_time_str
                }

        birthday_ts = parse_birth_date_ms(user.birth_date)

        # Gender matnini o'zgartirish
        gender_display = "Kişi" # Default male
        if user.gender == "female":
            gender_display = "Qadın"
        elif user.gender == "male":
            gender_display = "Kişi"

        return {
            "game_username": user.username or user.display_name or user.login or f"user_{user.id}",
            "status": user.status_text or None,
            "birthday_ts": birthday_ts,
            "balance": user.wallet.stars if user.wallet else 0,
            "gift_tokens": user.wallet.gift_tokens if user.wallet else 0,
            "daily_streak": user.daily_streak,
            "can_claim_bonus": 1 if (not user.last_bonus_claimed_at or datetime.now().date() > user.last_bonus_claimed_at.date()) else 0,
            "gender": gender_display,
            "add_balance": 1, 
            "free_profile_used": user.username_change_count 
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Error: {str(e)}")


@router.post("/api/auth/update-password")
@router.post("/auth/update-password")
@router.put("/api/auth/update-password")
@router.put("/auth/update-password")
async def update_password(
    request: Request, data: dict, session: AsyncSession = Depends(get_db)
):
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
                        payload = {"id": int(ids[0])}
                    except: pass
        except: pass

    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    new_password = data.get("password")
    if not new_password:
        raise HTTPException(status_code=400, detail="Password is required")

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(payload.get("id"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password = new_password
    await session.commit()
    return {"success": True, "message": "Password updated"}


@router.post("/api/auth/update-game-username")
@router.post("/auth/update-game-username")
@router.put("/api/auth/update-game-username")
@router.put("/auth/update-game-username")
async def update_game_username(
    request: Request, data: dict, session: AsyncSession = Depends(get_db)
):
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
                        payload = {"id": int(ids[0])}
                    except: pass
        except: pass

    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(payload.get("id"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if "game_username" in data:
        # Agar yangi ism eski ismdan farq qilsa, hisoblagichni oshiramiz
        if user.username != data["game_username"]:
            user.username = data["game_username"]
            user.username_change_count += 1
    if "status" in data:
        user.status_text = data["status"]
    if "birth_date" in data:
        birth_date_str = data["birth_date"]
        user.birth_date = birth_date_str
        try:
            from datetime import datetime, timezone

            ms = parse_birth_date_ms(birth_date_str)
            if ms:
                birth_dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
                today = datetime.now(timezone.utc)
                age = today.year - birth_dt.year - (
                    (today.month, today.day) < (birth_dt.month, birth_dt.day)
                )
                user.age = age
        except Exception:
            pass

    await session.commit()
    return {"success": True, "message": "Profile updated"}


@router.post("/api/auth/update-gender")
@router.post("/auth/update-gender")
@router.put("/api/auth/update-gender")
@router.put("/auth/update-gender")
async def update_gender(
    request: Request, data: dict, session: AsyncSession = Depends(get_db)
):
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
                        payload = {"id": int(ids[0])}
                    except: pass
        except: pass

    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    gender_input = data.get("gender", "").lower()
    if not gender_input:
        raise HTTPException(status_code=400, detail="Gender is required")

    # Gender normalizatsiya (Turli tillar uchun)
    gender_map = {
        "male": "male",
        "erkak": "male",
        "мужской": "male",
        "kişi": "male",
        "erkek": "male",
        "ер": "male",
        "мард": "male",
        "female": "female",
        "ayol": "female",
        "женский": "female",
        "qadın": "female",
        "kadın": "female",
        "әйел": "female",
        "zan": "female",
    }

    normalized_gender = gender_map.get(gender_input, "male")

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(payload.get("id"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.gender = normalized_gender
    await session.commit()
    return {"success": True, "message": "Gender updated", "gender": normalized_gender}


@router.post("/api/auth/profile")
@router.post("/auth/profile")
@router.put("/api/auth/profile")
@router.put("/auth/profile")
async def update_profile_picture(
    request: Request,
    profile_picture: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
):
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
                        payload = {"id": int(ids[0])}
                    except (ValueError, TypeError): pass
        except: pass

    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        user_id = payload.get("id")
        import time
        timestamp = int(time.time())
        
        # Rasmni saqlash yo'li
        base_dir = Path(__file__).parent.parent.parent.parent.parent.parent.resolve()
        photos_dir = base_dir / "src" / "app" / "site" / "media" / "photos"
        
        if not photos_dir.exists():
            os.makedirs(photos_dir, exist_ok=True)
            
        user_repo = UserRepository(session)
        user = await user_repo.get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # ESKI RASMNI O'CHIRISH
        if user.avatar_url:
            try:
                old_file_name = user.avatar_url.split("/")[-1]
                if old_file_name != "no_img.png":
                    old_file_path = photos_dir / old_file_name
                    if old_file_path.exists():
                        os.remove(old_file_path)
            except Exception as e:
                print(f">>> Eski rasmni o'chirishda xato: {e}")

        # YANGI RASMNI SAQLASH
        file_name = f"user_{user_id}_{timestamp}.png"
        file_path = photos_dir / file_name
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(profile_picture.file, buffer)
            
        # User modelini yangilash
        user.avatar_url = f"/photos/{file_name}"
        await session.commit()
        
        return {
            "success": True, 
            "message": "Profile picture updated",
            "avatar_url": user.avatar_url,
            "profile_picture": user.avatar_url,
            "profilePicture": user.avatar_url,
            "avatar": user.avatar_url,
            "photo": user.avatar_url,
            "url": user.avatar_url
        }
    except Exception as e:
        import traceback
        print(f">>> PHOTO UPLOAD ERROR: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

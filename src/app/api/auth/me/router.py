import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.auth.user_payload import game_display_name
from src.app.api.ws.player import parse_birth_date_ms
from src.app.core.jwt import verify_access_token
from src.app.database.repositories.user import UserRepository
from src.app.services.telegram_profile import _PHOTOS_DIR, _is_valid_image_bytes

router = APIRouter(tags=["Me"])
log = logging.getLogger("spinbottle")


def _resolve_auth_payload(request: Request) -> Optional[dict]:
    """Bearer (odatda localStorage) + cookie — eskirgan Bearer bo'lsa cookie bilan davom etadi."""
    import json
    import urllib.parse

    tokens: list[str] = []
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        t = auth.replace("Bearer ", "", 1).strip()
        if t:
            tokens.append(t)
    for key in ("device_user_ids", "accessToken"):
        v = request.cookies.get(key)
        if not v:
            continue
        s = str(v).strip()
        if s and s not in tokens:
            tokens.append(s)
    if not tokens:
        return None
    for token in tokens:
        p = verify_access_token(token)
        if p:
            return p
        try:
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if isinstance(ids, list) and ids:
                    return {"id": int(ids[0])}
        except Exception:
            pass
    return None


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
    session: AsyncSession = Depends(get_db),
):
    try:
        payload = _resolve_auth_payload(request)
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

        lb = user.last_bonus_claimed_at
        try:
            if lb is None:
                can_claim_bonus = 1
            elif hasattr(lb, "date"):
                can_claim_bonus = (
                    1 if datetime.now().date() > lb.date() else 0
                )
            else:
                can_claim_bonus = 1
        except Exception:
            can_claim_bonus = 0

        gift_bal = 0
        if user.wallet is not None:
            gift_bal = int(user.wallet.gift_tokens or 0)

        return {
            "game_username": game_display_name(user),
            "status": user.status_text or None,
            "birthday_ts": birthday_ts,
            "balance": gift_bal,
            "gift_tokens": gift_bal,
            "daily_streak": user.daily_streak,
            "can_claim_bonus": can_claim_bonus,
            "gender": gender_display,
            "add_balance": 1,
            "free_profile_used": int(user.username_change_count or 0),
            "profile_picture": user.avatar_url or None,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("get_me: profil yuklash xatosi (auth emas): %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Profile load error: {str(e)}",
        )


@router.post("/api/auth/update-password")
@router.post("/auth/update-password")
@router.put("/api/auth/update-password")
@router.put("/auth/update-password")
async def update_password(
    request: Request, data: dict, session: AsyncSession = Depends(get_db)
):
    payload = _resolve_auth_payload(request)

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
    payload = _resolve_auth_payload(request)

    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(payload.get("id"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if "game_username" in data:
        new_name = str(data["game_username"]).strip().lstrip("@")[:30]
        if new_name and user.username != new_name:
            user.username = new_name
            user.username_change_count = int(user.username_change_count or 0) + 1
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
    payload = _resolve_auth_payload(request)

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
    payload = _resolve_auth_payload(request)

    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        user_id = payload.get("id")
        import time
        timestamp = int(time.time())
        
        photos_dir = _PHOTOS_DIR
        photos_dir.mkdir(parents=True, exist_ok=True)
            
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

        raw = await profile_picture.read()
        if not _is_valid_image_bytes(raw):
            raise HTTPException(
                status_code=400,
                detail="Fayl rasm emas (JPEG/PNG/WEBP kerak)",
            )
        ext = ".jpg"
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif raw[:4] == b"RIFF" and b"WEBP" in raw[:16]:
            ext = ".webp"
        file_name = f"user_{user_id}_{timestamp}{ext}"
        file_path = photos_dir / file_name
        file_path.write_bytes(raw)
            
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

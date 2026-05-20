import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.jwt import create_access_token, create_refresh_token
from src.app.database.repositories.user import UserRepository
from src.app.core.security.validators import validate_username

log = logging.getLogger("spinbottle.register")

site_dir = Path(__file__).resolve().parents[3] / "site"
router = APIRouter(tags=["Register"])


async def get_db(request: Request) -> AsyncSession:
    async with request.app.state.db.session_factory() as session:
        yield session


class RegisterModel(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    gender: str = Field(max_length=32)
    ref: Optional[str] = Field(default=None, max_length=64)

    @field_validator("username")
    @classmethod
    def _username_safe(cls, v: str) -> str:
        clean = validate_username(v)
        if not clean:
            raise ValueError("username_invalid")
        return clean

    @field_validator("password")
    @classmethod
    def _password_strip(cls, v: str) -> str:
        return (v or "").strip()[:128]


@router.post("/api/auth/register")
async def register(
    request: Request, data: RegisterModel, session: AsyncSession = Depends(get_db)
):
    user_repo = UserRepository(session)
    username = validate_username(data.username) or data.username
    existing_user = await user_repo.get_user_by_login(username)

    if existing_user:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400, 
            content={"error": "Bu kullanıcı adı zaten alınmış"}
        )

    from src.app.core.geo import client_ip, country_code_from_ip

    ip = client_ip(request)
    user_country = country_code_from_ip(ip)
    if not user_country:
        log.warning("REGISTER: mamlakat aniqlanmadi ip=%s", ip)

    # Gender (Barcha tillar uchun: EN, RU, AZ, TR, UZ, KZ, TJ)
    gender_map = {
        # Male / Erkak
        "erkak": "male",
        "male": "male",
        "мужской": "male",
        "kişi": "male",
        "erkek": "male",
        "ер": "male",
        "мард": "male",
        # Female / Ayol
        "ayol": "female",
        "female": "female",
        "женский": "female",
        "qadın": "female",
        "kadın": "female",
        "әйел": "female",
        "зан": "female",
    }
    normalized_gender = gender_map.get(data.gender.lower(), "male")

    # Referral ID ni tekshirish (bo'sh string yoki noto'g'ri qiymatni None ga aylantiramiz)
    ref_id = (data.ref or "").strip() or None
    if ref_id:
        log.info(f"REFERRAL: Yangi foydalanuvchi '{data.username}' ref='{ref_id}' bilan ro'yxatdan o'tmoqda")

    # Create user (ref_id = referred_by_id sifatida saqlanadi)
    user = await user_repo.add_user(
        login=username,
        gender=normalized_gender,
        password=data.password,
        country=user_country,
        referred_by_id=ref_id,
        avatar_url="/photos/no_img.png",
    )

    if ref_id:
        try:
            from src.app.services.referral_rewards import process_referral_signup

            await process_referral_signup(
                session,
                ref_id,
                referee_label=data.username,
                new_user_id=user.id,
            )
        except Exception as e:
            log.error(f"REFERRAL BONUS xatosi: {e}", exc_info=True)

    # Generate tokens
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)
    if isinstance(access_token, bytes):
        access_token = access_token.decode("utf-8")
    if isinstance(refresh_token, bytes):
        refresh_token = refresh_token.decode("utf-8")

    gift_t = int(user.wallet.gift_tokens or 0) if user.wallet else 0
    stars = gift_t
    display_username = f"user_{user.id}"

    is_admin = await user_repo.is_admin(user.id)
    response_data = {
        "success": True,
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "device_user_ids": access_token,
        "redirectUrl": "/welcome",
        "user": {
            "id": user.id,
            "username": user.login,
            "game_username": display_username,
            "display_name": display_username,
            "stars": stars,
            "gift_tokens": gift_t,
            "gm_coin": int(user.wallet.stars_coin or 0) if user.wallet else 0,
            "level": user.level,
            "gender": user.gender or "male",
            "is_admin": is_admin,
            "profile_picture": user.avatar_url or "/photos/no_img.png",  # sayt: no_img
            "country": user.country or user_country,
        },
    }

    response = Response(
        content=json.dumps(response_data), media_type="application/json"
    )

    # Barcha cookielarni o'rnatamiz (100 yil)
    max_age_100_years = 3600 * 24 * 365 * 100
    cookie_params = {"httponly": False, "path": "/", "samesite": "lax", "max_age": max_age_100_years}
    
    response.set_cookie(key="device_user_ids", value=access_token, **cookie_params)
    response.set_cookie(key="accessToken", value=access_token, **cookie_params)
    response.set_cookie(key="refreshToken", value=refresh_token, **cookie_params)

    return response

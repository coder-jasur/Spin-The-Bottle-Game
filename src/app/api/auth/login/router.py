from pathlib import Path
from fastapi import APIRouter, Depends, Request, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import json
from src.app.database.repositories.user import UserRepository
from src.app.core.jwt import create_access_token, create_refresh_token

site_dir = Path(__file__).resolve().parents[3] / "site"
router = APIRouter(tags=["Login"])

async def get_db(request: Request) -> AsyncSession:
    async with request.app.state.db.session_factory() as session:
        yield session

class LoginModel(BaseModel):
    username: str
    password: str

@router.post("/api/auth/login")
async def login(data: LoginModel, session: AsyncSession = Depends(get_db)):
    user_repo = UserRepository(session) 
    user = await user_repo.get_user_by_login(data.username)

    if user and user.password == data.password:
        # Tokenlarni yaratamiz
        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)
        
        # Agar bytes bo'lsa str ga o'tkazamiz (ba'zi kutubxonalarda shunday)
        if isinstance(access_token, bytes): access_token = access_token.decode('utf-8')
        if isinstance(refresh_token, bytes): refresh_token = refresh_token.decode('utf-8')

        # Kunlik streakni yangilash
        await user_repo.update_daily_streak(user)
        await session.commit() # Streakni saqlash

        is_admin = await user_repo.is_admin(user.id)
        from src.app.api.ws.constants import ADMIN_DISPLAY_STARS

        if is_admin and user.wallet:
            floor = ADMIN_DISPLAY_STARS
            dirty = False
            if int(user.wallet.stars_coin or 0) < floor:
                user.wallet.stars_coin = floor
                dirty = True
            if int(user.wallet.stars or 0) < floor:
                user.wallet.stars = floor
                dirty = True
            if dirty:
                await session.commit()

        stars = user.wallet.stars if user.wallet else 0
        gift_tokens = user.wallet.gift_tokens if user.wallet else 0
        gm_coin_raw = int(user.wallet.stars_coin or 0) if user.wallet else 0
        display_username = user.username or user.display_name or f"user_{user.id}"

        if is_admin:
            stars = max(int(stars or 0), ADMIN_DISPLAY_STARS)
            gm_coin = max(gm_coin_raw, ADMIN_DISPLAY_STARS)
        else:
            gm_coin = gm_coin_raw

        user_payload = {
                "id": user.id,
                "username": user.login,
                "game_username": display_username,
                "display_name": display_username,
                "stars": stars,
                "gift_tokens": gift_tokens,
                "daily_streak": user.daily_streak,
                "gm_coin": gm_coin,
                "level": user.level,
                "gender": user.gender or "male",
                "is_admin": is_admin,
                "profile_picture": user.avatar_url or "/photos/no_img.png",
                "country": user.country or "UZ"
            }

        response_data = {
            "success": True,
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "device_user_ids": access_token,
            "user": user_payload,
        }

        response = Response(
            content=json.dumps(response_data), media_type="application/json"
        )

        # Barcha cookielarni o'rnatamiz (100 yil muddat bilan)
        max_age_100_years = 3600 * 24 * 365 * 100
        cookie_params = {"httponly": False, "path": "/", "samesite": "lax", "max_age": max_age_100_years}
        
        response.set_cookie(key="device_user_ids", value=access_token, **cookie_params)
        response.set_cookie(key="accessToken", value=access_token, **cookie_params)
        response.set_cookie(key="refreshToken", value=refresh_token, **cookie_params)

        return response

    raise HTTPException(status_code=404, detail="Login yoki parol xato!")

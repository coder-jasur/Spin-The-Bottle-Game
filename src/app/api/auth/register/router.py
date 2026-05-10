import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.jwt import create_access_token, create_refresh_token
from src.app.database.repositories.user import UserRepository

site_dir = Path(__file__).resolve().parents[3] / "site"
router = APIRouter(tags=["Register"])


async def get_db(request: Request) -> AsyncSession:
    async with request.app.state.db.session_factory() as session:
        yield session


class RegisterModel(BaseModel):
    username: str
    password: str
    gender: str


@router.post("/api/auth/register")
async def register(
    request: Request, data: RegisterModel, session: AsyncSession = Depends(get_db)
):
    user_repo = UserRepository(session)
    existing_user = await user_repo.get_user_by_login(data.username)

    if existing_user:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400, 
            content={"error": "Bu kullanıcı adı zaten alınmış"}
        )

    # IP va Country
    client_ip = (
        request.headers.get(
            "X-Forwarded-For", request.client.host if request.client else "127.0.0.1"
        )
        .split(",")[0]
        .strip()
    )
    from src.app.core.geo import get_country_by_ip

    user_country = get_country_by_ip(client_ip)
    if user_country == "Unknown":
        user_country = "Uzbekistan"

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

    # Create user
    user = await user_repo.add_user(
        login=data.username,
        gender=normalized_gender,
        password=data.password,
        country=user_country,
    )

    # Generate tokens
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)
    if isinstance(access_token, bytes):
        access_token = access_token.decode("utf-8")
    if isinstance(refresh_token, bytes):
        refresh_token = refresh_token.decode("utf-8")

    stars = user.wallet.stars if user.wallet else 0
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
            "gm_coin": 0,
            "level": user.level,
            "gender": user.gender or "male",
            "is_admin": is_admin,
            "profile_picture": user.avatar_url or "/photos/no_img.png",
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

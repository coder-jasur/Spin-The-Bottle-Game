import asyncio
import json
import logging
import secrets
import string
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_db
from src.app.api.game_entry import build_game_index_path
from src.app.api.auth.session import resolve_auth_payload
from src.app.core.config import load_config
from src.app.core.language import normalize_lang, to_game_locale
from src.app.core.jwt import create_access_token, create_refresh_token
from src.app.core.telegram_invite import build_telegram_invite_bundle, normalize_start_param
from src.app.api.auth.user_payload import build_auth_user_payload
from src.app.database.repositories.user import UserRepository
from src.app.services.telegram_profile import (
    NO_IMG,
    avatar_needs_telegram_sync,
    sync_telegram_user_avatar,
)

log = logging.getLogger("spinbottle.tg_auth")
router = APIRouter(tags=["Telegram Auth"])


async def _background_sync_telegram_avatar(
    session_factory,
    *,
    user_db_id: int,
    tg_id: int,
    bot_token: str,
    client_photo_url: str | None,
    current_avatar: str | None,
) -> None:
    """Auth javobini kechiktirmaslik uchun avatar fon rejimida."""
    try:
        async with session_factory() as session:
            user_repo = UserRepository(session)
            avatar = await sync_telegram_user_avatar(
                tg_id=tg_id,
                bot_token=bot_token,
                user_db_id=user_db_id,
                client_photo_url=client_photo_url,
                current_avatar=current_avatar or NO_IMG,
                timeout=12.0,
            )
            if avatar and avatar != (current_avatar or ""):
                await user_repo.update_user_by_id(user_db_id, avatar_url=avatar)
                await session.commit()
    except Exception as e:
        log.warning("TG avatar background sync user=%s: %s", user_db_id, e)


class TelegramAuthModel(BaseModel):
    tg_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    language_code: Optional[str] = None
    start_param: Optional[str] = None  # = users.referral_id (taklif qilgan)


@router.post("/api/auth/telegram")
async def telegram_auth(
    request: Request,
    data: TelegramAuthModel,
    session: AsyncSession = Depends(get_db),
):
    try:
        return await _telegram_auth_impl(request, data, session)
    except Exception as e:
        log.exception("TG_AUTH failed tg_id=%s: %s", getattr(data, "tg_id", None), e)
        return Response(
            status_code=500,
            content=json.dumps(
                {"success": False, "error": "auth_failed", "detail": str(e)[:200]},
                ensure_ascii=False,
            ),
            media_type="application/json; charset=utf-8",
        )


async def _telegram_auth_impl(
    request: Request,
    data: TelegramAuthModel,
    session: AsyncSession,
) -> Response:
    settings = load_config()
    user_repo = UserRepository(session)
    db_factory = getattr(getattr(request.app, "state", None), "db", None)
    session_factory = getattr(db_factory, "session_factory", None)

    ref = normalize_start_param(data.start_param)
    user_lang = normalize_lang(data.language_code)
    game_locale = to_game_locale(user_lang)

    user = await user_repo.get_user(data.tg_id)
    is_new_user = False

    if user:
        patch = {}
        if session_factory and avatar_needs_telegram_sync(user.avatar_url):
            asyncio.create_task(
                _background_sync_telegram_avatar(
                    session_factory,
                    user_db_id=user.id,
                    tg_id=data.tg_id,
                    bot_token=settings.bot_token,
                    client_photo_url=data.photo_url,
                    current_avatar=user.avatar_url,
                )
            )
        if data.username and data.username.strip():
            tg_un = data.username.strip().lstrip("@")
            if tg_un:
                patch["chat_id"] = tg_un
                game_un = (user.username or "").strip()
                if not game_un.startswith("user_") and int(
                    user.username_change_count or 0
                ) == 0:
                    patch["username"] = f"user_{user.id}"
        dn = (data.first_name or "").strip()
        if data.last_name:
            dn = f"{dn} {data.last_name}".strip()
        if dn and dn != (user.display_name or ""):
            patch["display_name"] = dn
        if game_locale and game_locale != (user.language_code or ""):
            patch["language_code"] = game_locale
        if patch:
            await user_repo.update_user_by_id(user.id, **patch)
            for k, v in patch.items():
                setattr(user, k, v)
    else:
        is_new_user = True
        display_name = data.first_name or data.username or f"User_{data.tg_id}"
        if data.last_name:
            display_name += f" {data.last_name}"

        random_pwd = "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(12)
        )

        client_ip = (
            request.headers.get("X-Forwarded-For", request.client.host if request.client else "127.0.0.1")
            .split(",")[0]
            .strip()
        )
        from src.app.core.geo import get_country_by_ip

        user_country = get_country_by_ip(client_ip)
        if user_country == "Unknown":
            user_country = "Uzbekistan"

        tg_handle = (data.username or "").strip().lstrip("@")
        user = await user_repo.add_user(
            tg_id=data.tg_id,
            login=f"tg_{data.tg_id}",
            display_name=display_name,
            avatar_url=NO_IMG,
            password=random_pwd,
            country=user_country,
            referred_by_id=ref,
            gender="male",
            language_code=game_locale,
            chat_id=tg_handle or None,
        )
        user.avatar_url = NO_IMG
        if session_factory:
            asyncio.create_task(
                _background_sync_telegram_avatar(
                    session_factory,
                    user_db_id=user.id,
                    tg_id=data.tg_id,
                    bot_token=settings.bot_token,
                    client_photo_url=data.photo_url,
                    current_avatar=NO_IMG,
                )
            )
        log.info(
            "TG_AUTH: Yangi foydalanuvchi yaratildi: %s (tg_id: %s, ref=%s)",
            display_name,
            data.tg_id,
            ref,
        )

        if ref:
            try:
                from src.app.services.referral_rewards import process_referral_signup

                await process_referral_signup(
                    session,
                    ref,
                    referee_label=display_name,
                    new_user_id=user.id,
                )
            except Exception as e:
                log.error("REFERRAL ERROR: %s", e, exc_info=True)

    await user_repo.update_daily_streak(user)
    await session.commit()

    # Wallet va avatar yangilanishlari bilan to'liq user
    user = await user_repo.get_user_by_id(user.id) or user
    is_admin = await user_repo.is_admin(user.id)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    if isinstance(access_token, bytes):
        access_token = access_token.decode("utf-8")
    if isinstance(refresh_token, bytes):
        refresh_token = refresh_token.decode("utf-8")

    invite = build_telegram_invite_bundle(
        user.referral_id,
        bot_username=settings.telegram_miniapp_bot,
        mini_slug=settings.telegram_miniapp_slug,
        share_text=settings.telegram_invite_share_text,
    )

    user_payload = build_auth_user_payload(user, is_admin=is_admin)

    redirect_path = build_game_index_path(
        request,
        user.id,
        language_code=user.language_code,
        telegram_language_code=data.language_code,
    )

    response_data = {
        "success": True,
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "device_user_ids": access_token,
        "user_id": user.id,
        "is_new": is_new_user,
        "lang": user_lang,
        "locale": game_locale,
        "redirectUrl": redirect_path,
        "user": user_payload,
        **invite,
    }

    response = Response(content=json.dumps(response_data), media_type="application/json")
    max_age = 3600 * 24 * 365 * 100
    cookie_params = {"httponly": False, "path": "/", "samesite": "lax", "max_age": max_age}

    response.set_cookie(key="device_user_ids", value=access_token, **cookie_params)
    response.set_cookie(key="accessToken", value=access_token, **cookie_params)
    response.set_cookie(key="refreshToken", value=refresh_token, **cookie_params)
    response.set_cookie(key="language", value=user_lang, **cookie_params)

    return response


@router.get("/api/auth/game-entry")
async def game_entry(request: Request, session: AsyncSession = Depends(get_db)):
    """JWT cookie bor, lekin URLda user_id yo'q — to'g'ri /index?... qaytaradi."""
    payload = resolve_auth_payload(request)
    if not payload or not payload.get("id"):
        # 401 emas — brauzer qizil xato ko'rsatmasin; klient Telegram auth qiladi
        return Response(
            content=json.dumps({"success": False, "needs_auth": True}),
            media_type="application/json",
        )

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(payload["id"])
    if not user:
        return Response(
            content=json.dumps({"success": False, "error": "user_not_found"}),
            media_type="application/json",
            status_code=404,
        )

    path = build_game_index_path(request, user.id, language_code=user.language_code)
    return Response(
        content=json.dumps({"success": True, "redirectUrl": path}),
        media_type="application/json",
    )

"""Telegram Mini App initData tekshiruvi va foydalanuvchini DB ga bog'lash."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import string
import time
from typing import Any, Optional
from urllib.parse import parse_qsl

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.language import normalize_lang, to_game_locale
from src.app.core.telegram_invite import normalize_start_param
from src.app.database.repositories.user import UserRepository
from src.app.services.telegram_profile import NO_IMG

log = logging.getLogger("spinbottle.tg_webapp")


def validate_init_data(init_data: str, bot_token: str, *, max_age_sec: int = 86400) -> dict | None:
    """Telegram WebApp initData (HMAC) — muvaffaqiyatli bo'lsa parsed dict."""
    raw = (init_data or "").strip()
    token = (bot_token or "").strip()
    if not raw or not token or "hash=" not in raw:
        return None
    try:
        parsed = dict(parse_qsl(raw, keep_blank_values=True))
    except Exception:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(
        secret_key, check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        return None
    try:
        auth_date = int(parsed.get("auth_date") or 0)
    except (TypeError, ValueError):
        auth_date = 0
    if auth_date and time.time() - auth_date > max_age_sec:
        return None
    user_raw = parsed.get("user")
    if isinstance(user_raw, str) and user_raw:
        try:
            parsed["user"] = json.loads(user_raw)
        except json.JSONDecodeError:
            return None
    return parsed


async def ensure_user_from_init_data(
    session: AsyncSession,
    parsed: dict,
    *,
    client_ip: str | None = None,
) -> Any | None:
    """initData.user bo'yicha DB user; yo'q bo'lsa yaratadi (referral bilan)."""
    user_obj = parsed.get("user")
    if not isinstance(user_obj, dict) or not user_obj.get("id"):
        return None

    tg_id = int(user_obj["id"])
    repo = UserRepository(session)
    existing = await repo.get_user(tg_id)
    if existing:
        return existing

    from src.app.core.geo import country_code_from_ip

    display_name = (user_obj.get("first_name") or user_obj.get("username") or f"User_{tg_id}").strip()
    last = (user_obj.get("last_name") or "").strip()
    if last:
        display_name = f"{display_name} {last}".strip()

    ref = normalize_start_param(parsed.get("start_param"))
    user_lang = normalize_lang(user_obj.get("language_code"))
    game_locale = to_game_locale(user_lang)
    country = country_code_from_ip(client_ip) if client_ip else None
    tg_handle = (user_obj.get("username") or "").strip().lstrip("@")
    random_pwd = "".join(
        secrets.choice(string.ascii_letters + string.digits) for _ in range(12)
    )

    user = await repo.add_user(
        tg_id=tg_id,
        login=f"tg_{tg_id}",
        display_name=display_name,
        avatar_url=NO_IMG,
        password=random_pwd,
        country=country,
        referred_by_id=ref,
        gender="male",
        language_code=game_locale,
        chat_id=tg_handle or None,
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
            log.error("REFERRAL on WS initData signup: %s", e, exc_info=True)

    log.info("WS initData: yangi user tg_id=%s db_id=%s ref=%s", tg_id, user.id, ref)
    return user


async def resolve_db_user_id_from_login(
    session: AsyncSession,
    token: str,
    auth: str | None,
    bot_token: str,
    *,
    client_ip: str | None = None,
) -> Optional[int]:
    """Login `id` + `auth` (initData) → users.id."""
    tok = (token or "").strip()
    if tok.isdigit():
        u = await UserRepository(session).get_user(int(tok))
        if u:
            return int(u.id)

    auth_raw = (auth or "").strip()
    if auth_raw and "hash=" in auth_raw:
        parsed = validate_init_data(auth_raw, bot_token)
        if parsed:
            u = await ensure_user_from_init_data(
                session, parsed, client_ip=client_ip
            )
            if u:
                return int(u.id)
    return None

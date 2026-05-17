"""Login / register / telegram uchun bir xil user JSON."""
from __future__ import annotations

from src.app.api.ws.constants import ADMIN_DISPLAY_STARS
from src.app.core.language import normalize_lang, to_game_locale
from src.app.database.models.user import User
from src.app.services.telegram_profile import NO_IMG, public_avatar_url


def _stored_telegram_handle(user: User) -> str | None:
    """Telegram @username — chat_id da saqlanadi (username = o'yin nomi)."""
    raw = (getattr(user, "chat_id", None) or "").strip().lstrip("@")
    if raw and not raw.isdigit():
        return raw
    if user.tg_id:
        un = (user.username or "").strip().lstrip("@")
        if (
            un
            and not un.startswith("user_")
            and un != f"user_{user.tg_id}"
            and int(user.username_change_count or 0) == 0
        ):
            return un
    return None


def game_display_name(user: User) -> str:
    """Stolda / o'yinda ko'rinadigan ism — Игровое имя (user_N yoki o'zgartirilgan)."""
    un = (user.username or "").strip().lstrip("@")
    if not un:
        return f"user_{user.id}"
    if un.startswith("user_"):
        return un
    if user.tg_id and int(user.username_change_count or 0) == 0:
        return f"user_{user.id}"
    return un[:30]


def telegram_username_label(user: User) -> str:
    """Sozlamalar: Telegram username (@siz)."""
    handle = _stored_telegram_handle(user)
    if handle:
        return handle
    if user.display_name and str(user.display_name).strip():
        return str(user.display_name).strip()
    return f"user_{user.id}"


def public_display_username(user: User) -> str:
    """UI: Telegram @username yoki ism; web uchun login."""
    if user.tg_id:
        un = (user.username or "").strip().lstrip("@")
        if un and not un.startswith("user_") and un != f"user_{user.tg_id}":
            return f"@{un}"
        if user.display_name and str(user.display_name).strip():
            return str(user.display_name).strip()
        return f"User {user.tg_id}"
    return user.username or user.display_name or user.login or f"user_{user.id}"


def build_auth_user_payload(user: User, *, is_admin: bool = False) -> dict:
    gift_tokens = int(user.wallet.gift_tokens or 0) if user.wallet else 0
    gm_coin_raw = int(user.wallet.stars_coin or 0) if user.wallet else 0
    stars = gm_coin_raw
    display_username = public_display_username(user)
    game_login = game_display_name(user)
    tg_handle = _stored_telegram_handle(user)
    tg_display = telegram_username_label(user)

    if is_admin:
        gift_tokens = max(gift_tokens, ADMIN_DISPLAY_STARS)
        gm_coin = max(gm_coin_raw, ADMIN_DISPLAY_STARS)
        stars = gm_coin
    else:
        gm_coin = gm_coin_raw

    avatar = public_avatar_url(user.avatar_url) or NO_IMG
    lang = normalize_lang(user.language_code)

    return {
        "id": user.id,
        "login": user.login,
        "username": tg_display,
        "telegram_username": tg_handle,
        "game_username": game_login,
        "display_name": user.display_name or display_username,
        "stars": stars,
        "gift_tokens": gift_tokens,
        "daily_streak": user.daily_streak or 0,
        "gm_coin": gm_coin,
        "level": user.level,
        "gender": user.gender or "male",
        "is_admin": is_admin,
        "profile_picture": avatar,
        "country": user.country or "UZ",
        "tg_id": user.tg_id,
        "lang": lang,
        "locale": to_game_locale(lang),
    }

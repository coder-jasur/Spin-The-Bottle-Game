"""Sayt sahifalari URL: stars-support, banned (+ til parametri)."""
from __future__ import annotations

import urllib.parse

from fastapi import Request

from src.app.api.config.server_json import public_base_url
from src.app.core.language import normalize_lang, resolve_user_lang

DEFAULT_SUPPORT_USERNAME = "SpinTheBottleSupport"


def lang_from_request(
    request: Request | None = None,
    *,
    explicit_lang: str | None = None,
    db_language_code: str | None = None,
) -> str:
    if explicit_lang:
        return normalize_lang(explicit_lang)
    cookie_lang = request.cookies.get("language") if request else None
    return resolve_user_lang(
        cookie_lang=cookie_lang,
        db_language_code=db_language_code,
    )


def _with_lang(params: dict[str, str], lang: str | None) -> dict[str, str]:
    if lang:
        code = normalize_lang(lang)
        if code:
            params["lang"] = code
    return params


def support_telegram_url(username: str | None = None) -> str:
    user = (username or DEFAULT_SUPPORT_USERNAME).strip().lstrip("@")
    return f"https://t.me/{user}" if user else f"https://t.me/{DEFAULT_SUPPORT_USERNAME}"


def build_stars_support_path(
    *,
    shortfall: int | None = None,
    back: str | None = None,
    support_user: str | None = None,
    lang: str | None = None,
) -> str:
    params: dict[str, str] = {}
    if shortfall and int(shortfall) > 0:
        params["stars"] = str(int(shortfall))
    if back:
        params["back"] = back
    user = (support_user or DEFAULT_SUPPORT_USERNAME).strip().lstrip("@")
    if user:
        params["user"] = user
    _with_lang(params, lang)
    if not params:
        return "/stars-support"
    return f"/stars-support?{urllib.parse.urlencode(params)}"


def build_banned_path(
    *,
    expires_at: str,
    lang: str | None = None,
    support_user: str | None = None,
) -> str:
    params: dict[str, str] = {"expires_at": expires_at}
    user = (support_user or DEFAULT_SUPPORT_USERNAME).strip().lstrip("@")
    if user:
        params["user"] = user
    _with_lang(params, lang)
    return f"/banned?{urllib.parse.urlencode(params)}"


def build_stars_support_url(
    request: Request,
    settings=None,
    *,
    shortfall: int | None = None,
    back: str | None = None,
    support_user: str | None = None,
    lang: str | None = None,
) -> str:
    base = public_base_url(request, settings)
    if not support_user and settings is not None:
        support_user = getattr(settings, "telegram_support_username", None)
    if not lang:
        lang = lang_from_request(request)
    path = build_stars_support_path(
        shortfall=shortfall,
        back=back,
        support_user=support_user,
        lang=lang,
    )
    return f"{base}{path}"

"""O'yin kirish URL (?user_id= sessiya tokeni)."""
from __future__ import annotations

import urllib.parse

from fastapi import Request

from src.app.api.game_session import game_sessions
from src.app.core.language import resolve_user_lang, to_game_locale


def build_game_index_path(
    request: Request,
    user_db_id: int,
    *,
    language_code: str | None = None,
    telegram_language_code: str | None = None,
    referral_id: str | None = None,
    bot_username: str | None = None,
    mini_slug: str | None = None,
) -> str:
    session_token = game_sessions.create(user_db_id)
    # Nisbiy yo'l — `back` query ichida to'liq https URL bo'lmasin (double-encode xatosi)
    back_with_session = (
        f"/exit-game?{urllib.parse.urlencode({'user_id': session_token})}"
    )
    client_lang = resolve_user_lang(
        telegram_language_code=telegram_language_code,
        cookie_lang=request.cookies.get("language"),
        db_language_code=language_code,
    )
    from src.app.core.config import load_config

    settings = getattr(request.app.state, "settings", None) or load_config()
    bot = (bot_username or settings.telegram_miniapp_bot or "SpinbottleTgBot").strip().lstrip("@")
    slug = (mini_slug or settings.telegram_miniapp_slug or "spin_bottle").strip().strip("/")
    params = {
        "query": "",
        "user_id": session_token,
        # Tunnel/brauzer: klient `signed_request` bo‘lsa s5 (Stars bank), yo‘qsa mm (M) bank
        "signed_request": session_token,
        "locale": to_game_locale(client_lang),
        "back": back_with_session,
        "bot": bot,
        "app": slug,
    }
    rid = (referral_id or "").strip()
    if rid:
        params["user_id2"] = rid
    return f"/index?{urllib.parse.urlencode(params)}"

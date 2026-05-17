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
) -> str:
    from src.app.api.config.server_json import public_base_url
    from src.app.core.config import load_config

    session_token = game_sessions.create(user_db_id)
    settings = getattr(request.app.state, "settings", None) or load_config()
    base = public_base_url(request, settings)
    exit_url = f"{base}/exit-game"
    back_with_session = (
        f"{exit_url}?{urllib.parse.urlencode({'user_id': session_token})}"
    )
    client_lang = resolve_user_lang(
        telegram_language_code=telegram_language_code,
        cookie_lang=request.cookies.get("language"),
        db_language_code=language_code,
    )
    params = {
        "signed_request": "fb",
        "query": "",
        "user_id": session_token,
        "locale": to_game_locale(client_lang),
        "back": back_with_session,
    }
    return f"/index?{urllib.parse.urlencode(params)}"

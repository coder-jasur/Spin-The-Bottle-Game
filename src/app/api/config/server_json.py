"""server.json — joriy host/tunnel bo'yicha WebSocket va asset URLlari."""
from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

from fastapi import Request

_SITE_DIR = pathlib.Path(__file__).resolve().parents[2] / "site"
_TEMPLATE: dict[str, Any] | None = None

# O'yin klienti signed_request=fb (va tg) orqali ulanadi
_WS_PLATFORMS = ("fb", "tg", "ok", "as", "gg", "ma", "vk", "mm", "ya", "fbig", "fb-ig")


def public_base_url(request: Request, settings=None) -> str:
    env_url = ""
    if settings and getattr(settings, "telegram_webapp_url", ""):
        env_url = settings.telegram_webapp_url.strip().rstrip("/")

    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    if host:
        host = host.split(",")[0].strip()
        base = f"{proto}://{host}".rstrip("/")
        # Tunnel orqali kelganda ham ba'zan localhost ko'rinadi — .env dagi URL ustun
        if env_url and ("localhost" in base or "127.0.0.1" in base):
            return env_url
        return base
    if env_url:
        return env_url
    return str(request.base_url).rstrip("/")


def _ws_url(base: str) -> str:
    if base.startswith("https://"):
        return base.replace("https://", "wss://", 1) + "/ws/"
    if base.startswith("http://"):
        return base.replace("http://", "ws://", 1) + "/ws/"
    return f"wss://{base}/ws/"


def _load_template() -> dict[str, Any]:
    global _TEMPLATE
    if _TEMPLATE is None:
        with open(_SITE_DIR / "server.json", encoding="utf-8") as f:
            _TEMPLATE = json.load(f)
    return copy.deepcopy(_TEMPLATE)


def build_server_json(request: Request, settings=None) -> dict[str, Any]:
    data = _load_template()
    base = public_base_url(request, settings)
    ws = _ws_url(base)

    assets = data.get("assets") or {}
    assets["images_url"] = f"{base}/"
    assets["images_url_v2"] = f"{base}/${{width}}/${{name}}.png"
    v3 = assets.get("images_url_v3") or {}
    if isinstance(v3, dict):
        v3["png"] = f"{base}/${{width}}/${{name}}.png"
        v3["webp"] = f"{base}/${{width}}/${{name}}.webp"
        assets["images_url_v3"] = v3
    v4 = assets.get("images_url_v4") or {}
    if isinstance(v4, dict):
        v4["png"] = f"{base}/${{width}}x${{height}}/${{name}}.png"
        assets["images_url_v4"] = v4
    data["assets"] = assets

    host_only = base.split("://", 1)[-1]
    for key in _WS_PLATFORMS:
        block = data.get(key)
        if isinstance(block, dict):
            block["server"] = host_only
            block["server_v2"] = [ws]

    return data

"""Admin handlerlar — panel, broadcast, referral."""
from __future__ import annotations

from aiogram import Router

from src.app.bot.handlers.admin.broadcast import router as broadcast_router
from src.app.bot.handlers.admin.panel import (
    PANEL_TITLE_MSGID,
    panel_keyboard,
    router as panel_router,
)
from src.app.bot.handlers.admin.referral import router as referral_router

ADMIN_ROUTERS: tuple[Router, ...] = (
    panel_router,
    broadcast_router,
    referral_router,
)

__all__ = [
    "ADMIN_ROUTERS",
    "PANEL_TITLE_MSGID",
    "broadcast_router",
    "panel_keyboard",
    "panel_router",
    "referral_router",
]

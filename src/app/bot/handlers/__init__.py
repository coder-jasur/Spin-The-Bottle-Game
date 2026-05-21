"""Bot handler routerlari."""
from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram import Router

if TYPE_CHECKING:
    from src.app.bot.handlers.admin import ADMIN_ROUTERS as _AdminRouters


def all_routers() -> tuple[Router, ...]:
    """Dispatcher ga ulash tartibi (lazy import — circular import yo'q)."""
    from src.app.bot.handlers.admin import ADMIN_ROUTERS
    from src.app.bot.handlers.payments import router as payments_router
    from src.app.bot.handlers.start import router as start_router
    from src.app.bot.handlers.store import router as store_router

    return (
        store_router,
        payments_router,
        *ADMIN_ROUTERS,
        start_router,
    )


__all__ = ["all_routers"]

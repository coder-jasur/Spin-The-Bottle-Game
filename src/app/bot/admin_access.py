"""Telegram admin — faqat DB admins / MAIN_ADMIN."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import load_config
from src.app.database.models.admin import Admins
from src.app.database.models.user import User
from src.app.database.repositories.game import GameRepository
from src.app.database.repositories.user import UserRepository


async def get_user_by_telegram_id(
    session: AsyncSession, tg_id: int
) -> Optional[User]:
    if not tg_id:
        return None
    repo = UserRepository(session)
    return await repo.get_user(int(tg_id))


async def is_telegram_admin(session: AsyncSession, tg_id: int) -> bool:
    user = await get_user_by_telegram_id(session, tg_id)
    if not user:
        return False
    game = GameRepository(session)
    return await game.is_admin_user(int(user.id))


async def list_admin_telegram_chat_ids(session: AsyncSession) -> list[int]:
    """Bot menyusida /admin_panel ko'rinadigan chat_id lar."""
    cfg = load_config()
    ids: set[int] = set()

    main = await session.get(User, cfg.main_admin_id)
    if main and main.tg_id:
        ids.add(int(main.tg_id))

    stmt = select(User.tg_id).join(Admins, Admins.user_id == User.id).where(
        User.tg_id.isnot(None)
    )
    result = await session.execute(stmt)
    for (tg_id,) in result.all():
        if tg_id:
            ids.add(int(tg_id))

    return sorted(ids)

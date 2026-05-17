"""Mavjud PostgreSQL jadvallariga yangi ustunlar (create_all o'zgartirmaydi)."""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger("spinbottle")

# Har bir patch idempotent: IF NOT EXISTS
_SCHEMA_PATCHES: tuple[str, ...] = (
    """
    ALTER TABLE music_tracks
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE
    NOT NULL DEFAULT NOW()
    """,
    """
    ALTER TABLE users
    ADD COLUMN IF NOT EXISTS gift_love_stock INTEGER NOT NULL DEFAULT 0
    """,
)


async def apply_schema_patches(engine: AsyncEngine) -> None:
    for stmt in _SCHEMA_PATCHES:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception as e:
            log.warning("schema patch skipped: %s", e)

    try:
        from src.app.database.sequence_sync import sync_all_sequences_engine

        n = await sync_all_sequences_engine(engine)
        if n:
            log.info("PostgreSQL id sequences synced (%s tables)", n)
    except Exception as e:
        log.warning("sequence sync failed: %s", e)

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
    """
    ALTER TABLE users
    ADD COLUMN IF NOT EXISTS harem_courts_received BIGINT NOT NULL DEFAULT 0
    """,
    # Eski migratsiya olib tashlandi: harem_price ≠ 2-yurak yig'indisi; noto'g'ri raqam berardi.
    """
    ALTER TABLE users
    ADD COLUMN IF NOT EXISTS harem_owner_paid_price INTEGER NOT NULL DEFAULT 0
    """,
    """
    CREATE TABLE IF NOT EXISTS user_music_folders (
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        folder TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'mv',
        song_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        PRIMARY KEY (user_id, folder, provider)
    )
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

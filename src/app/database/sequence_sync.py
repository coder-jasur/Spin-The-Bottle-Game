"""PostgreSQL SERIAL/IDENTITY sequence — MAX(id) bilan moslashtirish.

Backup merge yoki qo'lda id bilan importdan keyin sequence orqada qolishi mumkin;
yangi INSERT duplicate key (users_pkey, wallets_pkey) beradi.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.inspection import inspect as sa_inspect

from src.app.database.backup_restore import BACKUP_MODELS

log = logging.getLogger("spinbottle.db_sequences")

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

_PK_VIOLATION_MARKERS = ("_pkey", "unique constraint", "uniqueviolationerror")


def is_primary_key_violation(exc: BaseException) -> bool:
    msg = str(getattr(exc, "orig", exc)).lower()
    return any(m in msg for m in _PK_VIOLATION_MARKERS)


async def sync_all_sequences(session: AsyncSession) -> int:
    """Barcha backup jadvallari uchun id sequence ni yangilaydi."""
    synced = 0
    for _name, model in BACKUP_MODELS:
        if await _sync_model_sequence(session, model):
            synced += 1
    return synced


async def sync_all_sequences_engine(engine: AsyncEngine) -> int:
    async with engine.begin() as conn:
        count = 0
        for _name, model in BACKUP_MODELS:
            if await _sync_model_sequence_conn(conn, model):
                count += 1
        return count


async def _sync_model_sequence(session: AsyncSession, model: type) -> bool:
    return await _sync_model_sequence_conn(session, model)


async def _sync_model_sequence_conn(conn, model: type) -> bool:
    mapper = sa_inspect(model)
    pks = list(mapper.primary_key)
    if len(pks) != 1:
        return False
    col = pks[0].key
    table = model.__tablename__
    if not _SAFE_NAME.match(table) or not _SAFE_NAME.match(col):
        log.warning("sequence sync skip unsafe name: %s.%s", table, col)
        return False

    seq_row = await conn.execute(
        text("SELECT pg_get_serial_sequence(:tbl, :col)"),
        {"tbl": table, "col": col},
    )
    seq = seq_row.scalar_one_or_none()
    if not seq:
        return False

    max_row = await conn.execute(
        text(f'SELECT COALESCE(MAX("{col}"), 0)::bigint AS mx FROM "{table}"')
    )
    max_id = int(max_row.scalar_one() or 0)
    # Bo'sh jadval: setval(0) ba'zi sequence larda xato; keyingi id = 1
    if max_id <= 0:
        await conn.execute(
            text("SELECT setval(CAST(:seq AS regclass), 1, false)"),
            {"seq": seq},
        )
    else:
        await conn.execute(
            text("SELECT setval(CAST(:seq AS regclass), CAST(:mx AS bigint), true)"),
            {"seq": seq, "mx": max_id},
        )
    log.debug("sequence synced %s -> %s (max_id=%s)", table, seq, max_id)
    return True

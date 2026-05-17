"""PostgreSQL — barcha modellarni bitta JSON (gzip) faylga eksport/import."""
from __future__ import annotations

import gzip
import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.inspection import inspect as sa_inspect

from src.app.database import models as db_models

log = logging.getLogger("spinbottle.db_backup")

BACKUP_VERSION = 1

# FK tartibi: avval parent, keyin child
BACKUP_MODELS: list[tuple[str, type]] = [
    ("achievements", db_models.Achievement),
    ("users", db_models.User),
    ("partners", db_models.Partner),
    ("referral_bonus_settings", db_models.ReferralBonusSettings),
    ("referral_daily_earnings", db_models.ReferralDailyEarnings),
    ("wallets", db_models.Wallet),
    ("user_stats", db_models.UserStats),
    ("user_boosters", db_models.UserBooster),
    ("user_relations", db_models.UserRelation),
    ("user_achievements", db_models.UserAchievement),
    ("transactions", db_models.Transaction),
    ("admins", db_models.Admins),
    ("admin_action_logs", db_models.AdminActionLog),
    ("broadcast_messages", db_models.BroadcastMessage),
    ("stories", db_models.Story),
    ("story_views", db_models.StoryView),
    ("story_likes", db_models.StoryLike),
    ("table_rooms", db_models.TableRoom),
    ("table_chat_messages", db_models.TableChatMessage),
    ("music_tracks", db_models.MusicTrack),
    ("user_music_folders", db_models.UserMusicFolder),
    ("user_music_gallery_items", db_models.UserMusicGalleryItem),
]


def _serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return str(value)


def row_to_dict(instance: Any) -> dict[str, Any]:
    mapper = sa_inspect(instance.__class__)
    return {
        attr.key: _serialize_value(getattr(instance, attr.key))
        for attr in mapper.column_attrs
    }


def _deserialize_value(col: Any, value: Any) -> Any:
    if value is None:
        return None
    try:
        py_type = col.type.python_type
    except (NotImplementedError, AttributeError):
        py_type = None
    if py_type is datetime and isinstance(value, str):
        s = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return datetime.fromisoformat(s.split(".")[0])
    if py_type is date and isinstance(value, str):
        return date.fromisoformat(value.split("T")[0])
    if py_type is UUID and isinstance(value, str):
        return UUID(value)
    return value


def dict_to_row(model: type, data: dict[str, Any]) -> dict[str, Any]:
    mapper = sa_inspect(model)
    out: dict[str, Any] = {}
    for col in mapper.columns:
        if col.key not in data:
            continue
        out[col.key] = _deserialize_value(col, data[col.key])
    return out


def _pk_columns(model: type) -> list[str]:
    mapper = sa_inspect(model)
    return [c.key for c in mapper.primary_key]


async def export_database(session: AsyncSession) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    total_rows = 0
    for name, model in BACKUP_MODELS:
        result = await session.execute(select(model))
        rows = [row_to_dict(r) for r in result.scalars().all()]
        tables[name] = rows
        total_rows += len(rows)
    return {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
        "meta": {
            "table_count": len(BACKUP_MODELS),
            "row_count": total_rows,
        },
    }


def dump_backup_bytes(payload: dict[str, Any], *, compress: bool = True) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if compress:
        return gzip.compress(raw, compresslevel=6)
    return raw


def load_backup_bytes(data: bytes) -> dict[str, Any]:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict) or "tables" not in payload:
        raise ValueError("invalid_backup_format")
    if int(payload.get("version", 0)) != BACKUP_VERSION:
        raise ValueError("unsupported_backup_version")
    return payload


async def restore_merge(session: AsyncSession, payload: dict[str, Any]) -> int:
    """Mavjud qatorlarni o'zgartirmasdan, yo'q bo'lganlarini qo'shadi."""
    inserted = 0
    tables = payload.get("tables") or {}
    for name, model in BACKUP_MODELS:
        rows: Iterable[dict] = tables.get(name) or []
        pk_cols = _pk_columns(model)
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            values = dict_to_row(model, raw)
            if not values:
                continue
            stmt = pg_insert(model).values(**values)
            if pk_cols:
                stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
            res = await session.execute(stmt)
            if res.rowcount and res.rowcount > 0:
                inserted += int(res.rowcount)
    await session.commit()
    await _sync_sequences_after_restore(session)
    return inserted


async def restore_full(session: AsyncSession, payload: dict[str, Any]) -> int:
    """Barcha jadvallarni tozalab, backupdan qayta to'ldiradi."""
    tables = payload.get("tables") or {}
    table_names = ", ".join(f'"{m.__tablename__}"' for _, m in BACKUP_MODELS)
    await session.execute(
        text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE")
    )
    total = 0
    for name, model in BACKUP_MODELS:
        for raw in tables.get(name) or []:
            if not isinstance(raw, dict):
                continue
            values = dict_to_row(model, raw)
            if values:
                await session.execute(pg_insert(model).values(**values))
                total += 1
    await session.commit()
    await _sync_sequences_after_restore(session)
    return total


async def _sync_sequences_after_restore(session: AsyncSession) -> None:
    try:
        from src.app.database.sequence_sync import sync_all_sequences

        n = await sync_all_sequences(session)
        await session.commit()
        log.info("Sequences synced after restore (%s tables)", n)
    except Exception as e:
        log.warning("sequence sync after restore: %s", e)
        await session.rollback()


def backup_filename() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"spinbottle_backup_{ts}.json.gz"

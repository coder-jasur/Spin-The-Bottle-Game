"""SQLAlchemy modellari ↔ PostgreSQL: yo'q ustunlarni ADD COLUMN (ma'lumot saqlanadi)."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, Text, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.schema import Column
from sqlalchemy.types import BigInteger, JSON

from src.app.database.base import Base

log = logging.getLogger("spinbottle")

_PG_DIALECT = postgresql.dialect()


def _compile_column_type(column: Column[Any]) -> str:
    try:
        return column.type.compile(dialect=_PG_DIALECT)
    except Exception:
        t = column.type
        if isinstance(t, BigInteger):
            return "BIGINT"
        if isinstance(t, Integer):
            return "INTEGER"
        if isinstance(t, Boolean):
            return "BOOLEAN"
        if isinstance(t, DateTime):
            return "TIMESTAMP WITHOUT TIME ZONE"
        if isinstance(t, (JSONB, JSON)):
            return "JSONB"
        if isinstance(t, Text):
            return "TEXT"
        return "TEXT"


def _default_sql(column: Column[Any]) -> str | None:
    if column.server_default is not None:
        arg = column.server_default.arg
        if arg is None:
            return None
        if hasattr(arg, "text"):
            return str(arg.text)
        return str(arg)
    if column.default is not None and hasattr(column.default, "arg"):
        arg = column.default.arg
        if callable(arg):
            return None
        if isinstance(arg, bool):
            return "true" if arg else "false"
        if isinstance(arg, str):
            return f"'{arg.replace(chr(39), chr(39) + chr(39))}'"
        return str(arg)
    if not column.nullable:
        if isinstance(column.type, Boolean):
            return "false"
        if isinstance(column.type, (Integer, BigInteger)):
            return "0"
        if isinstance(column.type, Text):
            return "''"
    return None


def _build_add_column_sql(table_name: str, column: Column[Any]) -> str:
    col_type = _compile_column_type(column)
    parts = [
        f'ALTER TABLE "{table_name}"',
        f'ADD COLUMN IF NOT EXISTS "{column.name}" {col_type}',
    ]
    default = _default_sql(column)
    if default is not None:
        parts.append(f"DEFAULT {default}")
    if not column.nullable and default is not None:
        parts.append("NOT NULL")
    return " ".join(parts)


async def _load_public_columns(conn) -> dict[str, set[str]]:
    result = await conn.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            """
        )
    )
    rows = result.fetchall()
    out: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        out.setdefault(str(table_name), set()).add(str(column_name))
    return out


async def sync_schema_from_models(engine: AsyncEngine) -> int:
    """
    Modellarda bor, DB da yo'q ustunlarni qo'shadi.
    Jadval/o'chirish/ustun o'zgartirish qilmaydi — faqat ADD COLUMN IF NOT EXISTS.
    """
    added = 0
    async with engine.begin() as conn:
        db_cols = await _load_public_columns(conn)

        for table_name, table in Base.metadata.tables.items():
            if table_name not in db_cols:
                continue
            existing = db_cols[table_name]
            for column in table.columns:
                if column.primary_key:
                    continue
                if column.name in existing:
                    continue
                if column.foreign_keys:
                    # FK ni alohida patch orqali qo'shish kerak bo'lishi mumkin
                    log.debug(
                        "schema auto-sync skip FK column %s.%s",
                        table_name,
                        column.name,
                    )
                    continue
                ddl = _build_add_column_sql(table_name, column)
                try:
                    await conn.execute(text(ddl))
                    existing.add(column.name)
                    added += 1
                    log.info("schema auto-sync: %s.%s", table_name, column.name)
                except Exception as e:
                    log.warning(
                        "schema auto-sync failed %s.%s: %s",
                        table_name,
                        column.name,
                        e,
                    )

    return added


def collect_model_vs_db_diff(engine_sync) -> list[tuple[str, str, str]]:
    """Debug: (table, column, sql) ro'yxati."""
    inspector = inspect(engine_sync)
    db_cols: dict[str, set[str]] = {}
    for table_name in inspector.get_table_names(schema="public"):
        db_cols[table_name] = {c["name"] for c in inspector.get_columns(table_name)}

    diff: list[tuple[str, str, str]] = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in db_cols:
            continue
        for column in table.columns:
            if column.primary_key or column.name in db_cols[table_name]:
                continue
            if column.foreign_keys:
                continue
            diff.append((table_name, column.name, _build_add_column_sql(table_name, column)))
    return diff

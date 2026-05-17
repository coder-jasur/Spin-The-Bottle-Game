"""Har 24 soatda adminlarga DB backup yuborish (admin paneldagi kabi)."""
from __future__ import annotations

import asyncio
import logging

from aiogram.types import BufferedInputFile
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.app.bot.admin_access import list_admin_telegram_chat_ids
from src.app.database.backup_restore import (
    backup_filename,
    dump_backup_bytes,
    export_database,
)

log = logging.getLogger("spinbottle.scheduled_backup")

_task: asyncio.Task | None = None


async def send_backup_to_admins(bot, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        payload = await export_database(session)
        meta = payload.get("meta") or {}
        blob = dump_backup_bytes(payload, compress=True)
        fname = backup_filename()
        caption = (
            "🕐 Scheduled DB backup\n"
            f"Tables: <b>{meta.get('table_count', 0)}</b>\n"
            f"Rows: <b>{meta.get('row_count', 0)}</b>"
        )
        chat_ids = await list_admin_telegram_chat_ids(session)
        for chat_id in chat_ids:
            try:
                doc = BufferedInputFile(blob, filename=fname)
                await bot.send_document(chat_id, doc, caption=caption)
            except Exception as e:
                log.warning("scheduled backup to %s failed: %s", chat_id, e)


async def _backup_loop(
    bot,
    session_factory: async_sessionmaker,
    interval_seconds: float,
) -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await send_backup_to_admins(bot, session_factory)
            log.info("Scheduled DB backup sent to admins")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduled backup failed")
        await asyncio.sleep(interval_seconds)


def start_scheduled_backup(
    bot,
    session_factory: async_sessionmaker,
    *,
    interval_hours: float = 24.0,
    enabled: bool = True,
) -> None:
    global _task
    if not enabled or bot is None:
        return
    if _task and not _task.done():
        return
    interval_seconds = max(3600.0, float(interval_hours) * 3600.0)
    _task = asyncio.create_task(
        _backup_loop(bot, session_factory, interval_seconds)
    )
    log.info("Scheduled backup started (every %.1f h)", interval_hours)


async def stop_scheduled_backup() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None

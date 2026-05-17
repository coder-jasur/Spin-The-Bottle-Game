"""Bot /admin_panel — DB backup va restore (faqat adminlar)."""
from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.bot.admin_access import get_user_by_telegram_id, is_telegram_admin
from src.app.bot.commands import refresh_admin_commands_for_chat
from src.app.bot.i18n import _, get_locale, set_locale
from src.app.core.language import bot_lang_from_db_user
from src.app.database.backup_restore import (
    backup_filename,
    dump_backup_bytes,
    export_database,
    load_backup_bytes,
    restore_full,
    restore_merge,
)

log = logging.getLogger("spinbottle.bot.admin_panel")
router = Router(name="admin_panel")

_CB_BACKUP = "adm:backup"
_CB_REFERRAL = "adm:referral"
_CB_RESTORE = "adm:restore"
_CB_MERGE = "adm:merge"
_CB_FULL = "adm:full"
_CB_CANCEL = "adm:cancel"

_PANEL_TITLE_MSGID = "🛠 <b>Admin panel</b>\n\nChoose an action:"
_BTN_BACKUP_MSGID = "📥 Create DB backup"
_BTN_RESTORE_MSGID = "📤 Restore from backup"
_BTN_REFERRAL_MSGID = "🤝 Referral & partners"
_ACCESS_DENIED_MSGID = "⛔ Access denied."
_BACKUP_WORKING_MSGID = "⏳ Creating backup, please wait…"
_BACKUP_DONE_MSGID = (
    "✅ Backup ready.\nTables: <b>%(tables)s</b>\nRows: <b>%(rows)s</b>"
)
_RESTORE_PROMPT_MSGID = (
    "📤 Send the backup file as a <b>document</b> "
    "(<code>.json.gz</code> or <code>.json</code>)."
)
_RESTORE_CHOOSE_MSGID = (
    "📂 Backup loaded.\nTables: <b>%(tables)s</b>, rows: <b>%(rows)s</b>\n\n"
    "Choose restore mode:"
)
_BTN_MERGE_MSGID = "➕ Restore missing rows only"
_BTN_FULL_MSGID = "⚠️ Full DB replace"
_BTN_CANCEL_MSGID = "❌ Cancel"
_MERGE_DONE_MSGID = "✅ Merge complete. Rows added: <b>%(inserted)s</b>"
_FULL_DONE_MSGID = "✅ Full restore complete. Rows restored: <b>%(rows)s</b>"
_FAILED_MSGID = "❌ Restore failed: %(error)s"
_INVALID_FILE_MSGID = "❌ Invalid backup file."
_FULL_CONFIRM_MSGID = (
    "⚠️ <b>Full replace</b> will erase current database data and load the "
    "backup. Continue?"
)
_BTN_CONFIRM_FULL_MSGID = "⚠️ Yes, replace all"
_CANCELLED_MSGID = "Cancelled."


class AdminRestoreState(StatesGroup):
    waiting_file = State()
    choosing_mode = State()
    confirm_full = State()


def _panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_(_BTN_BACKUP_MSGID), callback_data=_CB_BACKUP
                )
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_RESTORE_MSGID), callback_data=_CB_RESTORE
                )
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_REFERRAL_MSGID), callback_data=_CB_REFERRAL
                )
            ],
        ]
    )


def _restore_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_(_BTN_MERGE_MSGID), callback_data=_CB_MERGE
                )
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_FULL_MSGID), callback_data=_CB_FULL
                )
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_CANCEL_MSGID), callback_data=_CB_CANCEL
                )
            ],
        ]
    )


def _full_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_(_BTN_CONFIRM_FULL_MSGID), callback_data=_CB_FULL
                )
            ],
            [
                InlineKeyboardButton(
                    text=_(_BTN_CANCEL_MSGID), callback_data=_CB_CANCEL
                )
            ],
        ]
    )


async def _apply_admin_locale(session: AsyncSession, tg_id: int) -> None:
    user = await get_user_by_telegram_id(session, tg_id)
    set_locale(bot_lang_from_db_user(user))


async def _deny_if_not_admin(
    event: Message | CallbackQuery, session: AsyncSession
) -> bool:
    tg_user = event.from_user
    if not tg_user or not await is_telegram_admin(session, tg_user.id):
        text = _(_ACCESS_DENIED_MSGID)
        if isinstance(event, Message):
            await event.answer(text)
        else:
            await event.answer(text, show_alert=True)
        return True
    return False


async def _show_panel(message: Message) -> None:
    await message.answer(_(_PANEL_TITLE_MSGID), reply_markup=_panel_keyboard())


@router.message(Command("admin_panel"))
async def cmd_admin_panel(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    if await _deny_if_not_admin(message, session):
        return
    await _apply_admin_locale(session, message.from_user.id)
    try:
        await refresh_admin_commands_for_chat(
            message.bot, message.from_user.id, get_locale()
        )
    except Exception as e:
        log.debug("admin commands refresh: %s", e)
    await _show_panel(message)


@router.callback_query(F.data == _CB_BACKUP)
async def on_backup(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await state.clear()
    await cb.answer()
    await cb.message.answer(_(_BACKUP_WORKING_MSGID))
    try:
        payload = await export_database(session)
        meta = payload.get("meta") or {}
        blob = dump_backup_bytes(payload, compress=True)
        fname = backup_filename()
        doc = BufferedInputFile(blob, filename=fname)
        await cb.message.answer_document(
            doc,
            caption=_(
                _BACKUP_DONE_MSGID,
                tables=meta.get("table_count", len(payload.get("tables", {}))),
                rows=meta.get("row_count", 0),
            ),
        )
    except Exception as e:
        log.exception("admin backup failed")
        await cb.message.answer(_(_FAILED_MSGID, error=str(e)[:200]))


@router.callback_query(F.data == _CB_RESTORE)
async def on_restore_start(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    await state.set_state(AdminRestoreState.waiting_file)
    await cb.answer()
    await cb.message.answer(_(_RESTORE_PROMPT_MSGID))


@router.message(StateFilter(AdminRestoreState.waiting_file), F.document)
async def on_restore_file(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    if not message.from_user or await _deny_if_not_admin(message, session):
        return
    await _apply_admin_locale(session, message.from_user.id)
    doc = message.document
    if not doc:
        return
    name = (doc.file_name or "").lower()
    if not (name.endswith(".json") or name.endswith(".json.gz") or name.endswith(".gz")):
        await message.answer(_(_INVALID_FILE_MSGID))
        return
    if doc.file_size and doc.file_size > 48 * 1024 * 1024:
        await message.answer(_(_INVALID_FILE_MSGID))
        return
    try:
        buf = io.BytesIO()
        await message.bot.download(doc, destination=buf)
        payload = load_backup_bytes(buf.getvalue())
    except Exception as e:
        log.warning("admin restore file parse: %s", e)
        await message.answer(_(_INVALID_FILE_MSGID))
        return

    meta = payload.get("meta") or {}
    tables = payload.get("tables") or {}
    row_count = meta.get("row_count")
    if row_count is None:
        row_count = sum(len(v) for v in tables.values() if isinstance(v, list))

    tmp = Path(tempfile.gettempdir()) / f"adm_restore_{message.from_user.id}.json.gz"
    tmp.write_bytes(dump_backup_bytes(payload, compress=True))

    await state.update_data(backup_path=str(tmp), tables=len(tables), rows=row_count)
    await state.set_state(AdminRestoreState.choosing_mode)
    await message.answer(
        _(
            _RESTORE_CHOOSE_MSGID,
            tables=len(tables),
            rows=row_count,
        ),
        reply_markup=_restore_mode_keyboard(),
    )


@router.callback_query(
    StateFilter(AdminRestoreState.choosing_mode, AdminRestoreState.confirm_full),
    F.data.in_({_CB_MERGE, _CB_FULL, _CB_CANCEL}),
)
async def on_restore_mode(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if await _deny_if_not_admin(cb, session):
        return
    if not cb.message or not cb.from_user:
        return
    await _apply_admin_locale(session, cb.from_user.id)
    data = await state.get_data()
    path = data.get("backup_path")
    if cb.data == _CB_CANCEL:
        await state.clear()
        _cleanup_temp(path)
        await cb.answer()
        await cb.message.answer(_(_CANCELLED_MSGID))
        return

    if cb.data == _CB_FULL:
        cur = await state.get_state()
        if cur != AdminRestoreState.confirm_full.state:
            await state.set_state(AdminRestoreState.confirm_full)
            await cb.answer()
            await cb.message.answer(
                _(_FULL_CONFIRM_MSGID),
                reply_markup=_full_confirm_keyboard(),
            )
            return

    if not path:
        await cb.answer(_(_INVALID_FILE_MSGID), show_alert=True)
        return

    await cb.answer()
    await cb.message.answer(_(_BACKUP_WORKING_MSGID))
    try:
        raw = Path(path).read_bytes()
        payload = load_backup_bytes(raw)
        if cb.data == _CB_MERGE:
            inserted = await restore_merge(session, payload)
            await cb.message.answer(_(_MERGE_DONE_MSGID, inserted=inserted))
        else:
            total = await restore_full(session, payload)
            await cb.message.answer(_(_FULL_DONE_MSGID, rows=total))
    except Exception as e:
        log.exception("admin restore failed")
        await cb.message.answer(_(_FAILED_MSGID, error=str(e)[:200]))
    finally:
        await state.clear()
        _cleanup_temp(path)


def _cleanup_temp(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


@router.message(StateFilter(AdminRestoreState.waiting_file))
async def on_restore_waiting_non_document(
    message: Message, session: AsyncSession
) -> None:
    if await _deny_if_not_admin(message, session):
        return
    if message.from_user:
        await _apply_admin_locale(session, message.from_user.id)
    await message.answer(_(_RESTORE_PROMPT_MSGID))

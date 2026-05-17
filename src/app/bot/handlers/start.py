"""Bot /start — banner, welcome matn, Mini App PLAY tugmasi."""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from src.app.bot.i18n import _
from src.app.bot.miniapp_url import miniapp_index_url
from src.app.bot.telegram_safe import is_bot_blocked_by_user, log_bot_blocked
from src.app.core.config import load_config

log = logging.getLogger("spinbottle.bot.start")
router = Router(name="start")

_BANNER_DIR = Path(__file__).resolve().parents[1] / "assets"
_BANNER_PATH = _BANNER_DIR / "start_banner.png"
_BANNER_FILE_ID_PATH = _BANNER_DIR / "start_banner_file_id.txt"
_start_banner_file_id_cache: str | None = None

_START_CAPTION_MSGID = (
    "Hey! Get ready to dive into the ultimate Spin the Bottle adventure.\n\n"
    "👀 See who's around you and start a conversation\n"
    "🎁 Exchange fun surprises and playful dares\n"
    "💌 Send hidden messages to someone special\n"
    "🎲 Enjoy entertaining games together in the room\n"
    "🎶 Play along with music in the background\n\n"
    "🎮 Tap PLAY and let the fun start!\n\n"
    "⭐ Buy Stars: /store"
)


def _load_start_banner_file_id() -> str:
    if not _BANNER_FILE_ID_PATH.is_file():
        return ""
    return _BANNER_FILE_ID_PATH.read_text(encoding="utf-8").strip()


def get_start_banner_file_id() -> str:
    global _start_banner_file_id_cache
    if _start_banner_file_id_cache:
        return _start_banner_file_id_cache
    cfg = load_config()
    fid = (cfg.telegram_start_banner_file_id or "").strip()
    if not fid:
        fid = _load_start_banner_file_id()
    if fid:
        _start_banner_file_id_cache = fid
    return fid


def _play_keyboard(webapp_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_("🎮 PLAY"),
                    web_app=WebAppInfo(url=webapp_url),
                )
            ],
            [
                InlineKeyboardButton(
                    text=_("⭐ Stars store"),
                    callback_data="store_open",
                )
            ],
        ]
    )


async def _send_start(message: Message, kb: InlineKeyboardMarkup) -> None:
    caption = _(_START_CAPTION_MSGID)
    fid = get_start_banner_file_id()

    if fid:
        try:
            await message.answer_photo(
                photo=fid,
                caption=caption,
                reply_markup=kb,
            )
            return
        except Exception as e:
            if is_bot_blocked_by_user(e):
                raise
            log.warning(
                "/start banner file_id yuborilmadi (%s), fayl orqali uriniladi",
                e,
            )
            global _start_banner_file_id_cache
            _start_banner_file_id_cache = None

    if _BANNER_PATH.is_file():
        await message.answer_photo(
            photo=FSInputFile(_BANNER_PATH),
            caption=caption,
            reply_markup=kb,
        )
        return

    await message.answer(caption, reply_markup=kb)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    settings = load_config()
    webapp_url = miniapp_index_url(settings)
    if not webapp_url:
        await message.answer(
            _("Mini App is not configured yet. Set TELEGRAM_WEBAPP_URL in .env.")
        )
        log.warning("TELEGRAM_WEBAPP_URL yo'q — /start PLAY tugmasi ishlamaydi")
        return

    kb = _play_keyboard(webapp_url)
    try:
        await _send_start(message, kb)
    except TelegramForbiddenError:
        log_bot_blocked(message.chat.id if message.chat else None, context="/start")
    except Exception as e:
        if is_bot_blocked_by_user(e):
            log_bot_blocked(message.chat.id if message.chat else None, context="/start")
            return
        log.exception("/start xabar yuborilmadi")
        try:
            await message.answer(_(_START_CAPTION_MSGID), reply_markup=kb)
        except TelegramForbiddenError:
            log_bot_blocked(message.chat.id if message.chat else None, context="/start_fallback")

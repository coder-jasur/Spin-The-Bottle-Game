"""Telegram bot buyruqlari — / menyuda ko'rinadi (setMyCommands)."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

from src.app.bot.i18n import translate
from src.app.core.language import DEFAULT_LANG, SUPPORTED_LANGS, normalize_lang

log = logging.getLogger("spinbottle.bot.commands")

# Telegram API language_code (bizda kz→kk, tj→tg)
_TELEGRAM_LANG: dict[str, str] = {
    "uz": "uz",
    "ru": "ru",
    "en": "en",
    "tr": "tr",
    "az": "az",
    "kz": "kk",
    "tj": "tg",
}

_CMD_START = "🎮 Start the game"
_CMD_STORE = "⭐ Buy Stars"
_CMD_ADMIN_PANEL = "🛠 Admin panel"


def _public_commands_for_lang(lang: str) -> list[BotCommand]:
    return [
        BotCommand(command="start", description=translate(lang, _CMD_START)),
        BotCommand(command="store", description=translate(lang, _CMD_STORE)),
    ]


def _admin_commands_for_lang(lang: str) -> list[BotCommand]:
    cmds = list(_public_commands_for_lang(lang))
    cmds.append(
        BotCommand(
            command="admin_panel",
            description=translate(lang, _CMD_ADMIN_PANEL),
        )
    )
    return cmds


async def register_bot_commands(bot: Bot) -> None:
    """Har bir qo'llab-quvvatlanadigan til uchun /start va /store."""
    scopes = (
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
    )
    for scope in scopes:
        for lang in sorted(SUPPORTED_LANGS):
            tg_code = _TELEGRAM_LANG.get(lang, lang)
            await bot.set_my_commands(
                _public_commands_for_lang(lang),
                scope=scope,
                language_code=tg_code,
            )

    names = "/start, /store"
    log.info("Bot commands registered (%s langs): %s", len(SUPPORTED_LANGS), names)
    print(f"[OK] Telegram bot buyruqlari ({len(SUPPORTED_LANGS)} til): {names}", flush=True)


async def refresh_admin_commands_for_chat(
    bot: Bot, chat_id: int, lang: str | None = None
) -> None:
    """Faqat shu admin chatida /admin_panel ko'rinadi."""
    loc = normalize_lang(lang) if lang else DEFAULT_LANG
    await bot.set_my_commands(
        _admin_commands_for_lang(loc),
        scope=BotCommandScopeChat(chat_id=int(chat_id)),
    )



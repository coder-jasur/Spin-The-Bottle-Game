"""Telegram long-polling — tarmoq uzilganda qayta ulanish, shutdownda xatosiz to'xtash."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.utils.backoff import BackoffConfig

log = logging.getLogger("spinbottle.bot.polling")

_POLLING_BACKOFF = BackoffConfig(
    min_delay=1.0,
    max_delay=30.0,
    factor=1.4,
    jitter=0.1,
)
_OUTER_RETRY_SEC = 5.0


async def run_bot_polling(
    bot: Bot,
    dp: Dispatcher,
    *,
    shutdown_event: asyncio.Event,
) -> None:
    """`start_polling` ni o'rab oladi: TG tarmoq uzilsa server yiqilmaydi."""
    while not shutdown_event.is_set():
        try:
            await dp.start_polling(
                bot,
                handle_signals=False,
                close_bot_session=False,
                polling_timeout=30,
                backoff_config=_POLLING_BACKOFF,
            )
            if shutdown_event.is_set():
                return
            log.warning("TG polling to'xtadi, qayta ishga tushirilmoqda...")
        except asyncio.CancelledError:
            if shutdown_event.is_set():
                return
            raise
        except TelegramNetworkError as exc:
            if shutdown_event.is_set():
                return
            log.warning(
                "TG tarmoq uzildi (%s), %ss dan keyin qayta...",
                exc,
                _OUTER_RETRY_SEC,
            )
        except Exception as exc:
            if shutdown_event.is_set():
                return
            log.error("TG polling xatosi: %s", exc, exc_info=True)
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=_OUTER_RETRY_SEC,
            )
            return
        except asyncio.TimeoutError:
            continue


async def stop_bot_polling(dp: Dispatcher | None) -> None:
    if not dp:
        return
    try:
        await dp.stop_polling()
    except RuntimeError:
        pass
    except Exception as exc:
        log.debug("stop_polling: %s", exc)

"""
Telegram Stars (XTR) to'lovlar — invoice yuborish va muvaffaqiyatli to'lovdan keyin balans.
"""
from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import FSInputFile, LabeledPrice
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.bot.i18n import get_locale, translate
from src.app.bot.telegram_safe import is_bot_blocked_by_user, log_bot_blocked
from src.app.core.config import Settings, load_config
from src.app.database.repositories.game import GameRepository

log = logging.getLogger("spinbottle.tg_pay")

_bot: Optional[Bot] = None
_stars_banner_file_id_cache: str | None = None

_BANNER_DIR = Path(__file__).resolve().parents[1] / "bot" / "assets"
_BANNER_PATH = _BANNER_DIR / "stars_banner.png"
_BANNER_FILE_ID_PATH = _BANNER_DIR / "stars_banner_file_id.txt"
_DEFAULT_STARS_BANNER_FILE_ID = (
    "AgACAgIAAxkBAAPpagmXXEEadSjeJmU9v2OjIYlWO_QAAs0baxvaGVFIBSIUj1X7kv0BAAMCAAN5AAM7BA"
)
_STARS_BANNER_CAPTION_MSGID = (
    "⭐ <b>Spin Bottle</b>\n"
    "Top up Telegram Stars — then return to the game to shop."
)
_INVOICE_TITLE_MSGID = "Spin Bottle — %(stars)s ★"
_INVOICE_DESC_MSGID = (
    "%(stars)s Stars will be added to your account. "
    "After payment you can return to the game."
)
_INVOICE_LINK_DESC_MSGID = "%(stars)s Telegram Stars top-up"
_INVOICE_PRICE_LABEL_MSGID = "%(stars)s Stars"
# (db_id, stars_miqdori) → oxirgi invoice vaqti — boshqa paket uchun yangi chek mumkin
_invoice_cooldown: dict[tuple[int, int], float] = {}
INVOICE_COOLDOWN_SEC = 20
MIN_TOPUP_STARS = 1
MAX_TOPUP_STARS = 1_000_000
PAYLOAD_PREFIX = "st:"
HEARTS_PAYLOAD_PREFIX = "hp:"


def set_telegram_bot(bot: Optional[Bot]) -> None:
    global _bot
    _bot = bot


def get_telegram_bot() -> Optional[Bot]:
    return _bot


def build_invoice_payload(
    user_id: int,
    stars: int,
    *,
    hearts: int | None = None,
) -> str:
    nonce = secrets.token_hex(4)
    if hearts and hearts > 0:
        return f"{HEARTS_PAYLOAD_PREFIX}{user_id}:{hearts}:{stars}:{nonce}"
    return f"{PAYLOAD_PREFIX}{user_id}:{stars}:{nonce}"


def parse_invoice_payload(payload: str) -> Optional[tuple[int, int, int | None]]:
    """(db_user_id, stars, hearts_yoki_None)."""
    if not payload:
        return None
    prefix = None
    if payload.startswith(HEARTS_PAYLOAD_PREFIX):
        prefix = HEARTS_PAYLOAD_PREFIX
    elif payload.startswith(PAYLOAD_PREFIX):
        prefix = PAYLOAD_PREFIX
    else:
        return None
    parts = payload[len(prefix) :].split(":")
    try:
        uid = int(parts[0])
        if prefix == HEARTS_PAYLOAD_PREFIX:
            hearts = int(parts[1])
            stars = int(parts[2])
            if uid <= 0 or hearts <= 0 or stars < MIN_TOPUP_STARS or stars > MAX_TOPUP_STARS:
                return None
            return uid, stars, hearts
        stars = int(parts[1])
        if uid <= 0 or stars < MIN_TOPUP_STARS or stars > MAX_TOPUP_STARS:
            return None
        return uid, stars, None
    except (TypeError, ValueError, IndexError):
        return None


def parse_tg_hearts_product_id(product_id: str) -> Optional[tuple[int, int]]:
    """`...tg.hearts.{hearts}.{stars}[.welcome]` → (hearts, stars)."""
    if not product_id or ".hearts." not in product_id:
        return None
    parts = product_id.split(".")
    try:
        idx = parts.index("hearts")
        hearts = int(parts[idx + 1])
        stars = int(parts[idx + 2])
        return hearts, stars
    except (ValueError, IndexError):
        return None


def _load_persisted_banner_file_id() -> str:
    if not _BANNER_FILE_ID_PATH.is_file():
        return ""
    return _BANNER_FILE_ID_PATH.read_text(encoding="utf-8").strip()


def _persist_banner_file_id(file_id: str) -> None:
    _BANNER_FILE_ID_PATH.write_text(file_id.strip(), encoding="utf-8")


def get_stars_banner_file_id(settings: Settings | None = None) -> str:
    """Env yoki diskdagi cache — har safar fayl yuklamaslik uchun."""
    global _stars_banner_file_id_cache
    if _stars_banner_file_id_cache:
        return _stars_banner_file_id_cache
    cfg = settings or load_config()
    fid = (cfg.telegram_stars_banner_file_id or "").strip()
    if not fid:
        fid = _load_persisted_banner_file_id()
    if not fid:
        fid = _DEFAULT_STARS_BANNER_FILE_ID
    if fid:
        _stars_banner_file_id_cache = fid
    return fid


async def send_stars_banner_to_chat(
    bot: Bot, chat_id: int, *, lang: str | None = None
) -> bool:
    """Stars chekidan oldin banner: avval file_id, xato bo'lsa fayl orqali."""
    global _stars_banner_file_id_cache
    caption = translate(lang or get_locale(), _STARS_BANNER_CAPTION_MSGID)
    fid = get_stars_banner_file_id()

    if fid:
        try:
            await bot.send_photo(chat_id=chat_id, photo=fid, caption=caption)
            return True
        except TelegramForbiddenError:
            log_bot_blocked(chat_id, context="stars_banner")
            return False
        except Exception as e:
            if is_bot_blocked_by_user(e):
                log_bot_blocked(chat_id, context="stars_banner")
                return False
            log.warning(
                "Stars banner file_id yuborilmadi (%s), fayl orqali uriniladi",
                e,
            )
            _stars_banner_file_id_cache = None

    if not _BANNER_PATH.is_file():
        log.warning("Stars banner rasm topilmadi: %s", _BANNER_PATH)
        return False
    try:
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=FSInputFile(_BANNER_PATH),
            caption=caption,
        )
        new_fid = msg.photo[-1].file_id
        _stars_banner_file_id_cache = new_fid
        _persist_banner_file_id(new_fid)
        log.info("Stars banner fayl orqali yuborildi, yangi file_id saqlandi")
        return True
    except TelegramForbiddenError:
        log_bot_blocked(chat_id, context="stars_banner")
        return False
    except Exception as e:
        if is_bot_blocked_by_user(e):
            log_bot_blocked(chat_id, context="stars_banner")
            return False
        log.error("Stars banner yuborilmadi: %s", e)
        return False


def _cooldown_ok(db_id: int, stars: int) -> bool:
    key = (int(db_id), int(stars))
    now = time.time()
    last = _invoice_cooldown.get(key, 0.0)
    if now - last < INVOICE_COOLDOWN_SEC:
        return False
    _invoice_cooldown[key] = now
    return True


async def send_stars_invoice_to_chat(
    chat_id: int,
    db_user_id: int,
    stars: int,
    *,
    hearts: int | None = None,
    title: str | None = None,
    lang: str | None = None,
) -> bool:
    """Foydalanuvchiga Telegram Stars invoice (chek) yuboradi."""
    bot = get_telegram_bot()
    if not bot:
        log.warning("TG invoice: bot ulanmagan")
        return False

    stars = int(stars)
    if stars < MIN_TOPUP_STARS:
        return False
    if not _cooldown_ok(db_user_id, stars):
        log.info("TG invoice: cooldown db_id=%s stars=%s", db_user_id, stars)
        return False

    loc = lang or get_locale()
    payload = build_invoice_payload(db_user_id, stars, hearts=hearts)
    if hearts and hearts > 0:
        label = title or f"❤️ {hearts} — {stars} ★"
    else:
        label = title or translate(loc, _INVOICE_TITLE_MSGID, stars=stars)
    description = translate(loc, _INVOICE_DESC_MSGID, stars=stars)[:255]
    if hearts and hearts > 0:
        description = f"❤️ {hearts} — {description}"[:255]
    price_label = translate(loc, _INVOICE_PRICE_LABEL_MSGID, stars=stars)
    try:
        await send_stars_banner_to_chat(bot, chat_id, lang=loc)
        await bot.send_invoice(
            chat_id=chat_id,
            title=label[:32],
            description=description,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=price_label, amount=stars)],
        )
        log.info("TG invoice yuborildi chat=%s user=%s stars=%s", chat_id, db_user_id, stars)
        return True
    except TelegramForbiddenError:
        log_bot_blocked(chat_id, context="invoice")
        return False
    except Exception as e:
        if is_bot_blocked_by_user(e):
            log_bot_blocked(chat_id, context="invoice")
            return False
        log.error("TG send_invoice xato: %s", e)
        return False


async def create_stars_invoice_link(
    db_user_id: int,
    stars: int,
    *,
    lang: str | None = None,
    hearts: int | None = None,
    title: str | None = None,
) -> Optional[str]:
    """Mini App: WebApp.openInvoice uchun havola."""
    bot = get_telegram_bot()
    if not bot:
        return None
    stars = int(stars)
    if stars < MIN_TOPUP_STARS:
        return None
    loc = lang or get_locale()
    payload = build_invoice_payload(db_user_id, stars, hearts=hearts)
    try:
        inv_title = (title or translate(loc, _INVOICE_TITLE_MSGID, stars=stars))[:32]
        inv_desc = translate(loc, _INVOICE_LINK_DESC_MSGID, stars=stars)[:255]
        if hearts and hearts > 0:
            inv_desc = f"❤️ {hearts} — {inv_desc}"[:255]
        return await bot.create_invoice_link(
            title=inv_title,
            description=inv_desc,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=translate(loc, _INVOICE_PRICE_LABEL_MSGID, stars=stars),
                    amount=stars,
                )
            ],
        )
    except Exception as e:
        log.error("TG create_invoice_link xato: %s", e)
        return None


async def apply_successful_stars_payment(
    session: AsyncSession,
    *,
    user_id: int,
    stars: int,
    charge_id: str,
    telegram_payment_charge_id: str | None = None,
) -> tuple[bool, int, int, int]:
    """
    To'lovdan keyin stars_coin va gift_tokens (1:1) to'ldiriladi.
    (ok, stars_coin, gift_tokens, hearts)
    """
    repo = GameRepository(session)
    await repo.ensure_wallet(user_id)
    charge_key = telegram_payment_charge_id or charge_id
    return await repo.apply_tg_stars_topup(user_id, stars, charge_key)


async def notify_player_topup(
    user_id: int,
    stars: int,
    stars_coin: int,
    gift_tokens: int,
    hearts: int,
) -> None:
    from src.app.api.ws.game_manager import manager

    player = manager.find_player_by_db_id(user_id)
    if not player:
        return
    player.apply_wallet_balances(
        hearts=hearts,
        stars_coin=stars_coin,
        gift_tokens=gift_tokens,
    )
    await manager._push_wallet_sync(player)
    await manager.send_to(
        player,
        {
            "type": "stars_topup_ok",
            "amount": stars,
            "stars_coin": stars_coin,
            "tokens": gift_tokens,
            "gold": hearts,
            "goldReal": hearts,
            "ts": manager._ts(),
        },
    )

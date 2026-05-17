"""Telegram Mini App taklif havolalari (`startapp` = users.referral_id)."""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

# Shablon yoki bo'sh qiymatlar — bazaga yozilmaydi
_INVALID_START = frozenset(
    {
        "",
        "none",
        "null",
        "undefined",
        "<ref_id>",
        "ref_id",
        "%3Cref_id%3E",
    }
)


def normalize_start_param(raw: Optional[str]) -> Optional[str]:
    """Telegram `start_param` ni referral_id sifatida ishlatishdan oldin tozalash."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    low = s.lower()
    if low in _INVALID_START:
        return None
    return s


def build_telegram_invite_bundle(
    referral_id: str,
    *,
    bot_username: str,
    mini_slug: str,
    share_text: str,
) -> dict[str, str]:
    """
    - telegram_mini_app_url: ochiladigan Mini App (startapp = referral_id)
    - telegram_share_url: t.me/share/url orqali do'stga yuborish
    """
    bot = (bot_username or "").strip().lstrip("@")
    slug = (mini_slug or "").strip().strip("/")
    rid = str(referral_id).strip()
    mini = f"https://t.me/{bot}/{slug}?" + urlencode({"startapp": rid})
    share = "https://t.me/share/url?" + urlencode({"url": mini, "text": share_text or ""})
    return {
        "referral_id": rid,
        "telegram_mini_app_url": mini,
        "telegram_share_url": share,
    }

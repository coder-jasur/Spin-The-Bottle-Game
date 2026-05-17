"""Google Translate (mobil web) — API kalitsiz, chat `translate` WS."""
from __future__ import annotations

import logging
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup

from src.app.core.language import normalize_lang

log = logging.getLogger("spinbottle.translate")

# Bizning til kodlari → Google `sl` / `tl`
_GOOGLE_LANG: dict[str, str] = {
    "uz": "uz",
    "ru": "ru",
    "en": "en",
    "tr": "tr",
    "az": "az",
    "kz": "kk",
    "tj": "tg",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
}


def to_google_lang(code: str | None) -> str:
    lang = normalize_lang(code)
    return _GOOGLE_LANG.get(lang, lang)


async def translate(
    text: str,
    language_to: str,
    language_from: str = "auto",
) -> str:
    """https://translate.google.com/m — manba til odatda `auto`."""
    raw = (text or "").strip()
    if not raw:
        return ""

    tl = to_google_lang(language_to)
    sl = "auto" if not language_from or language_from == "auto" else to_google_lang(language_from)
    url = (
        "https://translate.google.com/m"
        f"?tl={quote(tl, safe='')}&sl={quote(sl, safe='')}&q={quote(raw)}"
    )

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                html = await response.text()
    except Exception as e:
        log.error("Google Translate so'rov xato: %s", e)
        return raw

    try:
        soup = BeautifulSoup(html, "html.parser")
        node = soup.find("div", attrs={"class": "result-container"})
        if node is None:
            node = soup.select_one("div.result-container")
        if node is None:
            log.warning("Google Translate: result-container topilmadi")
            return raw
        out = node.get_text(separator=" ", strip=True)
        return out or raw
    except Exception as e:
        log.error("Google Translate parse xato: %s", e)
        return raw


async def translate_text(
    text: str,
    *,
    target_lang: str,
    source_lang: str | None = None,
) -> str:
    """WS handler uchun: `language_to` = foydalanuvchi tili, `language_from` = auto."""
    from_lang = "auto" if not source_lang else source_lang
    return await translate(text, language_to=target_lang, language_from=from_lang)

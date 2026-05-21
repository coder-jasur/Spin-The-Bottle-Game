"""Qo'llab-quvvatlanadigan tillar: default ru, mos kelmasa ru."""
from __future__ import annotations

DEFAULT_LANG = "ru"
SUPPORTED_LANGS = frozenset({"uz", "ru", "en", "tr", "az", "tj", "kz"})

_LANG_ALIASES: dict[str, str] = {
    "kk": "kz",
    "kaz": "kz",
    "tg": "tj",
    "tjk": "tj",
    "aze": "az",
    "tur": "tr",
    "eng": "en",
    "rus": "ru",
    "uzb": "uz",
}

LOCALE_MAP: dict[str, str] = {
    "uz": "uz_UZ",
    "kz": "kz_KZ",
    "tj": "tj_TJ",
    "en": "en_US",
    "az": "az_AZ",
    "tr": "tr_TR",
    "ru": "ru_RU",
}


def parse_supported_lang(raw: str | None) -> str | None:
    """Mos til kodini qaytaradi; qo'llab-quvvatlanmasa None."""
    if not raw:
        return None
    code = str(raw).strip().lower().replace("_", "-")
    if not code:
        return None
    primary = code.split("-")[0]
    if primary in SUPPORTED_LANGS:
        return primary
    if primary in _LANG_ALIASES:
        mapped = _LANG_ALIASES[primary]
        if mapped in SUPPORTED_LANGS:
            return mapped
    return None


def normalize_lang(raw: str | None) -> str:
    """uz|ru|en|tr|az|tj|kz yoki default ru."""
    return parse_supported_lang(raw) or DEFAULT_LANG


def to_game_locale(lang: str | None) -> str:
    """O'yin URL/WS: uz_UZ, ru_RU, en_US, ... (klient birinchi 2 harfni oladi)."""
    code = normalize_lang(lang)
    return LOCALE_MAP.get(code, LOCALE_MAP[DEFAULT_LANG])


def resolve_user_lang(
    *,
    telegram_language_code: str | None = None,
    cookie_lang: str | None = None,
    db_language_code: str | None = None,
) -> str:
    """Prioritet: Telegram → cookie → DB; hech biri mos kelmasa ru."""
    for candidate in (telegram_language_code, cookie_lang, db_language_code):
        lang = parse_supported_lang(candidate)
        if lang:
            return lang
    return DEFAULT_LANG


def bot_lang_from_db_user(user: object | None) -> str:
    """Bot push/xabar: faqat oluvchining DB `users.language_code` (mas. uz_UZ → uz)."""
    if not user:
        return DEFAULT_LANG
    raw = getattr(user, "language_code", None)
    return normalize_lang(raw if raw is not None else None)


def resolve_translate_target_lang(
    *,
    request_lang: str | None = None,
    player_locale: str | None = None,
    player_language: str | None = None,
) -> str:
    """Chat tarjimasi: so'rov → faol locale (uz_UZ) → language; noto'g'ri kod → ru."""
    for candidate in (request_lang, player_locale, player_language):
        lang = parse_supported_lang(candidate)
        if lang:
            return lang
    return DEFAULT_LANG


def sync_player_language_from_locale(player: object) -> None:
    """`locale` yangilanganda `language` ni ham moslashtirish (tarjima uchun)."""
    locale = getattr(player, "locale", None)
    lang = parse_supported_lang(locale)
    if lang:
        setattr(player, "language", lang)


def apply_player_locale(player: object, raw_locale: str | None) -> bool:
    """O'yinchining faol tilini yangilash (uz_UZ, ru_RU, ...)."""
    lang = parse_supported_lang(raw_locale)
    if not lang:
        return False
    setattr(player, "locale", to_game_locale(lang))
    sync_player_language_from_locale(player)
    return True

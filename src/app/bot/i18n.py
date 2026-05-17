"""Bot xabarlari — GNU gettext (Babel .po/.mo)."""
from __future__ import annotations

import gettext
from contextvars import ContextVar
from pathlib import Path

from src.app.core.language import DEFAULT_LANG, normalize_lang

LOCALES_DIR = Path(__file__).resolve().parents[1] / "locales"
_DOMAIN = "bot"

_locale_ctx: ContextVar[str] = ContextVar("bot_locale", default=DEFAULT_LANG)
_catalogs: dict[str, gettext.GNUTranslations] = {}
_fallback: gettext.GNUTranslations | None = None


def _load_catalog(lang: str) -> gettext.GNUTranslations:
    code = normalize_lang(lang)
    if code in _catalogs:
        return _catalogs[code]
    try:
        tr = gettext.translation(
            _DOMAIN,
            localedir=str(LOCALES_DIR),
            languages=[code],
        )
    except FileNotFoundError:
        global _fallback
        if _fallback is None:
            _fallback = gettext.translation(
                _DOMAIN,
                localedir=str(LOCALES_DIR),
                languages=[DEFAULT_LANG],
                fallback=True,
            )
        tr = _fallback
    _catalogs[code] = tr
    return tr


def set_locale(lang: str | None) -> None:
    _locale_ctx.set(normalize_lang(lang))


def get_locale() -> str:
    return _locale_ctx.get()


def translate(lang: str | None, message: str, **kwargs: object) -> str:
    tr = _load_catalog(lang or get_locale())
    text = tr.gettext(message)
    if kwargs:
        try:
            text = text % kwargs
        except (KeyError, TypeError, ValueError):
            pass
    return text


def _(message: str, **kwargs: object) -> str:
    """Joriy kontekst tilida matn (middleware `locale` o'rnatadi)."""
    return translate(get_locale(), message, **kwargs)


def ngettext(singular: str, plural: str, n: int, **kwargs: object) -> str:
    tr = _load_catalog(get_locale())
    text = tr.ngettext(singular, plural, n)
    if kwargs:
        try:
            text = text % kwargs
        except (KeyError, TypeError, ValueError):
            pass
    return text

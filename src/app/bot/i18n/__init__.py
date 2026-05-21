"""Bot tarjimalari (gettext)."""
from src.app.bot.i18n.core import (
    LOCALES_DIR,
    _,
    get_locale,
    ngettext,
    set_locale,
    translate,
)

__all__ = [
    "LOCALES_DIR",
    "_",
    "get_locale",
    "ngettext",
    "set_locale",
    "translate",
]

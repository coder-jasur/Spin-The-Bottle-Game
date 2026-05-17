"""O'yinchi nomi: ko'rinadigan belgilar (grapheme) bo'yicha cheklov."""
from __future__ import annotations

import re

# Do'kon / profil: klient ham 15 ta «belgi» deb ko'rsatadi
MAX_GAME_USERNAME_GRAPHEMES = 15
# DB va WS: xavfsiz yuqori chegar (UTF-16 uzunligi emas)
MAX_GAME_USERNAME_STORAGE = 64

# Bo'sh, bosh/oxir probel, boshqaruv belgilari
_INVALID_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def username_grapheme_len(name: str) -> int:
    """Ko'rinadigan belgilar soni (surrogate juftlari 1 ta hisoblanadi)."""
    return len(list((name or "").strip()))


def normalize_game_username(raw: str) -> str:
    s = str(raw or "").strip().lstrip("@")
    s = _INVALID_CHARS_RE.sub("", s)
    return s[:MAX_GAME_USERNAME_STORAGE]


def validate_game_username(name: str) -> str | None:
    """
    None — OK; aks holda xato matni (HTTP 400 uchun).
    """
    s = normalize_game_username(name)
    if not s:
        return "O'yin nomi bo'sh bo'lmasligi kerak"
    n = username_grapheme_len(s)
    if n > MAX_GAME_USERNAME_GRAPHEMES:
        return (
            f"O'yin nomi {MAX_GAME_USERNAME_GRAPHEMES} ta belgidan oshmasligi kerak "
            f"(hozir {n})"
        )
    return None

"""Input validatsiya — SQL injection va XSS uchun (ORM bilan birga)."""
from __future__ import annotations

import re
from typing import Optional

# SQL/operator belgilarini qidiruv matnidan olib tashlash (ORM parametrlashtirilgan bo'lsa ham)
_UNSAFE_SEARCH_RE = re.compile(r"['\";\\]|--|/\*|\*/|@@|char\(|concat\(|union\s|select\s|insert\s|update\s|delete\s|drop\s", re.I)
_USERNAME_RE = re.compile(r"^[\w.\-]{3,32}$", re.UNICODE)


def sanitize_search_text(raw: str, *, max_len: int = 64) -> str:
    """Admin/qidiruv: qisqa, xavfsiz matn."""
    s = (raw or "").strip()
    if len(s) > max_len:
        s = s[:max_len]
    s = _UNSAFE_SEARCH_RE.sub("", s)
    return s.strip()


def validate_username(raw: str) -> Optional[str]:
    """Login/username: faqat harf, raqam, . _ -"""
    s = (raw or "").strip()
    if not _USERNAME_RE.match(s):
        return None
    return s


def clamp_int(value: object, *, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))

"""Birinchi kirishda profil (yosh, jins) so'rovi."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from src.app.api.ws.player import parse_birth_date_ms

# Klient (TG ro'yxatdan o'tish): yosh tanlanmasa `age: r || 1e3` yuboriladi.
CLIENT_INVALID_AGE_PLACEHOLDER = 1000
PROFILE_AGE_MIN = 10
PROFILE_AGE_MAX = 99


def is_valid_profile_age(age: int) -> bool:
    return PROFILE_AGE_MIN <= int(age) <= PROFILE_AGE_MAX


def normalize_profile_age(raw: object) -> Optional[int]:
    """10–99 oralig'idagi yosh yoki None (1000 placeholder ham rad)."""
    if raw is None:
        return None
    try:
        age = int(raw)
    except (TypeError, ValueError):
        return None
    if age == CLIENT_INVALID_AGE_PLACEHOLDER:
        return None
    if not is_valid_profile_age(age):
        return None
    return age


def compute_age_from_birth_date(birth_date_raw: object) -> Optional[int]:
    """Tug'ilgan kundan yosh; 10–99 dan tashqari bo'lsa None."""
    ts_ms = parse_birth_date_ms(birth_date_raw)
    if not ts_ms:
        return None
    d_birth = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
    today = datetime.now(timezone.utc).date()
    age = (
        today.year
        - d_birth.year
        - ((today.month, today.day) < (d_birth.month, d_birth.day))
    )
    return normalize_profile_age(max(0, age))


def effective_profile_age(user: Any) -> int:
    """DB foydalanuvchi uchun ishlatiladigan yosh (birth_date ustun)."""
    if user is None:
        return 0
    from_bd = compute_age_from_birth_date(getattr(user, "birth_date", None))
    if from_bd is not None:
        return from_bd
    norm = normalize_profile_age(getattr(user, "age", None))
    return norm if norm is not None else 0


def user_needs_profile_setup(user: Any) -> bool:
    """Yosh tanlanmagan yoki noto'g'ri (masalan 1000) bo'lsa — profil dialogi kerak."""
    if user is None:
        return False
    return effective_profile_age(user) <= 0

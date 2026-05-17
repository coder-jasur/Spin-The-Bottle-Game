"""Birinchi kirishda profil (yosh, jins) so'rovi."""
from __future__ import annotations

from typing import Any


def user_needs_profile_setup(user: Any) -> bool:
    """Yosh tanlanmagan bo'lsa — o'yin oldidan profil dialogi kerak."""
    if user is None:
        return False
    age = getattr(user, "age", None)
    if age is None:
        return True
    try:
        return int(age) <= 0
    except (TypeError, ValueError):
        return True

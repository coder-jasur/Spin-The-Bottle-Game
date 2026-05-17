"""
Stollar: davlat + global (ALL) ajratish, UI da qadam-baqadam ochilish.
"""
from __future__ import annotations

import hashlib

# Har bir davlat uchun 150 ta slot (room_id = base + 1 .. base + 150)
COUNTRY_ROOM_SLOTS = 150
GLOBAL_ROOM_SLOTS = 20
# Global stollar (country_code ALL — DB da ba'zan "all" kichik harf)
GLOBAL_TABLE_BASE = 5500

# Tanilgan mamlakatlar — room_id diapazonlari to'qnashmasin (max ~14.5k)
COUNTRY_TABLE_BASE: dict[str, int] = {
    "UZBEKISTAN": 1000,
    "KAZAKHSTAN": 2500,
    "KAZAKSTAN": 2500,
    "RUSSIA": 4000,
    "UNITED STATES": 7000,
    "USA": 7000,
    "AMERICA": 7000,
    "TURKEY": 10000,
    "TÜRKIYE": 10000,
    "TURKISTAN": 10000,
    "AZERBAIJAN": 11500,
    "KYRGYZSTAN": 13000,
    "TAJIKISTAN": 14500,
}

# Startup / recreate_db: stollar yaratiladigan mamlakatlar (+ ALL → global)
DEFAULT_SEED_COUNTRY_CODES: tuple[str, ...] = (
    "UZBEKISTAN",
    "KAZAKHSTAN",
    "RUSSIA",
    "AZERBAIJAN",
    "TURKEY",
    "USA",
    "TAJIKISTAN",
    "ALL",
)

# Ro'yxatda boshlang'ich ko'rinadigan stollar; keyingisi ochilishi uchun oxirgi ko'rinadigan stolda o'yinchilar soni
BASE_VISIBLE_COUNTRY = 3
BASE_VISIBLE_GLOBAL = 3
# Stol to'lganida (yoki deyarli to'lganida) keyingi xona ro'yxatda paydo bo'ladi
BUSY_THRESHOLD_PLAYERS = 12


def normalize_country_code(c: str | None) -> str:
    s = (c or "UZBEKISTAN").strip().upper()
    return s if s else "UZBEKISTAN"


def is_global_country_code(code: str | None) -> bool:
    return str(code or "").strip().upper() == "ALL"


def country_room_id_base(country: str) -> int:
    """Noma'lum mamlakat uchun hash orqali 30k+ diapazon (tanlangan bazalar bilan to'qnashmaydi)."""
    c = normalize_country_code(country)
    if c in COUNTRY_TABLE_BASE:
        return COUNTRY_TABLE_BASE[c]
    h = int(hashlib.sha256(c.encode("utf-8")).hexdigest()[:8], 16)
    return 30000 + (h % 400) * 200


def global_room_id_bounds() -> tuple[int, int]:
    lo = GLOBAL_TABLE_BASE + 1
    hi = GLOBAL_TABLE_BASE + GLOBAL_ROOM_SLOTS
    return lo, hi


def visible_room_prefix_len(
    counts_in_room_order: list[int],
    *,
    max_rooms: int,
    base_visible: int = BASE_VISIBLE_COUNTRY,
    busy_threshold: int = BUSY_THRESHOLD_PLAYERS,
) -> int:
    """
    counts_in_room_order[i] — i-stol (room_id bo'yicha tartiblangan) dagi online o'yinchilar.
    Dastlab `base_visible` ta ko'rinadi; ketma-ketlikda i-stol (1-based) band bo'lsa i+1 ham ko'rinadi.
    """
    if not counts_in_room_order:
        return 0
    n = min(len(counts_in_room_order), max(0, max_rooms))
    v = min(max(base_visible, 0), n)
    if v == 0:
        return 0
    while v < n:
        if counts_in_room_order[v - 1] >= busy_threshold:
            v += 1
        else:
            break
    return v


def player_may_join_room_row(
    player_country: str,
    room_country_code: str,
    *,
    is_guest: bool = False,
) -> bool:
    if is_global_country_code(room_country_code):
        return True
    if is_guest:
        return room_country_code == normalize_country_code(player_country)
    return room_country_code == normalize_country_code(player_country)

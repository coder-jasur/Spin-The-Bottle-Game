"""
Stollar: davlat + global (ALL) ajratish, UI da qadam-baqadam ochilish.
"""
from __future__ import annotations

import hashlib
import re

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

# Ro'yxatda boshlang'ich ko'rinadigan stollar
BASE_VISIBLE_COUNTRY = 3
BASE_VISIBLE_GLOBAL = 3
# Har stol sig'imi (WS MAX_SEATS bilan bir xil)
ROOM_SEAT_CAPACITY = 12
# Ko'rinadigan stollar yig'indisi shu ulush to'lganda keyingi stol ochiladi (3×12×0.5 = 18 kishi)
AGGREGATE_OPEN_FILL_RATIO = 0.5
# Eski nom: bitta stol bandligi (endi aggregate mantiqda ishlatilmaydi)
BUSY_THRESHOLD_PLAYERS = ROOM_SEAT_CAPACITY


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


def aggregate_open_player_threshold(
    visible_count: int,
    *,
    seat_capacity: int = ROOM_SEAT_CAPACITY,
    fill_ratio: float = AGGREGATE_OPEN_FILL_RATIO,
) -> int:
    """Ko'rinadigan `visible_count` stol uchun keyingisini ochish chegarasi (jami o'yinchilar)."""
    if visible_count <= 0:
        return 0
    return int(visible_count * seat_capacity * fill_ratio)


def visible_room_prefix_len(
    counts_in_room_order: list[int],
    *,
    max_rooms: int,
    base_visible: int = BASE_VISIBLE_COUNTRY,
    seat_capacity: int = ROOM_SEAT_CAPACITY,
    fill_ratio: float = AGGREGATE_OPEN_FILL_RATIO,
) -> int:
    """
    counts_in_room_order[i] — i-stol (id bo'yicha tartib) dagi online (o'tirgan + navbat).
    Dastlab `base_visible` ta ko'rinadi; ko'rinadiganlar yig'indisi
    `visible * seat_capacity * fill_ratio` ga yetganda keyingi stol ham ochiladi
    (masalan 3 stol, jami 18 kishi → 4-chi stol).
    """
    if not counts_in_room_order:
        return 0
    n = min(len(counts_in_room_order), max(0, max_rooms))
    v = min(max(base_visible, 0), n)
    if v == 0:
        return 0
    while v < n:
        total = sum(counts_in_room_order[:v])
        need = aggregate_open_player_threshold(
            v, seat_capacity=seat_capacity, fill_ratio=fill_ratio
        )
        if total >= need:
            v += 1
        else:
            break
    return v


def global_room_slot_number(room_name: str | None, *, fallback: int = 1) -> int:
    """DB nomidan global stol raqami: G15 → 15, `GLOBAL #3` → 3."""
    s = (room_name or "").strip()
    if not s:
        return fallback
    if s.upper().startswith("G") and s[1:].isdigit():
        return int(s[1:])
    m = re.search(r"#\s*(\d+)", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if s.isdigit():
        return int(s)
    return fallback


def room_display_name(
    room: object,
    *,
    global_slot_fallback: int = 1,
) -> str:
    """Klient ro'yxati: global → 🌍 GLOBAL #N, mamlakat → DB name."""
    code = getattr(room, "country_code", None)
    if is_global_country_code(code):
        n = global_room_slot_number(
            getattr(room, "name", None),
            fallback=global_slot_fallback,
        )
        return f"🌍 GLOBAL #{n}"
    name = getattr(room, "name", None)
    return str(name) if name else str(getattr(room, "id", ""))


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

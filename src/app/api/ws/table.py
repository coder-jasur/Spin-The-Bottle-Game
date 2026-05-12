"""
Table — bitta o'yin stoli va uning holat mashinasi.
"""
import asyncio
import time
import random
from typing import Dict, Optional, List, TYPE_CHECKING
from src.app.api.ws.constants import (
    STATE_WAIT,
    STATE_SPINNING,
    STATE_OFFER,
    STATE_SELECT,
    MAX_SEATS,
    BOTTLE_TYPES,
    DEFAULT_BOTTLE_TYPE,
)


if TYPE_CHECKING:
    from src.app.api.ws.player import Player


def normalize_ws_user_ref(raw) -> str:
    """Klientdan kelgan user id ni stol kaliti bilan solishtirish uchun qatorga."""
    if raw is None:
        return ""
    if isinstance(raw, dict):
        inner = raw.get("id")
        if inner is None:
            inner = raw.get("userId")
        raw = inner
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return ""
    if isinstance(raw, float):
        if raw != raw:  # NaN
            return ""
        raw = int(raw) if raw == int(raw) else raw
    s = str(raw).strip()
    if not s or s.lower() == "null":
        return ""
    return s


class Table:
    # HTML5 klient: viewerControls C.add(6, ...) + timer uzunligi 9 → 6+9=15 s
    TURN_OFFER_TIMEOUT = 15
    # game_kiss / game_refuse dan keyin keyingi navbat (animatsiya + tanaffus)
    POST_RESOLVE_PAUSE_SEC = 8.0
    SPIN_DURATION = 3  # (faqat ma'lumot; prod klient aylanishni o‘zi boshqaradi)

    def __init__(self, table_id: str):
        self.table_id   = table_id
        self.players: Dict[str, "Player"] = {}   # user_id → Player
        self.state      = STATE_WAIT
        # Navbat kimda (aylantirish huquqi) — faqat _advance_bottle / init da o'zgaradi
        self.turn_seat: int = 0
        # Butilka qaysi o'rinni ko'rsatadi (animatsiya / OFFER) — start_spin da nishonga o'tadi
        self.bottle_seat: int = 0
        self.current_spinner: Optional[str] = None   # kim aylantirmoqda
        self.current_target:  Optional[str] = None   # kim tanlandi
        # wait_offer: HTML5 klient ikkala juftning tanlovini kutadi ("Kiss" / "NoKiss")
        self.spinner_choice: Optional[str] = None
        self.target_choice: Optional[str] = None
        # Juftlik yakunlanmoqda — ikki o‘yinchi bir vaqtda bosishidan saqlanish
        self.resolving: bool = False
        self.room_kiss_count  = 0
        self.bottle_type      = (
            DEFAULT_BOTTLE_TYPE
            if DEFAULT_BOTTLE_TYPE in BOTTLE_TYPES
            else "standart"
        )
        self._turn_task: Optional[asyncio.Task] = None
        self._spin_task: Optional[asyncio.Task] = None
        self._auto_spin_task: Optional[asyncio.Task] = None
        self._offer_timeout_task: Optional[asyncio.Task] = None

    def cancel_auto_spin_task(self):
        if self._auto_spin_task and not self._auto_spin_task.done():
            self._auto_spin_task.cancel()
            self._auto_spin_task = None

    def schedule_auto_spin_task(self, coro):
        self.cancel_auto_spin_task()
        self._auto_spin_task = asyncio.create_task(coro)

    def cancel_offer_timeout_task(self):
        if self._offer_timeout_task and not self._offer_timeout_task.done():
            self._offer_timeout_task.cancel()
        self._offer_timeout_task = None

    def schedule_offer_timeout_task(self, coro):
        self.cancel_offer_timeout_task()
        self._offer_timeout_task = asyncio.create_task(coro)

    # ── Seat management ────────────────────────────────────────────────────
    def next_free_seat(self) -> int:
        occupied = {p.seat for p in self.players.values()}
        for s in range(MAX_SEATS):
            if s not in occupied:
                return s
        return 0

    def add_player(self, player: "Player"):
        player.seat     = self.next_free_seat()
        player.table_id = self.table_id
        self.players[player.id] = player

    def remove_player(self, user_id: str):
        self.players.pop(user_id, None)
        # Agar o'yin davomidagi ishtirokchi ketsa — reset
        if user_id in (self.current_spinner, self.current_target):
            asyncio.create_task(self._cancel_turn())

    # ── Helpers ────────────────────────────────────────────────────────────
    def get_player(self, user_id: str) -> Optional["Player"]:
        return self.players.get(user_id)

    def resolve_player_key(self, raw) -> Optional[str]:
        """Klient refini players lug‘ati kalitiga moslashtiradi (tur/format farqi uchun)."""
        s = normalize_ws_user_ref(raw)
        if not s:
            return None
        if s in self.players:
            return s
        try:
            f = float(s)
            if abs(f - int(f)) < 1e-9:
                cand = str(int(f))
                if cand in self.players:
                    return cand
        except (ValueError, OverflowError):
            pass
        try:
            n = int(float(s))
            for pid, pl in self.players.items():
                if pl.db_id is not None and pl.db_id == n:
                    return pid
                try:
                    if int(pid) == n:
                        return pid
                except (ValueError, TypeError):
                    continue
        except (ValueError, TypeError, OverflowError):
            pass
        return None

    def get_player_flexible(self, raw) -> Optional["Player"]:
        key = self.resolve_player_key(raw)
        return self.players.get(key) if key else None

    def all_participants(self) -> List[dict]:
        return [p.to_participant() for p in self.players.values()]

    def player_count(self) -> int:
        return len(self.players)

    # ── Turn machine ───────────────────────────────────────────────────────
    def can_spin(self, user_id: str) -> bool:
        return (
            self.state == STATE_WAIT
            and user_id in self.players
            and self.turn_seat == self.players[user_id].seat
        )

    def start_spin(self, spinner_id: str) -> int:
        """
        Butilkani aylantiradi. Target (nishon) sifatida qarama-qarshi
        jins vakilini tanlashga harakat qiladi.
        """
        self.state          = STATE_SPINNING
        self.current_spinner= spinner_id
        spinner = self.players[spinner_id]

        # Qarama-qarshi jins vakillarini qidiramiz
        target_gender = "female" if spinner.gender == "male" else "male"
        opposites = [p for uid, p in self.players.items() if uid != spinner_id and p.gender == target_gender]

        if opposites:
            # Qarama-qarshi jins bor bo'lsa, ulardan birini tanlaymiz
            target = random.choice(opposites)
        else:
            # Aks holda o'zidan boshqa istalgan kishini
            other = [p for uid, p in self.players.items() if uid != spinner_id]
            target = random.choice(other) if other else spinner

        self.current_target = target.id
        # Vizual: butilka nishonga; navbat (turn_seat) o'zgarmaydi
        self.bottle_seat = target.seat
        return target.seat

    def offer_turn(self) -> None:
        self.state = STATE_OFFER
        self.spinner_choice = None
        self.target_choice = None

    def select_turn(self) -> None:
        self.state = STATE_SELECT

    def reset_turn(self) -> None:
        self.state            = STATE_WAIT
        self.current_spinner  = None
        self.current_target   = None
        self.spinner_choice   = None
        self.target_choice    = None
        self.resolving        = False

    async def _cancel_turn(self):
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self.reset_turn()
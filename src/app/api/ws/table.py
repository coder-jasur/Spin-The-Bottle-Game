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
    # Navbat berilgach 6 s ichida spin bo'lmasa — server o'zi spin qiladi (AUTO_SPIN_IDLE_SEC)
    AUTO_SPIN_IDLE_SEC = 6.0
    # game_kiss / game_refuse dan keyin keyingi navbat (animatsiya + tanaffus)
    POST_RESOLVE_PAUSE_SEC = 2.0
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
        # Har bir tomon actionini 1 marta bajarish uchun (Kiss/NoKiss bosilganda)
        self.spinner_action_done: bool = False
        self.target_action_done: bool = False
        self.room_kiss_count  = 0
        self.bottle_type      = (
            DEFAULT_BOTTLE_TYPE
            if DEFAULT_BOTTLE_TYPE in BOTTLE_TYPES
            else "standart"
        )
        self._turn_task: Optional[asyncio.Task] = None
        self._spin_task: Optional[asyncio.Task] = None
        self._auto_spin_task: Optional[asyncio.Task] = None
        # Mavjud avto-sping vazifasi qaysi turn_seat uchun — bir xil navbatda taymerni qayta nolga tushirmaslik
        self._auto_spin_for_seat: Optional[int] = None
        self._offer_timeout_task: Optional[asyncio.Task] = None
        # Juftlik tugagach keyingi navbatgacha (POST_RESOLVE_PAUSE) — shu paytda navbat taklifini yubormaslik
        self.round_closing: bool = False
        # Bank dovşani — bir vaqtda bitta faol
        self.rabbit_active: bool = False
        self.rabbit_active_until: float = 0.0
        self.rabbit_gift: Optional[str] = None
        self.rabbit_from_user: Optional[str] = None
        # Stolda hozir ijro etilayotgan trek (yangi kiruvchiga sinxronlash uchun)
        self.current_music: Optional[dict] = None
        self._music_clear_task: Optional[asyncio.Task] = None

    def clear_rabbit(self) -> None:
        self.rabbit_active = False
        self.rabbit_active_until = 0.0
        self.rabbit_gift = None
        self.rabbit_from_user = None

    def set_rabbit_active(
        self, gift: str, from_user: str, duration_sec: float = 120.0
    ) -> None:
        self.rabbit_active = True
        self.rabbit_gift = gift
        self.rabbit_from_user = from_user
        self.rabbit_active_until = time.time() + duration_sec

    def cancel_auto_spin_task(self):
        """Avto-spin vazifasini bekor qiladi.

        `_handle_game_turn` avto-spin taymeri ichidan chaqirilganda joriy Task
        `self._auto_spin_task` bilan bir xil bo'ladi — o'zini cancel qilmaslik kerak.
        """
        t = self._auto_spin_task
        ct = asyncio.current_task()
        if t is not None and not t.done() and t is not ct:
            t.cancel()
        self._auto_spin_task = None
        self._auto_spin_for_seat = None

    def schedule_auto_spin_task(self, coro, turn_seat: int):
        """Avvalgi taymerni bekor qilib yangisini boshlaydi (stol tarkibi o'zgarganda ham to'g'ri ishlasin)."""
        self.cancel_auto_spin_task()
        self._auto_spin_for_seat = int(turn_seat)
        self._auto_spin_task = asyncio.create_task(coro)

    def schedule_auto_spin_task_if_idle_turn_changed(
        self, coro_factory, turn_seat: int
    ) -> None:
        """
        coro_factory: chaqirilganda asyncio coroutine qaytaradi (masalan
        `lambda: self._auto_spin_timeout_task(table, ts_seat)`).

        Xuddi shu navbat (turn_seat) uchun taymer allaqachon kutilayotgan bo'lsa —
        factory chaqirilmaydi (koroutina chiqmaydi, RuntimeWarning bo'lmaydi).
        `_check_and_broadcast_turn` takror chaqirilganda taymer uzilib qolmasin.
        """
        ts = int(turn_seat)
        t = self._auto_spin_task
        if (
            t is not None
            and not t.done()
            and self._auto_spin_for_seat == ts
            and self.state == STATE_WAIT
        ):
            return
        self.schedule_auto_spin_task(coro_factory(), ts)

    def repair_turn_seat_if_orphaned(self) -> None:
        """turn_seat bo'sh yoki o'chib ketgan o'yinchiga ishora qilsa — eng kichik seatga tiklash."""
        if not self.players or self.state != STATE_WAIT:
            return
        occ = {p.seat for p in self.players.values()}
        if self.turn_seat in occ:
            return
        first = sorted(self.players.values(), key=lambda p: p.seat)[0]
        self.turn_seat = first.seat
        self.bottle_seat = first.seat

    def cancel_offer_timeout_task(self):
        if self._offer_timeout_task and not self._offer_timeout_task.done():
            self._offer_timeout_task.cancel()
        self._offer_timeout_task = None

    def schedule_offer_timeout_task(self, coro):
        self.cancel_offer_timeout_task()
        self._offer_timeout_task = asyncio.create_task(coro)

    # ── Seat management ────────────────────────────────────────────────────
    def is_full(self) -> bool:
        return len(self.players) >= MAX_SEATS

    def next_free_seat(self) -> int:
        """Bo'sh o'rinlardan tasodifiy bittasini beradi; joy bo'lmasa -1."""
        occupied = {p.seat for p in self.players.values()}
        free = [s for s in range(MAX_SEATS) if s not in occupied]
        if not free:
            return -1
        return random.choice(free)

    def add_player(self, player: "Player") -> bool:
        if self.is_full():
            return False
        seat = self.next_free_seat()
        if seat < 0:
            return False
        player.seat = seat
        player.table_id = self.table_id
        self.players[player.id] = player
        return True

    def remove_player(self, user_id: str):
        self.players.pop(user_id, None)
        # Agar o'yin davomidagi ishtirokchi ketsa — darhol reset (Wait holatiga qaytish)
        if user_id in (self.current_spinner, self.current_target):
            self.reset_turn()
            if self._turn_task and not self._turn_task.done():
                self._turn_task.cancel()
            if self._spin_task and not self._spin_task.done():
                self._spin_task.cancel()
            self.cancel_auto_spin_task()
            self.cancel_offer_timeout_task()

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
        """Navbat + WAIT. Jins balansi `_check_and_broadcast_turn` da (game_turn_offer)."""
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

        # Qarama-qarshi jins (gender qatori xato bo'lsa ham `male` ishonchli)
        opposites = [
            p for uid, p in self.players.items()
            if uid != spinner_id and bool(p.male) != bool(spinner.male)
        ]

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
        self.spinner_action_done = False
        self.target_action_done = False

    def select_turn(self) -> None:
        self.state = STATE_SELECT

    def reset_turn(self) -> None:
        self.state            = STATE_WAIT
        self.current_spinner  = None
        self.current_target   = None
        self.spinner_choice   = None
        self.target_choice    = None
        self.resolving        = False
        self.spinner_action_done = False
        self.target_action_done = False

    async def _cancel_turn(self):
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self.reset_turn()
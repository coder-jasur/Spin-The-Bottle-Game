"""
Table — bitta o'yin stoli va uning holat mashinasi.
"""
import asyncio
import time
import random
from typing import Dict, Optional, List, TYPE_CHECKING
from src.app.api.ws.constants import STATE_WAIT, STATE_SPINNING, STATE_OFFER, STATE_SELECT, MAX_SEATS, BOTTLE_TYPES


if TYPE_CHECKING:
    from src.app.api.ws.player import Player


class Table:
    TURN_OFFER_TIMEOUT = 15   # sekund: kiss/refuse tanlash vaqti
    SPIN_DURATION      = 3    # sekund: butilka aylanish vaqti

    def __init__(self, table_id: str):
        self.table_id   = table_id
        self.players: Dict[str, "Player"] = {}   # user_id → Player
        self.state      = STATE_WAIT
        self.bottle_seat= 0
        self.current_spinner: Optional[str] = None   # kim aylantirmoqda
        self.current_target:  Optional[str] = None   # kim tanlandi
        self.room_kiss_count  = 0
        self.bottle_type      = random.choice(BOTTLE_TYPES)
        self._turn_task: Optional[asyncio.Task] = None
        self._spin_task: Optional[asyncio.Task] = None

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

    def all_participants(self) -> List[dict]:
        return [p.to_participant() for p in self.players.values()]

    def player_count(self) -> int:
        return len(self.players)

    # ── Turn machine ───────────────────────────────────────────────────────
    def can_spin(self, user_id: str) -> bool:
        return (
            self.state == STATE_WAIT
            and user_id in self.players
            and self.bottle_seat == self.players[user_id].seat
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
        self.bottle_seat    = target.seat
        return target.seat

    def offer_turn(self) -> None:
        self.state = STATE_OFFER

    def select_turn(self) -> None:
        self.state = STATE_SELECT

    def reset_turn(self) -> None:
        self.state            = STATE_WAIT
        self.current_spinner  = None
        self.current_target   = None

    async def _cancel_turn(self):
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self.reset_turn()
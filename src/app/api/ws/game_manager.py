"""
GameManager — to'liq tuzatilgan va kengaytirilgan versiya.
Xonalar ro'yxati, real foydalanuvchi ma'lumotlari, to'liq statistika.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import random
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Tuple

import jwt as pyjwt
from fastapi import WebSocket

from src.app.api.game_session import game_sessions
from src.app.api.ws.constants import (
    ADMIN_DISPLAY_HEARTS,
    ADMIN_DISPLAY_STARS,
    BOOSTER_TYPES,
    BOTTLE_PRICES,
    BOTTLE_TYPES,
    COMPLIMENT_GOLD_REWARD,
    COMPLIMENTS_TO_REWARD,
    DAILY_BONUS_GOLD,
    DEFAULT_BOTTLE_TYPE,
    BOMB_GIFT_TYPES,
    DYNAMITE_DRINK_TYPE,
    DRINK_IDS_RECEIVER_HEART_PLUS_1,
    DRINK_PRICES,
    DRINK_TYPES,
    FRAME_TYPES,
    STONE_TYPES,
    GESTURE_PRICES,
    GESTURE_TYPES,
    GOLD2TOKENS_BY_GOLD,
    GOLD2TOKENS_ITEMS,
    GIFT_PRICES,
    GIFT_LOVE_ITEM_ID,
    GIFT_LOVE_UNLIMITED_MIN,
    GIFT_TYPES,
    GIFT_TYPES_FREE,
    GIFT_TYPES_VIP,
    HAT_PRICES,
    HAT_TYPES,
    KICKOUT_STREAK_RESET_SECONDS,
    KISS_BONUS_GOLD,
    RETENTION_BONUS_GOLD,
    REWARDED_VIDEO_GOLD,
    BOTTLE_PLAIN_IDLE_DISCONNECT_MS,
    MAX_SEATS,
    STATE_OFFER,
    STATE_SELECT,
    STATE_SPINNING,
    STATE_WAIT,
    WELCOME_BONUS_GOLD,
    league_state_for_total_kisses,
    league_tier_from_total_kisses,
    kickout_price_for_use_index,
    kickout_streak_effective_uses,
)
from src.app.api.ws.player import (
    Player,
    achievement_level_to_client,
    parse_birth_date_ms,
)
from src.app.api.ws.table import Table, normalize_ws_user_ref
from src.app.api.ws.utils import prepare_packet
from src.app.core.jwt import verify_access_token
from src.app.core.username import normalize_game_username, validate_game_username
from src.app.core.room_policy import (
    BASE_VISIBLE_COUNTRY,
    BASE_VISIBLE_GLOBAL,
    COUNTRY_ROOM_SLOTS,
    GLOBAL_ROOM_SLOTS,
    is_global_country_code,
    normalize_country_code,
    player_may_join_room_row,
    room_display_name,
    visible_room_prefix_len,
)
from src.app.database.repositories.game import GameRepository

log = logging.getLogger("spinbottle")

# Haydalgandan keyin shu stolga qayta kirish (ms). 0 = taqiq yo'q.
KICK_REENTRY_BAN_MS = 0
# HTML5: ikkinchi qurilma — eski WS `reason` (klient dialog uchun)
DUPLICATE_DEVICE_CLOSE_REASON = "DUPLICATE_DEVICE_LOGIN"
IDLE_TIMEOUT_CLOSE_REASON = "IDLE_TIMEOUT_10M"
# RFC 6455: 4000–4999 ilova kodi; `reason` ba'zi proksilarda yo'qoladi — klient 4410 ni taniydi.
IDLE_TIMEOUT_WS_CLOSE_CODE = 4410
# plain_ws: 10 daqiqa idle — bu turlar faollik sifatida hisoblanmaydi (telemetriya / fon).
# Navbatda turgan paytda ruxsat etilgan WS xabarlar
QUEUED_PLAYER_ALLOWED_TYPES: frozenset[str] = frozenset(
    {
        "ping",
        "get_rooms",
        "get_friend_games",
        "change_room",
        "goto_random",
        "goto_user",
        "goto_game",
    }
)

PLAIN_WS_IDLE_IGNORE_TYPES: frozenset[str] = frozenset(
    {
        "ping",
        "kickout_refresh",
        "view",
        "report_activity",
        "report_issue",
        "report_photo",
        "track_event",
        "client_event",
        "set_push_token",
        "set_friends_visibility",
        "mark_friends_invited",
        "fix_referrer_type",
        "reset_achievements_ms",
        "inbox_delete",
        "youtube_error",
        "rewarded_video_start",
        "rewarded_video_have_ads",
        "rewarded_video_no_ads",
        "rewarded_video_error",
        "interstitial_video_start",
        "interstitial_video_finish",
        "interstitial_video_no_ads",
        "interstitial_video_error",
        "goto_interstitial",
        "set_interactive_hints",
        "get_favorite_songs",
        "mark_song_favorite",
    }
)
# `spinbottle` logger uchun INFO darajasini yoqamiz va uvicorn handler'iga
# propagate qilamiz (agar root da handler bo'lsa). Aks holda StreamHandler qo'shamiz.
# Sovg'a spam: bitta o'yinchi stolni bloklamasligi uchun
GIFT_BURST_MAX = 14
GIFT_BURST_WINDOW_SEC = 2.0

log.setLevel(logging.INFO)
if not log.handlers and not logging.getLogger().handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    log.addHandler(_h)
    log.propagate = False


class GameManager:
    def __init__(self):
        self.tables: Dict[str, Table] = {}
        # ws → (table_id, user_id)
        self.ws_map: Dict[WebSocket, Tuple[str, str]] = {}
        self._db_factory = None
        self._tasks_started = False
        # (table_id, "u"|"g", id) → unix_ms gacha bu stolga kira olmaydi
        self._table_kick_reentry_until: Dict[Tuple[str, str, int | str], int] = {}
        self._idle_sweeper_started = False
        # Stol to'lganda: table_id → navbatdagi user_id lar (1-based pozitsiya)
        self._table_queues: Dict[str, Deque[str]] = {}
        # user_id → qaysi stol navbatida
        self._queue_waiting: Dict[str, str] = {}
        # Navbatda turgan Player (stol.players da emas)
        self._queue_players: Dict[str, Player] = {}
        # user_id → so'nggi sovg'a vaqtlari (monotonic)
        self._gift_burst: Dict[str, Deque[float]] = defaultdict(deque)
        # db_id → yutuq tekshiruvi (parallel create_task dan qayta unlock oldini olish)
        self._achievement_locks: Dict[int, asyncio.Lock] = {}

    def _achievement_lock(self, player: Player) -> asyncio.Lock:
        db_id = int(getattr(player, "db_id", 0) or 0)
        if db_id not in self._achievement_locks:
            self._achievement_locks[db_id] = asyncio.Lock()
        return self._achievement_locks[db_id]

    def _sync_achievement_notified(self, player: Player) -> None:
        """DB dagi ochiq darajalar uchun modal qayta chiqmasin."""
        notified = getattr(player, "_achievement_notified", None)
        if notified is None:
            player._achievement_notified = {}
            notified = player._achievement_notified
        for k, v in (player.achievements or {}).items():
            lvl = int(v or 0)
            if lvl > int(notified.get(k, 0) or 0):
                notified[k] = lvl

    def _find_duplicate_plain_player(self, table: Table, player: Player) -> Optional[Player]:
        """Bir xil akkaunt (db_id) shu stolda allaqachon onlayn bo'lsa — eski sessiya."""
        db = getattr(player, "db_id", None)
        if db is None:
            return None
        for ep in table.players.values():
            eid = getattr(ep, "db_id", None)
            if eid is not None and int(eid) == int(db):
                return ep
        return None

    async def _bottle_plain_idle_sweep(self) -> None:
        """HTML5 stol: 10 daqiqa klientdan haqiqiy faollik yo'q bo'lsa ulanishni yopish."""
        while True:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            try:
                now = self._ts()
                for ws in list(self.ws_map.keys()):
                    try:
                        pair = self.ws_map.get(ws)
                        if not pair:
                            continue
                        tid, uid = pair
                        table = self.tables.get(tid)
                        if not table:
                            continue
                        pl = table.get_player(uid)
                        if not pl or not getattr(pl, "plain_ws", False):
                            continue
                        last = int(getattr(pl, "last_activity_ms", 0) or 0)
                        if (
                            last
                            and now - last >= BOTTLE_PLAIN_IDLE_DISCONNECT_MS
                            and pl.ws is ws
                        ):
                            try:
                                log.info(
                                    "idle_disconnect: table=%s user=%s idle_ms=%s",
                                    tid,
                                    uid,
                                    now - last,
                                )
                                await ws.close(
                                    code=IDLE_TIMEOUT_WS_CLOSE_CODE,
                                    reason=IDLE_TIMEOUT_CLOSE_REASON,
                                )
                            except Exception:
                                pass
                    except Exception as e:
                        log.debug("idle sweep row: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("idle sweep: %s", e)

    def set_db_factory(self, factory):
        self._db_factory = factory
        # Factory ulanganda background tasklarni ham boshlab yuboramiz
        self.start_background_tasks()

    def start_background_tasks(self):
        """Fon vazifalarini xavfsiz ishga tushirish (Event loop tayyor bo'lganda)."""
        if self._tasks_started:
            return
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self._rabbit_scheduler())
                if not self._idle_sweeper_started:
                    self._idle_sweeper_started = True
                    asyncio.create_task(self._bottle_plain_idle_sweep())
                self._tasks_started = True
                log.info("GameManager background tasks started.")
        except RuntimeError:
            # Hali loop yo'q, keyinroq (masalan set_db_factory da) qayta urinib ko'ramiz
            pass

    @asynccontextmanager
    async def _db(self):
        if not self._db_factory:
            raise RuntimeError("DB factory o'rnatilmagan!")
        async with self._db_factory() as session:
            yield GameRepository(session)

    # ════════════════════════════════════════════════════════════════════════
    # CONNECTION
    # ════════════════════════════════════════════════════════════════════════
    def _resolve_conn_db_id(self, user_id: str) -> Optional[int]:
        """WS user_id (raqam yoki session token) → DB id; mehmon → None."""
        if not user_id or user_id.startswith("guest"):
            return None
        try:
            return int(user_id)
        except (ValueError, TypeError):
            uid = game_sessions.verify(user_id)
            return int(uid) if uid else None

    def _kick_reentry_key(
        self, table_id: str, user_id: str
    ) -> Tuple[str, str, int | str]:
        tid = str(table_id)
        db_id = self._resolve_conn_db_id(user_id)
        if db_id is not None:
            return (tid, "u", db_id)
        return (tid, "g", user_id)

    def kick_reentry_blocked(self, table_id: str, user_id: str) -> Tuple[bool, int]:
        """(bloklanganmi, taqiq tugash vaqti ms) — muddati o'tgan yozuvlar o'chiriladi."""
        key = self._kick_reentry_key(table_id, user_id)
        until = self._table_kick_reentry_until.get(key)
        if not until:
            return False, 0
        now = self._ts()
        if until <= now:
            self._table_kick_reentry_until.pop(key, None)
            return False, 0
        return True, until

    def _kick_reentry_ban_msg(self, until_ts: int) -> str:
        if KICK_REENTRY_BAN_MS <= 0:
            return "Bu stoldan haydalgansiz."
        remain = max(1, (until_ts - self._ts() + 999) // 1000)
        if remain >= 60:
            m = (remain + 59) // 60
            return f"Bu stoldan haydalgansiz. {m} daqiqadan keyin qayta kirishingiz mumkin."
        return f"Bu stoldan haydalgansiz. {remain} soniyadan keyin qayta kirishingiz mumkin."

    async def _adjust_harem_influence_score(
        self,
        admirer_db_id: int,
        delta: int,
        live: Optional[Player] = None,
    ) -> int:
        """«2 yurak» reytingi: uxajor (to'lovchi) yig'indisi (+ court, - nishon olib ketilganda)."""
        if not admirer_db_id or not delta:
            cur = int(getattr(live, "harem_courts_received", 0) or 0) if live else 0
            return max(0, cur)
        cur = 0
        if live and int(live.db_id or 0) == int(admirer_db_id):
            cur = int(getattr(live, "harem_courts_received", 0) or 0)
        else:
            try:
                async with self._db() as repo:
                    db_u = await repo.get_user_with_wallet(int(admirer_db_id))
                if db_u:
                    cur = int(getattr(db_u, "harem_courts_received", 0) or 0)
            except Exception as e:
                log.debug("_adjust_harem_influence_score load: %s", e)
        new_val = max(0, cur + int(delta))
        if live and int(live.db_id or 0) == int(admirer_db_id):
            live.harem_courts_received = new_val
        if admirer_db_id:
            await self._db_update_user(
                int(admirer_db_id), harem_courts_received=new_val
            )
        return new_val

    async def _apply_harem_court_to_target(
        self,
        target: Player,
        buyer_db: int,
        paid: int,
        buyer_live: Optional[Player] = None,
    ) -> None:
        """Nishonga yangi uxajor: `harem_owner_id` yangilanadi; 2-yurak — to'lovchiga."""
        paid = max(1, int(paid or 1))
        target_db = int(target.db_id or 0)
        if not target_db:
            return

        target.harem_owner_id = int(buyer_db)
        target.harem_owner_paid_price = paid
        target.harem_price = max(1, int(target.harem_price or 1)) + 1

        await self._db_update_user(
            target_db,
            harem_owner_id=int(buyer_db),
            harem_owner_paid_price=paid,
            harem_price=target.harem_price,
        )

        bl = buyer_live or self._find_player_by_db_id(int(buyer_db))
        await self._adjust_harem_influence_score(int(buyer_db), paid, bl)

    async def _revoke_harem_court_from_admirer(
        self,
        admirer_db_id: int,
        paid: int,
        live: Optional[Player] = None,
    ) -> None:
        """Nishon boshqa uxajorga o'tganda: eski to'lovchining 2-yurak yig'indisidan ayiriladi.

        Profil «otkaz» yoki o'z ixtiyori bilan ketganda chaqirilmaydi.
        """
        if not admirer_db_id:
            return
        paid_eff = max(0, int(paid or 0))
        if paid_eff <= 0:
            log.warning(
                "harem revoke skip: paid noma'lum (admirer_db=%s)",
                admirer_db_id,
            )
            return
        await self._adjust_harem_influence_score(
            int(admirer_db_id), -paid_eff, live
        )

    def register_kick_reentry_ban(self, table_id: str, target: Player) -> None:
        """Haydashdan keyin shu stolga vaqtincha kirish taqiqi (KICK_REENTRY_BAN_MS)."""
        if KICK_REENTRY_BAN_MS <= 0:
            return
        tid = str(table_id)
        if target.db_id:
            key = (tid, "u", int(target.db_id))
        else:
            key = (tid, "g", str(target.id))
        self._table_kick_reentry_until[key] = self._ts() + KICK_REENTRY_BAN_MS
        log.info(f"KICK re-entry ban: table={tid} key={key}")

    async def _reject_ws_kick_reentry(self, ws: WebSocket, until_ts: int) -> None:
        msg = {
            "type": "error",
            "msg": self._kick_reentry_ban_msg(until_ts),
            "kick_ban_until": until_ts,
            "ts": self._ts(),
        }
        raw = json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
        try:
            await ws.send_text(raw)
        except Exception:
            pass
        try:
            await ws.send_bytes(prepare_packet(msg))
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass

    def _room_online_count(self, room_id_str: str) -> int:
        """Stoldagi o'yinchilar + shu stol navbatidagi kutuvchilar (yangi stol ochilishi uchun)."""
        rid = str(room_id_str)
        t = self.tables.get(rid)
        seated = t.player_count() if t else 0
        queued = len(self._table_queues.get(rid, ()))
        return seated + queued

    def _room_seated_count(self, room_id_str: str) -> int:
        t = self.tables.get(str(room_id_str))
        return t.player_count() if t else 0

    def _is_table_full(self, table_id: str) -> bool:
        t = self.tables.get(str(table_id))
        if t:
            return t.is_full()
        return False

    def _queue_for_table(self, table_id: str) -> Deque[str]:
        tid = str(table_id)
        if tid not in self._table_queues:
            self._table_queues[tid] = deque()
        return self._table_queues[tid]

    def _queue_position_1based(self, table_id: str, user_id: str) -> int:
        """Klient `game_queue.queue_position`: 0 = joy yo'q, >0 = navbat o'rni."""
        q = self._queue_for_table(table_id)
        try:
            return list(q).index(str(user_id)) + 1
        except ValueError:
            return 0

    def _player_in_queue(self, user_id: str, table_id: Optional[str] = None) -> bool:
        tid = self._queue_waiting.get(str(user_id))
        if not tid:
            return False
        if table_id is not None and str(tid) != str(table_id):
            return False
        return True

    def _enqueue_player(self, table_id: str, player: Player) -> int:
        """Navbatga qo'shadi; 1-based pozitsiya qaytaradi."""
        uid = str(player.id)
        tid = str(table_id)
        self._dequeue_player(uid)
        q = self._queue_for_table(tid)
        if uid not in q:
            q.append(uid)
        self._queue_waiting[uid] = tid
        self._queue_players[uid] = player
        player.in_queue = True
        player.table_id = tid
        return self._queue_position_1based(tid, uid)

    def _dequeue_player(self, user_id: str) -> Optional[str]:
        uid = str(user_id)
        tid = self._queue_waiting.pop(uid, None)
        self._queue_players.pop(uid, None)
        if not tid:
            return None
        q = self._table_queues.get(tid)
        if q:
            try:
                q.remove(uid)
            except ValueError:
                pass
            if not q:
                self._table_queues.pop(tid, None)
        return tid

    def _find_player_ws(self, user_id: str) -> Optional[WebSocket]:
        uid = str(user_id)
        for ws, (_, wuid) in self.ws_map.items():
            if wuid == uid:
                return ws
        return None

    async def _send_game_queue(self, player: Player, table_id: str) -> None:
        pos = self._queue_position_1based(table_id, player.id)
        if pos <= 0:
            pos = 0
        await self.send_to(
            player,
            {
                "type": "game_queue",
                "game_id": str(table_id),
                "queue_position": pos,
                "ts": self._ts(),
            },
        )

    async def _broadcast_queue_positions(self, table_id: str) -> None:
        """Navbatdagi har biriga yangilangan o'rin yuboriladi."""
        tid = str(table_id)
        q = self._table_queues.get(tid)
        if not q:
            return
        for uid in list(q):
            if not self._find_player_ws(uid):
                self._dequeue_player(uid)
                continue
            pl = self._queue_players.get(uid)
            if pl:
                await self._send_game_queue(pl, tid)

    async def _promote_from_queue(self, table_id: str) -> None:
        """Bo'sh joy paydo bo'lganda navbatdan birinchi o'yinchini stolga o'tkazadi."""
        tid = str(table_id)
        table = self.tables.get(tid)
        if not table:
            return
        while not table.is_full():
            q = self._table_queues.get(tid)
            if not q:
                break
            uid = q[0]
            ws = self._find_player_ws(uid)
            player = self._queue_players.pop(uid, None)
            if not ws or not player:
                q.popleft()
                self._dequeue_player(uid)
                continue
            q.popleft()
            self._queue_waiting.pop(uid, None)
            player.in_queue = False
            if not table.add_player(player):
                player.in_queue = True
                q.appendleft(uid)
                self._queue_waiting[uid] = tid
                break
            ts = self._ts()
            log.info("QUEUE PROMOTE: %s → table=%s seat=%s", uid, tid, player.seat)
            if getattr(player, "plain_ws", False):
                await self._emit_game_enter_join_and_turn(player, table, ts)
                await self.send_to(
                    player,
                    await self._connection_success_payload(player, table, ts),
                )
            else:
                login_pl = await self._login_payload_with_friends(player, tid, ts)
                await self.send_to(player, login_pl)
                await asyncio.sleep(0.2)
                await self._emit_game_enter_join_and_turn(player, table, ts)
            await self._push_wallet_sync(player)
            await self._broadcast_queue_positions(tid)
            break

    async def _finish_table_join(self, player: Player, table_id: str, ts: int) -> None:
        """change_room / goto_random dan keyin: stolga kirish yoki navbat."""
        if getattr(player, "in_queue", False) or self._player_in_queue(
            player.id, table_id
        ):
            await self._send_game_queue(player, table_id)
            return
        table = self.tables.get(str(table_id))
        if table:
            await self._emit_game_enter_join_and_turn(player, table, ts)
            if getattr(player, "plain_ws", False):
                await self.send_to(
                    player,
                    await self._connection_success_payload(player, table, ts),
                )

    def get_online_presence_stats(self) -> dict:
        """Admin: hozir nechta o'yinchi onlayn (WS + stollar)."""
        db_ids: set[int] = set()
        guests = 0
        in_rooms = 0
        active_tables = 0
        for table in self.tables.values():
            n = table.player_count()
            if n <= 0:
                continue
            active_tables += 1
            in_rooms += n
            for p in table.players.values():
                rid = getattr(p, "db_id", None)
                if rid:
                    db_ids.add(int(rid))
                else:
                    guests += 1
        return {
            "websocket_connections": len(self.ws_map),
            "unique_registered": len(db_ids),
            "guests": guests,
            "total_in_rooms": in_rooms,
            "active_tables": active_tables,
        }

    async def _visible_country_and_global_rows(self, country: str):
        """Ko'rinadigan (bandlik bo'yicha ochilgan) mamlakat + global stollar."""
        c = normalize_country_code(country)
        db_all = []
        async with self._db() as repo:
            await repo.seed_country_tables(c)
            await repo.seed_global_tables()
            db_all = await repo.get_rooms_by_country(c)
        country_rows = sorted(
            [r for r in db_all if r.country_code == c],
            key=lambda r: r.id,
        )
        global_rows = sorted(
            [r for r in db_all if is_global_country_code(r.country_code)],
            key=lambda r: r.id,
        )
        cc = [self._room_online_count(str(r.id)) for r in country_rows]
        gc = [self._room_online_count(str(r.id)) for r in global_rows]
        vn = visible_room_prefix_len(
            cc, max_rooms=COUNTRY_ROOM_SLOTS, base_visible=BASE_VISIBLE_COUNTRY
        )
        vg = visible_room_prefix_len(
            gc, max_rooms=GLOBAL_ROOM_SLOTS, base_visible=BASE_VISIBLE_GLOBAL
        )
        return country_rows[:vn], global_rows[:vg]

    async def _room_id_is_visible_for_country(
        self, country: str, room_id_str: str
    ) -> bool:
        c_vis, g_vis = await self._visible_country_and_global_rows(country)
        ids = {str(r.id) for r in c_vis + g_vis}
        return str(room_id_str) in ids

    async def _country_visible_room_rows(
        self, repo: GameRepository, player: Player
    ) -> list:
        """Mamlakat stollari (id bo'yicha): faqat UI da ko'rinadiganlar."""
        c = normalize_country_code(player.country or "UZBEKISTAN")
        db_all = await repo.get_rooms_by_country(c)
        country_rows = sorted(
            [r for r in db_all if r.country_code == c],
            key=lambda r: r.id,
        )
        if not country_rows:
            return []
        counts = [self._room_online_count(str(r.id)) for r in country_rows]
        vn = visible_room_prefix_len(
            counts,
            max_rooms=COUNTRY_ROOM_SLOTS,
            base_visible=max(1, BASE_VISIBLE_COUNTRY),
        )
        return country_rows[: max(1, vn)]

    async def _pick_join_room_id_for_player(self, player: Player) -> str:
        """Bosh stoldan boshlab bo'sh joy; hammasi to'liq bo'lsa — bosh stol (navbat)."""
        if not self._db_factory:
            return "1"
        try:
            async with self._db() as repo:
                await repo.seed_country_tables(
                    normalize_country_code(player.country or "UZBEKISTAN")
                )
                visible = await self._country_visible_room_rows(repo, player)
                if not visible:
                    return "1"
                for r in visible:
                    rid = str(r.id)
                    if self._room_seated_count(rid) < MAX_SEATS:
                        return rid
                return str(visible[0].id)
        except Exception as e:
            log.warning("_pick_join_room_id_for_player: %s", e)
            return "1"

    async def _default_join_room_id_for_player(
        self, repo: GameRepository, player: Player
    ) -> str:
        """Mamlakat stollaridan bosh stol yoki bo'sh joyli birinchi stol."""
        visible = await self._country_visible_room_rows(repo, player)
        if not visible:
            return "1"
        for r in visible:
            rid = str(r.id)
            if self._room_seated_count(rid) < MAX_SEATS:
                return rid
        return str(visible[0].id)

    async def _resolve_join_table_id(self, requested: str, player: Player) -> str:
        raw = str(requested or "").strip() or "1"
        if not self._db_factory:
            return raw
        try:
            want = int(raw)
        except ValueError:
            want = None
        cc = normalize_country_code(player.country or "UZBEKISTAN")
        is_guest = player.db_id is None
        try:
            async with self._db() as repo:
                await repo.seed_country_tables(cc)
                await repo.seed_global_tables()
                if want is not None:
                    row = await repo.get_table_by_id(want)
                    if row and player_may_join_room_row(
                        cc, row.country_code, is_guest=is_guest
                    ):
                        if await self._room_id_is_visible_for_country(cc, str(want)):
                            return str(want)
                return await self._default_join_room_id_for_player(repo, player)
        except Exception as e:
            log.warning(f"_resolve_join_table_id: {e}")
            return raw

    async def connect(
        self,
        ws: WebSocket,
        table_id: str,
        user_id: str,
        *,
        strict: bool = False,
    ) -> Optional[Player]:
        """
        O'yinchi ulanadi.
        strict=True: URL ni qayta yo'naltirmaydi (change_room / goto_random uchun).
        """
        db_user = None
        real_uid = None

        # 1. user_id ni aniqlash (raqam yoki session token)
        if user_id and not user_id.startswith("guest"):
            try:
                real_uid = int(user_id)
            except (ValueError, TypeError):
                real_uid = game_sessions.verify(user_id)
                if real_uid:
                    log.info(f"Session verified: token={user_id} -> uid={real_uid}")
                else:
                    log.warning(f"Session invalid/expired: {user_id}")

        # 2. DB dan yuklash (id = users.id yoki Telegram tg_id)
        if real_uid:
            try:
                async with self._db() as repo:
                    db_user = await repo.get_user_with_wallet(real_uid)
                    if not db_user:
                        db_user = await repo.get_user(real_uid)
                        if db_user:
                            real_uid = int(db_user.id)
                    if db_user and not db_user.wallet:
                        await repo.ensure_wallet(int(db_user.id))
                        db_user = await repo.get_user_with_wallet(int(db_user.id))
                    if db_user:
                        await repo.sync_daily_login_streak(db_user)
            except Exception as e:
                log.error(f"DB xatosi (connect): {e}")

        # 3. Player yaratish
        if db_user:
            player = Player.from_db(ws, db_user)
            player.id = str(user_id)
            log.info(
                f"[+] DB user: {player.username}({real_uid}) joined with token={user_id}"
            )
            rid = int(real_uid)
            try:
                async with self._db() as repo:
                    player.compliments_lifetime = await repo.get_stat_total_value(
                        rid, "compliment"
                    )
                    player.total_spins = await repo.get_stat_total_value(
                        rid, "bottle_spin"
                    )
                    dj_cnt = await repo.get_stat_total_value(rid, "donjuan")
                    dj_tier = 0
                    for i, th in enumerate(self.ACHIEVEMENTS["donjuan"]["counters"]):
                        if dj_cnt >= th:
                            dj_tier = i + 1
                    await repo.upsert_user_achievement(
                        rid, "donjuan", dj_tier, exact=True
                    )
                    player.achievements["donjuan"] = dj_tier
                    ach_all = await repo.get_user_achievements(rid)
                    for k, v in (ach_all or {}).items():
                        player.achievements[k] = int(v or 0)
                    player.achievements_bonus_claimed = (
                        await repo.get_user_achievement_bonus_claimed(rid)
                    )
                    player._achievements_hydrated = True
                    self._sync_achievement_notified(player)
            except Exception as e:
                log.debug(f"connect lifetime stats: {e}")
            try:
                async with self._db() as repo:
                    if await repo.is_admin_user(rid):
                        player.apply_admin_privileges()
            except Exception as e:
                log.warning(f"connect admin privileges: {e}")
        else:
            g_name = (
                f"Mehmon_{user_id[-4:]}" if len(user_id) > 4 else f"Mehmon_{user_id}"
            )
            player = Player(ws, user_id, g_name)
            h = int(
                hashlib.md5(user_id.encode("utf-8"), usedforsecurity=False).hexdigest(),
                16,
            )
            player.male = (h % 2) == 0
            player.gender = "male" if player.male else "female"
            log.warning(f"[+] Guest: {user_id} → table={table_id} ({player.gender})")
            player.grant_default_owned_items()

        if strict:
            try:
                table_id = str(int(str(table_id).strip()))
            except ValueError:
                return None
        else:
            table_id = await self._resolve_join_table_id(table_id, player)

        blocked, until_ts = self.kick_reentry_blocked(table_id, user_id)
        if blocked:
            await self._reject_ws_kick_reentry(ws, until_ts)
            return None

        if table_id not in self.tables:
            self.tables[table_id] = Table(table_id)

        table = self.tables[table_id]
        dup = self._find_duplicate_plain_player(table, player)
        if dup is not None and dup.ws is not ws:
            old_ws = dup.ws
            self.ws_map.pop(old_ws, None)
            try:
                await old_ws.close(
                    code=1008, reason=DUPLICATE_DEVICE_CLOSE_REASON
                )
            except Exception:
                pass
            dup.ws = ws
            if getattr(player, "session_token", None):
                dup.session_token = player.session_token
            dup.session_started = False
            dup.last_activity_ms = self._ts()
            dup._html5_ws_rebind = True
            self.ws_map[ws] = (table_id, dup.id)
            # Eski RAM dagi g_love (masalan 478) DB dan yangilanmasin — har qayta ulanishda DB dan o'qiymiz
            if dup.db_id:
                await self._sync_gift_love_from_db(dup)
            log.info(
                "DUPLICATE_DEVICE: db_id=%s table=%s — eski WS yopildi, yangi ulanish",
                getattr(dup, "db_id", None),
                table_id,
            )
            return dup

        if table.is_full() and not strict:
            alt_id = await self._pick_join_room_id_for_player(player)
            if alt_id and alt_id != table_id and not self._is_table_full(alt_id):
                log.info(
                    "FULL REDIRECT: %s stol %s to'ldi → bosh/bo'sh stol %s",
                    player.username,
                    table_id,
                    alt_id,
                )
                table_id = alt_id
                if table_id not in self.tables:
                    self.tables[table_id] = Table(table_id)
                table = self.tables[table_id]

        if table.is_full():
            if not strict:
                main_id = await self._pick_join_room_id_for_player(player)
                if main_id and main_id != table_id:
                    table_id = main_id
                    if table_id not in self.tables:
                        self.tables[table_id] = Table(table_id)
                    table = self.tables[table_id]
                    log.info(
                        "FULL → BOSH STOL: %s navbat uchun stol %s",
                        player.username,
                        table_id,
                    )
            if table.is_full():
                self._enqueue_player(table_id, player)
                self.ws_map[ws] = (table_id, user_id)
                player.last_activity_ms = self._ts()
                log.info(
                    "QUEUE: %s stol=%s to'ldi, navbatga #%s",
                    player.username,
                    table_id,
                    self._queue_position_1based(table_id, player.id),
                )
                return player

        if not table.add_player(player):
            if not strict:
                alt_id = await self._pick_join_room_id_for_player(player)
                if alt_id and alt_id != table_id and not self._is_table_full(alt_id):
                    table_id = alt_id
                    if table_id not in self.tables:
                        self.tables[table_id] = Table(table_id)
                    table = self.tables[table_id]
                    if table.add_player(player):
                        self.ws_map[ws] = (table_id, user_id)
                        player.last_activity_ms = self._ts()
                        return player
            self._enqueue_player(table_id, player)
            self.ws_map[ws] = (table_id, user_id)
            player.last_activity_ms = self._ts()
            return player

        self.ws_map[ws] = (table_id, user_id)
        player.last_activity_ms = self._ts()
        if player.db_id:
            await self._sync_gift_love_from_db(player)
        return player

    async def disconnect(self, ws: WebSocket):
        if ws not in self.ws_map:
            return
        table_id, user_id = self.ws_map.pop(ws)
        table = self.tables.get(table_id)
        if not table:
            return

        if self._player_in_queue(user_id, table_id):
            pl = self._queue_players.get(user_id)
            self._dequeue_player(user_id)
            if pl:
                pl.in_queue = False
            log.info(f"[-] {user_id} navbatdan chiqdi → table={table_id}")
            await self._broadcast_queue_positions(table_id)
            if table.player_count() == 0 and not self._table_queues.get(table_id):
                self.tables.pop(table_id, None)
            return

        player = table.get_player(user_id)
        if player and player.ws is not ws:
            log.info(
                "disconnect skip (ws almashtirilgan): user_id=%s table=%s",
                user_id,
                table_id,
            )
            return

        table.remove_player(user_id)

        if player:
            await self.broadcast(
                table_id,
                {"type": "game_leave", "user": player.to_short(), "ts": self._ts()},
            )
            await self._recover_table_after_participant_left(table)
            await self._promote_from_queue(table_id)

        if table.player_count() == 0 and not self._table_queues.get(table_id):
            self.tables.pop(table_id, None)

        log.info(f"[-] {user_id} → table={table_id}")

    def _gender_label_html5(self, pl: Player) -> str:
        return "Qadın" if pl.gender == "female" else "Kişi"

    def _table_players_html5(self, table: Table) -> List[dict]:
        rows: List[dict] = []
        for pl in sorted(table.players.values(), key=lambda p: p.seat):
            rows.append(
                {
                    "user_id": pl.id,
                    "seat_number": pl.seat + 1,
                    "game_username": pl.username,
                    "profile_picture": pl.photo_url or "/photos/no_img.png",
                    "gender": self._gender_label_html5(pl),
                    "room_kiss_count": table.room_kiss_count,
                    "frame_name": getattr(pl, "frame", "") or "",
                    "is_vip": pl.vip,
                    "vip_color": getattr(pl, "vip_color", None),
                }
            )
        return rows

    def _html5_sync_state_fields(self, table: Table) -> dict:
        """
        HTML5 (plain_ws): butilka / navbat / juftlik — stol.state va current_* bilan mos.
        wait_offer yoki aylanish bosqichida yangi kiruvchi ham xuddi shu raundni ko‘radi.
        """
        table.repair_turn_seat_if_orphaned()
        has_male = any(bool(getattr(p, "male", True)) for p in table.players.values())
        has_female = any(
            not bool(getattr(p, "male", True)) for p in table.players.values()
        )
        game_on = bool(has_male and has_female)
        base = {
            "bottle_seat": None,
            "isSpinner": None,
            "isTarget": None,
            "isSpinner_choice": "",
            "isTarget_choice": "",
            "game_active": False,
        }
        if not game_on:
            return base

        spin_id = table.current_spinner
        targ_id = table.current_target
        if (
            spin_id
            and targ_id
            and table.state
            in (
                STATE_OFFER,
                STATE_SPINNING,
                STATE_SELECT,
            )
        ):
            spinner_p = table.get_player(spin_id)
            target_p = table.get_player(targ_id)
            if spinner_p and target_p:
                return {
                    "bottle_seat": target_p.seat + 1,
                    "isSpinner": spin_id,
                    "isTarget": targ_id,
                    "isSpinner_choice": table.spinner_choice or "",
                    "isTarget_choice": table.target_choice or "",
                    "game_active": True,
                }

        spinner = next(
            (p for p in table.players.values() if p.seat == table.turn_seat), None
        )
        if not spinner:
            players_list = sorted(table.players.values(), key=lambda p: p.seat)
            if not players_list:
                return base
            spinner = players_list[0]
        return {
            "bottle_seat": spinner.seat + 1,
            "isSpinner": spinner.id,
            "isTarget": None,
            "isSpinner_choice": "",
            "isTarget_choice": "",
            "game_active": True,
        }

    async def _connection_success_payload(
        self, player: Player, table: Table, ts: int
    ) -> dict:
        sync = self._html5_sync_state_fields(table)
        recent_messages: list[dict] = []
        if self._db_factory and str(table.table_id).isdigit():
            try:
                async with self._db() as repo:
                    recent_messages = await repo.get_recent_table_chat_messages(
                        int(table.table_id), limit=5
                    )
            except Exception as e:
                log.debug(f"recent_messages: {e}")
        wf = player.wallet_for_client()
        return {
            "type": "connection_success",
            "table_id": (
                int(table.table_id) if str(table.table_id).isdigit() else table.table_id
            ),
            "seat_number": player.seat + 1,
            "game_username": player.username,
            "profile_picture": player.photo_url or "/photos/no_img.png",
            "table_players": self._table_players_html5(table),
            "bottle_seat": sync["bottle_seat"],
            "isSpinner": sync["isSpinner"],
            "isTarget": sync["isTarget"],
            "isSpinner_choice": sync["isSpinner_choice"],
            "isTarget_choice": sync["isTarget_choice"],
            "game_active": sync["game_active"],
            "gender": self._gender_label_html5(player),
            "room_kiss_count": table.room_kiss_count,
            "game_start_timeout": None,
            "user_id": player.id,
            "user_id2": player.id,
            "recent_messages": recent_messages,
            "recent_gifts": [],
            "frame_name": getattr(player, "frame", "") or "",
            "is_vip": player.vip,
            "vip_color": getattr(player, "vip_color", None),
            "bottle": {"name": table.bottle_type},
            **wf,
            "balance": wf["tokens"],
            "ts": ts,
        }

    def _game_join_to_player_joined(self, msg: dict, table: Table) -> dict:
        u = msg.get("user") or {}
        sid = str(u.get("id") or u.get("userId") or "")
        seat0 = int(u.get("seat") or 0)

        sync = self._html5_sync_state_fields(table)

        return {
            "type": "player_joined",
            "user_id": sid,
            "seat_number": seat0 + 1,
            "game_username": u.get("name") or u.get("username") or "Bilinmeyen",
            "profile_picture": u.get("photo_url")
            or u.get("image")
            or "/photos/no_img.png",
            "bottle_seat": sync["bottle_seat"],
            "isSpinner": sync["isSpinner"],
            "isTarget": sync["isTarget"],
            "isSpinner_choice": sync["isSpinner_choice"],
            "isTarget_choice": sync["isTarget_choice"],
            "game_active": sync["game_active"],
            "gender": (
                "Qadın"
                if (u.get("gender") == "female")
                else ("Kişi" if u.get("gender") == "male" else "Bilinmeyen")
            ),
            "room_kiss_count": table.room_kiss_count,
            "game_start_timeout": None,
            "frame_name": u.get("frame") or "",
            "is_vip": u.get("vip") or u.get("premium") or False,
            "vip_color": u.get("vip_color"),
            "ts": msg.get("ts") or self._ts(),
        }

    def _game_leave_to_player_left(self, msg: dict, table: Table) -> dict:
        u = msg.get("user") or {}
        seat0 = int(u.get("seat") or 0)

        sync = self._html5_sync_state_fields(table)

        return {
            "type": "player_left",
            "user_id": str(u.get("id") or u.get("userId") or ""),
            "seat_number": seat0 + 1,
            "bottle_seat": sync["bottle_seat"],
            "isSpinner": sync["isSpinner"],
            "isTarget": sync["isTarget"],
            "isSpinner_choice": sync["isSpinner_choice"],
            "isTarget_choice": sync["isTarget_choice"],
            "game_active": sync["game_active"],
            "game_start_timeout": None,
            "ts": msg.get("ts") or self._ts(),
        }

    async def _broadcast_html5_turn_state(self, table: Table):
        table.repair_turn_seat_if_orphaned()
        spinner = next(
            (p for p in table.players.values() if p.seat == table.turn_seat), None
        )
        if not spinner:
            return
        await self.broadcast(
            table.table_id,
            {
                "type": "game_state_updated",
                "bottle_seat": spinner.seat + 1,
                "game_active": True,
                "game_start_timeout": None,
                "isSpinner": spinner.id,
                "isTarget": None,
                "isSpinner_choice": "",
                "isTarget_choice": "",
                "ts": self._ts(),
            },
        )

    async def _broadcast_html5_wait_state(self, table: Table):
        await self.broadcast(
            table.table_id,
            {
                "type": "game_state_updated",
                "bottle_seat": None,
                "game_active": False,
                "game_start_timeout": None,
                "isSpinner": None,
                "isTarget": None,
                "isSpinner_choice": "",
                "isTarget_choice": "",
                "ts": self._ts(),
            },
        )

    # ════════════════════════════════════════════════════════════════════════
    # BROADCAST / SEND
    # ════════════════════════════════════════════════════════════════════════
    def _allow_gift_burst(self, player_id: str) -> bool:
        """Juda ko'p sovg'a ketma-ket kelganda event loopni bo'g'maslik."""
        now = time.monotonic()
        dq = self._gift_burst[player_id]
        while dq and now - dq[0] > GIFT_BURST_WINDOW_SEC:
            dq.popleft()
        if len(dq) >= GIFT_BURST_MAX:
            return False
        dq.append(now)
        return True

    async def _recover_table_after_participant_left(self, table: Table) -> None:
        """Spinner/target ketganda yoki raund yarim qolganda stolni WAIT ga qaytaradi."""
        spin_id = table.current_spinner
        targ_id = table.current_target
        missing = (spin_id and spin_id not in table.players) or (
            targ_id and targ_id not in table.players
        )
        if table.state in (STATE_SPINNING, STATE_OFFER, STATE_SELECT) and missing:
            table.reset_turn()
            table.cancel_offer_timeout_task()
            table.cancel_auto_spin_task()
            if table._spin_task and not table._spin_task.done():
                table._spin_task.cancel()
                table._spin_task = None
        if table.state == STATE_WAIT and not getattr(table, "round_closing", False):
            await self._check_and_broadcast_turn(table)

    async def broadcast(self, table_id: str, msg: dict, exclude_id: str = None):
        table = self.tables.get(table_id)
        if not table:
            return
        msg_type = msg.get("type")
        tasks: list = []
        for uid, player in list(table.players.items()):
            if uid == exclude_id:
                continue
            if msg_type == "game_join" and getattr(player, "plain_ws", False):
                payload = self._game_join_to_player_joined(msg, table)
            elif msg_type == "game_leave" and getattr(player, "plain_ws", False):
                payload = self._game_leave_to_player_left(msg, table)
            else:
                payload = msg.copy()
            player.stamp_out_packet(payload)
            tasks.append(self._deliver(player, payload))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    log.debug("broadcast deliver: %s", r)

    async def send_to(self, player: Player, msg: dict):
        payload = copy.deepcopy(msg)
        player.stamp_out_packet(payload)
        await self._deliver(player, payload)

    async def _deliver(self, player: Player, payload: dict):
        """plain_ws: matnli JSON (main.be3d9225.js); aks holda legacy binary AES paket."""
        try:
            if getattr(player, "plain_ws", False):
                txt = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                await player.ws.send_text(txt)
            else:
                await player.ws.send_bytes(prepare_packet(payload))
        except Exception as e:
            log.debug(f"send error: {e}")

    async def _safe_send(self, ws: WebSocket, pkt: bytes):
        try:
            await ws.send_bytes(pkt)
        except Exception as e:
            log.debug(f"send error: {e}")

    def _gender_counts_for_table(self, table: Optional[Table]) -> Tuple[int, int]:
        if not table:
            return 0, 0
        m = w = 0
        for pl in table.players.values():
            if pl.male:
                m += 1
            else:
                w += 1
        return m, w

    def _find_player_table(
        self, user_id_str: str
    ) -> Optional[Tuple[str, Table, Player]]:
        for tid, tbl in self.tables.items():
            pl = tbl.players.get(user_id_str)
            if pl:
                return tid, tbl, pl
        try:
            uid_int = int(user_id_str)
        except (TypeError, ValueError):
            return None
        for tid, tbl in self.tables.items():
            for pl in tbl.players.values():
                if pl.db_id == uid_int:
                    return tid, tbl, pl
        return None

    def _friend_game_row(self, table_id: str, tbl: Table, who: Player) -> dict:
        m, w = self._gender_counts_for_table(tbl)
        return {
            "game_id": table_id,
            "user": {
                "id": str(who.id),
                "name": who.username,
                "photo_url": who.photo_url or "/photos/no_img.png",
            },
            "men": m,
            "women": w,
        }

    async def _friend_ids_for_games_lookup(
        self, player: Player, data: dict
    ) -> List[int]:
        """Stol almashish: klient friend_ids + DB dagi o'yin ichidagi do'stlar."""
        seen: set[int] = set()
        out: List[int] = []

        def add(raw: object) -> None:
            try:
                fid = int(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return
            if fid <= 0 or fid in seen:
                return
            seen.add(fid)
            out.append(fid)

        for x in data.get("friend_ids") or []:
            add(x)

        if player.db_id:
            try:
                async with self._db() as repo:
                    for f in await repo.get_friends(int(player.db_id)):
                        add(f.id)
            except Exception as e:
                log.error("friend_ids_for_games_lookup DB: %s", e)

        return out

    def _friend_game_history_row(
        self,
        table_id: str,
        live: Optional[Table],
        bottle: str,
        men: int,
        women: int,
        *,
        room: object | None = None,
        global_slot_fallback: int = 1,
    ) -> dict:
        seated = live.player_count() if live else 0
        if room is not None and is_global_country_code(
            getattr(room, "country_code", None)
        ):
            title = room_display_name(room, global_slot_fallback=global_slot_fallback)
        elif room is not None:
            nm = str(getattr(room, "name", "") or "").strip()
            title = f"#{nm}" if nm else f"#{table_id}"
        else:
            title = f"#{table_id}"
        return {
            "game_id": table_id,
            "table_title": title,
            "bottle": bottle,
            "men": men,
            "women": women,
            "online": seated,
            "maxPlayers": MAX_SEATS,
            "isFull": seated >= MAX_SEATS,
            "queueSize": len(self._table_queues.get(str(table_id), ())),
        }

    async def _login_payload_with_friends(
        self, player: Player, table_id: str, ts: int
    ) -> dict:
        await self._sync_gift_love_from_db(player)
        login_payload = player.to_login_payload(table_id, ts)
        recent_messages: list[dict] = []
        if self._db_factory and str(table_id).strip().isdigit():
            try:
                async with self._db() as repo:
                    recent_messages = await repo.get_recent_table_chat_messages(
                        int(table_id), limit=5
                    )
            except Exception as e:
                log.debug(f"login recent_messages: {e}")
        login_payload["recent_messages"] = recent_messages
        try:
            login_payload["kickout_info"] = await self._kickout_info_for_player(player)
        except Exception as e:
            log.error(f"login kickout_info: {e}")
        if player.db_id:
            try:
                async with self._db() as repo:
                    friends = await repo.get_friends(player.db_id)
                    login_payload["friend_user_ids"] = [str(f.id) for f in friends]
            except Exception as e:
                log.error(f"login friend_user_ids: {e}")
        return login_payload

    async def _kickout_info_for_player(self, player: Player) -> dict:
        eff = await self._kickout_effective_uses(player)
        return {"price": kickout_price_for_use_index(eff), "refresh_ms": 60_000}

    async def _kickout_effective_uses(self, player: Player) -> int:
        now = datetime.now()
        if player.db_id:
            try:
                async with self._db() as repo:
                    u = await repo.get_user_with_wallet(player.db_id)
                if not u:
                    return 0
                sc = int(getattr(u, "kickout_streak_count", 0) or 0)
                la = getattr(u, "kickout_last_at", None)
                return kickout_streak_effective_uses(sc, la, now)
            except Exception as e:
                log.error(f"kickout_effective_uses db: {e}")
                return 0
        return self._guest_kick_effective_uses(player)

    def _guest_kick_effective_uses(self, player: Player) -> int:
        now_ms = self._ts()
        last_ms = int(getattr(player, "guest_kickout_last_ms", 0) or 0)
        streak = int(getattr(player, "guest_kickout_streak", 0) or 0)
        if last_ms and (now_ms - last_ms) > KICKOUT_STREAK_RESET_SECONDS * 1000:
            return 0
        return streak

    async def _commit_kickout_streak_after_success(self, player: Player) -> None:
        now = datetime.now()
        if player.db_id:
            new_streak = 1
            async with self._db() as repo:
                u = await repo.get_user_with_wallet(player.db_id)
                if not u:
                    return
                la = u.kickout_last_at
                expired = (
                    la is None
                    or (now - la).total_seconds() > KICKOUT_STREAK_RESET_SECONDS
                )
                new_streak = 1 if expired else int(u.kickout_streak_count or 0) + 1
                await repo.update_user_fields(
                    player.db_id,
                    kickout_streak_count=new_streak,
                    kickout_last_at=now,
                )
            player.kickout_streak_count = new_streak
            player.kickout_last_at = now
        else:
            self._guest_register_kickout(player)

    def _guest_register_kickout(self, player: Player) -> None:
        now_ms = self._ts()
        last_ms = int(getattr(player, "guest_kickout_last_ms", 0) or 0)
        if not last_ms or (now_ms - last_ms) > KICKOUT_STREAK_RESET_SECONDS * 1000:
            player.guest_kickout_streak = 1
        else:
            player.guest_kickout_streak = (
                int(getattr(player, "guest_kickout_streak", 0) or 0) + 1
            )
        player.guest_kickout_last_ms = now_ms

    async def _emit_game_enter_join_and_turn(self, player: Player, tbl: Table, ts: int):
        """Klient sp.start(t) — maydon: game_id (snake_case), scheduled/achievements — massiv."""
        # Stoldagi kiss nishoni har yangi kirishda 0 dan (umumiy total_kisses alohida)
        player.kisses = 0
        recent_messages: list[dict] = []
        if self._db_factory and str(tbl.table_id).isdigit():
            try:
                async with self._db() as repo:
                    recent_messages = await repo.get_recent_table_chat_messages(
                        int(tbl.table_id), limit=5
                    )
            except Exception as e:
                log.debug(f"game_enter recent_messages: {e}")
        # Yutuqlar: login bilan bir xil format — DB dan yangilab (lazy bo‘lib qolmasin)
        ach_list: list = []
        if self._db_factory and player.db_id:
            try:
                async with self._db() as repo:
                    fresh = await repo.get_user_achievements(int(player.db_id))
                    for k, v in (fresh or {}).items():
                        player.achievements[k] = int(v or 0)
                    player.achievements_bonus_claimed = (
                        await repo.get_user_achievement_bonus_claimed(
                            int(player.db_id)
                        )
                    )
                    player._achievements_hydrated = True
                    self._sync_achievement_notified(player)
            except Exception as e:
                log.debug("game_enter achievements: %s", e)
        ach_list = [
            {
                "achievement_id": k,
                "level": achievement_level_to_client(v),
                "timestamp": ts,
            }
            for k, v in sorted((player.achievements or {}).items())
        ]
        ge: dict = {
            "type": "game_enter",
            "game_id": tbl.table_id,
            "tableId": tbl.table_id,
            "participants": tbl.all_participants(),
            "bottle_type": tbl.bottle_type,
            "scheduled": [],
            "achievements": ach_list,
            "achievements_ms": 0,
            "recent_messages": recent_messages,
            "ts": ts + 5,
        }
        await self.send_to(player, ge)
        await asyncio.sleep(0.2)
        await self._send_table_chat_history(player, tbl, ts)
        await asyncio.sleep(0.15)
        await self._send_table_music_sync(player, tbl)
        await self.broadcast(
            tbl.table_id,
            {"type": "game_join", "user": player.to_participant(), "ts": ts + 10},
            exclude_id=player.id,
        )
        await asyncio.sleep(0.1)
        await self._check_and_broadcast_turn(tbl)

    # ════════════════════════════════════════════════════════════════════════
    # MAIN ROUTER
    # ════════════════════════════════════════════════════════════════════════
    async def handle(self, ws: WebSocket, data: dict):
        if ws not in self.ws_map:
            return
        table_id, user_id = self.ws_map[ws]
        table = self.tables.get(table_id)
        if not table:
            return
        player = table.get_player(user_id)
        in_queue = player is None and self._player_in_queue(user_id, table_id)
        if not player and not in_queue:
            return
        if in_queue:
            t = str(data.get("type", "") or "")
            if t not in QUEUED_PLAYER_ALLOWED_TYPES:
                return
            qpl = self._queue_players.get(user_id)
            if not qpl:
                return
            if t == "get_rooms":
                await self._handle_get_rooms(qpl, data)
            elif t == "get_friend_games":
                await self._handle_get_friend_games(qpl, data)
            elif t == "change_room":
                await self._handle_change_room(ws, qpl, data)
            elif t == "goto_random":
                await self._handle_goto_random(ws, qpl)
            elif t in ("goto_user", "goto_game"):
                await self._handle_goto_user(ws, qpl, data)
            return

        t = str(data.get("type", "") or "")
        if getattr(player, "plain_ws", False) and t not in PLAIN_WS_IDLE_IGNORE_TYPES:
            player.last_activity_ms = self._ts()
        # Faqat idle hisobiga kiradigan xabarlar (telemetriya / fon — log va last_activity tashqari)
        if t not in PLAIN_WS_IDLE_IGNORE_TYPES:
            log.info(f"DEBUG_RECV: user={player.username} id={player.id} type={t} data={data}")

        # ── O'yin ──────────────────────────────────────────────────────────
        if t == "login":
            await self._handle_login(ws, table, player, data)
        elif t in (
            "game_turn",
            "game_turn_spin",
            "game_spin",
            "spin",
            "spin_bottle",
        ):
            await self._handle_game_turn(player)
        elif t == "select_choice":
            await self._handle_select_choice(table, player, data)
        elif t in ("game_kiss", "send_kiss"):
            await self._handle_game_kiss(table, player, data)
        elif t == "game_refuse":
            await self._handle_game_refuse(table, player, data)
        elif t in ("game_gift", "send_gift"):
            await self._handle_game_gift(table, player, data)
        elif t == "game_drink":
            await self._handle_game_drink(table, player, data)
        elif t == "game_hat":
            await self._handle_game_hat(table, player, data)
        elif t == "game_gesture":
            await self._handle_game_gesture(table, player, data)
        elif t in ("game_bottle", "bottle_change", "set_bottle", "game:bottle"):
            await self._handle_game_bottle(table, player, data)
        elif t in ("game_random", "random_gift"):
            await self._handle_game_random(table, player, data)
        elif t in ("game_chat_message", "chat_message"):
            await self._handle_chat(table, player, data)
        elif t == "locked_message":
            await self._handle_locked_message(table, player, data)
        elif t == "game_music":
            await self._handle_game_music(table, player, data)
        elif t == "game_turn_booster":
            await self._handle_game_turn_booster(table, player, data)
        # ── Xonalar ro'yxati ────────────────────────────────────────────────
        elif t == "get_rooms":
            await self._handle_get_rooms(player, data)
        elif t == "change_room":
            await self._handle_change_room(ws, player, data)
        # ── Profil ──────────────────────────────────────────────────────────
        elif t == "update_profile":
            await self._handle_update_profile(table, player, data)
        elif t == "get_profile":
            await self._handle_get_profile(player, data)
        elif t == "set_decorations":
            await self._handle_set_decorations(table, player, data)
        elif t == "set_interactive_hints":
            await self.send_to(player, {"type": "ok", "ts": self._ts()})
        elif t == "reset_profile_photo":
            await self._handle_reset_photo(table, player)
        # ── Navigatsiya ─────────────────────────────────────────────────────
        elif t == "goto_random":
            await self._handle_goto_random(ws, player)
        elif t == "goto_user":
            await self._handle_goto_user(ws, player, data)
        elif t in ("goto_history", "goto_view_table"):
            await self._handle_goto_room(ws, player, data)
        # ── Kickout ─────────────────────────────────────────────────────────
        elif t == "user_kickout":
            await self._handle_user_kickout(table, player, data)
        elif t == "admin_unsafe_drop_user":
            await self._handle_admin_unsafe_drop_user(table, player, data)
        elif t == "user_save":
            await self._handle_user_save(table, player, data)
        elif t == "kickout_refresh":
            await self._handle_kickout_refresh(player)
        elif t == "block_user":
            await self.send_to(
                player, {"type": "block_user", "ok": True, "ts": self._ts()}
            )
        elif t == "unblock_user":
            await self.send_to(
                player, {"type": "unblock_user", "ok": True, "ts": self._ts()}
            )
        # ── Do'stlar ────────────────────────────────────────────────────────
        elif t == "mark_song_favorite":
            await self._handle_mark_song_favorite(player, data)
        elif t == "get_friends":
            await self._handle_get_friends(player, data)
        elif t == "get_friend_games":
            await self._handle_get_friend_games(player, data)
        elif t == "friend_add":
            await self._handle_friend_add(player, data)
        elif t == "friend_remove":
            await self._handle_friend_remove(player, data)
        elif t == "friend_request_answer":
            await self._handle_friend_request_answer(player, data)
        elif t in ("invite_friend", "invite_to_table", "game_invite"):
            await self._handle_invite_to_table(table, player, data)
        elif t in ("admirer_add", "fellow_invite", "uxajor_invite"):
            await self._handle_admirer_add(player, data)
        # ── Info ────────────────────────────────────────────────────────────
        elif t == "items_get":
            await self._handle_items_get(player)
        elif t == "items_use":
            await self._handle_items_use(player, data)
        elif t == "pass_info":
            await self._handle_pass_info(player)
        elif t == "league_info":
            await self._handle_league_info(player)
        elif t == "league_claim_reward":
            await self._handle_league_claim_reward(player)
        elif t == "get_tops":
            await self._handle_get_tops(player, data)
        elif t == "get_stickers":
            await self.send_to(
                player,
                {
                    "type": "get_stickers",
                    "categories": [],
                    "user_stickers": [],
                    "ts": self._ts(),
                },
            )
        elif t == "get_favorite_songs":
            await self._handle_get_favorite_songs(player, data)
        elif t == "translate":
            await self._handle_translate(player, data)
        elif t == "view":
            await self.send_to(player, {"type": "view", "ok": True, "ts": self._ts()})
        # ── Bonuslar ────────────────────────────────────────────────────────
        elif t == "claim_kiss_bonus":
            await self._give_hearts(
                player, KISS_BONUS_GOLD, "claim_kiss_bonus", save_to_db=True
            )
        elif t in ("claim_retention_bonus", "claim_rewarded_retention"):
            await self._give_hearts(player, RETENTION_BONUS_GOLD, t, save_to_db=True)
        elif t == "claim_achievement_bonus":
            await self._handle_claim_achievement_bonus(player, data)
        elif t == "claim_rewarded_video_bonus":
            await self._handle_claim_rewarded_video(player)
        elif t == "claim_vip_tokens":
            await self._handle_claim_vip_tokens(player)
        elif t == "pass_claim_pending_reward":
            await self.send_to(
                player,
                {
                    "type": t,
                    "next_state": "running",
                    "keys": [],
                    "reward": {"gold": 10},
                    "ts": self._ts(),
                },
            )
        elif t == "pass_claim_level_reward":
            await self._handle_pass_claim_level_reward(player, data)
        elif t == "pass_claim_chest_reward":
            await self._handle_pass_claim_chest_reward(player)
        elif t == "gold2tokens":
            await self._handle_gold2tokens(player, data)
        elif t == "gold2tokens_get":
            await self._handle_gold2tokens_get(player)
        elif t == "vk_quest_bonus":
            await self._handle_vk_quest_bonus(player)
        elif t == "get_uninvited_friends":
            await self._handle_get_uninvited_friends(player)
        elif t == "messages_is_allowed":
            await self.send_to(
                player,
                {"type": "messages_is_allowed", "result": True, "ts": self._ts()},
            )
        # ── Sotib olish ─────────────────────────────────────────────────────
        elif t == "vip_purchase":
            await self._handle_vip_purchase(player, data)
        elif t == "item_purchase":
            await self._handle_item_purchase(player, data)
        # ── Wallet ma'lumotlari ─────────────────────────────────────────────
        elif t == "get_wallet":
            await self._handle_get_wallet(player)
        # ── Ping ────────────────────────────────────────────────────────────
        elif t == "ping":
            if player.session_token:
                from src.app.api.game_session import game_sessions

                if not game_sessions.verify(player.session_token):
                    await self.send_to(
                        player,
                        {
                            "type": "error",
                            "error": "session_expired",
                            "message": "Sessiya tugadi. Qaytadan kiring.",
                            "ts": self._ts(),
                        },
                    )
                    await ws.close(code=4001)
                    return
            await self.send_to(player, {"type": "pong", "ts": self._ts()})
        elif t == "delete_account":
            await self._handle_delete_account(ws, player)
        elif t in (
            "report_activity",
            "report_issue",
            "report_photo",
            "track_event",
            "client_event",
            "set_push_token",
            "set_friends_visibility",
            "mark_friends_invited",
            "fix_referrer_type",
            "reset_achievements_ms",
            "inbox_delete",
            "profile_navigate",
            "youtube_error",
            "rewarded_video_start",
            "rewarded_video_have_ads",
            "rewarded_video_no_ads",
            "rewarded_video_error",
            "interstitial_video_start",
            "interstitial_video_finish",
            "interstitial_video_no_ads",
            "interstitial_video_error",
            "goto_interstitial",
        ):
            pass  # ignore
        elif t == "bottle_selected":
            await self._handle_bottle_selected(player, data)
        elif t == "harem_purchase":
            await self._handle_harem_purchase(table, player, data)
        # ── HTML5 "uxajor" oqimi (welcome/main.be3d9225.js) ─────────────────
        elif t == "like_user":
            await self._handle_like_user(player, data)
        elif t == "profile_clicked":
            await self._handle_profile_clicked(player, data)
        elif t == "seat_clicked":
            await self._handle_seat_clicked(table, player, data)
        elif t == "shop_clicked":
            await self._handle_shop_clicked(player, data)
        elif t == "compliment_next":
            await self._handle_compliment_next(player)
        elif t == "compliment_send":
            await self._handle_compliment_send(player, data)
        elif t == "compliment_group":
            await self._handle_compliment_group(player)
        elif t == "gm_hearts_purchase":
            await self._handle_gm_hearts_purchase(player, data)
        elif t == "tg_purchase":
            await self._handle_tg_purchase(player, data)
        elif t == "rabbit_gift_send":
            await self._handle_rabbit_gift_send(player, data)
        elif t == "rabbit_gift_caught":
            await self._handle_rabbit_gift_caught(player, data)
        else:
            log.warning(f"[UNKNOWN] type={t!r} from {user_id}")

    async def _handle_seat_clicked(self, table: Table, player: Player, data: dict):
        """O'rin bosilganda: agar spinner o'z o'rnini (yoki butilkani) bossa — SPIN."""
        try:
            seat_idx = int(data.get("seat") if data.get("seat") is not None else -1)
        except (ValueError, TypeError):
            seat_idx = -1

        log.info(f"WS-CLICK: {player.username} clicked seat={seat_idx}. Table state={table.state}, Turn seat={table.turn_seat}")

        # HTML5 klientida butilkani bosish ko'pincha spinner o'rnini bosish bilan bir xil
        if table.state == STATE_WAIT and player.seat == table.turn_seat:
            # Agar o'z o'rnini yoki butilka turgan joyni bossa (seat_idx -1 bo'lsa ham butilka markazi deb hisoblaymiz)
            if seat_idx == table.turn_seat or seat_idx == -1:
                log.info(f"SPIN-EXEC: {player.username} triggered spin via click.")
                await self._handle_game_turn(player)
            else:
                log.info(f"SPIN-SKIP: {player.username} clicked {seat_idx}, but their seat is {player.seat}")
        else:
            log.debug(f"CLICK-IGNORE: {player.username} clicked {seat_idx}, turn={table.turn_seat}, state={table.state}")

    async def _apply_player_profile_fields(
        self, player: Player, data: dict, *, persist: bool = False
    ) -> dict:
        """login/update_profile dan kelgan yosh/jins va boshqa profil maydonlari."""
        from datetime import datetime, timezone

        db_fields: dict = {}

        if "name" in data:
            new_name = normalize_game_username(str(data["name"]))
            err = validate_game_username(new_name)
            if err:
                await self.send_to(
                    player,
                    {"type": "error", "msg": err, "ts": self._ts()},
                )
                return db_fields
            player.username = new_name
            db_fields["display_name"] = new_name
            if player.db_id:
                db_fields["username"] = new_name

        if "male" in data:
            player.male = bool(data["male"])
            player.gender = "male" if player.male else "female"
            db_fields["gender"] = player.gender

        if "locale" in data:
            from src.app.core.language import apply_player_locale

            if apply_player_locale(player, str(data["locale"])):
                db_fields["language_code"] = player.locale

        if "status" in data:
            player.status = str(data["status"])[:100]
            db_fields["status_text"] = player.status

        from src.app.api.ws.profile_setup import (
            compute_age_from_birth_date,
            normalize_profile_age,
        )

        if "birthday_full" in data:
            ts_ms = parse_birth_date_ms(str(data["birthday_full"]).strip())
            if ts_ms:
                player.birthday_ts = ts_ms
                d_birth = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
                db_fields["birth_date"] = d_birth.isoformat()
                valid_age = compute_age_from_birth_date(d_birth.isoformat())
                if valid_age is not None:
                    player.age = valid_age
                    db_fields["age"] = valid_age

        if "age" in data and "birthday_full" not in data:
            valid_age = normalize_profile_age(data.get("age"))
            if valid_age is not None:
                player.age = valid_age
                db_fields["age"] = valid_age

        if db_fields:
            player.is_new = 0
            db_fields["level"] = player.level

        if persist and db_fields and player.db_id:
            await self._db_update_user(player.db_id, **db_fields)

        return db_fields

    async def _sync_player_profile_from_db(self, player: Player) -> None:
        """Login oldidan DB dagi yosh/jinsni RAM ga yuklash (ikkinchi ulanish uchun)."""
        if not player.db_id:
            return
        try:
            async with self._db() as repo:
                u = await repo.get_user_with_wallet(int(player.db_id))
            if not u:
                return
            from src.app.api.ws.profile_setup import effective_profile_age

            fixed_age = effective_profile_age(u)
            player.age = fixed_age
            if u.birth_date:
                player.birthday_ts = parse_birth_date_ms(u.birth_date)
            raw_age = int(u.age or 0)
            if fixed_age > 0 and raw_age != fixed_age and (
                raw_age <= 0 or raw_age >= 1000
            ):
                await self._db_update_user(int(player.db_id), age=fixed_age)
            player.gender = u.gender or "male"
            player.male = player.gender != "female"
            player.frame = str(getattr(u, "frame", None) or "")
            player.stone = str(getattr(u, "stone", None) or "")
            from src.app.core.language import normalize_lang, to_game_locale

            if getattr(u, "language_code", None):
                player.language = normalize_lang(u.language_code)
                player.locale = to_game_locale(player.language)
        except Exception as e:
            log.debug("sync_player_profile_from_db: %s", e)

    # ════════════════════════════════════════════════════════════════════════
    # LOGIN
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_login(
        self, ws: WebSocket, table: Table, player: Player, data: dict
    ):
        if getattr(player, "session_started", False):
            return

        self._admin_floor_wallet(player)

        ts = self._ts()
        table_id = table.table_id

        log.info(
            f"LOGIN: {player.username}({player.id}) | "
            f"hearts={player.hearts} | stars={player.stars} | "
            f"vip={player.vip} | table={table_id} | "
            f"guest={'guest' in player.id}"
        )

        reb = getattr(player, "_html5_ws_rebind", False)
        if reb:
            player._html5_ws_rebind = False

        # Klient profil dialogidan keyin qayta ulanadi: login + registration + age/male
        if data.get("registration") or (
            player.db_id and int(data.get("age") or 0) > 0
        ):
            applied = await self._apply_player_profile_fields(
                player, data, persist=True
            )
            if applied:
                log.info(
                    "PROFILE_SETUP: %s(%s) login orqali saqlandi age=%s male=%s",
                    player.username,
                    player.id,
                    player.age,
                    player.male,
                )

        await self._sync_player_profile_from_db(player)

        if data.get("locale"):
            from src.app.core.language import apply_player_locale

            apply_player_locale(player, str(data["locale"]))

        needs_profile = bool(player.db_id) and int(getattr(player, "age", 0) or 0) <= 0
        if needs_profile:
            player.is_new = 1

        # 1–3. Login + game_enter + game_join + navbat (klient sp.start uchun game_id majburiy)
        if not getattr(player, "plain_ws", False):
            login_payload = await self._login_payload_with_friends(player, table_id, ts)
            if needs_profile:
                login_payload["is_new"] = 1
            await self.send_to(player, login_payload)

        if needs_profile and not getattr(player, "plain_ws", False):
            await asyncio.sleep(0.15)
            await self.send_to(
                player,
                {
                    "type": "needs_registration",
                    "name": player.username or "",
                },
            )
            await self._push_items_sync(player)
            player.session_started = True
            log.info(
                "PROFILE_SETUP: %s(%s) — yosh/jins dialogi",
                player.username,
                player.id,
            )
            return

        if getattr(player, "in_queue", False) or self._player_in_queue(
            player.id, table_id
        ):
            if not getattr(player, "plain_ws", False):
                login_payload = await self._login_payload_with_friends(
                    player, table_id, ts
                )
                if needs_profile:
                    login_payload["is_new"] = 1
                await self.send_to(player, login_payload)
            await self._send_game_queue(player, table_id)
            await self._push_wallet_sync(player)
            await self._push_items_sync(player)
            player.session_started = True
            log.info(
                "LOGIN QUEUE: %s table=%s pos=%s",
                player.username,
                table_id,
                self._queue_position_1based(table_id, player.id),
            )
            return

        if not reb:
            await asyncio.sleep(0.3)
            await self._emit_game_enter_join_and_turn(player, table, ts)

        # HTML5: playerConnected chat `recent_messages` ni kutadi — bonusdan oldin yuboramiz
        if getattr(player, "plain_ws", False):
            await self.send_to(
                player,
                await self._connection_success_payload(player, table, self._ts()),
            )

        # 4. Kunlik bonus (Faqat bir kunda bir marta)
        if player.can_claim_bonus:
            await asyncio.sleep(0.5)
            day = player.daily_streak or 1

            # NameError dan qochish uchun
            try:
                bonus_gold = WELCOME_BONUS_GOLD
            except NameError:
                bonus_gold = 10

            bonus_stars = 5 if player.vip else 0

            await self._give_hearts(
                player,
                bonus_gold,
                "gold_daily",
                save_to_db=True,
                extra={"day": day, "stars_added": bonus_stars},
            )

            if (
                bonus_stars > 0
                and player.db_id
                and not getattr(player, "is_admin", False)
            ):
                player.stars_coin = int(player.stars_coin or 0) + bonus_stars
                player.sync_token_display()
                asyncio.create_task(
                    self._db_add_stars(player.db_id, bonus_stars, "daily_vip_bonus")
                )
            elif bonus_stars > 0 and getattr(player, "is_admin", False):
                self._admin_floor_wallet(player)

            # DB da belgilab qo'yamiz
            if player.db_id:
                asyncio.create_task(self._db_mark_bonus_claimed(player.db_id))

            player.can_claim_bonus = False

        # Navbat / game_turn_offer `_emit_game_enter_join_and_turn` oxirida allaqachon yuborilgan;
        # bu yerda qayta chaqirish ikki marta ovoz + "kim aylantiradi" chalkashuviga olib kelardi.

        # 5. DB da saqlangan do'stlik so'rovlari (foydalanuvchi oflayn bo'lgan vaqtda)
        await self._flush_pending_friend_requests(player)

        self._admin_floor_wallet(player)
        await self._push_wallet_sync(player)
        # Ekrandagi «Коктейль Любви» hisoblagichi = DB gift_love_stock (klient cache emas)
        await self._push_items_sync(player)

        player.session_started = True

    # ════════════════════════════════════════════════════════════════════════
    # ROOMS LIST — yangi qo'shilgan
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_get_rooms(self, player: Player, data: dict):
        """
        Foydalanuvchi mamlakatiga mos xonalar + global xonalar (ochiq ro'yxat).
        DB da 150 ta mamlakat / 20 ta global; UI da ko'rinadigan stollar yig'indisi
        ~50% to'lganda keyingi stol ochiladi (3 stol → jami 18 kishi).
        """
        country = normalize_country_code(
            data.get("country", player.country or "UZBEKISTAN")
        )
        log.debug(f"get_rooms country={country} user={player.username}")

        tables_list = []
        try:
            country_vis, global_vis = await self._visible_country_and_global_rows(
                country
            )

            def _entry(room, scope: str) -> dict:
                room_id_str = str(room.id)
                participants = []
                if room_id_str in self.tables:
                    participants = self.tables[room_id_str].all_participants()
                seated = self._room_seated_count(room_id_str)
                return {
                    "tableId": room_id_str,
                    "tableUsers": participants,
                    "tablePresenter": {},
                    "tableBoosters": [],
                    "tableActions": [],
                    "tableView": {"id": room_id_str},
                    "tableScale": 1.0,
                    "tableUrl": "",
                    "name": room.name,
                    "country": room.country_code,
                    "scope": scope,
                    "online": seated,
                    "maxPlayers": MAX_SEATS,
                    "isFull": seated >= MAX_SEATS,
                    "queueSize": len(self._table_queues.get(room_id_str, ())),
                }

            for r in country_vis:
                tables_list.append(_entry(r, "country"))
            for gi, r in enumerate(global_vis, start=1):
                entry = _entry(r, "global")
                entry["name"] = room_display_name(r, global_slot_fallback=gi)
                tables_list.append(entry)
        except Exception as e:
            log.error(f"get_rooms DB xatosi: {e}")

        if not tables_list:
            for i in range(1, 4):
                rid = str(i)
                tables_list.append(
                    {
                        "tableId": rid,
                        "tableUsers": [],
                        "tablePresenter": {},
                        "tableBoosters": [],
                        "tableActions": [],
                        "tableView": {"id": rid},
                        "tableScale": 1.0,
                        "tableUrl": "",
                        "name": str(i),
                        "country": country,
                        "scope": "country",
                    }
                )

        await self.send_to(
            player,
            {
                "type": "get_rooms",
                "ok": True,
                "tables": tables_list,
                "ts": self._ts(),
            },
        )

    async def http_tables_list_payload(self, country: str) -> list[dict]:
        """GET /api/tables uchun — WS get_rooms bilan bir xil filtrlash."""
        c = normalize_country_code(country)
        out: list[dict] = []
        try:
            c_vis, g_vis = await self._visible_country_and_global_rows(c)
            for room in c_vis:
                rid = str(room.id)
                seated = self._room_seated_count(rid)
                queued = len(self._table_queues.get(rid, ()))
                cap = min(int(room.capacity or MAX_SEATS), MAX_SEATS)
                out.append(
                    {
                        "id": rid,
                        "room_id": room.id,
                        "name": room.name,
                        "currentPlayers": seated,
                        "online": seated,
                        "queueSize": queued,
                        "maxPlayers": cap,
                        "capacity": cap,
                        "isFull": seated >= cap,
                        "is_vip": room.is_vip,
                        "min_level": room.min_level,
                        "country": room.country_code,
                        "scope": "country",
                    }
                )
            for gi, room in enumerate(g_vis, start=1):
                rid = str(room.id)
                seated = self._room_seated_count(rid)
                queued = len(self._table_queues.get(rid, ()))
                cap = min(int(room.capacity or MAX_SEATS), MAX_SEATS)
                out.append(
                    {
                        "id": rid,
                        "room_id": room.id,
                        "name": room_display_name(room, global_slot_fallback=gi),
                        "currentPlayers": seated,
                        "online": seated,
                        "queueSize": queued,
                        "maxPlayers": cap,
                        "capacity": cap,
                        "isFull": seated >= cap,
                        "is_vip": room.is_vip,
                        "min_level": room.min_level,
                        "country": room.country_code,
                        "scope": "global",
                    }
                )
        except Exception as e:
            log.error(f"http_tables_list_payload: {e}")
        return out

    async def _handle_change_room(self, ws: WebSocket, player: Player, data: dict):
        """O'yinchini boshqa xonaga o'tkazadi."""
        new_room_id = str(data.get("room_id", "")).strip()
        if not new_room_id:
            await self.send_to(
                player, {"type": "error", "msg": "Noto'g'ri xona", "ts": self._ts()}
            )
            return
        if new_room_id == player.table_id:
            await self.send_to(
                player, {"type": "ok", "room_id": new_room_id, "ts": self._ts()}
            )
            return

        cc = normalize_country_code(player.country or "UZBEKISTAN")
        is_guest = player.db_id is None
        try:
            rid_int = int(new_room_id)
        except (ValueError, TypeError):
            await self.send_to(
                player,
                {"type": "error", "msg": "Noto'g'ri xona ID", "ts": self._ts()},
            )
            return
        try:
            async with self._db() as repo:
                row = await repo.get_table_by_id(rid_int)
                if not row:
                    await self.send_to(
                        player,
                        {
                            "type": "error",
                            "msg": "Bu stol topilmadi",
                            "ts": self._ts(),
                        },
                    )
                    return
                if not player_may_join_room_row(
                    cc, row.country_code, is_guest=is_guest
                ):
                    await self.send_to(
                        player,
                        {
                            "type": "error",
                            "msg": "Bu stol sizning mamlakatingiz uchun emas",
                            "ts": self._ts(),
                        },
                    )
                    return
                if not await self._room_id_is_visible_for_country(cc, new_room_id):
                    await self.send_to(
                        player,
                        {
                            "type": "error",
                            "msg": "Bu stol hali ochilmagan yoki ro'yxatda yo'q",
                            "ts": self._ts(),
                        },
                    )
                    return
        except Exception as e:
            log.error(f"change_room validate: {e}")
            await self.send_to(
                player,
                {"type": "error", "msg": "Stolni tekshirib bo'lmadi", "ts": self._ts()},
            )
            return

        old_user_id = player.id
        blocked, until_ts = self.kick_reentry_blocked(new_room_id, old_user_id)
        if blocked:
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": self._kick_reentry_ban_msg(until_ts),
                    "kick_ban_until": until_ts,
                    "ts": self._ts(),
                },
            )
            return

        await self.disconnect(ws)

        new_player = await self.connect(ws, new_room_id, old_user_id, strict=True)
        if not new_player:
            log.warning(
                f"CHANGE ROOM blocked after disconnect: {old_user_id} → {new_room_id}"
            )
            return
        ts = self._ts()

        if not getattr(new_player, "plain_ws", False):
            login_pl = await self._login_payload_with_friends(new_player, new_room_id, ts)
            await self.send_to(new_player, login_pl)
            await asyncio.sleep(0.2)

        await self._finish_table_join(new_player, new_room_id, ts)
        await self._flush_pending_friend_requests(new_player)

        log.info(f"CHANGE ROOM: {old_user_id} → {new_room_id}")

    async def _handle_get_friend_games(self, player: Player, data: dict):
        """
        Stol almashish oynasi (PL.show): queryFriendGames → friend_games.
        Klient kutadi: friends, fellows, games_history (g4 / ep konstruktorlari).
        """
        friends_rows: List[dict] = []
        fellows_rows: List[dict] = []
        history_country: list[dict] = []
        history_global: list[dict] = []
        try:
            friend_ids = await self._friend_ids_for_games_lookup(player, data)
            seen_games: set[str] = set()

            for fid in friend_ids[:100]:
                loc = self._find_player_table(str(fid))
                if not loc:
                    continue
                tid, tbl, pl = loc
                key = f"{fid}:{tid}"
                if key in seen_games:
                    continue
                seen_games.add(key)
                friends_rows.append(self._friend_game_row(tid, tbl, pl))

            if player.db_id:
                try:
                    async with self._db() as repo:
                        admirers = await repo.get_admirer_targets(player.db_id)
                        for u in admirers:
                            loc = self._find_player_table(str(u.id))
                            if not loc:
                                continue
                            tid, tbl, pl = loc
                            key = f"fellow:{u.id}:{tid}"
                            if key in seen_games:
                                continue
                            seen_games.add(key)
                            fellows_rows.append(self._friend_game_row(tid, tbl, pl))
                except Exception as e:
                    log.error(f"get_friend_games fellows: {e}")

            country = normalize_country_code(player.country or "UZBEKISTAN")
            try:
                c_vis, g_vis = await asyncio.wait_for(
                    self._visible_country_and_global_rows(country),
                    timeout=8.0,
                )
                for room in c_vis[:40]:
                    tid = str(room.id)
                    live = self.tables.get(tid)
                    m, w = self._gender_counts_for_table(live)
                    bottle = live.bottle_type if live else DEFAULT_BOTTLE_TYPE
                    history_country.append(
                        self._friend_game_history_row(
                            tid, live, bottle, m, w, room=room
                        )
                    )
                for gi, room in enumerate(g_vis[:40], start=1):
                    tid = str(room.id)
                    live = self.tables.get(tid)
                    m, w = self._gender_counts_for_table(live)
                    bottle = live.bottle_type if live else DEFAULT_BOTTLE_TYPE
                    history_global.append(
                        self._friend_game_history_row(
                            tid,
                            live,
                            bottle,
                            m,
                            w,
                            room=room,
                            global_slot_fallback=gi,
                        )
                    )
            except asyncio.TimeoutError:
                log.error(f"get_friend_games history timeout user={player.id}")
            except Exception as e:
                log.error(f"get_friend_games history: {e}")
        except Exception as e:
            log.exception(f"get_friend_games failed user={player.id}: {e}")

        ts = self._ts()
        await self.send_to(
            player,
            {
                "type": "friend_games",
                "friends": friends_rows,
                "fellows": fellows_rows,
                "games_history": history_country,
                "games_history_global": history_global,
                "ts": ts,
            },
        )
        log.info(
            "friend_games sent user=%s friends=%d fellows=%d history=%d",
            player.id,
            len(friends_rows),
            len(fellows_rows),
            len(history_country) + len(history_global),
        )

    def _admin_floor_wallet(self, player: Player) -> None:
        if not getattr(player, "is_admin", False):
            return
        player.hearts = max(int(player.hearts or 0), ADMIN_DISPLAY_HEARTS)
        player.hearts_real = player.hearts
        player.gift_tokens = max(
            int(getattr(player, "gift_tokens", 0) or 0), ADMIN_DISPLAY_STARS
        )
        player.stars_coin = max(
            int(getattr(player, "stars_coin", 0) or 0), ADMIN_DISPLAY_STARS
        )
        player.sync_token_display()

    # ════════════════════════════════════════════════════════════════════════
    # WALLET — yangi qo'shilgan
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_get_wallet(self, player: Player):
        """Foydalanuvchining joriy balansini qaytaradi."""
        # DB dan real balans
        if player.db_id:
            try:
                async with self._db() as repo:
                    wallet = await repo.get_wallet(player.db_id)
                    if wallet:
                        player.apply_wallet_balances(
                            hearts=int(wallet.hearts or 0),
                            stars_coin=int(wallet.stars_coin or 0),
                            gift_tokens=int(wallet.gift_tokens or 0),
                        )
            except Exception as e:
                log.error(f"get_wallet DB xatosi: {e}")

        self._admin_floor_wallet(player)

        wf = player.wallet_for_client()
        await self.send_to(
            player,
            {
                "type": "get_wallet",
                "ok": True,
                **wf,
                "ts": self._ts(),
            },
        )

    # ════════════════════════════════════════════════════════════════════════
    # GAME TURN
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_turn(self, player: Player):
        # Stolni olish
        table = self.tables.get(player.table_id)
        if not table:
            log.warning(
                f"SPIN: Table {player.table_id} not found for {player.username}"
            )
            return

        # 1. Navbatni tekshirish
        if player.seat != table.turn_seat:
            log.warning(f"SPIN: {player.username} navbatida emas (seat {player.seat} != turn_seat {table.turn_seat})")
            return

        # 2. Holatni tekshirish (Force reset if stuck)
        if table.state != STATE_WAIT:
            log.info(f"SPIN: Stol {table.table_id} state={table.state} edi. Majburiy WAIT ga qaytarildi.")
            table.reset_turn()
            table.state = STATE_WAIT

        table.cancel_auto_spin_task()
        table.cancel_offer_timeout_task()
        target_seat = table.start_spin(player.id)
        log.info(f"SPIN: {player.username} started spin. Target seat: {target_seat}")

        player.total_spins = int(getattr(player, "total_spins", 0) or 0) + 1

        target_p = table.get_player(table.current_target)
        if not target_p:
            table.reset_turn()
            await self._check_and_broadcast_turn(table)
            return

        # Joriy spin vazifasini saqlaymiz (chiqib ketganda cancel qilish uchun)
        table._spin_task = asyncio.current_task()

        # Darhol SPIN animatsiyasini hamma ko'rishi kerak
        await self.broadcast(
            table.table_id,
            {
                "type": "game_spin",
                "uid": player.id,
                "user_id": player.id,
                "gameId": table.table_id,
                "tableId": table.table_id,
                "user": player.to_short(),
                "target_seat": target_seat + 1,
                "delay": 0,
                "ts": self._ts(),
            },
        )

        try:
            # Klient aylanishini server kutmasin; xabarlarni ketma-ket yetkazish uchun bitta yield.
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            log.info(f"SPIN bekor qilindi: {player.username} chiqqan ko'rinadi (stol={table.table_id})")
            return

        # Xavfsizlik: o'yinchi hali ham stoldami va holat o'zgarmaganmi?
        if table.state != STATE_SPINNING or table.current_spinner != player.id:
            log.info(f"SPIN yakunlanmadi: holat o'zgargan (state={table.state})")
            return

        # TARGETga tanlov beriladi (Stage 1)
        table.offer_turn()
        await self.broadcast(
            table.table_id,
            {
                "type": "game_turn",
                "gameId": table.table_id,
                "tableId": table.table_id,
                "uid": target_p.id,
                "user_id": target_p.id,
                "user": target_p.to_short(),
                "receiver": player.to_short(),
                "stage": 1,
                "ts": self._ts(),
            },
        )

        table.schedule_offer_timeout_task(self._turn_timeout(table, player.id))

        await self._check_achievements(player, "spins", player.total_spins)
        if player.db_id:
            asyncio.create_task(self._save_spin_stat(player.db_id, 1))

    async def _turn_timeout(self, table: Table, spinner_id: str):
        await asyncio.sleep(Table.TURN_OFFER_TIMEOUT)
        if table.state == STATE_OFFER and table.current_spinner == spinner_id:
            # Timeout: raundni yopamiz, kim bosmagan bo'lsa — hech narsa qilinmaydi.
            table.reset_turn()
            await self.broadcast(
                table.table_id,
                {"type": "game_wait", "user_id": spinner_id, "ts": self._ts()},
            )
            spinner = table.get_player(spinner_id)
            if spinner:
                await self._advance_bottle(table, spinner)
            else:
                log.info(
                    "TURN-TIMEOUT: spinner %s topilmadi (ketgan). Navbatni qayta tekshiramiz (stol=%s)",
                    spinner_id,
                    table.table_id,
                )
                if table.state in (STATE_OFFER, STATE_SPINNING, STATE_SELECT):
                    table.reset_turn()
                    table.cancel_offer_timeout_task()
                    table.cancel_auto_spin_task()
                await self._check_and_broadcast_turn(table)

    def _partner_for_offer_turn(
        self, table: Table, player: Player, data: dict
    ) -> Optional[Tuple[Player, str]]:
        """
        wait_offer bosqichida juftdagi qarama-tomon.
        Klient receiver_id ni xato/bo'sh yuborsa ham spinner/target bo'yicha tiklanadi —
        shunda ikkala odam ham bir xil raundda o'pish/rad qila oladi.
        """
        spin = table.current_spinner
        targ = table.current_target
        if not spin or not targ:
            return None
        if player.id not in (spin, targ):
            log.warning(
                "offer juftda emas: uid=%s spinner=%s target=%s",
                player.id,
                spin,
                targ,
            )
            return None
        expected_key = targ if player.id == spin else spin
        expected = table.get_player(expected_key)
        if not expected:
            return None
        hinted = table.get_player_flexible(data.get("receiver_id"))
        if hinted and hinted.id != player.id and hinted.id in (spin, targ):
            return hinted, hinted.id
        return expected, expected.id

    async def _apply_kiss_reward_after_offer(
        self, table: Table, sender: Player, receiver: Player
    ):
        """game_kiss + statlar (wait_offer jufti uchun)."""
        receiver_id = receiver.id
        ts = self._ts()
        has_kiss_fire = "kiss_fire" in sender.boosters

        await self.broadcast(
            table.table_id,
            {
                "type": "game_kiss",
                "user": sender.to_short(),
                "receiver": receiver.to_short(),
                "ts": ts,
            },
        )

        if has_kiss_fire:
            await asyncio.sleep(0.3)
            await self.broadcast(
                table.table_id,
                {
                    "type": "game_turn_booster",
                    "booster": "kiss_fire",
                    "user_id": sender.id,
                    "receiver_id": receiver_id,
                    "ts": self._ts(),
                },
            )
            sender.boosters = [b for b in sender.boosters if b != "kiss_fire"]

        score_to_add = 1
        if "league_kiss2x" in sender.boosters:
            score_to_add = 2

        # Kiss bosgan odamga emas, faqat juftiga (receiver) qo'shiladi.
        sender.league_score += score_to_add
        receiver.kisses += 1
        receiver.total_kisses += 1
        receiver.league_score += score_to_add
        table.room_kiss_count += 1

        # DB + reyting: faqat receiver (kiss qabul qilgan) uchun +1
        await self._save_kiss_stats(sender_id=None, receiver_id=receiver.db_id)

        ts_ls = self._ts()
        # UI yangilanishi: receiverning umumiy kissini yangilab beramiz
        await self.broadcast(
            table.table_id,
            {
                "type": "league_score",
                "user": receiver.to_short(),
                "user_id": receiver.id,
                "score": score_to_add,
                "assign": {"kisses": 1, "league_score": score_to_add},
                "kisses": receiver.total_kisses,
                "total_kiss_count": receiver.total_kisses,
                "kisses_lim": 500,
                "ts": ts_ls,
            },
        )

        if sender.total_kisses % 5 == 0:
            await self._give_hearts(
                sender, KISS_BONUS_GOLD, "kiss_bonus", save_to_db=True
            )

        # Yutuq tekshiruvi (kissing yutiqlari)
        await self._check_achievements(receiver, "total_kisses", receiver.total_kisses)

    async def _broadcast_refuse_pair_effects(
        self, table: Table, refuser: Player, receiver: Player
    ):
        receiver_id = receiver.id
        has_refuse_slap = "refuse_slap" in refuser.boosters

        await self.broadcast(
            table.table_id,
            {
                "type": "game_refuse",
                "user": refuser.to_short(),
                "receiver": receiver.to_short(),
                "ts": self._ts(),
            },
        )

        if has_refuse_slap:
            await asyncio.sleep(0.3)
            await self.broadcast(
                table.table_id,
                {
                    "type": "game_turn_booster",
                    "booster": "refuse_slap",
                    "user_id": refuser.id,
                    "receiver_id": receiver_id,
                    "ts": self._ts(),
                },
            )
            refuser.boosters = [b for b in refuser.boosters if b != "refuse_slap"]

    async def _handle_select_choice(self, table: Table, player: Player, data: dict):
        """
        HTML5 klient: type select_choice, choice Kiss | NoKiss
        Har bir o'yinchi alohida action qiladi.
          • Kim "Kiss" bossa — o'sha odam kiss qilgan bo'lib ko'rinadi va juftiga kiss qo'shiladi.
          • Kim "NoKiss" bossa — faqat refuse effekti (juftiga kiss qo'shilmaydi).
        Raund: ikkala tomon ham action qilganda (yoki timeout) yopiladi.
        """
        if table.state != STATE_OFFER or table.resolving:
            log.info(
                "select_choice rad: state=%s resolving=%s uid=%s",
                table.state,
                table.resolving,
                player.id,
            )
            return

        raw_choice = (data.get("choice") or "").strip()
        cl = raw_choice.lower()
        if cl in ("kiss", "yes", "accept", "ok"):
            choice = "Kiss"
        elif cl in ("nokiss", "no", "refuse", "reject", "decline"):
            choice = "NoKiss"
        else:
            await self.send_to(
                player,
                {
                    "type": "choice_error",
                    "message": "Noto'g'ri tanlov",
                    "ts": self._ts(),
                },
            )
            return

        spin_id = table.current_spinner
        targ_id = table.current_target
        if not spin_id or not targ_id:
            log.warning(
                "select_choice: spinner/target yo'q (spin=%r, targ=%r)",
                spin_id,
                targ_id,
            )
            return

        if player.id not in (spin_id, targ_id):
            log.warning(
                "select_choice: o'yinchi juftlikda emas. uid=%s spin=%s targ=%s",
                player.id,
                spin_id,
                targ_id,
            )
            return

        is_spinner = player.id == spin_id
        if is_spinner:
            table.spinner_choice = choice
            if table.spinner_action_done:
                return
        else:
            table.target_choice = choice
            if table.target_action_done:
                return

        log.info(
            "select_choice[%s]: %s -> %s (table=%s)",
            "spinner" if is_spinner else "target",
            player.id,
            choice,
            table.table_id,
        )

        spinner_p = table.get_player(spin_id)
        target_p = table.get_player(targ_id)
        if not spinner_p or not target_p:
            table.reset_turn()
            await self._check_and_broadcast_turn(table)
            return

        bottle_seat_1 = target_p.seat + 1

        # 1) choice_updated — bosilgan tanlov UI da darhol icon orqali ko'rinadi
        await self.broadcast(
            table.table_id,
            {
                "type": "choice_updated",
                "isSpinner_choice": table.spinner_choice or "",
                "isTarget_choice": table.target_choice or "",
                "isSpinner": spin_id,
                "isTarget": targ_id,
                "bottle_seat": bottle_seat_1,
                "game_active": True,
                "updated_user_id": player.id,
                "updated_room_kiss_count": table.room_kiss_count,
                "game_start_timeout": None,
                "ts": self._ts(),
            },
        )

        # 2) Actionni darhol bajarish (Kiss/NoKiss)
        if choice == "Kiss":
            # Kim bosgan bo'lsa o'sha sender; jufti receiver
            sender = player
            receiver = target_p if is_spinner else spinner_p
            await self._apply_kiss_reward_after_offer(table, sender, receiver)
        else:
            # Refuse: kim bosgan bo'lsa refuser
            refuser = player
            receiver = target_p if is_spinner else spinner_p
            await self._broadcast_refuse_pair_effects(table, refuser, receiver)

        if is_spinner:
            table.spinner_action_done = True
        else:
            table.target_action_done = True

        # 3) Ikkalasi ham action qilsa — raundni yopamiz (animatsiya uchun choices_complete ham yuboramiz)
        if table.spinner_action_done and table.target_action_done:
            table.round_closing = True
            table.cancel_auto_spin_task()
            table.resolving = True
            table.cancel_offer_timeout_task()
            await self.broadcast(
                table.table_id,
                {
                    "type": "choices_complete",
                    "isSpinner_choice": table.spinner_choice or "",
                    "isTarget_choice": table.target_choice or "",
                    "spinner_seat": spinner_p.seat + 1,
                    "target_seat": target_p.seat + 1,
                    "bottle_seat": bottle_seat_1,
                    "game_active": True,
                    "delay": 3000,
                    "game_start_timeout": None,
                    "ts": self._ts(),
                },
            )
            table.reset_turn()

            # Ikkala taraf ham o'psa 5 soniya, aks holda 2 soniya kutamiz
            pause_sec = Table.POST_RESOLVE_PAUSE_SEC
            if table.spinner_choice == "Kiss" and table.target_choice == "Kiss":
                pause_sec = 2.0

            try:
                await asyncio.sleep(pause_sec)
            finally:
                table.round_closing = False

            # Blokdan yechilgandan keyin keyingi raundni boshlaymiz
            await self._advance_bottle(table, receiver)

    # `_resolve_pair` endi ishlatilmaydi: har bir bosish alohida action qiladi.

    # ════════════════════════════════════════════════════════════════════════
    # KISS
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_kiss(self, table: Table, player: Player, data: dict):
        """Legacy `game_kiss` paketini select_choice juftlik oqimiga yo'naltiramiz —
        shunda sherigining tanlovi kutiladi va keyingi klik bosolmay qolmaydi."""
        if table.state != STATE_OFFER:
            log.debug(
                "kiss e'tiborsiz: state=%s (kerak %s). uid=%s",
                table.state,
                STATE_OFFER,
                player.id,
            )
            return
        await self._handle_select_choice(table, player, {"choice": "Kiss"})

    async def _save_kiss_stats(
        self,
        sender_id: Optional[int],
        receiver_id: Optional[int],
    ):
        if not sender_id and not receiver_id:
            return
        try:
            async with self._db() as repo:
                if receiver_id:
                    # Kiss faqat qabul qilgan odam (receiver) uchun reytingga qo'shiladi
                    await repo.add_stat(receiver_id, "kisses", 1)
                # emotion reytingi faqat game_gesture (emotsiya ko'rsatish) da oshadi
        except Exception as e:
            log.error(f"Kiss stat DB xatosi: {e}")

    def _bump_emotion_rating(self, player: Player, amount: int = 1) -> None:
        """Emotsiya (gesture) reytingi — har bir bosilish uchun +1."""
        if amount <= 0:
            return
        player.emotion = int(getattr(player, "emotion", 0) or 0) + amount
        if player.db_id:
            asyncio.create_task(self._save_emotion_stat(player.db_id, amount))

    async def _save_emotion_stat(self, db_id, amount: int) -> None:
        if not db_id or amount <= 0:
            return
        try:
            async with self._db() as repo:
                await repo.add_stat(db_id, "emotion", amount)
        except Exception as e:
            log.error(f"Emotion stat DB xatosi: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # REFUSE
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_refuse(self, table: Table, player: Player, data: dict):
        """Legacy `game_refuse` paketini ham juftlik orqali yakunlaymiz."""
        if table.state != STATE_OFFER:
            log.debug(
                "refuse e'tiborsiz: state=%s uid=%s",
                table.state,
                player.id,
            )
            return
        await self._handle_select_choice(table, player, {"choice": "NoKiss"})

    # ════════════════════════════════════════════════════════════════════════
    # ADVANCE BOTTLE
    # ════════════════════════════════════════════════════════════════════════
    async def _advance_bottle(self, table: Table, current: Player):
        players_list = sorted(table.players.values(), key=lambda p: p.seat)
        if not players_list:
            return
        idx = next((i for i, p in enumerate(players_list) if p.id == current.id), 0)
        next_p = players_list[(idx + 1) % len(players_list)]
        table.turn_seat = next_p.seat
        table.bottle_seat = next_p.seat
        table.state = STATE_WAIT

        # Keyingi navbatni tekshirish (jinslar balansi bilan)
        await self._check_and_broadcast_turn(table)

    # ════════════════════════════════════════════════════════════════════════
    # GIFT
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_gift(self, table: Table, player: Player, data: dict):
        if not self._allow_gift_burst(str(player.id)):
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "Juda tez yuboryapsiz, biroz kuting",
                    "ts": self._ts(),
                },
            )
            return

        gift_raw = data.get("gift_type", "")
        gt_raw = str(gift_raw or "").strip().lower()
        gt = self._normalize_item_type(gt_raw)

        receiver_id = str(data.get("receiver_id", ""))
        price = int(data.get("price", GIFT_PRICES.get(gt, HAT_PRICES.get(gt, 5))))
        receiver = table.get_player_flexible(receiver_id)

        # Gift yoki Hat bo'lishi mumkin (crown1 kabi sovg'alar hat kategoriyasida)
        valid = gt in GIFT_TYPES or gt in HAT_TYPES
        if not receiver or not valid:
            await self.send_to(
                player, {"type": "error", "msg": "Noto'g'ri sovg'a", "ts": self._ts()}
            )
            return

        is_love = self._is_gift_love(gt, gt_raw)
        if is_love and not self._gift_love_unlimited(player):
            if GIFT_LOVE_ITEM_ID not in player.items:
                await self._sync_gift_love_from_db(player)
        if is_love and not self._gift_love_unlimited(player) and self._gift_love_stock(player) < 1:
            await self.send_to(
                player,
                {"type": "error", "msg": "«Коктейль Любви» tugadi.", "ts": self._ts()},
            )
            return

        ok = await self._spend_hearts(player, price, "gift", f"gift:{gt}")
        if not ok:
            return

        if is_love:
            self._consume_gift_love(player)
            asyncio.create_task(self._persist_gift_love_stock(player))

        if not self._is_bomb_gift(gt):
            self._clear_dynamite(receiver)

        receiver.kisses += 1
        # g_love («Коктейль Любви»): qabul qiluvchiga +1 ❤️
        if is_love:
            asyncio.create_task(
                self._credit_wallet_hearts(
                    receiver,
                    1,
                    "gift_love_bonus",
                    description=f"from:{player.id}",
                )
            )
            asyncio.create_task(self._push_items_sync(player))
        # Air kiss sovg'asi — receiverga umumiy kiss (reyting) sifatida ham qo'shiladi.
        if "air_kiss" in gt:
            receiver.total_kisses = int(getattr(receiver, "total_kisses", 0) or 0) + 1
            if receiver.db_id:
                asyncio.create_task(
                    self._save_kiss_stats(
                        sender_id=None, receiver_id=receiver.db_id
                    )
                )
                asyncio.create_task(
                    self._check_achievements(
                        receiver,
                        "total_kisses",
                        int(receiver.total_kisses or 0),
                    )
                )
        stick_random = random.randint(0, 1_000_000_000)

        if is_love:
            love_log = {
                "sovga": "g_love",
                "nomi": "Коктейль Любви",
                "kimdan": {
                    "id": player.id,
                    "db_id": player.db_id,
                    "name": player.username,
                },
                "kimga": {
                    "id": receiver.id,
                    "db_id": receiver.db_id,
                    "name": receiver.username,
                },
                "stol": table.table_id,
                "narx": price,
            }
            log.info("[g_love] yuborildi: %s", love_log)
            print(f"[g_love] Коктейль Любви: {player.username} ({player.id}) → {receiver.username} ({receiver.id})", flush=True)

        await self.broadcast(
            table.table_id,
            {
                "type": "game_gift",
                "gift_type": gt,
                "user": player.to_short(),
                "receiver": receiver.to_short(),
                "price": price,
                "magic": False,
                "random": stick_random,
                "ts": self._ts(),
            },
        )

        asyncio.create_task(self._bump_dj_gift_to_active_dj(table, receiver))
        asyncio.create_task(self._save_gift_stats(player.db_id, receiver.db_id, price))

    async def _save_gift_stats(self, sender_id, receiver_id, price: int):
        try:
            async with self._db() as repo:
                if sender_id:
                    await repo.add_stat(sender_id, "expense", price)
                if receiver_id:
                    await repo.add_stat(receiver_id, "importance", price)
                # emotion reytingi faqat game_gesture da (_bump_emotion_rating)
        except Exception as e:
            log.error(f"Gift stat DB xatosi: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # DRINK
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_drink(self, table: Table, player: Player, data: dict):
        drink_raw = str(data.get("drink_type", data.get("drink", ""))).strip()
        dt = self._normalize_item_type(drink_raw.lower())
        dt_drink = "love" if dt == GIFT_LOVE_ITEM_ID else dt
        receiver_id = str(data.get("receiver_id", ""))
        price = int(
            data.get("price", DRINK_PRICES.get(dt_drink, DRINK_PRICES.get(dt, 10)))
        )
        receiver = table.get_player_flexible(receiver_id)

        if not receiver or dt_drink not in DRINK_TYPES:
            await self.send_to(
                player, {"type": "error", "msg": "Noto'g'ri ichimlik", "ts": self._ts()}
            )
            return

        is_love_drink = self._is_gift_love(dt, drink_raw)
        if is_love_drink:
            await self._sync_gift_love_from_db(player)
        if is_love_drink:
            if not self._gift_love_unlimited(player) and self._gift_love_stock(player) < 1:
                await self.send_to(
                    player,
                    {
                        "type": "error",
                        "msg": "«Коктейль Любви» tugadi.",
                        "ts": self._ts(),
                    },
                )
                return

        ok = await self._spend_hearts(player, price, "drink", f"drink:{dt}")
        if not ok:
            return
        # Yuboruvchi: to'lovdan keyin gold klientda yangilansin
        await self._push_wallet_sync(player)

        player.drink_count = int(player.drink_count or 0) + 1

        if dt != DYNAMITE_DRINK_TYPE:
            self._clear_dynamite(receiver)

        drink_rnd = random.randint(0, 1_000_000_000)
        receiver.drink = dt
        receiver.drink_random = drink_rnd
        receiver.drink_count = int(receiver.drink_count or 0) + 1

        if dt == DYNAMITE_DRINK_TYPE:
            await self._wipe_dynamite_victim_chat(table, receiver)
            await self._stop_table_music_if_player_is_sender(table, receiver)

        # Faqat `DRINK_IDS_RECEIVER_HEART_PLUS_1` dagi ichimlik: qabul qiluvchiga +1 gold
        if dt in DRINK_IDS_RECEIVER_HEART_PLUS_1:
            await self._give_hearts(
                receiver,
                1,
                "drink_cocktail_bonus",
                save_to_db=True,
                extra={"from_user_id": str(player.id), "drink_type": dt},
                await_db=True,
            )

        if is_love_drink:
            self._consume_gift_love(player)
            await self._persist_gift_love_stock(player)
            await self._push_items_sync(player)
            # «Коктейль Любви» — qabul qiluvchiga +1 ❤️ (DB + klient balansi)
            await self._credit_wallet_hearts(
                receiver,
                1,
                "drink_love_bonus",
                description=f"from:{player.id}",
            )
            love_drink_log = {
                "ichimlik": "love",
                "nomi": "Коктейль Любви",
                "kimdan": {
                    "id": player.id,
                    "db_id": player.db_id,
                    "name": player.username,
                },
                "kimga": {
                    "id": receiver.id,
                    "db_id": receiver.db_id,
                    "name": receiver.username,
                },
                "stol": table.table_id,
                "narx": price,
            }
            log.info("[love_drink] yuborildi: %s", love_drink_log)
            print(
                f"[love_drink] Коктейль Любви: {player.username} ({player.id}) "
                f"→ {receiver.username} ({receiver.id})",
                flush=True,
            )

        await self.broadcast(
            table.table_id,
            {
                "type": "game_drink",
                "drink_type": dt,
                "user": player.to_short(),
                "receiver": receiver.to_short(),
                "price": price,
                "drink_random": drink_rnd,
                "random": drink_rnd,
                "count": int(receiver.drink_count or 0),
                "count_diff": 1,
                "ts": self._ts(),
            },
        )

    # ════════════════════════════════════════════════════════════════════════
    # HAT
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_hat(self, table: Table, player: Player, data: dict):
        hat_type = data.get("hat_type", "")
        receiver_id = str(data.get("receiver_id", ""))
        price = int(data.get("price", HAT_PRICES.get(hat_type, 20)))
        receiver = table.get_player_flexible(receiver_id)

        if not receiver or hat_type not in HAT_TYPES:
            await self.send_to(
                player, {"type": "error", "msg": "Noto'g'ri shapka", "ts": self._ts()}
            )
            return

        ok = await self._spend_hearts(player, price, "hat", f"hat:{hat_type}")
        if not ok:
            return

        self._clear_dynamite(receiver)
        receiver.hat = hat_type

        hat_rnd = random.randint(0, 1_000_000_000)
        await self.broadcast(
            table.table_id,
            {
                "type": "game_hat",
                "hat_type": hat_type,
                "user": player.to_short(),
                "receiver": receiver.to_short(),
                "price": price,
                "hat_random": hat_rnd,
                "random": hat_rnd,
                "ts": self._ts(),
            },
        )

    # ════════════════════════════════════════════════════════════════════════
    # GESTURE
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_gesture(self, table: Table, player: Player, data: dict):
        gesture = data.get("gesture", "")
        price = int(data.get("price", GESTURE_PRICES.get(gesture, 5)))

        if gesture not in GESTURE_TYPES:
            return

        ok = await self._spend_stars(player, price, "gesture", gesture)
        if not ok:
            return

        # Har bir emotsiya — emotion reytingiga +1 (yuboruvchi).
        self._bump_emotion_rating(player, 1)

        # Oddiy gesture "kiss" ham reytingga tushsin (umumiy kiss).
        # Bu bottle juftligidan mustaqil: foydalanuvchi istalgan vaqtda gesture kiss yuborishi mumkin.
        if gesture == "kiss":
            player.total_kisses = int(getattr(player, "total_kisses", 0) or 0) + 1
            # DB ga yozib qo'yamiz (User.kisses / UserStats.kisses)
            try:
                await self._save_kiss_stats(
                    sender_id=None,
                    receiver_id=player.db_id,
                )
            except Exception:
                pass
            await self._check_achievements(
                player, "total_kisses", int(player.total_kisses or 0)
            )

        await self.broadcast(
            table.table_id,
            {
                "type": "game_gesture",
                "gesture": gesture,
                "user": player.to_short(),
                "price": price,
                "pay_tokens": True,
                "ts": self._ts(),
            },
        )

    async def _db_spend_stars(self, db_id, amount, tx_type, description=""):
        if not db_id:
            return
        try:
            async with self._db() as repo:
                await repo.spend_stars_balance(db_id, amount, tx_type, description)
        except Exception as e:
            log.error(f"Stars balance spend DB xatosi: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # BOTTLE TYPE CHANGE
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_bottle(self, table: Table, player: Player, data: dict):
        bottle_type = data.get("bottle_type", "standart")
        price = int(data.get("price", 10))

        if bottle_type not in BOTTLE_TYPES:
            return

        ok = await self._spend_hearts(player, price, "bottle", f"bottle:{bottle_type}")
        if not ok:
            return

        table.bottle_type = bottle_type
        await self.broadcast(
            table.table_id,
            {
                "type": "game_bottle",
                "bottle_type": bottle_type,
                "user": player.to_short(),
                "price": price,
                "ts": self._ts(),
            },
        )

    # ════════════════════════════════════════════════════════════════════════
    # RANDOM GIFT
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_random(self, table: Table, player: Player, data: dict):
        receiver_id = str(data.get("receiver_id", ""))
        receiver = table.get_player_flexible(receiver_id)
        if not receiver:
            return

        price = 15
        ok = await self._spend_hearts(player, price, "random_gift")
        if not ok:
            return

        gift_type = random.choice(GIFT_TYPES)
        if not self._is_bomb_gift(gift_type):
            self._clear_dynamite(receiver)
        receiver.kisses += 1
        stick_random = random.randint(0, 1_000_000_000)

        await self.broadcast(
            table.table_id,
            {
                "type": "game_gift",
                "gift_type": gift_type,
                "user": player.to_short(),
                "receiver": receiver.to_short(),
                "price": price,
                "magic": True,
                "random": stick_random,
                "ts": self._ts(),
            },
        )
        await self._bump_dj_gift_to_active_dj(table, receiver)
        asyncio.create_task(self._save_gift_stats(player.db_id, receiver.db_id, price))

    # ════════════════════════════════════════════════════════════════════════
    # CHAT
    # ════════════════════════════════════════════════════════════════════════
    async def _game_chat_user_from_history_row(self, table: Table, row: dict) -> dict:
        """DB chat qatoridan klient `game_chat` uchun user obyekti (rasm bilan)."""
        from src.app.services.telegram_profile import NO_IMG, public_avatar_url

        uid = str(row.get("user_id") or "").strip()
        pl = table.get_player_flexible(uid) if uid else None
        if pl:
            return pl.to_short()

        name = (
            row.get("game_username")
            or row.get("username")
            or row.get("from_user")
            or "User"
        )
        photo = NO_IMG
        male = True
        locale = "ru"

        if uid.isdigit() and self._db_factory:
            try:
                async with self._db() as repo:
                    u = await repo.get_user_with_wallet(int(uid))
                if u:
                    photo = public_avatar_url(u.avatar_url) or NO_IMG
                    male = (u.gender or "").lower() != "female"
                    from src.app.api.auth.user_payload import game_display_name

                    name = game_display_name(u) or name
                    locale = (u.language_code or "ru")[:2]
            except Exception as e:
                log.debug("chat history user db: %s", e)

        return {
            "id": uid,
            "uid": uid,
            "userId": uid,
            "name": name,
            "username": name,
            "male": male,
            "photo_url": photo,
            "image": photo,
            "locale": locale,
        }

    async def _send_table_chat_history(
        self, player: Player, table: Table, ts: int
    ) -> None:
        """Yangi kirgan / stol almashtirgan o'yinchiga oxirgi 5 ta chat xabari."""
        if not self._db_factory or not str(table.table_id).isdigit():
            return
        try:
            async with self._db() as repo:
                rows = await repo.get_recent_table_chat_messages(
                    int(table.table_id), limit=5
                )
        except Exception as e:
            log.debug("table chat history: %s", e)
            return
        if not rows:
            return
        messages: list[dict] = []
        for row in rows:
            body = str(row.get("message") or row.get("body") or "").strip()
            if not body:
                continue
            ts_msg = int(row.get("timestamp") or row.get("ts") or ts)
            user = await self._game_chat_user_from_history_row(table, row)
            messages.append(
                {
                    "type": "game_chat",
                    "body": body,
                    "user": user,
                    "timestamp": ts_msg,
                    "ts": ts_msg,
                }
            )
        if not messages:
            return
        await self.send_to(
            player,
            {"type": "game_chat_history", "messages": messages, "ts": ts},
        )

    @staticmethod
    def _strip_chat_mention_prefix(body: str, receiver_name: str) -> str:
        """Klient `@Ism, matn` yuborsa — matn qismi qoladi."""
        text = str(body or "")
        name = str(receiver_name or "").strip()
        if not name:
            return text
        for prefix in (f"@{name},", f"@{name}, ", f"@{name} "):
            if text.startswith(prefix):
                return text[len(prefix) :].lstrip()
        return text

    async def _handle_chat(self, table: Table, player: Player, data: dict):
        if await self._reject_if_dynamite(player, scope="chat"):
            return

        body = str(data.get("body") or data.get("message", "")).strip()[:500]
        receiver_id = str(
            data.get("receiver_id") or data.get("receiver_user_id") or ""
        ).strip()
        receiver = table.get_player_flexible(receiver_id)
        receiver_name = str(
            data.get("receiver_name")
            or data.get("receiver_username")
            or ""
        ).strip()
        if receiver:
            receiver_name = receiver_name or receiver.username
            receiver_id = str(receiver.id)
        if receiver_name:
            body = self._strip_chat_mention_prefix(body, receiver_name)
        if not body:
            return
        if self._db_factory and str(table.table_id).isdigit():
            try:
                async with self._db() as repo:
                    chat_uid = (
                        str(player.db_id)
                        if getattr(player, "db_id", None)
                        else player.id
                    )
                    await repo.append_table_chat_message(
                        int(table.table_id),
                        chat_uid,
                        player.username,
                        body,
                    )
            except Exception as e:
                log.warning(f"chat persist: {e}")
        recv_short = receiver.to_short() if receiver else None
        ts = self._ts()
        await self.broadcast(
            table.table_id,
            {
                "type": "game_chat",
                "body": body,
                "user": player.to_short(),
                "receiver": recv_short,
                "receiver_id": receiver_id or (recv_short or {}).get("id", ""),
                "receiver_name": receiver_name,
                "timestamp": ts,
                "ts": ts,
            },
        )

    async def _handle_locked_message(self, table: Table, player: Player, data: dict):
        """Gizli (locked) xabar — VIP va do'st sharti yo'q; qabul qiluvchi shu stolda bo'lishi kerak."""
        if await self._reject_if_dynamite(player, scope="chat"):
            return

        body = str(data.get("body", "")).strip()[:500]
        receiver_id_raw = data.get("receiver_id", "")
        receiver_name = str(data.get("receiver_name", "") or "").strip()

        async def _err(msg: str) -> None:
            await self.send_to(
                player,
                {
                    "type": "locked_message_error",
                    "message": msg,
                    "ts": self._ts(),
                },
            )

        if not body:
            await _err("Xabar matni bo'sh bo'lishi mumkin emas.")
            return

        sender_key = table.resolve_player_key(player.id) or str(player.id)
        recv_key = table.resolve_player_key(receiver_id_raw)
        if recv_key and recv_key == sender_key:
            await _err("O'zingizga gizli xabar yubora olmaysiz.")
            return

        receiver = table.get_player_flexible(receiver_id_raw)
        if not receiver:
            await _err("Foydalanuvchi bu stolda topilmadi.")
            return

        ts = self._ts()
        locked_payload = {
            "type": "locked_message",
            "body": body,
            "user": player.to_short(),
            "receiver_id": str(receiver.id),
            "receiver_name": receiver_name or receiver.username,
            "timestamp": ts,
            "ts": ts,
        }
        await self.send_to(receiver, locked_payload)
        # Yuboruvchi ham chatda o'z xabarini ko'radi (klient _recv_locked_message).
        await self.send_to(player, locked_payload)

    def _seat_shop_row(
        self,
        *,
        item_id: str,
        display_name: str,
        price: int,
        progress: Player,
        payer: Player,
        kiss_req: int = 0,
        level_req: int = 0,
        music_req: int = 0,
        smile_req: int = 0,
        vip_only: bool = False,
    ) -> dict:
        """HTML5 `seat_clicked_response` uchun bitta sovg'a/smiley qatori."""
        ul = int(progress.level or 1)
        uk = int(progress.total_kisses or 0)
        um = int(progress.dj_score or 0)
        usm = max(
            int(getattr(progress, "compliments_lifetime", 0) or 0),
            int(getattr(progress, "compliments_sent", 0) or 0),
        )
        bal = int(payer.hearts or 0)
        vip_ok = bool(progress.vip)
        if getattr(payer, "is_admin", False):
            is_avail = True
        else:
            is_avail = True
            if vip_only and not vip_ok:
                is_avail = False
            if uk < kiss_req:
                is_avail = False
            if ul < level_req:
                is_avail = False
            if um < music_req:
                is_avail = False
            if usm < smile_req:
                is_avail = False
            if int(price) > bal:
                is_avail = False
        return {
            "id": item_id,
            "name": display_name,
            "price": int(price),
            "kiss_count": int(kiss_req),
            "level_count": int(level_req),
            "music_count": int(music_req),
            "smiley_count": int(smile_req),
            "user_level": ul,
            "user_music_count": um,
            "user_smiley_count": usm,
            "user_balance": bal,
            "isAvailable": is_avail,
            "is_vip": bool(vip_only),
        }

    async def _handle_seat_clicked(
        self, table: Table, player: Player, data: dict
    ) -> None:
        """O'rindiq bosilganda klient `seat_clicked_response` kutadi (sovg'a/smiley ro'yxati)."""
        ts = self._ts()
        seat_number = int(data.get("seat_number", 0) or 0)
        target_uid = normalize_ws_user_ref(data.get("target_user_id"))

        target: Optional[Player] = None
        if target_uid:
            target = table.get_player(target_uid)
        if target is None and seat_number > 0:
            seat_idx = seat_number - 1
            for p in table.players.values():
                if int(p.seat) == seat_idx:
                    target = p
                    break

        if not target:
            await self.send_to(
                player,
                {
                    "type": "seat_clicked_response",
                    "seat_number": seat_number,
                    "game_username": "Boş",
                    "user_id": "",
                    "profile_picture": "/photos/no_img.png",
                    "gifts": [],
                    "smiles": [],
                    "bottles": [],
                    "is_own_seat": False,
                    "is_vip": False,
                    "is_moderator": False,
                    "total_kiss_count": 0,
                    "total_music_count": 0,
                    "total_smile_count": 0,
                    "level": 0,
                    "balance": int(player.hearts or 0),
                    "liked_by_username": "",
                    "like_price": 1,
                    "level_period": 0,
                    "ts": ts,
                },
            )
            return

        is_own = str(player.id) == str(target.id)
        gift_keys = sorted(
            set(GIFT_PRICES.keys())
            | set(GIFT_TYPES_FREE)
            | set(GIFT_TYPES_VIP)
            | set(DRINK_TYPES)
            | set(HAT_TYPES)
        )
        gifts: list[dict] = [
            self._seat_shop_row(
                item_id=g,
                display_name=g,
                price=int(GIFT_PRICES.get(g, 5)),
                progress=target,
                payer=player,
                vip_only=(g in set(GIFT_TYPES_VIP)),
            )
            for g in gift_keys
        ]
        smiles: list[dict] = [
            self._seat_shop_row(
                item_id=g,
                display_name=g,
                price=int(GESTURE_PRICES.get(g, 5)),
                progress=target,
                payer=player,
                vip_only=(g == "braggingvip"),
            )
            for g in GESTURE_TYPES
        ]
        tsm = max(
            int(getattr(target, "compliments_lifetime", 0) or 0),
            int(getattr(target, "compliments_sent", 0) or 0),
        )

        await self.send_to(
            player,
            {
                "type": "seat_clicked_response",
                "seat_number": seat_number,
                "game_username": target.username,
                "user_id": str(target.id),
                "profile_picture": target.photo_url or "/photos/no_img.png",
                "gifts": gifts,
                "smiles": smiles,
                "bottles": [],
                "is_own_seat": is_own,
                "is_vip": bool(target.vip),
                "is_moderator": bool(getattr(target, "is_admin", False)),
                "total_kiss_count": int(target.total_kisses or 0),
                "total_music_count": int(target.dj_score or 0),
                "total_smile_count": tsm,
                "level": int(target.level or 1),
                "balance": int(player.hearts or 0),
                "liked_by_username": "",
                "like_price": int(getattr(target, "harem_price", 1) or 1),
                "level_period": 0,
                "ts": ts,
            },
        )

    async def _handle_shop_clicked(self, player: Player, data: dict) -> None:
        """Butulkalar menyusi — `shop_clicked_response`."""
        ts = self._ts()
        bal = int(player.hearts or 0)
        is_adm = bool(getattr(player, "is_admin", False))
        bottles: list[dict] = []
        for bid in BOTTLE_TYPES:
            price = int(BOTTLE_PRICES.get(bid, 0))
            vip_only = bid == "vipbottle"
            if is_adm:
                ok = True
            else:
                ok = True
                if vip_only and not player.vip:
                    ok = False
                if price > bal:
                    ok = False
            bottles.append(
                {
                    "id": bid,
                    "name": bid,
                    "price_hourly": price,
                    "user_balance": bal,
                    "isAvailable": ok,
                }
            )
        await self.send_to(
            player,
            {
                "type": "shop_clicked_response",
                "user_id": str(data.get("user_id") or player.id),
                "bottles": bottles,
                "ts": ts,
            },
        )

    # ════════════════════════════════════════════════════════════════════════
    # MUSIC
    # ════════════════════════════════════════════════════════════════════════

    def _music_payload_active(self, payload: Optional[dict]) -> bool:
        if not payload:
            return False
        start = int(payload.get("start_timestamp") or 0)
        if start <= 0:
            return False
        dur = int(payload.get("duration") or 0)
        if dur <= 0:
            return True
        return self._ts() < start + dur * 1000

    def _music_gold_cost(self, data: dict) -> int:
        """Oltin narxi — klient musicPrice bilan mos (audio 5, video/YouTube 9)."""
        provider = str(data.get("provider") or "").strip().lower()
        if provider in ("yt", "mv"):
            return 9
        try:
            price = int(data.get("price", 0) or 0)
            if price > 0:
                return price
        except (TypeError, ValueError):
            pass
        return 5

    def _music_dj_points(self, data: dict) -> int:
        """DJ reyting: videosiz musiqa 5, YouTube 9."""
        provider = str(data.get("provider") or "").strip().lower()
        return 9 if provider == "yt" else 5

    async def _bump_dj_gift_to_active_dj(self, table: Table, receiver: Player) -> None:
        """Musiqa ijrosida DJ ga sovg'a — har bir sovg'a uchun +1 DJ."""
        music = table.current_music
        if not music or not self._music_payload_active(music):
            return
        sender_info = music.get("sender") or music.get("user") or {}
        dj = table.get_player_flexible(sender_info.get("id") or sender_info)
        if not dj or dj.id != receiver.id:
            return
        receiver.dj_score = int(getattr(receiver, "dj_score", 0) or 0) + 1
        asyncio.create_task(self._save_dj_stat(receiver.db_id, 1))
        asyncio.create_task(
            self._check_achievements(receiver, "dj_score", receiver.dj_score)
        )

    def _schedule_table_music_clear(self, table: Table, payload: dict) -> None:
        old = table._music_clear_task
        if old is not None and not old.done():
            old.cancel()
        dur = int(payload.get("duration") or 0)
        if dur <= 0:
            table._music_clear_task = None
            return
        start = int(payload.get("start_timestamp") or self._ts())
        delay = max(0.0, (start + dur * 1000 - self._ts()) / 1000.0) + 0.5
        tid = table.table_id

        async def _clear() -> None:
            await asyncio.sleep(delay)
            tbl = self.tables.get(tid)
            if tbl and tbl.current_music is payload:
                tbl.current_music = None

        table._music_clear_task = asyncio.create_task(_clear())

    async def _send_table_music_sync(self, player: Player, table: Table) -> None:
        """Yangi stolga kirganda — stolda qo'yilgan qo'shiq davom etishi uchun."""
        payload = table.current_music
        if not payload or not self._music_payload_active(payload):
            if payload and not self._music_payload_active(payload):
                table.current_music = None
            return
        sync = copy.deepcopy(payload)
        sync["ts"] = self._ts()
        await self.send_to(player, sync)

    def _game_music_broadcast_payload(
        self, table: Table, player: Player, data: dict
    ) -> dict:
        """
        Klient _recv_game_music: sender.id, song_id, provider, start_timestamp.
        Faqat `user` + `id` yuborilsa — JS xato, musiqa ijro bo‘lmaydi.
        """
        sender = player.to_short()
        dj = int(getattr(player, "dj_score", 0) or 0)
        sender["recorder_level"] = max(0, dj // 9)

        song_id = str(
            data.get("id") or data.get("song_id") or data.get("video_id") or ""
        ).strip()
        provider = str(data.get("provider") or "cz").strip().lower() or "cz"
        url = str(data.get("url") or "").strip()
        track_kind = str(
            data.get("type") or data.get("source") or ""
        ).strip().lower()
        source_kind = str(data.get("source") or "").strip().lower()
        is_video = (
            provider == "mv"
            or track_kind in ("movie", "video", "mv")
            or source_kind in ("movie", "video", "mv")
        )
        data_type = str(data.get("type") or "")

        if is_video:
            # Video: YouTube iframe (klient `Ct`), audio stream emas
            provider = "mv"
            if song_id and (
                not url
                or "/api_music/play/" in url
                or "youtube.com" not in url
            ):
                url = f"https://www.youtube.com/watch?v={song_id}"
            data_type = "movie"
        elif provider in ("yt", "cz") and song_id:
            provider = "cz"
            if not url or "youtube.com" in url or "youtu.be" in url:
                url = f"/api_music/play/{song_id}"

        payload: dict = {
            "type": "game_music",
            "sender": sender,
            "user": sender,
            "song_id": song_id,
            "id": song_id,
            "artist": str(data.get("artist") or ""),
            "title": str(data.get("title") or ""),
            "url": url,
            "duration": int(data.get("duration") or 0),
            "icon": str(data.get("icon") or data.get("thumbnail") or ""),
            "provider": provider,
            "track_type": data_type if is_video else str(data.get("type") or ""),
            "source": str(data.get("source") or ""),
            "start_timestamp": self._ts(),
            "ts": self._ts(),
        }

        recv_id = str(data.get("receiver_id") or "").strip()
        if recv_id:
            payload["receiver_id"] = recv_id
            receiver = table.get_player_flexible(recv_id)
            if receiver:
                payload["receiver"] = receiver.to_short()

        return payload

    async def _handle_game_music(self, table: Table, player: Player, data: dict):
        if await self._reject_if_dynamite(player, scope="music"):
            return
        log.info(f"MUSIC: {player.username} requested music: {data}")
        gold_cost = self._music_gold_cost(data)
        dj_points = self._music_dj_points(data)
        ok = await self._spend_hearts(player, gold_cost, "music")
        if not ok:
            await self.send_to(
                player,
                {
                    "type": "gold_music_revert",
                    "gold_diff": gold_cost,
                    "reason": "insufficient_gold",
                    "ts": self._ts(),
                },
            )
            return

        player.dj_score += dj_points
        asyncio.create_task(self._save_dj_stat(player.db_id, dj_points))

        payload = self._game_music_broadcast_payload(table, player, data)
        table.current_music = payload
        self._schedule_table_music_clear(table, payload)
        await self.broadcast(table.table_id, payload)

        asyncio.create_task(self._append_music_history(player, data))

        # Yutuq tekshiruvi (DJ score)
        await self._check_achievements(player, "dj_score", player.dj_score)

    async def _append_music_history(self, player: Player, data: dict) -> None:
        """O'yin xonasida ijro etilgan trek — history papkasiga."""
        if not player.db_id:
            return
        vid = str(
            data.get("id") or data.get("song_id") or data.get("video_id") or ""
        ).strip()
        if not vid:
            return
        provider = str(data.get("provider") or "yt").strip() or "yt"
        is_video = provider == "mv" or str(
            data.get("type") or data.get("source") or ""
        ).lower() in ("movie", "video", "mv")
        if is_video:
            provider = "mv"
        folder = "history_videos" if is_video else "history_songs"
        try:
            from src.app.database.repositories.music import (
                MusicFavoritesRepository,
            )

            async with self._db() as repo:
                mf = MusicFavoritesRepository(repo.session)
                await mf.mark_song(
                    int(player.db_id),
                    folder,
                    provider,
                    vid,
                    favorite=True,
                )
                await repo.session.commit()
            log.info(
                "music history saved user=%s folder=%s provider=%s id=%s",
                player.db_id,
                folder,
                provider,
                vid,
            )
        except Exception as e:
            log.warning("music history append user=%s: %s", player.db_id, e)

    async def _save_dj_stat(self, db_id, amount: int):
        if not db_id:
            return
        try:
            async with self._db() as repo:
                await repo.add_stat(db_id, "dj", amount)
        except Exception as e:
            log.error(f"DJ stat DB xatosi: {e}")

    async def _save_compliment_stat(self, db_id: int, amount: int) -> None:
        if not db_id or amount <= 0:
            return
        try:
            async with self._db() as repo:
                await repo.add_stat(int(db_id), "compliment", int(amount))
        except Exception as e:
            log.debug(f"compliment stat DB: {e}")

    async def _save_spin_stat(self, db_id: int, amount: int) -> None:
        if not db_id or amount <= 0:
            return
        try:
            async with self._db() as repo:
                await repo.add_stat(int(db_id), "bottle_spin", int(amount))
        except Exception as e:
            log.debug(f"spin stat DB: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # COMPLIMENT / COURT (UXAJIVAT)
    # ════════════════════════════════════════════════════════════════════════
    async def _harem_purchase_fail(self, player: Player, err: str = "not_enough_gold"):
        await self.send_to(
            player,
            {"type": "harem_purchase", "error": err, "ts": self._ts()},
        )

    def _schedule_harem_court_taken_notify(
        self, displaced_db_id: int, target_db_id: int, new_owner_db_id: int
    ) -> None:
        """Eski uxajorga Telegram: nishon boshqa odamga o'tdi."""
        if not displaced_db_id or displaced_db_id == new_owner_db_id:
            return
        from src.app.services.harem_notifications import notify_harem_court_taken

        asyncio.create_task(
            notify_harem_court_taken(
                int(displaced_db_id),
                int(target_db_id or 0),
                int(new_owner_db_id),
            )
        )

    async def _deliver_harem_purchase_event(
        self,
        *,
        purchase_table_id: str,
        payload: dict,
        buyer: Player,
        displaced_db_id: int = 0,
    ) -> None:
        """harem_purchase: xarid stoli + boshqa stoldagi eski uxajor (chat va dialog)."""
        table_key = str(purchase_table_id or "")
        broadcast_payload = {
            k: v
            for k, v in payload.items()
            if k not in ("gold", "goldReal")
        }
        await self.send_to(buyer, payload)
        if table_key:
            await self.broadcast(table_key, broadcast_payload)

        oid = int(displaced_db_id or 0)
        buyer_db = int(buyer.db_id or 0)
        if not oid or oid == buyer_db:
            return
        displaced = self._find_player_by_db_id(oid)
        if not displaced:
            return
        if table_key and str(displaced.table_id or "") == table_key:
            return
        await self.send_to(displaced, broadcast_payload)

    async def _release_prior_harem_targets(
        self, pursuer_db_id: int, new_target_db_id: int
    ) -> None:
        """Eski mantiq (bitta nishon). Hozir chaqirilmaydi — ko'p nishon ruxsat."""
        if not pursuer_db_id:
            return
        keep_id = int(new_target_db_id or 0)
        cleared_pairs: list[tuple[int, int]] = []
        try:
            async with self._db() as repo:
                cleared_pairs = await repo.clear_harem_owner_except(
                    pursuer_db_id, except_user_id=keep_id
                )
        except Exception as e:
            log.warning("_release_prior_harem_targets DB: %s", e)

        handled_ids: set[int] = set()

        for tid, paid in cleared_pairs:
            live = self._find_player_by_db_id(int(tid))
            revoke_paid = int(paid or 0)
            if live and revoke_paid <= 0:
                revoke_paid = int(getattr(live, "harem_owner_paid_price", 0) or 0)
            await self._revoke_harem_court_from_admirer(
                pursuer_db_id, revoke_paid, self._find_player_by_db_id(pursuer_db_id)
            )
            if live:
                live.harem_owner_id = 0
                live.harem_owner_paid_price = 0
            handled_ids.add(int(tid))

        for table in self.tables.values():
            for pl in table.players.values():
                db_id = int(pl.db_id or 0)
                if keep_id and db_id == keep_id:
                    continue
                if int(pl.harem_owner_id or 0) != pursuer_db_id:
                    continue
                if db_id and db_id not in handled_ids:
                    await self._revoke_harem_court_from_admirer(
                        pursuer_db_id,
                        int(getattr(pl, "harem_owner_paid_price", 0) or 0),
                        self._find_player_by_db_id(pursuer_db_id),
                    )
                    await self._db_update_user(
                        db_id, harem_owner_id=0, harem_owner_paid_price=0
                    )
                pl.harem_owner_id = 0
                pl.harem_owner_paid_price = 0
                if db_id:
                    handled_ids.add(db_id)
                part = pl.to_participant()
                part["harem_owner_id"] = 0
                await self._attach_harem_owner_payload(part, 0)
                await self.broadcast(
                    table.table_id,
                    self._make_update_user_payload(part),
                )

    async def _resolve_harem_purchase_target(
        self, viewer: Player, target_id: str
    ) -> Tuple[Optional[Player], Optional[int]]:
        """TOP / boshqa stol / offline: uxajor nishonini DB yoki onlayn topadi."""
        if not target_id:
            return None, None

        live = self._find_player_loose(target_id)
        target_db_id: Optional[int] = None
        if live and live.db_id:
            target_db_id = int(live.db_id)
            await self._refresh_player_harem_from_db(live)
        else:
            target_db_id = self._resolve_client_user_ref_to_db_id(target_id, viewer)

        if live:
            return live, target_db_id

        if target_db_id:
            try:
                async with self._db() as repo:
                    db_u = await repo.get_user_with_wallet(int(target_db_id))
                if db_u:
                    return Player.from_db(None, db_u), int(target_db_id)
            except Exception as e:
                log.error("_resolve_harem_purchase_target DB: %s", e)

        return None, None

    async def _sync_harem_state_to_live_tables(
        self, target: Player, owner_db_id: int
    ) -> None:
        """Nishon boshqa stolda onlayn bo'lsa — u yerdagi UI ham yangilanadi."""
        if not target.db_id:
            return
        live = self._find_player_by_db_id(int(target.db_id))
        if not live:
            return
        live.harem_owner_id = int(target.harem_owner_id or 0)
        live.harem_price = int(target.harem_price or 1)
        live.harem_owner_paid_price = int(
            getattr(target, "harem_owner_paid_price", 0) or 0
        )
        await self._refresh_player_harem_from_db(live)
        if not live.table_id:
            return
        part = live.to_participant()
        await self._attach_harem_owner_payload(part, owner_db_id)
        part["harem_price"] = live.harem_price
        await self.broadcast(
            live.table_id,
            self._make_update_user_payload(part),
        )

    async def _handle_harem_purchase(self, table: Table, player: Player, data: dict):
        """
        HTML5 klient: send { type, target_id }; javob o2.fromJSON — target, new_owner,
        ixtiyoriy old_owner, price, price_rank, ts.
        """
        target_id = str(
            data.get("target_id")
            or data.get("user_id")
            or data.get("receiver_id")
            or ""
        ).strip()
        target, target_db_id = await self._resolve_harem_purchase_target(
            player, target_id
        )
        if not target:
            await self._harem_purchase_fail(player, "invalid_target")
            return

        if target.id == player.id or (
            target_db_id
            and player.db_id
            and int(target_db_id) == int(player.db_id)
        ):
            if str(target.table_id or "") == str(table.table_id):
                await self._handle_harem_release(table, player, target)
            else:
                await self._harem_purchase_fail(player, "invalid_target")
            return

        price = int(target.harem_price)
        buyer_db = player.db_id or 0
        if not buyer_db:
            try:
                buyer_db = int(str(player.id))
            except (ValueError, TypeError):
                buyer_db = 0

        # buyer_db = 0 bo'lsa, harid butunlay rad etiladi — aks holda
        # target.harem_owner_id xotirada 0 ga "tozalanardi" va profil
        # bardamlik tugaganidan keyin "Hech kim uxajivat qilmaydi" deb
        # ko'rinardi. Bunday holatda foydalanuvchi qayta kirishi kerak.
        if not buyer_db:
            log.warning(
                "harem_purchase rad: buyer db_id yo'q (id=%r). Sessiya eskirgan.",
                player.id,
            )
            await self._harem_purchase_fail(player, "auth_required")
            return

        if not getattr(player, "is_admin", False) and player.hearts < price:
            await self._harem_purchase_fail(player, "not_enough_gold")
            return

        old_oid = int(target.harem_owner_id or 0)
        old_owner_paid = int(getattr(target, "harem_owner_paid_price", 0) or 0)
        old_owner_short = None
        if old_oid and old_oid != buyer_db:
            old_p = self._find_player_by_db_id(old_oid)
            if old_p:
                old_owner_short = old_p.to_short()
            else:
                try:
                    async with self._db() as repo:
                        db_old = await repo.get_user_with_wallet(old_oid)
                        if db_old:
                            old_owner_short = Player.from_db(None, db_old).to_short()
                except Exception as e:
                    log.debug(f"harem old_owner load: {e}")

        ok = await self._spend_hearts(
            player, price, "harem_purchase", f"harem:{target_id}"
        )
        if not ok:
            await self._harem_purchase_fail(player, "not_enough_gold")
            return

        if old_oid and old_oid != buyer_db and old_owner_paid > 0:
            old_live = self._find_player_by_db_id(old_oid)
            await self._revoke_harem_court_from_admirer(
                old_oid, old_owner_paid, old_live
            )

        await self._apply_harem_court_to_target(
            target, buyer_db, price, buyer_live=player
        )
        await self._refresh_player_harem_from_db(player)

        hp = {
            "type": "harem_purchase",
            "ts": self._ts(),
            "price": price,
            "price_rank": target.harem_price,
            "target": target.to_short(),
            "new_owner": player.to_short(),
        }
        if old_owner_short:
            hp["old_owner"] = old_owner_short

        await self._deliver_harem_purchase_event(
            purchase_table_id=str(table.table_id),
            payload=hp,
            buyer=player,
            displaced_db_id=old_oid if old_oid and old_oid != buyer_db else 0,
        )
        await self._handle_get_wallet(player)

        await self._sync_harem_state_to_live_tables(target, buyer_db)

        if str(player.table_id or "") == str(table.table_id):
            buyer_part = player.to_participant()
            await self._attach_harem_owner_payload(
                buyer_part, int(player.harem_owner_id or 0)
            )
            await self.broadcast(
                table.table_id,
                self._make_update_user_payload(buyer_part),
            )

        if str(target.table_id or "") == str(table.table_id):
            target_participant = target.to_participant()
            await self._attach_harem_owner_payload(target_participant, buyer_db)
            target_participant["harem_price"] = target.harem_price
            await self._refresh_player_harem_from_db(target)
            target_participant["harem_courts_received"] = int(
                getattr(target, "harem_courts_received", 0) or 0
            )
            await self.broadcast(
                table.table_id,
                self._make_update_user_payload(target_participant),
            )

        log.info(
            "HAREM: %s → %s (db=%s) price=%s new=%s from_top=%s",
            player.username,
            target.username,
            target_db_id,
            price,
            target.harem_price,
            str(target.table_id or "") != str(table.table_id),
        )

        if old_oid and old_oid != buyer_db:
            self._schedule_harem_court_taken_notify(
                old_oid, int(target.db_id or 0), buyer_db
            )

        # Don Juan (assets): qarshi jinsga uxajor — har bir muvaffaqiyatli gold-harid
        if bool(player.male) != bool(target.male) and player.db_id:
            try:
                async with self._db() as repo:
                    await repo.add_stat(int(player.db_id), "donjuan", 1)
                    total_dj = await repo.get_stat_total_value(
                        int(player.db_id), "donjuan"
                    )
                await self._check_achievements(player, "donjuan", total_dj)
            except Exception as e:
                log.warning("harem donjuan stat: %s", e)

        # Yutuq tekshiruvi — target uchun (mashhurlik o'sishi)
        await self._check_achievements(target, "harem_price", target.harem_price)

    def _harem_dismiss_hearts_cost(self, harem_price: int) -> int:
        """Otkaz: profil egasidan oxirgi uxajorlik narxi (10 to'langan → harem_price 11)."""
        return max(1, int(harem_price or 1) - 1)

    async def _push_wallet_to_player(self, player: Player) -> None:
        """DB dan qayta o'qimasdan — xotiradagi balansni klientga yuboradi."""
        self._admin_floor_wallet(player)
        wf = player.wallet_for_client()
        await self.send_to(
            player,
            {"type": "get_wallet", "ok": True, **wf, "ts": self._ts()},
        )

    async def _charge_harem_dismiss(
        self, player: Player, cost: int, target_ref: str
    ) -> bool:
        if cost <= 0:
            return True
        if getattr(player, "is_admin", False):
            return True
        if player.hearts < cost:
            await self._harem_purchase_fail(player, "not_enough_gold")
            return False

        player.hearts -= cost
        player.hearts_real = int(player.hearts or 0)

        if player.db_id:
            try:
                async with self._db() as repo:
                    ok, new_bal = await repo.spend_hearts(
                        int(player.db_id),
                        cost,
                        "harem_dismiss",
                        f"harem_dismiss:{target_ref}",
                    )
            except Exception as e:
                log.error("harem_dismiss DB: %s", e)
                ok, new_bal = False, player.hearts
            if not ok:
                player.hearts += cost
                player.hearts_real = int(player.hearts or 0)
                await self._harem_purchase_fail(player, "not_enough_gold")
                return False
            player.hearts = int(new_bal)
            player.hearts_real = int(new_bal)

        await self._push_wallet_to_player(player)
        return True

    async def _handle_harem_release(self, table: Table, player: Player, target: Player):
        """O'z profilidagi «otkaz» / leave: harem_owner_id bo'shatiladi, hearts yechiladi."""
        cur_owner = int(target.harem_owner_id or 0)
        if not cur_owner:
            await self._harem_purchase_fail(player, "invalid_target")
            return

        dismiss_cost = self._harem_dismiss_hearts_cost(target.harem_price)
        if not await self._charge_harem_dismiss(player, dismiss_cost, target.id):
            return

        old_owner_short = None
        if cur_owner != int(player.db_id or 0):
            old_p = self._find_player_by_db_id(cur_owner)
            if old_p:
                old_owner_short = old_p.to_short()
            else:
                try:
                    async with self._db() as repo:
                        db_old = await repo.get_user_with_wallet(cur_owner)
                        if db_old:
                            old_owner_short = Player.from_db(None, db_old).to_short()
                except Exception as e:
                    log.debug(f"harem release old_owner load: {e}")

        await self._clear_target_harem_owner(
            live_target=target,
            target_db_id=target.db_id,
        )
        new_price = await self._bump_target_harem_price(
            live_target=target,
            target_db_id=target.db_id,
        )

        hp = {
            "type": "harem_purchase",
            "ts": self._ts(),
            "price": dismiss_cost,
            "price_rank": new_price,
            "target": target.to_short(),
            "new_owner": target.to_short(),
            "gold": int(player.hearts or 0),
            "goldReal": int(player.hearts_real or 0),
        }
        if old_owner_short:
            hp["old_owner"] = old_owner_short

        await self.send_to(player, hp)
        await self.broadcast(
            table.table_id,
            {k: v for k, v in hp.items() if k not in ("gold", "goldReal")},
        )
        log.info(
            "HAREM release: %s dismissed admirer db_id=%s cost=%s",
            player.username,
            cur_owner,
            dismiss_cost,
        )

    # ════════════════════════════════════════════════════════════════════════
    # HTML5 "UXAJOR" (Like / Court) — welcome page main.be3d9225.js
    # ════════════════════════════════════════════════════════════════════════
    async def _liked_by_info(self, owner_db_id: int) -> Tuple[str, str]:
        """Liked_by (uxajor) username/photo ni jonli stoldan yoki DB dan oladi.

        Agar foydalanuvchi topilmasa ham, owner_db_id mavjud ekan bo'sh
        qaytarmaymiz — klient `liked_by_profile_picture` bo'sh bo'lsa
        umuman uxajor yo'q deb ko'radi. Shu sababli zaxira qiymatlar
        qaytariladi.
        """
        if not owner_db_id:
            return ("", "")
        live = self._find_player_by_db_id(owner_db_id)
        if not live:
            # Guest/JWT id holatida player.db_id None bo'lishi mumkin —
            # players lug'atining str kaliti bo'yicha ham qidiramiz.
            live = self._find_player(str(owner_db_id))
        if live:
            name = live.username or f"user_{owner_db_id}"
            photo = live.photo_url or "/photos/no_img.png"
            return (name, photo)
        try:
            async with self._db() as repo:
                u = await repo.get_user_with_wallet(owner_db_id)
                if u:
                    fake = Player.from_db(None, u)
                    return (
                        fake.username or f"user_{owner_db_id}",
                        fake.photo_url or "/photos/no_img.png",
                    )
        except Exception as e:
            log.debug(f"_liked_by_info: {e}")
        # owner_db_id bor lekin jonli/DB topilmagan — placeholder
        return (f"user_{owner_db_id}", "/photos/no_img.png")

    def _viewer_db_id_or_int_id(self, viewer: Player) -> int:
        """player.db_id; aks holda raqamli id ga aylantirishga harakat qiladi."""
        if viewer.db_id:
            return int(viewer.db_id)
        try:
            return int(str(viewer.id))
        except (TypeError, ValueError):
            return 0

    def _viewer_is_target_profile_owner(
        self,
        viewer: Player,
        target_db_id: Optional[int],
        live_target: Optional[Player],
        raw_target: str,
    ) -> bool:
        """Ko'ruvchi profil egasi (o'z profili) ekanini tekshiradi."""
        viewer_db = self._viewer_db_id_or_int_id(viewer)
        if not viewer_db:
            return False
        if target_db_id and int(viewer_db) == int(target_db_id):
            return True
        if live_target and str(live_target.id) == str(viewer.id):
            return True
        if raw_target and str(raw_target) in (str(viewer.id), str(viewer_db)):
            return True
        return False

    async def _clear_target_harem_owner(
        self,
        *,
        live_target: Optional[Player],
        target_db_id: Optional[int],
    ) -> None:
        """Target uxajorini bo'shatadi; 2-yurak — to'lovchi (admirer) yig'indisidan court narxi ayiriladi."""
        admirer_db = 0
        paid = 0
        if live_target:
            admirer_db = int(live_target.harem_owner_id or 0)
            paid = int(getattr(live_target, "harem_owner_paid_price", 0) or 0)
        tid = int(target_db_id or 0) or int(getattr(live_target, "db_id", 0) or 0)
        if tid and (not admirer_db or paid <= 0):
            try:
                async with self._db() as repo:
                    db_u = await repo.get_user_with_wallet(tid)
                if db_u:
                    if not admirer_db:
                        admirer_db = int(getattr(db_u, "harem_owner_id", 0) or 0)
                    if paid <= 0:
                        paid = int(getattr(db_u, "harem_owner_paid_price", 0) or 0)
            except Exception as e:
                log.debug("_clear_target_harem_owner load: %s", e)

        if admirer_db and paid > 0:
            admirer_live = self._find_player_by_db_id(admirer_db)
            await self._revoke_harem_court_from_admirer(
                admirer_db, paid, admirer_live
            )
            if admirer_live:
                await self._refresh_player_harem_from_db(admirer_live)
                if admirer_live.table_id:
                    adm_part = admirer_live.to_participant()
                    await self.broadcast(
                        admirer_live.table_id,
                        self._make_update_user_payload(adm_part),
                    )

        if live_target:
            live_target.harem_owner_id = 0
            live_target.harem_owner_paid_price = 0
        if target_db_id:
            await self._db_update_user(
                int(target_db_id), harem_owner_id=0, harem_owner_paid_price=0
            )
        if live_target and live_target.table_id:
            part = live_target.to_participant()
            part["harem_owner_id"] = 0
            part["harem_courts_received"] = int(
                getattr(live_target, "harem_courts_received", 0) or 0
            )
            await self._attach_harem_owner_payload(part, 0)
            await self.broadcast(
                live_target.table_id,
                self._make_update_user_payload(part),
            )

    async def _bump_target_harem_price(
        self,
        *,
        live_target: Optional[Player],
        target_db_id: Optional[int],
    ) -> int:
        """harem_price ni aynan 1 ga oshiradi (9 → 10)."""
        cur = 1
        if live_target:
            cur = max(1, int(live_target.harem_price or 1))
        elif target_db_id:
            try:
                async with self._db() as repo:
                    db_u = await repo.get_user_with_wallet(int(target_db_id))
                if db_u:
                    cur = max(1, int(getattr(db_u, "harem_price", 1) or 1))
            except Exception as e:
                log.debug(f"_bump_target_harem_price DB load: {e}")

        new_price = cur + 1
        if live_target:
            live_target.harem_price = new_price
        if target_db_id:
            await self._db_update_user(int(target_db_id), harem_price=new_price)
        if live_target and live_target.table_id:
            part = live_target.to_participant()
            part["harem_price"] = new_price
            part["harem_courts_received"] = int(
                getattr(live_target, "harem_courts_received", 0) or 0
            )
            await self._attach_harem_owner_payload(part, 0)
            await self.broadcast(
                live_target.table_id,
                self._make_update_user_payload(part),
            )
        if live_target:
            await self._check_achievements(live_target, "harem_price", new_price)
        return new_price

    async def _resolve_target_user(
        self, viewer: Player, raw_target: str
    ) -> Tuple[Optional[Player], Optional[int]]:
        """
        (live_player, db_id) qaytaradi. live_player None bo'lishi mumkin —
        u holda faqat DB orqali ishlanadi.
        """
        if not raw_target:
            return None, None
        live = self._find_player_loose(raw_target)
        db_id = live.db_id if live and live.db_id else None
        if not db_id:
            db_id = self._resolve_client_user_ref_to_db_id(raw_target, viewer)
        return live, db_id

    async def _handle_like_user(self, player: Player, data: dict):
        """
        HTML5 klient: { type:"like_user", target_user_id, action: "like"|"cancel" }
          • like   — viewer target ni uxajor qiladi (gold to'lab),
          • cancel — (a) uxajor o'zini olib tashlaydi yoki
                     (b) profil egasi o'z profilida «Ləğv et» bosib uxajorni olib tashlaydi.
        Javob: yangi profile_clicked_response (klient profilni qayta o'qiydi).
        """
        raw_target = str(
            data.get("target_user_id")
            or data.get("user_id")
            or data.get("target_id")
            or ""
        ).strip()
        action = str(data.get("action", "like")).strip().lower() or "like"
        log.info(
            "like_user: viewer=%s db_id=%s target=%r action=%s",
            player.id,
            player.db_id,
            raw_target,
            action,
        )

        live_target, target_db_id = await self._resolve_target_user(player, raw_target)
        if not target_db_id and not live_target:
            log.warning("like_user: target topilmadi raw=%r", raw_target)
            await self.send_to(
                player,
                {
                    "type": "profile_clicked_error",
                    "message": "Foydalanuvchi topilmadi",
                    "ts": self._ts(),
                },
            )
            return

        if live_target and live_target.id == player.id:
            await self.send_to(
                player,
                {
                    "type": "profile_clicked_error",
                    "message": "O'zingizni bəyəna olmaysiz",
                    "ts": self._ts(),
                },
            )
            return

        viewer_db = self._viewer_db_id_or_int_id(player)
        if not viewer_db:
            # Guest/eskirgan sessiya: like qabul qilinmaydi, aks holda
            # harem_owner_id = 0 yozilib profil "uxajorsiz" ko'rinadi.
            log.warning(
                "like_user rad: viewer db_id yo'q (id=%r). Login kerak.", player.id
            )
            await self.send_to(
                player,
                {
                    "type": "profile_clicked_error",
                    "message": "Iltimos, qayta kiring (sessiya eskirgan)",
                    "ts": self._ts(),
                },
            )
            return

        # Target hozirgi holatini DB dan yoki jonli obyektdan olamiz
        cur_owner: int = 0
        cur_price: int = 1
        target_username: str = ""
        target_photo: str = "/photos/no_img.png"
        target_for_extra: Optional[Player] = live_target

        if live_target:
            await self._refresh_player_harem_from_db(live_target)
            cur_owner = int(live_target.harem_owner_id or 0)
            cur_price = int(live_target.harem_price or 1)
            target_username = live_target.username
            target_photo = live_target.photo_url or "/photos/no_img.png"
        elif target_db_id:
            try:
                async with self._db() as repo:
                    db_u = await repo.get_user_with_wallet(target_db_id)
                if db_u:
                    cur_owner = int(getattr(db_u, "harem_owner_id", 0) or 0)
                    cur_price = int(getattr(db_u, "harem_price", 1) or 1)
                    fake = Player.from_db(None, db_u)
                    target_for_extra = fake
                    target_username = fake.username
                    target_photo = fake.photo_url or "/photos/no_img.png"
            except Exception as e:
                log.debug(f"like_user DB load: {e}")

        # Action ishlash ──────────────────────────────────────────────────────
        if action == "cancel":
            dismissed = False
            if viewer_db and cur_owner == viewer_db:
                dismissed = True
                log.info(
                    "LIKE/cancel: viewer=%s removed self as admirer of %s",
                    player.id,
                    raw_target,
                )
            elif (
                viewer_db
                and cur_owner
                and self._viewer_is_target_profile_owner(
                    player, target_db_id, live_target, raw_target
                )
            ):
                dismiss_cost = self._harem_dismiss_hearts_cost(cur_price)
                if not await self._charge_harem_dismiss(
                    player, dismiss_cost, raw_target
                ):
                    return
                dismissed = True
                log.info(
                    "LIKE/cancel: owner=%s dismissed admirer db_id=%s from profile %s cost=%s",
                    player.id,
                    cur_owner,
                    raw_target,
                    dismiss_cost,
                )
            if dismissed:
                await self._clear_target_harem_owner(
                    live_target=live_target,
                    target_db_id=target_db_id,
                )
                cur_owner = 0
                cur_price = await self._bump_target_harem_price(
                    live_target=live_target,
                    target_db_id=target_db_id,
                )
            else:
                log.info(
                    "LIKE/cancel ignore: viewer=%s cur_owner=%s",
                    player.id,
                    cur_owner,
                )

        else:  # action == "like"
            # Allaqachon shu odam uxajor — qaytadan to'lov olmaymiz
            if viewer_db and cur_owner == viewer_db:
                log.info("LIKE: viewer=%s already admirer", player.id)
            else:
                displaced_owner = (
                    int(cur_owner)
                    if cur_owner and int(cur_owner) != viewer_db
                    else 0
                )
                price = max(1, int(cur_price))
                if not getattr(player, "is_admin", False) and player.hearts < price:
                    await self.send_to(
                        player,
                        {
                            "type": "profile_clicked_error",
                            "message": "Yetarli gold yo'q",
                            "ts": self._ts(),
                        },
                    )
                    return
                ok = await self._spend_hearts(
                    player, price, "like_user", f"like:{raw_target}"
                )
                if not ok:
                    return

                displaced_paid = 0
                if displaced_owner:
                    displaced_paid = int(
                        getattr(live_target or target_for_extra, "harem_owner_paid_price", 0)
                        or 0
                    )
                    if displaced_paid <= 0 and target_db_id:
                        try:
                            async with self._db() as repo:
                                db_t = await repo.get_user_with_wallet(int(target_db_id))
                            if db_t:
                                displaced_paid = int(
                                    getattr(db_t, "harem_owner_paid_price", 0) or 0
                                )
                        except Exception as e:
                            log.debug("like_user displaced_paid load: %s", e)
                    if displaced_paid > 0:
                        await self._revoke_harem_court_from_admirer(
                            displaced_owner,
                            displaced_paid,
                            self._find_player_by_db_id(displaced_owner),
                        )

                court_pl = live_target or target_for_extra
                if court_pl:
                    if target_db_id and not court_pl.db_id:
                        court_pl.db_id = int(target_db_id)
                    await self._apply_harem_court_to_target(
                        court_pl, viewer_db, price, buyer_live=player
                    )
                    await self._refresh_player_harem_from_db(player)
                    cur_owner = viewer_db
                    cur_price = int(court_pl.harem_price or 1)
                else:
                    cur_owner = viewer_db
                    cur_price = price + 1
                log.info(
                    "LIKE: viewer=%s → target=%s price=%d new=%d",
                    player.id,
                    raw_target,
                    price,
                    cur_price,
                )

                if displaced_owner:
                    self._schedule_harem_court_taken_notify(
                        displaced_owner,
                        int(target_db_id or 0),
                        viewer_db,
                    )
                    if court_pl:
                        old_owner_short = None
                        old_p = self._find_player_by_db_id(displaced_owner)
                        if old_p:
                            old_owner_short = old_p.to_short()
                        else:
                            try:
                                async with self._db() as repo:
                                    db_old = await repo.get_user_with_wallet(
                                        displaced_owner
                                    )
                                if db_old:
                                    old_owner_short = Player.from_db(
                                        None, db_old
                                    ).to_short()
                            except Exception as e:
                                log.debug("like_user harem old_owner: %s", e)
                        hp_like = {
                            "type": "harem_purchase",
                            "ts": self._ts(),
                            "price": price,
                            "price_rank": cur_price,
                            "target": court_pl.to_short(),
                            "new_owner": player.to_short(),
                        }
                        if old_owner_short:
                            hp_like["old_owner"] = old_owner_short
                        await self._deliver_harem_purchase_event(
                            purchase_table_id=str(player.table_id or ""),
                            payload=hp_like,
                            buyer=player,
                            displaced_db_id=displaced_owner,
                        )

                # Stol uchastniklariga ham eski-uy uchun update_user yuboramiz
                if live_target and live_target.table_id:
                    part = live_target.to_participant()
                    part["harem_owner_id"] = cur_owner
                    part["harem_price"] = cur_price
                    part["harem_courts_received"] = int(
                        getattr(live_target, "harem_courts_received", 0) or 0
                    )
                    await self._attach_harem_owner_payload(part, cur_owner)
                    await self.broadcast(
                        live_target.table_id,
                        self._make_update_user_payload(part),
                    )

                if player.table_id:
                    buyer_part = player.to_participant()
                    await self._attach_harem_owner_payload(
                        buyer_part, int(player.harem_owner_id or 0)
                    )
                    await self.broadcast(
                        player.table_id,
                        self._make_update_user_payload(buyer_part),
                    )

                # Yangi viewer wallet holati
                await self._handle_get_wallet(player)

        # Yangilangan profile_clicked_response — klient bir xil shartlar bo'yicha o'qiydi
        payload = await self._build_profile_clicked_payload(
            target_db_id=target_db_id,
            live_target=live_target,
            target_for_extra=target_for_extra,
            target_username=target_username,
            target_photo=target_photo,
            cur_owner=cur_owner,
            cur_price=cur_price,
            raw_target=raw_target,
        )
        await self.send_to(player, payload)

    async def _handle_profile_clicked(self, player: Player, data: dict):
        """
        HTML5 klient: { type: "profile_clicked", target_user_id }
        Javob: profile_clicked_response (klient profile_clicked_response event ga yozilgan).
        """
        raw_target = str(
            data.get("target_user_id")
            or data.get("user_id")
            or data.get("target_id")
            or ""
        ).strip()
        live_target, target_db_id = await self._resolve_target_user(player, raw_target)
        if not live_target and not target_db_id:
            await self.send_to(
                player,
                {
                    "type": "profile_clicked_error",
                    "message": "Profil topilmadi",
                    "ts": self._ts(),
                },
            )
            return

        cur_owner = 0
        cur_price = 1
        target_username = ""
        target_photo = "/photos/no_img.png"
        target_for_extra: Optional[Player] = live_target

        if live_target:
            await self._refresh_player_harem_from_db(live_target)
            cur_owner = int(live_target.harem_owner_id or 0)
            cur_price = int(live_target.harem_price or 1)
            target_username = live_target.username
            target_photo = live_target.photo_url or "/photos/no_img.png"
        elif target_db_id:
            try:
                async with self._db() as repo:
                    db_u = await repo.get_user_with_wallet(target_db_id)
                if db_u:
                    cur_owner = int(getattr(db_u, "harem_owner_id", 0) or 0)
                    cur_price = int(getattr(db_u, "harem_price", 1) or 1)
                    fake = Player.from_db(None, db_u)
                    target_for_extra = fake
                    target_username = fake.username
                    target_photo = fake.photo_url or "/photos/no_img.png"
            except Exception as e:
                log.debug(f"profile_clicked DB load: {e}")

        payload = await self._build_profile_clicked_payload(
            target_db_id=target_db_id,
            live_target=live_target,
            target_for_extra=target_for_extra,
            target_username=target_username,
            target_photo=target_photo,
            cur_owner=cur_owner,
            cur_price=cur_price,
            raw_target=raw_target,
        )
        await self.send_to(player, payload)

    async def _build_profile_clicked_payload(
        self,
        target_db_id: Optional[int],
        live_target: Optional[Player],
        target_for_extra: Optional[Player],
        target_username: str,
        target_photo: str,
        cur_owner: int,
        cur_price: int,
        raw_target: str,
    ) -> dict:
        """profile_clicked_response payloadini quradi (welcome client formati)."""
        liked_by_username, liked_by_photo = await self._liked_by_info(cur_owner)

        # Statistik maydonlarni jonli yoki DB Player obyektidan olamiz
        extra = target_for_extra
        gender = (extra.gender if extra else "male") or "male"
        level = int(extra.level if extra else 1) or 1
        is_vip = bool(extra.vip if extra else False)
        is_moderator = bool(getattr(extra, "is_admin", False))
        total_kiss_count = int(extra.total_kisses if extra else 0) or 0
        total_music_count = int(extra.dj_score if extra else 0) or 0
        # smile_count: umumiy iltifotlar (lifetime) + bank sikli (kamida)
        _life = int(getattr(extra, "compliments_lifetime", 0) or 0) if extra else 0
        _sent = int(getattr(extra, "compliments_sent", 0) or 0) if extra else 0
        total_smile_count = max(_life, _sent)

        ranks, top = await self._fetch_profile_ranks(target_db_id)

        kiss_rank = int(ranks.get("total_kisses_rank") or 0)
        music_rank = int(ranks.get("dj_score_rank") or 0)
        smile_rank = int(ranks.get("gestures_rank") or 0)
        league_name = ""
        frame_name = (extra.frame if extra else "") or ""
        vip_color = getattr(extra, "vip_color", None)
        status = (extra.status if extra else "") or ""
        user_id_out = (live_target.id if live_target else None) or (
            str(target_db_id) if target_db_id else raw_target
        )

        return {
            "type": "profile_clicked_response",
            "user_id": user_id_out,
            "game_username": target_username,
            "profile_picture": target_photo,
            "level": level,
            "gender": gender,
            "is_vip": is_vip,
            "is_moderator": is_moderator,
            "total_kiss_count": total_kiss_count,
            "kiss_rank": kiss_rank,
            "total_music_count": total_music_count,
            "music_rank": music_rank,
            "total_smile_count": total_smile_count,
            "smile_rank": smile_rank,
            "top": top,
            "league_name": league_name,
            "frame_name": frame_name,
            "vip_color": vip_color,
            "status": status,
            # ── UXAJOR ────────────────────────────────────────────────────
            "liked_by_user_id": str(cur_owner) if cur_owner else "",
            "liked_by_username": liked_by_username,
            "liked_by_profile_picture": liked_by_photo,
            "like_price": int(cur_price or 1),
            "like_price_rank": int(ranks.get("price_rank") or 0),
            "price_rank": int(ranks.get("price_rank") or 0),
            "harem_price_rank": int(ranks.get("harem_price_rank") or 0),
            "total_kisses_rank": kiss_rank,
            "dj_score_rank": music_rank,
            "gestures_rank": smile_rank,
            "ts": self._ts(),
        }

    # ════════════════════════════════════════════════════════════════════════
    # ACHIEVEMENTS (Yutiqlar) — kritik o'yin voqealarida unlock qiladi
    # ════════════════════════════════════════════════════════════════════════
    # Har bir yutiq uchun bosqichlar (1-based level). Klient `assets.json` dagi
    # `counters` ro'yxati bilan mos kelishi kerak.
    # `bonus`: o'sha darajaga yetganda berib yuboriladigan gold.
    ACHIEVEMENTS: dict[str, dict] = {
        # Ko'p o'pgan (kissing) — faqat "Kapitan" (Don Juan emas; assets: Don Juan = uxajor)
        "captain": {
            "metric": "total_kisses",
            "counters": [10, 20, 50, 100, 200],
            "bonus": 30,
        },
        # DJ
        "dj": {"metric": "dj_score", "counters": [5, 15, 50], "bonus": 40},
        "recorder": {
            "metric": "dj_score",
            "counters": [10, 30, 100, 300, 1000, 3000, 10000],
            "bonus": 100,
        },
        # Iltifot (compliment) yuborish
        "kindlysoul": {
            "metric": "compliments_sent",
            "counters": [15, 75, 300, 750, 1500],
            "bonus": 30,
        },
        # Uxajivat narxi (harem_price)
        "celebrity": {
            "metric": "harem_price",
            "counters": [20, 80, 250, 750, 1500],
            "bonus": 50,
        },
        # Bottle aylantirish (newcomer milestone)
        "newcomer": {"metric": "spins", "counters": [1, 5, 10, 50, 100], "bonus": 20},
        # Don Juan — assets.json / RU matn: qarshi jinsga uxajor (gold) haridi; UserStats `donjuan`
        "donjuan": {
            "metric": "donjuan",
            "counters": [5, 25, 100, 250, 500],
            "bonus": 50,
        },
    }

    async def _refresh_player_achievements_from_db(
        self, player: Player, *, force: bool = False
    ) -> None:
        """Sessiyada yutuq holati bo'sh/qayta berilmasligi uchun DB dan yuklash."""
        if not player.db_id or not self._db_factory:
            return
        if not force and getattr(player, "_achievements_hydrated", False):
            return
        try:
            async with self._db() as repo:
                fresh = await repo.get_user_achievements(int(player.db_id))
                for k, v in (fresh or {}).items():
                    player.achievements[k] = int(v or 0)
                player.achievements_bonus_claimed = (
                    await repo.get_user_achievement_bonus_claimed(int(player.db_id))
                )
            player._achievements_hydrated = True
            self._sync_achievement_notified(player)
        except Exception as e:
            log.debug("refresh achievements: %s", e)

    async def _check_achievements(
        self, player: Player, metric: str, total: int
    ) -> None:
        """Berilgan metrika bo'yicha mos yutuqlarni tekshiradi va kerakli
        bo'lsa `achievement_bonus` paketini yuboradi.
        """
        if total <= 0 or not player.db_id:
            return
        async with self._achievement_lock(player):
            await self._refresh_player_achievements_from_db(player, force=True)
            notified = getattr(player, "_achievement_notified", None) or {}
            for key, cfg in self.ACHIEVEMENTS.items():
                if cfg["metric"] != metric:
                    continue
                counters = cfg["counters"]
                cur_level = int(player.achievements.get(key, 0) or 0)
                new_level = cur_level
                for i, threshold in enumerate(counters):
                    if total >= threshold:
                        new_level = max(new_level, i + 1)
                if new_level <= cur_level:
                    continue
                already = int(notified.get(key, 0) or 0)
                if new_level <= already:
                    player.achievements[key] = new_level
                    continue
                bonus_amount = int(cfg.get("bonus", 20))
                try:
                    async with self._db() as repo:
                        await repo.upsert_user_achievement(
                            int(player.db_id), key, new_level
                        )
                except Exception as e:
                    log.error(
                        "achievement persist failed: user=%s key=%s err=%s",
                        player.db_id,
                        key,
                        e,
                        exc_info=True,
                    )
                    continue
                player.achievements[key] = new_level
                if getattr(player, "_achievement_notified", None) is None:
                    player._achievement_notified = {}
                player._achievement_notified[key] = new_level
                log.info(
                    "ACHIEVEMENT unlocked: %s lvl=%d for %s (%s=%d)",
                    key,
                    new_level,
                    player.username,
                    metric,
                    total,
                )
                payload = {
                    "type": "achievement_bonus",
                    "ts": self._ts(),
                    "timestamp": self._ts(),
                    "user": player.to_short(),
                    "achievement_id": key,
                    "level": new_level - 1,
                    "bonus": bonus_amount,
                }
                await self.send_to(player, payload)
                if player.table_id:
                    await self.broadcast(
                        player.table_id,
                        {**payload, "type": "game_achievement"},
                    )

    async def _handle_claim_achievement_bonus(self, player: Player, data: dict) -> None:
        """Klient yutuq mukofotini olishni so'raydi.

        `claim_achievement_bonus` paketida `achievement_id`, `bonus` (gold
        sonu) keladi. Biz bonus qiymatini ACHIEVEMENTS lug'atidan tekshirib,
        gold qo'shamiz va wallet yangilanadi.
        """
        key = str(data.get("achievement_id", "")).strip()
        shared = bool(data.get("shared", False))
        cfg = self.ACHIEVEMENTS.get(key)
        if not cfg or not player.db_id:
            log.debug(f"claim_achievement_bonus: noma'lum id={key!r}")
            return
        async with self._achievement_lock(player):
            await self._refresh_player_achievements_from_db(player, force=True)
            unlocked = int((player.achievements or {}).get(key, 0) or 0)
            if unlocked < 1:
                log.debug(
                    "claim_achievement_bonus: hali ochilmagan id=%s user=%s",
                    key,
                    player.username,
                )
                return
            claimed = int((player.achievements_bonus_claimed or {}).get(key, 0) or 0)
            if claimed >= unlocked:
                log.debug(
                    "claim_achievement_bonus: mukofot allaqachon olingan id=%s user=%s lvl=%s",
                    key,
                    player.username,
                    unlocked,
                )
                return
            next_claim = claimed + 1
            bonus = int(cfg.get("bonus", 20))
            if shared:
                bonus *= 2
            tx_type = f"achievement:{key}:L{next_claim}"
            credited = await self._credit_wallet_hearts(
                player, bonus, tx_type, description=tx_type
            )
            if not credited:
                log.error(
                    "claim_achievement_bonus: hearts DB yozilmadi id=%s user=%s bonus=%d",
                    key,
                    player.username,
                    bonus,
                )
                return
            try:
                async with self._db() as repo:
                    await repo.set_user_achievement_bonus_claimed(
                        int(player.db_id), key, next_claim
                    )
            except Exception as e:
                log.error("achievement bonus claimed persist: %s", e)
                return
            player.achievements_bonus_claimed[key] = next_claim
            log.info(
                "ACHIEVEMENT claim: %s id=%s bonus=%d shared=%s level=%s/%s",
                player.username,
                key,
                bonus,
                shared,
                next_claim,
                unlocked,
            )

    async def _handle_compliment_next(self, player: Player):
        need = COMPLIMENTS_TO_REWARD
        sent = min(player.compliments_sent, need)
        left = max(0, need - sent)
        rewarded = sent >= need
        reward_amt = COMPLIMENT_GOLD_REWARD if rewarded else 0
        if rewarded:
            player.compliments_sent = 0
            if reward_amt:
                player.hearts += reward_amt
                if player.db_id:
                    asyncio.create_task(
                        self._db_add_hearts(
                            player.db_id, reward_amt, "compliment_reward"
                        )
                    )
        msg = {
            "type": "compliment_next",
            "compliments_to_reward": need,
            "compliments_left": left,
            "group_sent": False,
            # Klient tryRewardCompliments ham qo‘shadi — serverda allaqachon qo‘shilgan
            "reward": 0,
            "rewarded": rewarded,
            "refresh_ts": None,
            "ts": self._ts(),
        }
        await self.send_to(player, msg)
        if rewarded and reward_amt:
            await self._handle_get_wallet(player)

    async def _handle_compliment_send(self, player: Player, data: dict):
        player.compliments_sent = min(
            player.compliments_sent + 1, COMPLIMENTS_TO_REWARD
        )
        player.compliments_lifetime += 1
        log.debug(
            f"compliment_send from {player.username} id={data.get('compliment_id')}"
        )
        await self._check_achievements(
            player, "compliments_sent", player.compliments_lifetime
        )
        if player.db_id:
            asyncio.create_task(self._save_compliment_stat(player.db_id, 1))

    async def _handle_compliment_group(self, player: Player):
        player.compliments_sent = COMPLIMENTS_TO_REWARD
        player.compliments_lifetime += COMPLIMENTS_TO_REWARD
        log.debug(f"compliment_group from {player.username}")
        await self._check_achievements(
            player, "compliments_sent", player.compliments_lifetime
        )
        if player.db_id:
            asyncio.create_task(
                self._save_compliment_stat(player.db_id, COMPLIMENTS_TO_REWARD)
            )

    # ════════════════════════════════════════════════════════════════════════
    # BOOSTER
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_turn_booster(self, table: Table, player: Player, data: dict):
        await self.broadcast(
            table.table_id,
            {
                "type": "game_turn_booster",
                "booster": data.get("booster", ""),
                "user_id": player.id,
                "receiver_id": str(data.get("receiver_id", "")),
                "ts": self._ts(),
            },
        )

    # ════════════════════════════════════════════════════════════════════════
    # PROFILE
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_update_profile(self, table: Table, player: Player, data: dict):
        db_fields = await self._apply_player_profile_fields(
            player, data, persist=True
        )
        if not db_fields:
            await self.send_to(
                player, {"type": "update_profile", "ok": True, "ts": self._ts()}
            )
            return

        part = player.to_participant()
        await self._attach_harem_owner_payload(part, int(player.harem_owner_id or 0))
        await self.broadcast(
            table.table_id,
            self._make_update_user_payload(part),
        )
        await self.send_to(
            player, {"type": "update_profile", "ok": True, "ts": self._ts()}
        )

    def _make_update_user_payload(self, part: dict) -> dict:
        """`update_user` paketini ikkala klient uchun mos shaklda quradi.

        • Modern klient `t.user` ichidan o'qiydi.
        • Legacy klient esa to'g'ridan-to'g'ri `t.user_id`, `t.harem_price`,
          `t.photo_url`, `t.frame`, `t.stone` kabi maydonlarni yuqori darajada
          kutadi. Shuning uchun fieldlarni ham yuqorida, ham `user` ichida
          jo'natamiz.
        """
        sid = part.get("id") or part.get("userId") or ""
        flat = {k: v for k, v in part.items()}
        flat["user_id"] = str(sid)
        return {
            "type": "update_user",
            "user": part,
            **flat,
            "ts": self._ts(),
        }

    async def _attach_harem_owner_payload(
        self, payload: dict, harem_owner_db_id: int
    ) -> None:
        """Profil/uxajor payload ga `owner` va `harem_owner` ni qo'shadi.

        Legacy klient (`index-…js`) `get_profile` javobida `i.owner` maydonini
        o'qiydi; modern welcome klient esa `harem_owner`. Shuning uchun ikkala
        maydonni ham bir xil qiymat bilan to'ldiramiz. Aks holda profil
        dialogi `M.owner = t (target)` ga tushadi va "Hech kim uxajivat
        qilmaydi" deb chiqaradi (bug: admire bosgandan keyin g'oyib bo'ladi).
        """
        if not harem_owner_db_id:
            payload.pop("harem_owner", None)
            payload.pop("owner", None)
            return

        def _set(short: dict) -> None:
            payload["harem_owner"] = short
            payload["owner"] = short
            payload["harem_owner_id"] = harem_owner_db_id

        owner_p = self._find_player_by_db_id(harem_owner_db_id)
        if not owner_p:
            # Guest/JWT id holatida player.db_id None bo'lishi mumkin —
            # players lug'atining str kaliti bo'yicha ham qidiramiz.
            owner_p = self._find_player(str(harem_owner_db_id))
        if owner_p:
            _set(owner_p.to_short())
            return
        try:
            async with self._db() as repo:
                db_owner = await repo.get_user_with_wallet(harem_owner_db_id)
                if db_owner:
                    _set(Player.from_db(None, db_owner).to_short())
                    return
        except Exception as e:
            log.debug(f"_attach_harem_owner_payload DB: {e}")
        # Topilmadi — bo'sh qoldirmasdan placeholder beraylik, aks holda
        # klient "Hech kim uxajivat qilmaydi" deb ko'rsatadi.
        sid = str(harem_owner_db_id)
        _set(
            {
                "id": sid,
                "userId": sid,
                "name": f"user_{harem_owner_db_id}",
                "username": f"user_{harem_owner_db_id}",
                "photo_url": "/photos/no_img.png",
                "image": "/photos/no_img.png",
                "premium": False,
                "vip": False,
                "male": True,
            }
        )

    # Profil / get_tops: DB ustun → klientdagi rank maydoni
    _PROFILE_RANK_COLUMNS: tuple[tuple[str, str], ...] = (
        ("total_kisses_rank", "kisses"),
        ("dj_score_rank", "dj"),
        ("gestures_rank", "emotion"),
        # Profil stat qatorlari
        ("price_rank", "expense"),  # 1-yurak: sovg'a sarfi
        ("harem_price_rank", "harem_courts_received"),  # 2-yurak: uxajor yig'indisi
        ("court_price_rank", "harem_price"),  # pastki uxajor bloki: court narxi
    )
    TOP_RANK_MAX = 10

    async def _fetch_profile_ranks(self, db_id: Optional[int]) -> tuple[dict[str, int], bool]:
        """(rank_maydonlari, top_10_ichidami) — har qanday reytingda 1..10."""
        ranks: dict[str, int] = {key: 0 for key, _ in self._PROFILE_RANK_COLUMNS}
        if not db_id:
            return ranks, False
        in_top = False
        try:
            async with self._db() as repo:
                for key, col in self._PROFILE_RANK_COLUMNS:
                    rk, _ = await repo.get_user_rank_by_column(db_id, col)
                    ranks[key] = int(rk or 0)
                    if 1 <= ranks[key] <= self.TOP_RANK_MAX:
                        in_top = True
        except Exception as e:
            log.error(f"_fetch_profile_ranks: {e}")
        return ranks, in_top

    async def _apply_profile_ranks_to_payload(
        self, payload: dict, db_id: Optional[int]
    ) -> dict[str, int]:
        """Reyting o‘rinlari va `top` ni payload ga yozadi."""
        ranks, in_top = await self._fetch_profile_ranks(db_id)
        payload["top"] = in_top
        for key, val in ranks.items():
            payload[key] = val
        return ranks

    async def _enrich_get_profile_payload(
        self, payload: dict, db_id: Optional[int]
    ) -> None:
        """Klient profil dialogi: «в рейтинге» va yutiq kubogi (achievements)."""
        if not db_id:
            payload.setdefault("achievements", [])
            payload.setdefault("top", False)
            return
        try:
            await self._apply_profile_ranks_to_payload(payload, db_id)
            async with self._db() as repo:
                ach = await repo.get_user_achievements(db_id)
                payload["achievements"] = [
                    {
                        "achievement_id": k,
                        "level": achievement_level_to_client(v),
                        "timestamp": 0,
                    }
                    for k, v in sorted(ach.items())
                ]
        except Exception as e:
            log.error(f"_enrich_get_profile_payload: {e}")
            payload.setdefault("achievements", [])
            payload.setdefault("top", False)

    async def _handle_get_profile(self, player: Player, data: dict):
        target_raw = str(data.get("user_id", player.id)).strip()

        p_live = self._find_player_loose(target_raw)
        if p_live:
            await self._refresh_player_harem_from_db(p_live)
            payload = p_live.to_participant()
            await self._attach_harem_owner_payload(
                payload, int(p_live.harem_owner_id or 0)
            )
            payload.update(
                {
                    "type": "get_profile",
                    "ok": True,
                    "ts": self._ts(),
                }
            )
            await self._enrich_get_profile_payload(
                payload, int(p_live.db_id) if p_live.db_id else None
            )
            await self.send_to(player, payload)
            return

        uid_int = self._resolve_client_user_ref_to_db_id(target_raw, player)

        try:
            if uid_int:
                async with self._db() as repo:
                    db_user = await repo.get_user_with_wallet(uid_int)
                    if db_user:
                        fake = Player.from_db(None, db_user)
                        payload = fake.to_participant()
                        await self._attach_harem_owner_payload(
                            payload, int(fake.harem_owner_id or 0)
                        )
                        payload.update(
                            {
                                "type": "get_profile",
                                "ok": True,
                                "ts": self._ts(),
                            }
                        )
                        await self._enrich_get_profile_payload(payload, uid_int)
                        await self.send_to(player, payload)
                        return
        except Exception as e:
            log.error(f"get_profile DB xatosi: {e}")

        await self.send_to(
            player, {"type": "error", "msg": "Topilmadi", "ts": self._ts()}
        )

    async def _handle_set_decorations(self, table: Table, player: Player, data: dict):
        frame = str(data.get("frame") or "").strip()
        stone = str(data.get("stone") or "").strip()
        player.frame = frame
        player.stone = stone
        if player.db_id:
            await self._db_update_user(
                int(player.db_id),
                frame=frame,
                stone=stone,
            )
        part = player.to_participant()
        await self._attach_harem_owner_payload(part, int(player.harem_owner_id or 0))
        await self.broadcast(
            table.table_id,
            self._make_update_user_payload(part),
        )

    async def _handle_reset_photo(self, table: Table, player: Player):
        player.photo_url = "/photos/no_img.png"
        if player.db_id:
            asyncio.create_task(self._db_update_user(player.db_id, avatar_url=None))
        part = player.to_participant()
        await self._attach_harem_owner_payload(part, int(player.harem_owner_id or 0))
        await self.broadcast(
            table.table_id,
            self._make_update_user_payload(part),
        )

    async def _db_update_user(self, db_id: int, **fields):
        try:
            async with self._db() as repo:
                await repo.update_user_fields(db_id, **fields)
        except Exception as e:
            log.error(f"User update DB xatosi: {e}")

    @staticmethod
    def _is_persistable_decor_item(item_id: str) -> bool:
        key = str(item_id or "").strip().lower()
        return key in FRAME_TYPES or key in STONE_TYPES

    async def _db_add_owned_decor(self, db_id: int, item_id: str) -> None:
        try:
            async with self._db() as repo:
                await repo.add_owned_decor_item(int(db_id), item_id)
        except Exception as e:
            log.error("owned_decor save DB: %s", e)

    def find_player_by_db_id(self, db_id: int) -> Optional[Player]:
        """Onlayn o'yinchini DB id bo'yicha topish (to'lovdan keyin sinxron)."""
        if not db_id:
            return None
        uid = int(db_id)
        for table in self.tables.values():
            for p in table.players.values():
                if getattr(p, "db_id", None) == uid:
                    return p
        return None

    async def _send_tokens_insufficient(self, player: Player, required: int) -> None:
        """Token yetarli emas: balance_low + tg_id bo'lsa bot orqali Stars cheki."""
        from src.app.core.stars_support import build_stars_support_path

        current = player.spendable_tokens()
        shortfall = max(0, int(required) - current)
        invoice_sent = False
        if shortfall > 0 and getattr(player, "tg_id", None) and player.db_id:
            from src.app.services.telegram_payments import send_stars_invoice_to_chat

            invoice_sent = await send_stars_invoice_to_chat(
                int(player.tg_id),
                int(player.db_id),
                shortfall,
            )
        from src.app.core.language import normalize_lang

        lang = normalize_lang(
            getattr(player, "language", None) or getattr(player, "locale", None)
        )
        support_path = build_stars_support_path(
            shortfall=shortfall if shortfall > 0 else None,
            lang=lang,
        )
        await self.send_to(
            player,
            {
                "type": "balance_low",
                "required": required,
                "shortfall": shortfall,
                "invoice_amount": shortfall if invoice_sent else 0,
                "invoice_sent": invoice_sent,
                "support_path": support_path,
                "lang": lang,
                "tokens": current,
                "stars_coin": int(player.stars_coin or 0),
                "ts": self._ts(),
            },
        )

    async def _push_wallet_sync(self, player: Player) -> None:
        """Klient `viewer.viewer` gold/tokens bilan sinxron (wallet_sync)."""
        wf = player.wallet_for_client()
        await self.send_to(
            player,
            {
                "type": "wallet_sync",
                **wf,
                "ts": self._ts(),
            },
        )

    def _gift_love_stock_authoritative(self, player: Player) -> int:
        """Ekranda ko‘rsatiladigan son — faqat DB dan sinxronlangan `items.g_love`."""
        if self._gift_love_unlimited(player):
            return GIFT_LOVE_UNLIMITED_MIN
        return max(0, self._gift_love_stock(player))

    def _items_for_client(self, player: Player) -> dict[str, Any]:
        """Klientga yuboriladigan inventar: zaxira 0 bo‘lsa g_love yo‘q (paneldan yashiriladi)."""
        items = dict(player.items)
        stock = self._gift_love_stock_authoritative(player)
        items.pop(GIFT_LOVE_ITEM_ID, None)
        if stock > 0:
            items[GIFT_LOVE_ITEM_ID] = stock
        return items

    def _items_get_payload(self, player: Player) -> dict[str, Any]:
        """items_get + `gift_love_stock` (klient eski merge xatosini oldini oladi)."""
        stock = self._gift_love_stock_authoritative(player)
        return {
            "type": "items_get",
            "items": self._items_for_client(player),
            "gift_love_stock": stock,
            "ts": self._ts(),
        }

    async def _push_gift_love_stock(self, player: Player) -> None:
        """Klient: 0 bo‘lsa kokteylni yashirish (`gift_love_stock` + items_get)."""
        stock = self._gift_love_stock_authoritative(player)
        await self.send_to(
            player,
            {
                "type": "gift_love_stock",
                "stock": stock,
                "ts": self._ts(),
            },
        )

    async def _push_items_sync(self, player: Player) -> None:
        """Klient `viewer.viewer.items` (g_love hisoblagichi va boshqalar)."""
        await self._sync_gift_love_from_db(player)
        await self.send_to(player, self._items_get_payload(player))
        await self._push_gift_love_stock(player)

    @staticmethod
    def _normalize_item_type(raw: str) -> str:
        """`g_dynamite1.webp`, `/dlg100/g_love` kabi asset nomlarini canonical type ga."""
        gt = str(raw or "").strip().lower().split("?", 1)[0].strip()
        if "/" in gt:
            gt = gt.rsplit("/", 1)[-1]
        if gt.endswith(".webp"):
            gt = gt[:-5]
        if gt.startswith("g_dynamite") or gt.startswith("s_dynamite"):
            return DYNAMITE_DRINK_TYPE
        if gt in ("g_love", "love_cocktail"):
            return GIFT_LOVE_ITEM_ID
        return gt

    @staticmethod
    def _is_dynamite_blocked(player: Player) -> bool:
        return str(getattr(player, "drink", "") or "").lower() == DYNAMITE_DRINK_TYPE

    async def _reject_if_dynamite(self, player: Player, *, scope: str = "chat") -> bool:
        """scope: chat — SMS/yozuv; music — DJ qo'shiq (cz/mv/yt)."""
        if not self._is_dynamite_blocked(player):
            return False
        reason = "dont_music" if scope == "music" else "dynamite"
        await self.send_to(
            player,
            {"type": "block_message", "reason": reason, "ts": self._ts()},
        )
        return True

    async def _stop_table_music_if_player_is_sender(
        self, table: Table, player: Player
    ) -> None:
        """Dinamit: qurbon hozir DJ bo'lsa — stol musiqasini to'xtatish."""
        music = table.current_music
        if not music:
            return
        sender = music.get("sender") or music.get("user") or {}
        sid = str(sender.get("id") or sender.get("uid") or "")
        if sid != str(player.id):
            return
        task = getattr(table, "_music_clear_task", None)
        if task is not None and not task.done():
            task.cancel()
        table._music_clear_task = None
        table.current_music = None

    @staticmethod
    def _is_bomb_gift(gift_type: str) -> bool:
        return str(gift_type or "").strip().lower() in BOMB_GIFT_TYPES

    @staticmethod
    def _clear_dynamite(player: Player) -> bool:
        if not GameManager._is_dynamite_blocked(player):
            return False
        player.drink = ""
        player.drink_random = 0
        return True

    async def _wipe_dynamite_victim_chat(self, table: Table, victim: Player) -> None:
        """Dinamit: qurbonning shu stoldagi barcha chat/SMS yozuvlari (DB + UI)."""
        uid_ws = str(victim.id or "").strip()
        uid_db = (
            str(victim.db_id).strip()
            if getattr(victim, "db_id", None)
            else ""
        )
        persist_ids: list[str] = []
        for u in (uid_ws, uid_db):
            if u and u not in persist_ids:
                persist_ids.append(u)

        table_key = str(table.table_id)
        if self._db_factory and table_key.isdigit() and persist_ids:
            try:
                async with self._db() as repo:
                    n = await repo.delete_table_chat_messages_for_user(
                        int(table_key), persist_ids
                    )
                log.info(
                    "dynamite chat wipe table=%s victim=%s deleted=%d",
                    table_key,
                    victim.username,
                    n,
                )
            except Exception as e:
                log.error("dynamite chat wipe DB: %s", e)

        delete_payload = {
            "type": "game_chat_delete_last",
            "ts": self._ts(),
        }
        for uid in persist_ids:
            payload = {**delete_payload, "user_id": uid}
            await self.broadcast(table_key, payload)
            await self.send_to(victim, payload)

    @staticmethod
    def _is_gift_love(gift_type: str, gift_raw: str = "") -> bool:
        gt = str(gift_type or "").strip().lower()
        raw = str(gift_raw or "").strip().lower()
        return gt in (GIFT_LOVE_ITEM_ID, "love") or GIFT_LOVE_ITEM_ID in raw

    def _gift_love_stock(self, player: Player) -> int:
        return int(player.items.get(GIFT_LOVE_ITEM_ID, 0) or 0)

    def _gift_love_unlimited(self, player: Player) -> bool:
        # Faqat aynan 999 = cheksiz; 1000+ oddiy son (har yuborishda −1).
        return self._gift_love_stock(player) == GIFT_LOVE_UNLIMITED_MIN

    def _consume_gift_love(self, player: Player) -> Optional[str]:
        """Inventardan 1 ta ayirish; >=999 cheksiz. Xato matni yoki None."""
        stock = self._gift_love_stock(player)
        if self._gift_love_unlimited(player):
            return None
        if stock < 1:
            return "«Коктейль Любви» tugadi."
        new_stock = stock - 1
        if new_stock > 0:
            player.items[GIFT_LOVE_ITEM_ID] = new_stock
        else:
            player.items.pop(GIFT_LOVE_ITEM_ID, None)
        return None

    async def _persist_gift_love_stock(self, player: Player) -> None:
        """g_love qoldig'ini DB ga yozish (faqat cheklangan zaxira uchun ham saqlanadi)."""
        if not player.db_id or not self._db_factory:
            return
        stock = self._gift_love_stock(player)
        try:
            async with self._db() as repo:
                await repo.update_user_fields(
                    int(player.db_id), gift_love_stock=max(0, stock)
                )
        except Exception as e:
            log.debug("persist gift_love_stock: %s", e)

    async def _sync_gift_love_from_db(self, player: Player) -> None:
        """DB `gift_love_stock` → `player.items.g_love` (ekrandagi hisoblagich)."""
        if not player.db_id or not self._db_factory:
            return
        try:
            async with self._db() as repo:
                db_user = await repo.get_user_by_id(int(player.db_id))
            if not db_user:
                return
            love_stock = int(getattr(db_user, "gift_love_stock", 0) or 0)
            if love_stock > 50_000:
                log.warning(
                    "gift_love_stock juda katta: user_id=%s stock=%s — admin tekshiruvi kerak",
                    player.db_id,
                    love_stock,
                )
            if getattr(player, "is_admin", False):
                player.items[GIFT_LOVE_ITEM_ID] = GIFT_LOVE_UNLIMITED_MIN
            elif love_stock > 0:
                player.items[GIFT_LOVE_ITEM_ID] = love_stock
            else:
                player.items.pop(GIFT_LOVE_ITEM_ID, None)
        except Exception as e:
            log.debug("sync gift_love_from_db: %s", e)

    def _find_online_player_by_db_id(self, db_id: int) -> Optional[Player]:
        """Stolda yoki navbatda turgan onlayn o'yinchi."""
        if not db_id:
            return None
        for qp in self._queue_players.values():
            if qp.db_id == db_id:
                return qp
        for table in self.tables.values():
            for p in table.players.values():
                if p.db_id == db_id:
                    return p
        return None

    async def admin_sync_gift_love_stock(self, db_id: int, stock: int) -> bool:
        """Admin berishdan keyin onlayn sessiyaga items_get yuborish."""
        player = self._find_online_player_by_db_id(int(db_id))
        if not player:
            return False
        stock = max(0, int(stock or 0))
        if getattr(player, "is_admin", False) and stock >= GIFT_LOVE_UNLIMITED_MIN:
            player.items[GIFT_LOVE_ITEM_ID] = GIFT_LOVE_UNLIMITED_MIN
        elif stock > 0:
            player.items[GIFT_LOVE_ITEM_ID] = stock
        else:
            player.items.pop(GIFT_LOVE_ITEM_ID, None)
        # DB allaqachon yangilangan — qayta sync eski qiymatni qaytarib yubormasin
        await self.send_to(player, self._items_get_payload(player))
        await self._push_gift_love_stock(player)
        return True

    async def admin_sync_wallet_after_reset(
        self, user_id: int, *, still_admin: bool = False
    ) -> None:
        """Wallet DB allaqachon 0 — onlayn o'yinchini sinxronlash."""
        uid = int(user_id)
        player = self._find_online_player_by_db_id(uid)
        if not player:
            return

        player.hearts = 0
        player.hearts_real = 0
        player.gift_tokens = 0
        player.stars_coin = 0

        if still_admin:
            player.apply_admin_privileges()
        else:
            player.is_admin = False

        try:
            await self._push_wallet_sync(player)
        except Exception as e:
            log.debug("admin_sync_wallet_after_reset user=%s: %s", uid, e)

    # ════════════════════════════════════════════════════════════════════════
    # NAVIGATION
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_goto_random(self, ws: WebSocket, player: Player):
        """Tasodifiy boshqa xonaga: faqat ko'rinadigan mamlakat + global stollar orasidan."""
        country = normalize_country_code(player.country or "UZBEKISTAN")
        available_ids: List[str] = []

        try:
            c_vis, g_vis = await self._visible_country_and_global_rows(country)
            for r in c_vis + g_vis:
                tid = str(r.id)
                if tid == player.table_id:
                    continue
                if self._room_seated_count(tid) >= MAX_SEATS:
                    continue
                available_ids.append(tid)
        except Exception as e:
            log.error(f"goto_random DB xatosi: {e}")

        if not available_ids:
            available_ids = [
                tid
                for tid, t in self.tables.items()
                if tid != player.table_id and t.player_count() < MAX_SEATS
            ]

        if available_ids:
            new_tid = random.choice(available_ids)
        else:
            try:
                async with self._db() as repo:
                    new_tid = await self._default_join_room_id_for_player(repo, player)
            except Exception:
                new_tid = "1" if player.table_id != "1" else "2"

        log.debug(f"goto_random {player.username} -> {new_tid}")

        old_user_id = player.id
        blocked, until_ts = self.kick_reentry_blocked(new_tid, old_user_id)
        if blocked:
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": self._kick_reentry_ban_msg(until_ts),
                    "kick_ban_until": until_ts,
                    "ts": self._ts(),
                },
            )
            return

        await self.disconnect(ws)
        new_player = await self.connect(ws, new_tid, old_user_id, strict=True)
        if not new_player:
            log.warning(f"goto_random connect failed: {old_user_id} -> {new_tid}")
            return

        ts = self._ts()
        if not getattr(new_player, "plain_ws", False):
            login_pl = await self._login_payload_with_friends(new_player, new_tid, ts)
            await self.send_to(new_player, login_pl)
            await asyncio.sleep(0.2)

        await self._finish_table_join(new_player, new_tid, ts)

        log.info(f"GOTO RANDOM: {old_user_id} -> {new_tid}")

    async def _handle_goto_user(self, ws: WebSocket, player: Player, data: dict):
        target_id = str(data.get("user_id", "")).strip()
        if not target_id:
            await self.send_to(
                player,
                {"type": "error", "msg": "Foydalanuvchi topilmadi", "ts": self._ts()},
            )
            return

        loc = self._find_player_table(target_id)
        if not loc:
            await self.send_to(
                player,
                {"type": "error", "msg": "Foydalanuvchi topilmadi", "ts": self._ts()},
            )
            return

        tid, _t, _pl = loc
        if tid == player.table_id:
            return

        if self._db_factory:
            try:
                rid = int(tid)
                async with self._db() as repo:
                    row = await repo.get_table_by_id(rid)
                    if row:
                        cc = normalize_country_code(player.country or "UZBEKISTAN")
                        is_guest = player.db_id is None
                        if not player_may_join_room_row(
                            cc, row.country_code, is_guest=is_guest
                        ):
                            await self.send_to(
                                player,
                                {
                                    "type": "error",
                                    "msg": "Bu o'yinchining stoli sizning mamlakatingiz uchun emas",
                                    "ts": self._ts(),
                                },
                            )
                            return
                        if not await self._room_id_is_visible_for_country(cc, tid):
                            await self.send_to(
                                player,
                                {
                                    "type": "error",
                                    "msg": "Bu stol hali ro'yxatda ochilmagan",
                                    "ts": self._ts(),
                                },
                            )
                            return
            except (ValueError, TypeError):
                pass
            except Exception as e:
                log.debug(f"goto_user room check: {e}")

        old_uid = player.id
        blocked, until_ts = self.kick_reentry_blocked(tid, old_uid)
        if blocked:
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": self._kick_reentry_ban_msg(until_ts),
                    "kick_ban_until": until_ts,
                    "ts": self._ts(),
                },
            )
            return

        await self.disconnect(ws)
        new_player = await self.connect(ws, tid, old_uid, strict=True)
        if not new_player:
            log.warning(f"goto_user connect failed: {old_uid} -> {tid}")
            return
        ts = self._ts()
        if not getattr(new_player, "plain_ws", False):
            login_pl = await self._login_payload_with_friends(new_player, tid, ts)
            await self.send_to(new_player, login_pl)
            await asyncio.sleep(0.2)
        await self._finish_table_join(new_player, tid, ts)

    async def _handle_goto_room(self, ws: WebSocket, player: Player, data: dict):
        """Tarix / ko'rish: game_id bo'yicha xonaga o'tish (change_room bilan bir xil)."""
        gid = str(data.get("game_id", "")).strip()
        if not gid:
            await self.send_to(
                player,
                {"type": "error", "msg": "game_id yo'q", "ts": self._ts()},
            )
            return
        await self._handle_change_room(ws, player, {"room_id": gid})

    # ════════════════════════════════════════════════════════════════════════
    # KICKOUT
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_user_kickout(self, table: Table, player: Player, data: dict):
        target_id = str(data.get("user_id", ""))
        target = table.get_player(target_id)
        if not target:
            return

        eff = await self._kickout_effective_uses(player)
        req_price = kickout_price_for_use_index(eff)
        try:
            expected_price = int(data.get("expected_price", req_price))
        except (TypeError, ValueError):
            expected_price = -1
        if expected_price != req_price:
            wf = player.wallet_for_client()
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "Kick narxi yangilandi, qayta urinib ko'ring",
                    "gold": wf["gold"],
                    "kickout_info": {
                        "price": req_price,
                        "refresh_ms": 60_000,
                    },
                    "ts": self._ts(),
                },
            )
            return

        ok = await self._spend_hearts(player, req_price, "kickout")
        if not ok:
            return

        await self._commit_kickout_streak_after_success(player)
        if player.db_id:
            next_kick_price = kickout_price_for_use_index(player.kickout_streak_count)
        else:
            next_kick_price = kickout_price_for_use_index(
                self._guest_kick_effective_uses(player)
            )

        now = self._ts()
        kickout_deadline = now + 30_000
        target.kickout_ts = kickout_deadline

        await self.broadcast(
            table.table_id,
            {
                "type": "user_kickout",
                "kicker_user": player.to_short(),
                "kicked_user": target.to_short(),
                "kickout_ts": kickout_deadline,
                "kickout_info": {
                    "price": next_kick_price,
                    "refresh_ms": 60_000,
                },
                "ts": now,
            },
        )

        # 30 soniyadan keyin avtomatik haydash taymeri
        asyncio.create_task(
            self._kickout_timer(table, target, kickout_deadline, player)
        )

    async def _kickout_timer(
        self, table: Table, target: Player, kickout_ts: int, kicker: Player
    ):
        """30 soniya kutadi va agar user qutqarilmagan bo'lsa, stoldan chiqaradi."""
        await asyncio.sleep(30)
        # Hali ham o'sha stoldami va kickout_ts o'zgarmaganmi (qutqarilmaganmi)?
        if target.id in table.players and target.kickout_ts == kickout_ts:
            log.info(f"KICKOUT: {target.username} timed out. Disconnecting.")
            now = self._ts()
            await self.broadcast(
                table.table_id,
                {
                    "type": "user_kicked",
                    "game_id": table.table_id,
                    "kicker_user": kicker.to_short(),
                    "kicked_user": target.to_short(),
                    "unkick_ts": now,
                    "ts": now,
                },
            )
            target.kickout_ts = 0
            self.register_kick_reentry_ban(table.table_id, target)
            await self.send_to(
                target,
                {"type": "session_expired", "reason": "kicked", "ts": now},
            )
            if target.ws:
                await self.disconnect(target.ws)

    async def _handle_admin_unsafe_drop_user(
        self, table: Table, player: Player, data: dict
    ):
        """Adminlar uchun gold sarflamasdan haydash (diagramma 5)."""
        if not player.is_admin:
            return
        target_id = str(data.get("user_id", ""))
        target = table.get_player(target_id)
        if not target:
            return

        log.info(f"ADMIN KICK: {player.username} kicked {target.username}")
        self.register_kick_reentry_ban(table.table_id, target)
        await self.send_to(
            target,
            {"type": "session_expired", "reason": "admin_kick", "ts": self._ts()},
        )
        if target.ws:
            await self.disconnect(target.ws)

    async def _handle_user_save(self, table: Table, player: Player, data: dict):
        target_id = str(data.get("user_id", ""))
        price = int(data.get("expected_price", 15))
        target = table.get_player(target_id)
        if not target:
            return

        ok = await self._spend_hearts(player, price, "save_user")
        if not ok:
            return

        target.kickout_ts = 0
        next_kick = kickout_price_for_use_index(
            await self._kickout_effective_uses(player)
        )
        await self.broadcast(
            table.table_id,
            {
                "type": "user_save",
                "saviour_user": player.to_short(),
                "saved_user": target.to_short(),
                "kickout_info": {"price": next_kick, "refresh_ms": 60_000},
                "ts": self._ts(),
            },
        )

    async def _handle_kickout_refresh(self, player: Player):
        eff = await self._kickout_effective_uses(player)
        p = kickout_price_for_use_index(eff)
        await self.send_to(
            player,
            {
                "type": "kickout_refresh",
                "kickout_info": {"price": p, "refresh_ms": 60_000},
                "ts": self._ts(),
            },
        )

    async def _handle_get_favorite_songs(self, player: Player, data: dict) -> None:
        """Klient `favorite_songs` javobini kutadi: song_ids, max_items."""
        from src.app.database.repositories.music import (
            MusicFavoritesRepository,
            folder_limit,
        )

        folder = str(data.get("folder") or "fav_songs").strip()
        provider = str(data.get("provider") or "cz").strip() or "cz"
        song_ids: list[str] = []

        if player.db_id:
            try:
                async with self._db() as repo:
                    mf = MusicFavoritesRepository(repo.session)
                    song_ids = await mf.get_song_ids(player.db_id, folder, provider)
                    if not song_ids and provider != "cz":
                        song_ids = await mf.get_song_ids(
                            player.db_id, folder, "cz"
                        )
            except Exception as e:
                log.warning("get_favorite_songs user=%s: %s", player.db_id, e)

        await self.send_to(
            player,
            {
                "type": "favorite_songs",
                "folder": folder,
                "provider": provider,
                "song_ids": song_ids,
                "max_items": folder_limit(folder),
                "ts": self._ts(),
            },
        )

    async def _handle_mark_song_favorite(self, player: Player, data: dict) -> None:
        if not player.db_id:
            return
        from src.app.database.repositories.music import MusicFavoritesRepository

        folder = str(data.get("folder") or "fav_songs").strip()
        provider = str(data.get("provider") or "cz").strip() or "cz"
        song_id = str(data.get("song_id") or "").strip()
        favorite = bool(data.get("favorite"))
        if not song_id:
            return
        try:
            async with self._db() as repo:
                mf = MusicFavoritesRepository(repo.session)
                await mf.mark_song(
                    player.db_id,
                    folder,
                    provider,
                    song_id,
                    favorite=favorite,
                )
                await repo.session.commit()
        except Exception as e:
            log.warning("mark_song_favorite user=%s: %s", player.db_id, e)

    async def _handle_get_friends(self, player: Player, data: dict):
        """Do'stlar ro'yxatini qaytaradi (Privacy hisobga olingan)."""
        raw = data.get("user_id")
        if raw is None or raw == "":
            target_uid = player.db_id
        else:
            target_uid = self._resolve_client_user_ref_to_db_id(str(raw), player)

        if not target_uid:
            await self.send_to(
                player,
                {"type": "friends_list", "friends": [], "ts": self._ts()},
            )
            return

        try:
            async with self._db() as repo:
                target_user = await repo.get_user_with_wallet(target_uid)
                if (
                    target_user
                    and target_user.friends_privacy == "only_me"
                    and target_uid != player.db_id
                ):
                    await self.send_to(
                        player,
                        {"type": "friends_list", "friends": [], "ts": self._ts()},
                    )
                    return

                friends = await repo.get_friends(target_uid)
                friends_list = []
                for f in friends:
                    fake = Player.from_db(None, f)
                    friends_list.append(fake.to_participant())

                await self.send_to(
                    player,
                    {
                        "type": "friends_list",
                        "friends": friends_list,
                        "ts": self._ts(),
                    },
                )
        except Exception as e:
            log.error(f"get_friends error: {e}")

    async def _flush_pending_friend_requests(self, player: Player):
        if not player.db_id:
            return
        try:
            async with self._db() as repo:
                pending = await repo.get_incoming_friend_requests(player.db_id)
            for u in pending:
                fake = Player.from_db(None, u)
                await self.send_to(
                    player,
                    {
                        "type": "friend_request",
                        "user": fake.to_short(),
                        "user_id": str(u.id),
                        "ts": self._ts(),
                    },
                )
        except Exception as e:
            log.error(f"flush friend requests: {e}")

    async def _handle_friend_add(self, player: Player, data: dict):
        target_id = str(data.get("user_id", "")).strip()
        target_uid = self._resolve_client_user_ref_to_db_id(target_id, player)
        if player.db_id and target_uid and target_uid != player.db_id:
            try:
                async with self._db() as repo:
                    await repo.add_relation(player.db_id, target_uid, "friend_request")
            except Exception as e:
                log.error(f"friend_add DB: {e}")

        target = self._find_player_loose(target_id)
        if target:
            await self.send_to(
                target,
                {
                    "type": "friend_request",
                    "user": player.to_short(),
                    "user_id": player.id,
                    "ts": self._ts(),
                },
            )
        await self.send_to(player, {"type": "ok", "ts": self._ts()})

    async def _handle_friend_remove(self, player: Player, data: dict):
        target_id = str(data.get("user_id", "")).strip()
        target_uid = self._resolve_client_user_ref_to_db_id(target_id, player)

        if player.db_id and target_uid:
            try:
                async with self._db() as repo:
                    await repo.remove_relation(player.db_id, target_uid, "friend")
                    await repo.remove_relation(target_uid, player.db_id, "friend")
            except Exception as e:
                log.error(f"friend_remove DB: {e}")

        target = self._find_player_loose(target_id)
        if target:
            await self.send_to(
                target,
                {"type": "remove_friend", "user_id": player.id, "ts": self._ts()},
            )
        await self.send_to(player, {"type": "ok", "ts": self._ts()})

    async def _handle_friend_request_answer(self, player: Player, data: dict):
        accepted = self._parse_friend_accept(data)
        target_id = str(data.get("user_id", "")).strip()
        target_uid = self._resolve_client_user_ref_to_db_id(target_id, player)
        if target_id and not target_uid:
            tid_log = target_id if len(target_id) <= 80 else target_id[:80] + "…"
            log.warning(
                "friend_request_answer: user_id DB ga map qilinmadi (%s)", tid_log
            )

        if player.db_id and target_uid and player.db_id != target_uid:
            try:
                async with self._db() as repo:
                    # (yuboruvchi → qabul qiluvchi) va teskarisi — har ikki yo'nalishdagi so'rovni tozalash
                    await repo.remove_relation(
                        target_uid, player.db_id, "friend_request"
                    )
                    await repo.remove_relation(
                        player.db_id, target_uid, "friend_request"
                    )
                    if accepted:
                        ok_a = await repo.add_relation(
                            player.db_id, target_uid, "friend"
                        )
                        ok_b = await repo.add_relation(
                            target_uid, player.db_id, "friend"
                        )
                        if not ok_a or not ok_b:
                            log.warning(
                                f"friend rows incomplete ok_a={ok_a} ok_b={ok_b} "
                                f"{player.db_id}↔{target_uid}"
                            )
            except Exception as e:
                log.error(f"friend_request_answer DB: {e}")

        target = self._find_player_loose(target_id)
        if target:
            if accepted:
                await self.send_to(
                    target,
                    {"type": "add_new_friends", "user_id": player.id, "ts": self._ts()},
                )
            else:
                await self.send_to(
                    target,
                    {"type": "friend_reject", "user_id": player.id, "ts": self._ts()},
                )
        await self.send_to(player, {"type": "ok", "ts": self._ts()})

        if accepted:
            sender_key = target.id if target else str(target_uid)
            await self.send_to(
                player,
                {"type": "add_new_friends", "user_id": sender_key, "ts": self._ts()},
            )
            await self._send_friends_list_snapshot(player)
            await self._send_friends_list_snapshot(target)

    async def _handle_invite_to_table(self, table: Table, player: Player, data: dict):
        """Do'stni joriy stolga taklif qilish (onlayn bo'lsa xabar boradi)."""
        tid = str(data.get("user_id", data.get("target_id", data.get("friend_id", ""))))
        if not tid:
            await self.send_to(
                player, {"type": "error", "msg": "user_id kerak", "ts": self._ts()}
            )
            return
        target = self._find_player(tid)
        if target:
            await self.send_to(
                target,
                {
                    "type": "table_invite",
                    "from": player.to_short(),
                    "inviter": player.to_short(),
                    "room_id": table.table_id,
                    "game_id": table.table_id,
                    "tableId": table.table_id,
                    "ts": self._ts(),
                },
            )
        await self.send_to(
            player, {"type": "ok", "invite_sent": bool(target), "ts": self._ts()}
        )

    async def _handle_admirer_add(self, player: Player, data: dict):
        """Yashirin muxlis / «uxajor» munosabatini DB ga yozadi va onlayn bo'lsa xabar."""
        if not player.db_id:
            await self.send_to(player, {"type": "ok", "ts": self._ts()})
            return
        raw = str(data.get("user_id", data.get("target_id", ""))).strip()
        target_uid = self._resolve_client_user_ref_to_db_id(raw, player)
        if not target_uid:
            await self.send_to(player, {"type": "ok", "ts": self._ts()})
            return
        if target_uid == player.db_id:
            await self.send_to(player, {"type": "ok", "ts": self._ts()})
            return
        try:
            async with self._db() as repo:
                await repo.add_relation(player.db_id, target_uid, "admirer")
        except Exception as e:
            log.error(f"admirer_add: {e}")
        target = self._find_player(str(target_uid)) or self._find_player_by_db_id(
            target_uid
        )
        if target:
            await self.send_to(
                target,
                {
                    "type": "fellow_invite",
                    "user": player.to_short(),
                    "from": player.to_short(),
                    "ts": self._ts(),
                },
            )
        await self.send_to(player, {"type": "ok", "ts": self._ts()})

    def _find_player(self, user_id: str) -> Optional[Player]:
        for t in self.tables.values():
            p = t.get_player(user_id)
            if p:
                return p
        return None

    def _find_player_by_db_id(self, db_id: int) -> Optional[Player]:
        if not db_id:
            return None
        for t in self.tables.values():
            for p in t.players.values():
                if p.db_id == db_id:
                    return p
        return None

    def _jwt_numeric_id_unverified(self, raw: str) -> Optional[int]:
        """Telegram / tashqi JWT — imzosiz dekod (faqat `id` chiqarish uchun)."""
        if not raw or len(raw) < 10:
            return None
        try:
            payload = pyjwt.decode(
                raw,
                options={"verify_signature": False},
                algorithms=["HS256", "RS256", "ES256"],
            )
            for key in ("id", "sub", "user_id"):
                if key not in payload or payload[key] is None:
                    continue
                try:
                    return int(payload[key])
                except (TypeError, ValueError):
                    continue
        except pyjwt.PyJWTError:
            pass
        return None

    def _resolve_client_user_ref_to_db_id(
        self, raw_id: str, viewer: Player
    ) -> Optional[int]:
        """
        Klient `user_id` ba'zan JWT, ba'zan sessiya yoki raqamli string yuboradi —
        friends_list / profil / friend_answer uchun DB id kerak.
        Bo'sh satr uchun None (chaqiruvchi o'zi viewer.db_id ni tanlaydi).
        """
        if not raw_id:
            return None
        raw_id = raw_id.strip()
        if not raw_id:
            return None
        if raw_id == viewer.id and viewer.db_id:
            return viewer.db_id
        try:
            return int(raw_id)
        except (TypeError, ValueError):
            pass
        payload = verify_access_token(raw_id)
        if payload and payload.get("id") is not None:
            try:
                return int(payload["id"])
            except (TypeError, ValueError):
                pass
        sess_uid = game_sessions.verify(raw_id)
        if sess_uid:
            return sess_uid
        pl = self._find_player(raw_id)
        if pl and pl.db_id:
            return pl.db_id
        ext = self._jwt_numeric_id_unverified(raw_id)
        return ext

    def _find_player_loose(self, raw_id: str) -> Optional[Player]:
        if not raw_id:
            return None
        raw_id = raw_id.strip()
        p = self._find_player(raw_id)
        if p:
            return p
        uid = self._jwt_numeric_id_unverified(raw_id)
        if uid:
            return self._find_player_by_db_id(uid)
        try:
            uid = int(raw_id)
            return self._find_player_by_db_id(uid)
        except (TypeError, ValueError):
            pass
        payload = verify_access_token(raw_id)
        if payload and payload.get("id") is not None:
            try:
                return self._find_player_by_db_id(int(payload["id"]))
            except (TypeError, ValueError):
                pass
        sess_uid = game_sessions.verify(raw_id)
        if sess_uid:
            return self._find_player_by_db_id(sess_uid)
        return None

    def _parse_friend_accept(self, data: dict) -> bool:
        if "accepted" in data:
            v = data["accepted"]
        elif "accept" in data:
            v = data["accept"]
        else:
            return False
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return False

    async def _refresh_player_harem_from_db(self, p: Player) -> None:
        if not p.db_id:
            return
        try:
            async with self._db() as repo:
                db_u = await repo.get_user_with_wallet(p.db_id)
                if db_u:
                    p.harem_owner_id = int(db_u.harem_owner_id or 0)
                    p.harem_price = int(db_u.harem_price or 1)
                    p.harem_courts_received = int(
                        getattr(db_u, "harem_courts_received", 0) or 0
                    )
                    p.harem_owner_paid_price = int(
                        getattr(db_u, "harem_owner_paid_price", 0) or 0
                    )
                    # Profil zodiak: klient `birthday_ts` dan hisoblaydi — DB bilan sinxron
                    p.birthday_ts = parse_birth_date_ms(
                        getattr(db_u, "birth_date", None)
                    )
        except Exception as e:
            log.debug(f"_refresh_player_harem_from_db: {e}")

    async def _friends_list_message_for_db_user(self, db_uid: int) -> dict:
        friends_list: List[dict] = []
        async with self._db() as repo:
            friends = await repo.get_friends(db_uid)
            for f in friends:
                friends_list.append(Player.from_db(None, f).to_participant())
        return {"type": "friends_list", "friends": friends_list, "ts": self._ts()}

    async def _send_friends_list_snapshot(self, pl: Optional[Player]) -> None:
        if not pl or not pl.db_id:
            return
        try:
            msg = await self._friends_list_message_for_db_user(pl.db_id)
            await self.send_to(pl, msg)
        except Exception as e:
            log.error(f"_send_friends_list_snapshot: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # ITEMS / PASS / LEAGUE
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_items_get(self, player: Player):
        await self._sync_gift_love_from_db(player)
        await self.send_to(player, self._items_get_payload(player))
        await self._push_gift_love_stock(player)

    async def _try_apply_offer_booster_now(
        self, table: Table, player: Player, booster: str
    ) -> bool:
        """
        O'pish/rad tanlovidan keyin boost bosilganda — shu raund juftiga darhol effekt.
        Aks holda kiss_fire keyingi raunddagi boshqa odamga qo'llanib qoladi.
        """
        if booster not in ("kiss_fire", "refuse_slap"):
            return False
        if table.state != STATE_OFFER or booster not in player.boosters:
            return False

        spin_id = table.current_spinner
        targ_id = table.current_target
        if not spin_id or not targ_id or player.id not in (spin_id, targ_id):
            return False

        is_spinner = player.id == spin_id
        if booster == "kiss_fire":
            acted = (is_spinner and table.spinner_action_done) or (
                not is_spinner and table.target_action_done
            )
            choice = table.spinner_choice if is_spinner else table.target_choice
            if not acted or choice != "Kiss":
                return False
        else:
            acted = (is_spinner and table.spinner_action_done) or (
                not is_spinner and table.target_action_done
            )
            choice = table.spinner_choice if is_spinner else table.target_choice
            if not acted or choice != "NoKiss":
                return False

        partner_id = targ_id if is_spinner else spin_id
        partner = table.get_player(partner_id)
        if not partner:
            return False

        await self.broadcast(
            table.table_id,
            {
                "type": "game_turn_booster",
                "booster": booster,
                "user_id": player.id,
                "receiver_id": partner_id,
                "ts": self._ts(),
            },
        )
        player.boosters = [b for b in player.boosters if b != booster]
        return True

    async def _handle_items_use(self, player: Player, data: dict):
        item = data.get("item", "")
        table = self.tables.get(player.table_id)
        if item in BOOSTER_TYPES and player.items.get(item, 0) > 0:
            player.items[item] -= 1
            player.boosters.append(item)
            if table:
                await self._try_apply_offer_booster_now(table, player, item)
        await self.send_to(
            player,
            {
                "type": "items_use",
                "ok": True,
                "item": item,
                "items": player.items,
                "ts": self._ts(),
            },
        )

    async def _handle_pass_info(self, player: Player):
        await self.send_to(
            player,
            {
                "type": "pass_info",
                "state": "running",
                "pass_state": "running",
                "pass_premium": False,
                "level": 1,
                "score": 0,
                "chest": {
                    "gold": 0,
                    "gold_max": 1000,
                    "overscore2gold": 10,
                    "claimed": False,
                },
                "levels": [],
                "tasks": [],
                "ts": self._ts(),
            },
        )

    async def _handle_league_info(self, player: Player):
        total_k = int(player.total_kisses or 0)
        league_tier = league_tier_from_total_kisses(total_k)
        state = league_state_for_total_kisses(total_k)
        await self.send_to(
            player,
            {
                "type": "league_info",
                "league": max(league_tier, 1) if state == "running" else league_tier,
                "league_state": state,
                "max_league": 16,
                "finish_ms": self._ts() + 86400000,
                "frame": "",
                "gifts": ["heartangel", "brokenheart", "heartdevil"],
                "gold": [4, 3, 2, 1, 1, 1, 1, 1, 1, 1],
                "items": {},
                "kisses": total_k,
                "kisses_lim": 500,
                "move_down": 0,
                "move_up": 6,
                "start_ms": self._ts() - 3600000,
                "stone": "",
                "tokens": [5, 1],
                "users_max": 20,
                "users": [],
                "ts": self._ts(),
            },
        )

    async def _handle_league_claim_reward(self, player: Player):
        await self._give_hearts(player, 50, "league_claim_reward", save_to_db=True)

    # Legacy `get_tops` so'rovi → DB ustun nomi (User jadvalida).
    # Klient `M[<top_type>]` orqali skor o'qiydi, shuning uchun maydon
    # nomini saqlash zarur.
    _LEGACY_TOPS_COL_MAP = {
        "total_kisses": "kisses",
        "dj_score": "dj",
        # TOP «Самые дорогие» (1 yurak) — court narxi (keyingi uxajorlik narxi)
        "price": "harem_price",
        # TOP (2 yurak) — uxajor to'lovlari yig'indisi (profil 2-yurak bilan bir xil)
        "harem_price": "harem_courts_received",
        # «Emotsiyalar» reytingi — faqat users.emotion (game_gesture +1)
        "gestures": "emotion",
        # Eski (UserStats) atashlar — backward compatibility uchun
        "kisses": "kisses",
        "dj": "dj",
        "expense": "expense",
        "importance": "importance",
        "emotion": "emotion",
    }

    async def _handle_get_tops(self, player: Player, data: dict):
        """Legacy klient TOP reytinglarini olish.

        Klient kutgan shakl:
        {
          "type": "get_tops",
          "tops": {
            "<top_type>": {
              "top": [{"id", "male", "name", "photo_url", "<top_type>": <score>}, ...],
              "self_rank": <int>,
              "self_score": <int>,
              "top_reset_ms": <int>
            }
          }
        }
        """
        tops_types = data.get("tops", ["total_kisses"])
        if isinstance(tops_types, str):
            tops_types = [tops_types]
        size = int(data.get("size", 50) or 50)
        size = min(max(size, 1), 100)

        result: dict[str, dict] = {}
        try:
            async with self._db() as repo:
                for top_type in tops_types:
                    col_name = self._LEGACY_TOPS_COL_MAP.get(top_type)
                    if not col_name:
                        result[top_type] = {
                            "top": [],
                            "self_rank": 0,
                            "self_score": 0,
                            "top_reset_ms": 0,
                        }
                        continue
                    top_min = 1 if col_name == "harem_price" else 0
                    rows = await repo.get_top_by_user_column(
                        col_name, limit=size, min_score=top_min
                    )
                    top_items = []
                    for row in rows:
                        item = {
                            "id": row["id"],
                            "male": row["male"],
                            "name": row["name"],
                            "username": row["username"],
                            "photo_url": row["photo_url"],
                            top_type: row["score"],
                        }
                        top_items.append(item)

                    self_rank, self_score = (0, 0)
                    if player.db_id:
                        self_rank, self_score = await repo.get_user_rank_by_column(
                            int(player.db_id), col_name, min_score=top_min
                        )

                    result[top_type] = {
                        "top": top_items,
                        "self_rank": self_rank,
                        "self_score": self_score,
                        "top_reset_ms": 0,
                    }
        except Exception as e:
            log.error(f"get_tops DB xatosi: {e}")
            result = {
                t: {"top": [], "self_rank": 0, "self_score": 0, "top_reset_ms": 0}
                for t in tops_types
            }

        await self.send_to(
            player,
            {
                "type": "get_tops",
                "ok": True,
                "tops": result,
                "ts": self._ts(),
            },
        )

    async def _handle_translate(self, player: Player, data: dict):
        from src.app.core.language import resolve_translate_target_lang
        from src.app.services.google_translate import translate_text

        text = str(data.get("text") or "").strip()
        req_id = data.get("req_id", 0)
        target = resolve_translate_target_lang(
            request_lang=data.get("lang"),
            player_locale=getattr(player, "locale", None),
            player_language=getattr(player, "language", None),
        )
        ttext = await translate_text(text, target_lang=target)
        await self.send_to(
            player,
            {
                "type": "translate",
                "req_id": req_id,
                "ttext": ttext,
                "ts": self._ts(),
            },
        )

    # ════════════════════════════════════════════════════════════════════════
    # BONUSLAR
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_claim_rewarded_video(self, player: Player):
        rewarded_ms = self._ts() + 3_600_000
        await self._give_hearts(
            player,
            REWARDED_VIDEO_GOLD,
            "claim_rewarded_video_bonus",
            save_to_db=True,
            extra={"rewarded_video_ms": rewarded_ms},
        )

    async def _handle_claim_vip_tokens(self, player: Player):
        tokens_ms = self._ts() + 86400000
        if getattr(player, "is_admin", False):
            self._admin_floor_wallet(player)
        else:
            player.stars_coin = int(player.stars_coin or 0) + 10
            player.sync_token_display()
            if player.db_id:
                asyncio.create_task(self._db_add_stars(player.db_id, 10, "vip_tokens"))
        wf = player.wallet_for_client()
        await self.send_to(
            player,
            {
                "type": "claim_vip_tokens",
                "tokens_vip_ms": tokens_ms,
                "tokens_inc": 10,
                "tokens": wf["tokens"],
                "ts": self._ts(),
            },
        )

    async def _db_add_stars(self, db_id, amount, tx_type):
        if not db_id:
            return
        try:
            async with self._db() as repo:
                await repo.add_stars_coin(db_id, amount, tx_type)
        except Exception as e:
            log.error(f"Stars coin add DB xatosi: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # SOTIB OLISH
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_vip_purchase(self, player: Player, data: dict):
        from src.app.api.ws.constants import (
            VIP_BONUS_STARS,
            VIP_PLAN_DAYS,
            VIP_PLAN_STARS,
        )

        if getattr(player, "is_admin", False):
            player.vip = True
            player.grant_default_owned_items()
            self._admin_floor_wallet(player)
            wf = player.wallet_for_client()
            uid = str(player.id)
            await self.send_to(
                player,
                {
                    "type": "update_vip",
                    "user_id": uid,
                    "vip": True,
                    "tokens": wf["tokens"],
                    "ts": self._ts(),
                },
            )
            await self._push_wallet_sync(player)
            table = self.tables.get(player.table_id)
            if table:
                await self.broadcast(
                    table.table_id,
                    {"type": "user_vip_upgraded", "uid": uid, "ts": self._ts()},
                )
            return

        if not player.db_id:
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "VIP uchun akkaunt bilan kiring.",
                    "ts": self._ts(),
                },
            )
            return

        plan = str(data.get("plan") or "week").lower()
        if plan not in VIP_PLAN_STARS:
            plan = "week"
        price = VIP_PLAN_STARS[plan]
        extend_days = VIP_PLAN_DAYS[plan]

        try:
            async with self._db() as repo:
                ok, new_sc, new_gt = await repo.purchase_vip_with_gift_tokens(
                    player.db_id,
                    price,
                    VIP_BONUS_STARS,
                    extend_days,
                )
        except Exception as e:
            log.error(f"VIP purchase DB: {e}")
            await self.send_to(
                player,
                {"type": "error", "msg": "VIP sotib olishda xato.", "ts": self._ts()},
            )
            return

        if not ok:
            await self._send_tokens_insufficient(player, price)
            return

        player.stars_coin = new_sc
        player.gift_tokens = new_gt
        player.sync_token_display()
        player.vip = True
        player.grant_default_owned_items()

        try:
            async with self._db() as repo:
                fresh = await repo.get_user_with_wallet(int(player.db_id))
                if fresh:
                    player.vip = bool(fresh.vip_status)
                    exp = getattr(fresh, "vip_expires_at", None)
                    if exp is not None and exp < datetime.now():
                        player.vip = False
        except Exception as e:
            log.debug("vip_purchase refresh user: %s", e)

        wf = player.wallet_for_client()
        uid = str(player.id)
        await self.send_to(
            player,
            {
                "type": "update_vip",
                "user_id": uid,
                "vip": True,
                "tokens": wf["tokens"],
                "ts": self._ts(),
            },
        )
        await self._push_wallet_sync(player)

        table = self.tables.get(player.table_id)
        if table:
            await self.broadcast(
                table.table_id,
                {"type": "user_vip_upgraded", "uid": uid, "ts": self._ts()},
            )

    async def _handle_item_purchase(self, player: Player, data: dict):
        item = data.get("item", "")
        canon = self._normalize_item_type(str(item))
        if canon == GIFT_LOVE_ITEM_ID or self._is_gift_love(canon, str(item)):
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "«Коктейль Любви» faqat admin orqali beriladi.",
                    "ts": self._ts(),
                },
            )
            return

        price = int(data.get("price", 30))

        ok = await self._spend_hearts(player, price, "item_purchase", f"item:{item}")
        if not ok:
            return

        store_key = canon if self._is_persistable_decor_item(canon) else str(item)

        if item in BOOSTER_TYPES:
            player.boosters.append(item)
        player.items[store_key] = player.items.get(store_key, 0) + 1

        if player.db_id and self._is_persistable_decor_item(store_key):
            await self._db_add_owned_decor(int(player.db_id), store_key)

        await self.send_to(
            player,
            {
                "type": "item_purchase",
                "ok": True,
                "item": store_key,
                "items": self._items_for_client(player),
                "gift_love_stock": self._gift_love_stock_authoritative(player),
                "ts": self._ts(),
            },
        )

        # Agar sotib olingan item butilka bo'lsa, stolga qollash
        if item.startswith("b_") or item in [
            "champagnebot",
            "vodkabot",
            "jackdaniels",
            "yacht",
            "skeleton",
            "pirate",
            "standart",
        ]:
            await self._apply_bottle_to_table(player.table_id, item)

    async def _handle_bottle_selected(self, player: Player, data: dict):
        item = data.get("item", data.get("bottle", ""))
        if item:
            await self._apply_bottle_to_table(player.table_id, item)

    async def _apply_bottle_to_table(self, table_id: str, bottle_type: str):
        table = self.tables.get(table_id)
        if table:
            table.bottle_type = bottle_type
            await self.broadcast(
                table_id,
                {
                    "type": "game_bottle",
                    "bottle": bottle_type,
                    "bottle_type": bottle_type,
                    "ts": self._ts(),
                },
            )

    # ════════════════════════════════════════════════════════════════════════
    # KLIENT REQUEST — minifikatsiya qilingan JS (request/recv) bilan mos
    # ════════════════════════════════════════════════════════════════════════

    async def _handle_pass_claim_level_reward(self, player: Player, data: dict):
        ts = self._ts()
        level = data.get("level", 0)
        line = data.get("line", "free")
        await self.send_to(
            player,
            {
                "type": "pass_claim_level_reward",
                "level": level,
                "line": line,
                "ts": ts,
            },
        )

    async def _handle_pass_claim_chest_reward(self, player: Player):
        ts = self._ts()
        await self.send_to(
            player,
            {
                "type": "pass_claim_chest_reward",
                "ts": ts,
            },
        )

    async def _handle_activate_percent_bonus(self, player: Player):
        """activate_percent_bonus so'rovi → javob type=percent_bonus (klient request() kaliti)."""
        ts = self._ts()
        await self.send_to(
            player,
            {
                "type": "percent_bonus",
                "percent_bonus": {
                    "percent": 0,
                    "purchases_left": 0,
                    "active_upto": ts,
                },
                "ts": ts,
            },
        )

    async def _handle_gold2tokens_get(self, player: Player):
        """Almashtirish narxlari (klient gold2tokens_get: s => t(s.items))."""
        ts = self._ts()
        await self.send_to(
            player,
            {
                "type": "gold2tokens_get",
                "items": list(GOLD2TOKENS_ITEMS),
                "ts": ts,
            },
        )

    async def _handle_gold2tokens(self, player: Player, data: dict):
        """gold (hearts) → stars_coin (tokens). Klient goldni kamaytirgan; server authoritative."""
        gold = int(data.get("gold", 0) or 0)
        if gold <= 0:
            return
        tokens_inc = GOLD2TOKENS_BY_GOLD.get(gold)
        if tokens_inc is None:
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "Noto'g'ri almashtirish",
                    "ts": self._ts(),
                },
            )
            return
        ok = await self._spend_hearts(player, gold, "gold2tokens")
        if not ok:
            return
        # Admin: gold yechilmaydi (`_spend_hearts`); yulduz ham DB/oddiy foydalanuvchi kabi oshmasin — hearts bilan bir xil.
        if getattr(player, "is_admin", False):
            self._admin_floor_wallet(player)
            wf = player.wallet_for_client()
            await self.send_to(
                player,
                {
                    "type": "gold2tokens",
                    "tokens_inc": 0,
                    "tokens": wf["tokens"],
                    "gold": wf["gold"],
                    "ts": self._ts(),
                },
            )
            return
        player.stars_coin = int(player.stars_coin or 0) + tokens_inc
        player.sync_token_display()
        if player.db_id:
            asyncio.create_task(
                self._db_add_stars(player.db_id, tokens_inc, "gold2tokens")
            )
        wf = player.wallet_for_client()
        await self.send_to(
            player,
            {
                "type": "gold2tokens",
                "tokens_inc": tokens_inc,
                "tokens": wf["tokens"],
                "gold": wf["gold"],
                "ts": self._ts(),
            },
        )

    async def _handle_vk_quest_bonus(self, player: Player):
        bonus = 30
        await self._give_hearts(player, bonus, "vk_quest_bonus", save_to_db=True)

    async def _handle_get_uninvited_friends(self, player: Player):
        await self.send_to(
            player,
            {
                "type": "uninvited_friends",
                "ids": [],
                "ts": self._ts(),
            },
        )

    # ════════════════════════════════════════════════════════════════════════
    # DELETE ACCOUNT
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_delete_account(self, ws: WebSocket, player: Player):
        await self.send_to(player, {"type": "session_expired", "ts": self._ts()})
        await self.disconnect(ws)

    # ════════════════════════════════════════════════════════════════════════
    # UTIL
    # ════════════════════════════════════════════════════════════════════════
    def _ts(self) -> int:
        return int(time.time() * 1000)

    async def _spend_hearts(
        self, player: Player, amount: int, tx_type: str, description: str = ""
    ) -> bool:
        if amount <= 0:
            return True
        if getattr(player, "is_admin", False):
            return True
        if player.hearts < amount:
            wf = player.wallet_for_client()
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "Gold yetarli emas",
                    "gold": wf["gold"],
                    "ts": self._ts(),
                },
            )
            return False

        player.hearts -= amount
        if not getattr(player, "is_admin", False):
            player.hearts_real = int(player.hearts or 0)

        if player.db_id:
            asyncio.create_task(
                self._db_spend_hearts(player.db_id, amount, tx_type, description)
            )
        return True

    async def _spend_stars(
        self, player: Player, amount: int, tx_type: str, description: str = ""
    ) -> bool:
        """Token yechish; admin uchun `_spend_hearts` kabi — yechilmaydi, DB ga yozilmaydi."""
        if amount <= 0:
            return True
        if getattr(player, "is_admin", False):
            return True
        if player.spendable_tokens() < amount:
            await self._send_tokens_insufficient(player, amount)
            return False
        player.stars_coin = int(player.stars_coin or 0) - amount
        player.sync_token_display()
        if player.db_id:
            asyncio.create_task(
                self._db_spend_stars(player.db_id, amount, tx_type, description)
            )
        return True

    async def _db_spend_hearts(
        self, db_id: int, amount: int, tx_type: str, description: str
    ):
        try:
            async with self._db() as repo:
                await repo.spend_hearts(db_id, amount, tx_type, description)
        except Exception as e:
            log.error(f"Hearts spend DB xatosi: {e}")

    async def _give_hearts(
        self,
        player: Player,
        amount: int,
        tx_type: str,
        save_to_db: bool = False,
        extra: dict = None,
        *,
        await_db: bool = False,
    ):
        player.hearts += amount
        player.hearts_real = int(player.hearts or 0)

        if save_to_db and player.db_id and not getattr(player, "is_admin", False):
            if await_db:
                await self._db_add_hearts(player.db_id, amount, tx_type)
            else:
                asyncio.create_task(self._db_add_hearts(player.db_id, amount, tx_type))

        wf = player.wallet_for_client()
        msg = {
            "type": tx_type,
            **wf,
            "gold_diff": amount,
            "ts": self._ts(),
        }
        if extra:
            msg.update(extra)
        await self.send_to(player, msg)
        await self._push_wallet_sync(player)

    async def _db_add_hearts(self, db_id: int, amount: int, tx_type: str):
        try:
            async with self._db() as repo:
                await repo.add_hearts(db_id, amount, tx_type)
        except Exception as e:
            log.error(f"Hearts add DB xatosi: {e}")

    async def _credit_wallet_hearts(
        self,
        player: Player,
        amount: int,
        tx_type: str,
        *,
        description: str = "",
    ) -> bool:
        """`wallets.hearts` ga qo'shish (klientda gold deb ko'rinadi). Admin ham DB ga yoziladi."""
        if amount <= 0 or not player.db_id:
            return False
        new_balance: Optional[int] = None
        if self._db_factory:
            try:
                async with self._db() as repo:
                    await repo.ensure_wallet(int(player.db_id))
                    new_balance = await repo.add_hearts(
                        int(player.db_id),
                        amount,
                        tx_type,
                        description or tx_type,
                    )
            except Exception as e:
                log.error(
                    "credit wallet hearts user=%s: %s", player.db_id, e
                )
                return False
        if new_balance is None:
            player.hearts = int(player.hearts or 0) + amount
            player.hearts_real = int(player.hearts or 0)
        elif getattr(player, "is_admin", False):
            player.hearts = int(player.hearts or 0) + amount
            player.hearts_real = player.hearts
        else:
            player.hearts = int(new_balance)
            player.hearts_real = int(new_balance)
        await self._push_wallet_sync(player)
        return True

    async def _db_mark_bonus_claimed(self, db_id: int):
        try:
            async with self._db() as repo:
                await repo.mark_bonus_claimed(db_id)
        except Exception as e:
            log.error(f"Mark bonus claimed DB xatosi: {e}")

    async def _check_and_broadcast_turn(self, table: Table):
        """
        Stolda o'yin boshlanishi mumkinligini tekshiradi (kamida bitta o'g'il va bitta qiz).
        Agar jinslar balansi bo'lmasa, 'game_wait' yuboradi.
        Aks holda 'game_turn_offer' yuboradi va 6 s ichida spin bo'lmasa server avtomatik spin qiladi.
        """
        if not table.players:
            table.cancel_auto_spin_task()
            return

        if getattr(table, "round_closing", False):
            log.debug(
                "TABLE %s: _check_and_broadcast_turn — round_closing, skip",
                table.table_id,
            )
            return

        table.repair_turn_seat_if_orphaned()

        if table.state != STATE_WAIT:
            log.debug(
                "TABLE %s: _check_and_broadcast_turn — state is %s, skip joining reset",
                table.table_id,
                table.state,
            )
            return

        # 1. Jinslarni tekshirish (gender qatori DB da noto'g'ri bo'lishi mumkin — male boolean asosiy)
        has_male = any(bool(getattr(p, "male", True)) for p in table.players.values())
        has_female = any(
            not bool(getattr(p, "male", True)) for p in table.players.values()
        )

        if not has_female or not has_male:
            table.cancel_auto_spin_task()
            # Kutish holati: frontend 'Qizlarni kutamiz' yoki 'Yigitlarni kutamiz' deb ko'rsatishi uchun
            await self.broadcast(
                table.table_id, {"type": "game_wait", "ts": self._ts()}
            )
            await self._broadcast_html5_wait_state(table)
            log.info(
                f"TABLE {table.table_id}: Waiting for opposite gender. (M:{has_male}, F:{has_female})"
            )
            return

        # 2. Navbat kimdaligini aniqlash (turn_seat — start_spin bottle_seat ni buzmaydi)
        spinner = next(
            (p for p in table.players.values() if p.seat == table.turn_seat), None
        )
        if not spinner:
            players_list = sorted(table.players.values(), key=lambda p: p.seat)
            spinner = players_list[0]
            table.turn_seat = spinner.seat
            table.bottle_seat = spinner.seat

        # Navbat taklifini hamma ko'rishi kerak
        await self.broadcast(
            table.table_id,
            {
                "type": "game_turn_offer",
                "gameId": table.table_id,
                "tableId": table.table_id,
                "user": spinner.to_short(),
                "ts": self._ts(),
            },
        )
        await self._broadcast_html5_turn_state(table)
        log.info(f"TABLE {table.table_id}: Turn offered to {spinner.username}")

        # 6 s ichida navbat egasi spin qilmasa — server o'sha o'yinchi nomidan avtomatik spin qiladi
        ts_seat = int(spinner.seat)
        log.info(
            "TABLE %s: Auto-spin taymer (6s) boshlandi (seat=%s, user=%s, state=%s)",
            table.table_id,
            ts_seat,
            spinner.username,
            table.state,
        )
        table.schedule_auto_spin_task_if_idle_turn_changed(
            lambda: self._auto_spin_timeout_task(table, ts_seat), ts_seat
        )

    async def _auto_spin_timeout_task(self, table: Table, expected_turn_seat: int):
        """AUTO_SPIN_IDLE_SEC kutadi; shu vaqt ichida bottle spin bo'lmasa — server navbat egasi o'rnida spin qiladi."""
        try:
            await asyncio.sleep(Table.AUTO_SPIN_IDLE_SEC)
        except asyncio.CancelledError:
            return
        table.repair_turn_seat_if_orphaned()
        if getattr(table, "round_closing", False):
            log.debug(
                "AUTO-SPIN: round_closing (stol=%s), skip",
                table.table_id,
            )
            return
        if table.turn_seat != expected_turn_seat:
            log.info(
                "AUTO-SPIN bekor: navbat o'zgardi (kutilgan=%s, hozir=%s, stol=%s) — qayta tekshiramiz",
                expected_turn_seat,
                table.turn_seat,
                table.table_id,
            )
            await self._check_and_broadcast_turn(table)
            return
        player = next(
            (p for p in table.players.values() if p.seat == table.turn_seat), None
        )
        if not player:
            log.warning(
                "AUTO-SPIN: turn_seat=%s uchun o'yinchi topilmadi (stol=%s)",
                table.turn_seat,
                table.table_id,
            )
            await self._check_and_broadcast_turn(table)
            return
        if table.state != STATE_WAIT:
            log.info(
                "AUTO-SPIN bekor: state=%s (STATE_WAIT kerak), stol=%s",
                table.state,
                table.table_id,
            )
            return
        if not table.can_spin(player.id):
            log.info(
                "AUTO-SPIN: can_spin=false (stol=%s uid=%s seat=%s turn_seat=%s state=%s) — navbatni qayta tekshiramiz",
                table.table_id,
                player.id,
                player.seat,
                table.turn_seat,
                table.state,
            )
            # can_spin false bo'lsa ham navbatni qayta tekshiramiz — zanjir uzilmasin
            await self._check_and_broadcast_turn(table)
            return
        log.info(
            "AUTO-SPIN: %s navbatda spin qilmadi — server %s o'rnida bajaradi (stol=%s).",
            player.username,
            player.id,
            table.table_id,
        )
        try:
            await self._handle_game_turn(player)
        except Exception as e:
            log.error(f"AUTO-SPIN xatosi (stol={table.table_id}): {e}", exc_info=True)
            # Xatolik bo'lsa ham keyingi navbatga o'tishga harakat qilamiz
            try:
                table.reset_turn()
                await self._advance_bottle(table, player)
            except Exception:
                pass

    async def _save_relation(self, user_id: int, target_id: int, rel_type: str):
        try:
            async with self._db() as repo:
                await repo.add_relation(user_id, target_id, rel_type)
        except Exception as e:
            log.error(f"Save relation error: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # RABBIT SCHEDULER
    # ════════════════════════════════════════════════════════════════════════
    async def _rabbit_scheduler(self):
        """Mustaqil quyon yuborish tizimi (Diagramma 14)."""
        import random

        while True:
            # 10-15 daqiqa kutamiz (600-900 soniya)
            await asyncio.sleep(random.randint(600, 900))

            for table_id, table in list(self.tables.items()):
                if table.player_count() >= 2:
                    # Quyon yuboramiz
                    await self.broadcast(
                        table_id,
                        {
                            "type": "rabbit_event",
                            "gift": random.choice(GIFT_TYPES),
                            "ts": self._ts(),
                        },
                    )
                    log.info(f"RABBIT: Sent to table {table_id}")

    async def _handle_tg_purchase(self, player: Player, data: dict) -> None:
        """Mini App / sayt: Telegram Stars invoice havolasi (openInvoice)."""
        from src.app.services.telegram_payments import (
            create_stars_invoice_link,
            parse_tg_hearts_product_id,
        )

        product_id = str(data.get("product_id") or "")
        parsed = parse_tg_hearts_product_id(product_id)
        if not parsed:
            await self.send_to(
                player,
                {
                    "type": "tg_purchase",
                    "error": "Unknown product",
                    "ts": self._ts(),
                },
            )
            return

        if not player.db_id:
            await self.send_to(
                player,
                {
                    "type": "tg_purchase",
                    "error": "Login required",
                    "ts": self._ts(),
                },
            )
            return

        hearts, stars = parsed
        from src.app.api.ws.constants import hearts_for_stars_price, validate_hearts_product

        if not validate_hearts_product(stars, hearts):
            expected = hearts_for_stars_price(stars)
            if expected is None:
                await self.send_to(
                    player,
                    {
                        "type": "tg_purchase",
                        "error": "Unknown product",
                        "ts": self._ts(),
                    },
                )
                return
            hearts = expected
        lang = getattr(player, "language_code", None)
        title = str(data.get("title") or "")[:32] or None
        link = await create_stars_invoice_link(
            int(player.db_id),
            stars,
            lang=lang,
            hearts=hearts,
            title=title,
        )
        if not link:
            await self.send_to(
                player,
                {
                    "type": "tg_purchase",
                    "error": "Invoice unavailable",
                    "ts": self._ts(),
                },
            )
            return

        await self.send_to(
            player,
            {
                "type": "tg_purchase",
                "link": link,
                "ts": self._ts(),
            },
        )
        log.info(
            "tg_purchase link user=%s product=%s stars=%s hearts=%s",
            player.db_id,
            product_id,
            stars,
            hearts,
        )

    async def _handle_gm_hearts_purchase(self, player: Player, data: dict):
        from src.app.api.ws.constants import HEARTS_PACKAGES

        if not player.db_id:
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "Yurak paketi uchun akkaunt bilan kiring.",
                    "ts": self._ts(),
                },
            )
            return

        raw = data.get("STARS", data.get("amount"))
        try:
            amount_stars = int(raw)
        except (TypeError, ValueError):
            await self.send_to(
                player,
                {"type": "error", "msg": "Miqdor xato", "ts": self._ts()},
            )
            return

        if amount_stars not in HEARTS_PACKAGES:
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "Bunday paket mavjud emas",
                    "ts": self._ts(),
                },
            )
            return

        hearts_to_add = HEARTS_PACKAGES[amount_stars]

        if getattr(player, "is_admin", False):
            player.hearts += hearts_to_add
            player.hearts_real = player.hearts
            self._admin_floor_wallet(player)
            await self._push_wallet_sync(player)
            wf = player.wallet_for_client()
            await self.send_to(
                player,
                {
                    "type": "gm_hearts_purchase_success",
                    "amount": hearts_to_add,
                    **wf,
                    "ts": self._ts(),
                },
            )
            return

        try:
            async with self._db() as repo:
                ok, new_sc, new_gt, new_hearts = await repo.purchase_hearts_with_gift_tokens(
                    player.db_id, amount_stars, hearts_to_add
                )
        except Exception as e:
            log.error(f"Hearts purchase DB: {e}")
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "Sotib olishda xato.",
                    "ts": self._ts(),
                },
            )
            return

        if not ok:
            await self._send_tokens_insufficient(player, amount_stars)
            return

        player.stars_coin = new_sc
        player.gift_tokens = new_gt
        player.hearts = new_hearts
        player.hearts_real = new_hearts
        player.sync_token_display()

        await self._push_wallet_sync(player)

        wf = player.wallet_for_client()
        await self.send_to(
            player,
            {
                "type": "gm_hearts_purchase_success",
                "amount": hearts_to_add,
                **wf,
                "ts": self._ts(),
            },
        )

    async def _give_gift_tokens(
        self,
        player: Player,
        amount: int,
        tx_type: str,
        *,
        save_to_db: bool = True,
    ) -> None:
        """Rabbit va h.k. — gift_tokens (jeton) qo'shish."""
        if amount <= 0:
            return
        if getattr(player, "is_admin", False):
            self._admin_floor_wallet(player)
            await self._push_wallet_sync(player)
            return
        player.gift_tokens = int(player.gift_tokens or 0) + amount
        if save_to_db and player.db_id:
            asyncio.create_task(self._db_add_gift_tokens(player.db_id, amount, tx_type))
        await self._push_wallet_sync(player)

    async def _db_add_gift_tokens(self, db_id: int, amount: int, tx_type: str) -> None:
        if not db_id:
            return
        try:
            async with self._db() as repo:
                await repo.add_gift_tokens(db_id, amount, tx_type)
        except Exception as e:
            log.error(f"Gift tokens add DB xatosi: {e}")

    async def _handle_rabbit_gift_send(self, player: Player, data: dict) -> None:
        from src.app.api.ws.constants import (
            RABBIT_ACTIVE_DURATION_SEC,
            RABBIT_GIFT_TYPES,
            RABBIT_MIN_PLAYERS,
            RABBIT_SEND_COST_HEARTS,
            RABBIT_SEND_COST_TOKENS,
        )

        table = self.tables.get(player.table_id)
        if not table:
            return

        if table.player_count() < RABBIT_MIN_PLAYERS:
            await self.send_to(
                player, {"type": "rabbit_min_players", "ts": self._ts()}
            )
            return

        if table.rabbit_active and time.time() < table.rabbit_active_until:
            await self.send_to(
                player, {"type": "rabbit_already_active", "ts": self._ts()}
            )
            return

        gift = str(data.get("gift") or "")
        if gift not in RABBIT_GIFT_TYPES:
            await self.send_to(
                player,
                {"type": "error", "msg": "Noto'g'ri sovg'a", "ts": self._ts()},
            )
            return

        if gift == "rabbit_gm":
            if not await self._spend_stars(
                player, RABBIT_SEND_COST_TOKENS, "rabbit_gift_send"
            ):
                return
        elif not await self._spend_hearts(
            player, RABBIT_SEND_COST_HEARTS, "rabbit_gift_send"
        ):
            return

        from_uid = normalize_ws_user_ref(player.user_id)
        table.set_rabbit_active(gift, from_uid, RABBIT_ACTIVE_DURATION_SEC)
        await self._push_wallet_sync(player)
        await self.broadcast(
            table.table_id,
            {
                "type": "rabbit_gift_sent",
                "gift": gift,
                "from_user": from_uid,
                "ts": self._ts(),
            },
        )

    async def _handle_rabbit_gift_caught(self, player: Player, data: dict) -> None:
        from src.app.api.ws.constants import (
            RABBIT_CATCH_REWARD_HEARTS,
            RABBIT_CATCH_REWARD_TOKENS,
        )

        table = self.tables.get(player.table_id)
        if not table or not table.rabbit_active:
            return

        gift = str(data.get("gift") or "")
        if gift != table.rabbit_gift:
            return

        caught_id = normalize_ws_user_ref(data.get("caught_by"))
        if not caught_id:
            caught_id = normalize_ws_user_ref(player.user_id)
        catcher = table.players.get(caught_id)
        if not catcher:
            return

        from_user = table.rabbit_from_user or normalize_ws_user_ref(
            data.get("from_user")
        )
        table.clear_rabbit()

        if gift == "rabbit_heart":
            await self._give_hearts(
                catcher,
                RABBIT_CATCH_REWARD_HEARTS,
                "rabbit_gift_caught",
                save_to_db=True,
            )
        else:
            await self._give_gift_tokens(
                catcher, RABBIT_CATCH_REWARD_TOKENS, "rabbit_gift_caught"
            )

        await self.broadcast(
            table.table_id,
            {
                "type": "rabbit_gift_caught",
                "gift": gift,
                "from_user": from_user,
                "caught_by": caught_id,
                "ts": self._ts(),
            },
        )


# ── Singleton ────────────────────────────────────────────────────────────────
manager = GameManager()

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
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import jwt as pyjwt
from fastapi import WebSocket

from src.app.api.game_session import game_sessions
from src.app.core.jwt import verify_access_token
from src.app.api.ws.constants import (
    ADMIN_DISPLAY_HEARTS,
    ADMIN_DISPLAY_STARS,
    BOOSTER_TYPES,
    BOTTLE_TYPES,
    DEFAULT_BOTTLE_TYPE,
    COMPLIMENT_GOLD_REWARD,
    COMPLIMENTS_TO_REWARD,
    DAILY_BONUS_GOLD,
    DRINK_PRICES,
    DRINK_TYPES,
    GESTURE_PRICES,
    GESTURE_TYPES,
    GIFT_PRICES,
    GIFT_TYPES,
    HAT_PRICES,
    HAT_TYPES,
    KISS_BONUS_GOLD,
    RETENTION_BONUS_GOLD,
    REWARDED_VIDEO_GOLD,
    STATE_OFFER,
    STATE_WAIT,
    WELCOME_BONUS_GOLD,
    KICKOUT_STREAK_RESET_SECONDS,
    kickout_price_for_use_index,
    kickout_streak_effective_uses,
)
from src.app.core.room_policy import (
    BASE_VISIBLE_COUNTRY,
    BASE_VISIBLE_GLOBAL,
    COUNTRY_ROOM_SLOTS,
    GLOBAL_ROOM_SLOTS,
    is_global_country_code,
    normalize_country_code,
    player_may_join_room_row,
    visible_room_prefix_len,
)
from src.app.api.ws.player import Player, parse_birth_date_ms
from src.app.api.ws.table import Table
from src.app.api.ws.utils import prepare_packet
from src.app.database.repositories.game import GameRepository

log = logging.getLogger("spinbottle")

# Haydalgandan keyin shu stolga qayta kirish (ms)
KICK_REENTRY_BAN_MS = 15 * 60 * 1000
# `spinbottle` logger uchun INFO darajasini yoqamiz va uvicorn handler'iga
# propagate qilamiz (agar root da handler bo'lsa). Aks holda StreamHandler qo'shamiz.
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

    def register_kick_reentry_ban(self, table_id: str, target: Player) -> None:
        """Haydashdan keyin shu stolga 15 daqiqa kirish taqiqi."""
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
            "msg": "Bu stoldan haydalgansiz. 15 daqiqadan keyin qayta kirishingiz mumkin.",
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
        t = self.tables.get(str(room_id_str))
        return t.player_count() if t else 0

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

    async def _room_id_is_visible_for_country(self, country: str, room_id_str: str) -> bool:
        c_vis, g_vis = await self._visible_country_and_global_rows(country)
        ids = {str(r.id) for r in c_vis + g_vis}
        return str(room_id_str) in ids

    async def _default_join_room_id_for_player(self, repo: GameRepository, player: Player) -> str:
        """Mamlakat stollaridan birinchisi (ko'rinadiganlar ichidan, joy bo'lsa)."""
        c = normalize_country_code(player.country or "UZBEKISTAN")
        db_all = await repo.get_rooms_by_country(c)
        country_rows = sorted(
            [r for r in db_all if r.country_code == c],
            key=lambda r: r.id,
        )
        if not country_rows:
            return "1"
        counts = [self._room_online_count(str(r.id)) for r in country_rows]
        vn = visible_room_prefix_len(
            counts,
            max_rooms=COUNTRY_ROOM_SLOTS,
            base_visible=max(1, BASE_VISIBLE_COUNTRY),
        )
        visible = country_rows[: max(1, vn)]
        for r in visible:
            if self._room_online_count(str(r.id)) < 12:
                return str(r.id)
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

        # 2. DB dan yuklash
        if real_uid:
            try:
                async with self._db() as repo:
                    db_user = await repo.get_user_with_wallet(real_uid)
                    if db_user and not db_user.wallet:
                        await repo.ensure_wallet(real_uid)
                        db_user = await repo.get_user_with_wallet(real_uid)
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
            h = int(hashlib.md5(user_id.encode("utf-8"), usedforsecurity=False).hexdigest(), 16)
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
        table.add_player(player)
        self.ws_map[ws] = (table_id, user_id)
        return player

    async def disconnect(self, ws: WebSocket):
        if ws not in self.ws_map:
            return
        table_id, user_id = self.ws_map.pop(ws)
        table = self.tables.get(table_id)
        if not table:
            return

        player = table.get_player(user_id)
        table.remove_player(user_id)

        if player:
            await self.broadcast(
                table_id,
                {"type": "game_leave", "user": player.to_short(), "ts": self._ts()},
            )
            # Jinslar balansini qayta tekshiramiz
            await self._check_and_broadcast_turn(table)

        if table.player_count() == 0:
            del self.tables[table_id]

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

    async def _connection_success_payload(self, player: Player, table: Table, ts: int) -> dict:
        spinner = next(
            (p for p in table.players.values() if p.seat == table.turn_seat), None
        )
        bottle_seat_1 = (spinner.seat + 1) if spinner else (table.bottle_seat + 1)
        has_male = any(p.gender == "male" for p in table.players.values())
        has_female = any(p.gender == "female" for p in table.players.values())
        game_on = bool(has_male and has_female)
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
            "table_id": int(table.table_id)
            if str(table.table_id).isdigit()
            else table.table_id,
            "seat_number": player.seat + 1,
            "game_username": player.username,
            "profile_picture": player.photo_url or "/photos/no_img.png",
            "table_players": self._table_players_html5(table),
            "bottle_seat": bottle_seat_1 if game_on else None,
            "isSpinner": spinner.id if spinner and game_on else None,
            "isTarget": None,
            "isSpinner_choice": "",
            "isTarget_choice": "",
            "game_active": game_on,
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
        return {
            "type": "player_joined",
            "user_id": sid,
            "seat_number": seat0 + 1,
            "game_username": u.get("name") or u.get("username") or "Bilinmeyen",
            "profile_picture": u.get("photo_url") or u.get("image") or "/photos/no_img.png",
            "bottle_seat": table.bottle_seat + 1 if table.bottle_seat is not None else None,
            "isSpinner": None,
            "isTarget": None,
            "isSpinner_choice": "",
            "isTarget_choice": "",
            "game_active": True,
            "gender": "Qadın"
            if (u.get("gender") == "female")
            else ("Kişi" if u.get("gender") == "male" else "Bilinmeyen"),
            "room_kiss_count": table.room_kiss_count,
            "game_start_timeout": None,
            "frame_name": u.get("frame") or "",
            "is_vip": u.get("vip") or u.get("premium") or False,
            "vip_color": u.get("vip_color"),
            "ts": msg.get("ts") or self._ts(),
        }

    def _game_leave_to_player_left(self, msg: dict) -> dict:
        u = msg.get("user") or {}
        seat0 = int(u.get("seat") or 0)
        return {
            "type": "player_left",
            "user_id": str(u.get("id") or u.get("userId") or ""),
            "seat_number": seat0 + 1,
            "bottle_seat": None,
            "isSpinner": None,
            "isTarget": None,
            "isSpinner_choice": "",
            "isTarget_choice": "",
            "game_active": False,
            "game_start_timeout": None,
            "ts": msg.get("ts") or self._ts(),
        }

    async def _broadcast_html5_turn_state(self, table: Table):
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
    async def broadcast(self, table_id: str, msg: dict, exclude_id: str = None):
        table = self.tables.get(table_id)
        if not table:
            return
        for uid, player in list(table.players.items()):
            if uid == exclude_id:
                continue
            payload = copy.deepcopy(msg)
            if msg.get("type") == "game_join" and getattr(player, "plain_ws", False):
                payload = self._game_join_to_player_joined(msg, table)
            elif msg.get("type") == "game_leave" and getattr(player, "plain_ws", False):
                payload = self._game_leave_to_player_left(msg)
            player.stamp_out_packet(payload)
            await self._deliver(player, payload)

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

    def _find_player_table(self, user_id_str: str) -> Optional[Tuple[str, Table, Player]]:
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

    async def _login_payload_with_friends(self, player: Player, table_id: str, ts: int) -> dict:
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
                expired = la is None or (now - la).total_seconds() > KICKOUT_STREAK_RESET_SECONDS
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
            player.guest_kickout_streak = int(getattr(player, "guest_kickout_streak", 0) or 0) + 1
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
        ge: dict = {
            "type": "game_enter",
            "game_id": tbl.table_id,
            "tableId": tbl.table_id,
            "participants": tbl.all_participants(),
            "bottle_type": tbl.bottle_type,
            "scheduled": [],
            "achievements": [],
            "achievements_ms": 0,
            "recent_messages": recent_messages,
            "ts": ts + 5,
        }
        await self.send_to(player, ge)
        await asyncio.sleep(0.2)
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
        player = table.get_player(user_id) if table else None
        if not table or not player:
            return

        t = data.get("type", "")
        if t not in ("ping", "report_activity"):
            log.debug(f"[{t}] ← {player.username}")

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
        elif t == "game_chat_message":
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
            await self.send_to(
                player, {"type": "get_favorite_songs", "songs": [], "ts": self._ts()}
            )
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
        elif t == "activate_percent_bonus":
            await self._handle_activate_percent_bonus(player)
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
            "mark_song_favorite",
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
        elif t == "compliment_next":
            await self._handle_compliment_next(player)
        elif t == "compliment_send":
            await self._handle_compliment_send(player, data)
        elif t == "compliment_group":
            await self._handle_compliment_group(player)
        elif t == "gm_hearts_purchase":
            await self._handle_gm_hearts_purchase(player, data)
        else:
            log.warning(f"[UNKNOWN] type={t!r} from {user_id}")

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

        # 1–3. Login + game_enter + game_join + navbat (klient sp.start uchun game_id majburiy)
        if not getattr(player, "plain_ws", False):
            login_payload = await self._login_payload_with_friends(player, table_id, ts)
            await self.send_to(player, login_payload)
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

            if bonus_stars > 0 and player.db_id and not getattr(
                player, "is_admin", False
            ):
                player.stars += bonus_stars
                asyncio.create_task(
                    self._db_add_stars(player.db_id, bonus_stars, "daily_vip_bonus")
                )
            elif bonus_stars > 0 and getattr(player, "is_admin", False):
                self._admin_floor_wallet(player)

            # DB da belgilab qo'yamiz
            if player.db_id:
                asyncio.create_task(self._db_mark_bonus_claimed(player.db_id))

            player.can_claim_bonus = False

        # 5. Navbat/Kutish holatini tekshirish
        await asyncio.sleep(0.3)
        await self._check_and_broadcast_turn(table)

        # 6. DB da saqlangan do'stlik so'rovlari (foydalanuvchi oflayn bo'lgan vaqtda)
        await self._flush_pending_friend_requests(player)

        self._admin_floor_wallet(player)
        await self._push_wallet_sync(player)

        player.session_started = True

    # ════════════════════════════════════════════════════════════════════════
    # ROOMS LIST — yangi qo'shilgan
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_get_rooms(self, player: Player, data: dict):
        """
        Foydalanuvchi mamlakatiga mos xonalar + global xonalar (ochiq ro'yxat).
        DB da 150 ta mamlakat / 20 ta global; UI da bandlik bo'yicha qadam-baqadam ochiladi.
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
                }

            for r in country_vis:
                tables_list.append(_entry(r, "country"))
            for r in global_vis:
                tables_list.append(_entry(r, "global"))
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
            for room in c_vis + g_vis:
                rid = str(room.id)
                online = self._room_online_count(rid)
                out.append(
                    {
                        "id": rid,
                        "room_id": room.id,
                        "name": room.name,
                        "currentPlayers": online,
                        "online": online,
                        "maxPlayers": room.capacity,
                        "capacity": room.capacity,
                        "is_vip": room.is_vip,
                        "min_level": room.min_level,
                        "country": room.country_code,
                        "scope": "global"
                        if is_global_country_code(room.country_code)
                        else "country",
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
            await self.send_to(player, {"type": "ok", "room_id": new_room_id, "ts": self._ts()})
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
                    "msg": "Bu stoldan haydalgansiz. 15 daqiqadan keyin qayta urinib ko'ring.",
                    "kick_ban_until": until_ts,
                    "ts": self._ts(),
                },
            )
            return

        await self.disconnect(ws)

        new_player = await self.connect(ws, new_room_id, old_user_id, strict=True)
        if not new_player:
            log.warning(f"CHANGE ROOM blocked after disconnect: {old_user_id} → {new_room_id}")
            return
        ts = self._ts()

        login_pl = await self._login_payload_with_friends(new_player, new_room_id, ts)
        await self.send_to(new_player, login_pl)
        await asyncio.sleep(0.2)

        new_table = self.tables.get(new_room_id)
        if new_table:
            await self._emit_game_enter_join_and_turn(new_player, new_table, ts)
        await self._flush_pending_friend_requests(new_player)

        log.info(f"CHANGE ROOM: {old_user_id} → {new_room_id}")

    async def _handle_get_friend_games(self, player: Player, data: dict):
        """
        Stol almashish oynasi (PL.show): queryFriendGames → friend_games.
        Klient kutadi: friends, fellows, games_history (g4 / ep konstruktorlari).
        """
        raw_ids = data.get("friend_ids") or []
        friend_ids: List[int] = []
        for x in raw_ids:
            try:
                friend_ids.append(int(x))
            except (TypeError, ValueError):
                continue

        friends_rows: List[dict] = []
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

        fellows_rows: List[dict] = []
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

        history_entries: list[dict] = []
        country = normalize_country_code(player.country or "UZBEKISTAN")
        try:
            c_vis, g_vis = await self._visible_country_and_global_rows(country)
            for room in c_vis[:40]:
                tid = str(room.id)
                live = self.tables.get(tid)
                m, w = self._gender_counts_for_table(live)
                bottle = live.bottle_type if live else DEFAULT_BOTTLE_TYPE
                history_entries.append(
                    {
                        "game_id": tid,
                        "bottle": bottle,
                        "men": m,
                        "women": w,
                    }
                )
            for room in g_vis[:40]:
                tid = str(room.id)
                live = self.tables.get(tid)
                m, w = self._gender_counts_for_table(live)
                bottle = live.bottle_type if live else DEFAULT_BOTTLE_TYPE
                history_entries.append(
                    {
                        "game_id": tid,
                        "bottle": bottle,
                        "men": m,
                        "women": w,
                    }
                )
        except Exception as e:
            log.error(f"get_friend_games history: {e}")

        ts = self._ts()
        await self.send_to(
            player,
            {
                "type": "friend_games",
                "friends": friends_rows,
                "fellows": fellows_rows,
                "games_history": history_entries,
                "games_history_global": [],
                "ts": ts,
            },
        )

    def _admin_floor_wallet(self, player: Player) -> None:
        if not getattr(player, "is_admin", False):
            return
        player.hearts = max(int(player.hearts or 0), ADMIN_DISPLAY_HEARTS)
        player.hearts_real = player.hearts
        player.stars = max(int(player.stars or 0), ADMIN_DISPLAY_STARS)
        player.gift_tokens = max(
            int(getattr(player, "gift_tokens", 0) or 0), ADMIN_DISPLAY_STARS
        )

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
                        player.hearts = wallet.hearts
                        player.stars = wallet.stars
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

        can = table.can_spin(player.id)
        if not can:
            log.warning(
                f"SPIN: {player.username} (seat {player.seat}) cannot spin. "
                f"state={table.state} turn_seat={table.turn_seat} bottle_seat={table.bottle_seat}"
            )
            # OFFER/SPINNING paytida qayta game_turn_offer yubormaslik — klient FSM buziladi
            if table.state == STATE_WAIT:
                await self._check_and_broadcast_turn(table)
            return

        table.cancel_auto_spin_task()
        table.cancel_offer_timeout_task()
        target_seat = table.start_spin(player.id)
        log.info(f"SPIN: {player.username} started spin. Target seat: {target_seat}")

        # Spin yutiqlari (umumiy — qayta kirganda ham davom etadi)
        player.total_spins = int(getattr(player, "total_spins", 0) or 0) + 1
        await self._check_achievements(player, "spins", player.total_spins)
        if player.db_id:
            asyncio.create_task(self._save_spin_stat(player.db_id, 1))

        target_p = table.get_player(table.current_target)
        if not target_p:
            table.reset_turn()
            await self._check_and_broadcast_turn(table)
            return

        # Prod HTML5 klientida _recv_game_spin yo'q: aylanish game_turn_offer →
        # foydalanuvchi game_turn yuboradi → darhol game_turn (user = nishon)
        # orqali turnSelect / toCenter animatsiyasi ishga tushadi.
        table.offer_turn()
        await self.broadcast(
            table.table_id,
            {
                "type": "game_turn",
                "gameId": table.table_id,
                "tableId": table.table_id,
                "user": target_p.to_short(),
                "receiver": player.to_short(),
                "ts": self._ts(),
            },
        )

        table.schedule_offer_timeout_task(self._turn_timeout(table, player.id))

    async def _turn_timeout(self, table: Table, spinner_id: str):
        await asyncio.sleep(Table.TURN_OFFER_TIMEOUT)
        if table.state == STATE_OFFER and table.current_spinner == spinner_id:
            table.reset_turn()
            await self.broadcast(
                table.table_id,
                {"type": "game_wait", "user_id": spinner_id, "ts": self._ts()},
            )
            # Navbatni keyingi odamga o'tkazamiz
            spinner = table.get_player(spinner_id)
            if spinner:
                await self._advance_bottle(table, spinner)

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

        sender.kisses += 1
        sender.total_kisses += 1
        sender.league_score += score_to_add
        receiver.kisses += 1
        receiver.league_score += score_to_add
        table.room_kiss_count += 1

        asyncio.create_task(self._save_kiss_stats(sender.db_id, receiver.db_id))

        await self.broadcast(
            table.table_id,
            {
                "type": "league_score",
                "user": sender.to_short(),
                "user_id": sender.id,
                "score": score_to_add,
                "assign": {"kisses": 1, "league_score": score_to_add},
                "kisses": sender.total_kisses,
                "kisses_lim": 500,
                "ts": self._ts(),
            },
        )

        if sender.total_kisses % 5 == 0:
            await self._give_hearts(
                sender, KISS_BONUS_GOLD, "kiss_bonus", save_to_db=True
            )

        # Yutuq tekshiruvi (kissing yutiqlari)
        await self._check_achievements(sender, "total_kisses", sender.total_kisses)

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
        BIRINCHI bosgan o'yinchining tanlovi yakuniy — sherigini kutmaymiz.
          • Kiss bosilsa — darhol o'pish (kiss) animatsiyasi
          • NoKiss bosilsa — darhol rad (refuse)
        Sherigi keyin bossa, e'tibor bermaymiz (resolving flag).
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

        # Birinchi tanlov yakuniy — keyingi presslarni bloklash uchun resolving=True
        table.resolving = True
        table.cancel_offer_timeout_task()

        is_spinner = (player.id == spin_id)
        if is_spinner:
            table.spinner_choice = choice
        else:
            table.target_choice = choice

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

        # 2) Yakunlaymiz — bosgan o'yinchining tanlovi ikkala tomon uchun
        await self._resolve_pair(
            table,
            spinner_p,
            target_p,
            bottle_seat_1,
            press_choice=choice,
            press_by_spinner=is_spinner,
        )

    async def _resolve_pair(
        self,
        table: Table,
        spinner_p: Player,
        target_p: Player,
        bottle_seat_1: int,
        press_choice: str,
        press_by_spinner: bool,
    ):
        """
        Yakuniy raund: birinchi press o'sha tanlovni ikkala tomon uchun ham qo'llaydi.
          • Kiss  → JS choices_complete ikkala "Kiss" deb ko'rsatadi → kiss animation
          • NoKiss → refuse effekti, animatsiyasiz delay
        """
        # choices_complete payloadida JS shartiga mos ravishda
        # ikkala tomon ham bir xil tanlov ko'rinishi kerak — aks holda
        # kiss animation chiqmaydi.
        sc = press_choice
        tc = press_choice

        await self.broadcast(
            table.table_id,
            {
                "type": "choices_complete",
                "isSpinner_choice": sc,
                "isTarget_choice": tc,
                "spinner_seat": spinner_p.seat + 1,
                "target_seat": target_p.seat + 1,
                "bottle_seat": bottle_seat_1,
                "game_active": True,
                "delay": 3000,
                "game_start_timeout": None,
                "ts": self._ts(),
            },
        )
        log.info(
            "choices_complete: sc=%s tc=%s table=%s",
            sc,
            tc,
            table.table_id,
        )

        if press_choice == "Kiss":
            # Bosgan o'yinchi — kisser (sender), sherigi — receiver
            sender = spinner_p if press_by_spinner else target_p
            receiver = target_p if press_by_spinner else spinner_p
            await self._apply_kiss_reward_after_offer(table, sender, receiver)
            advance_player = receiver
        else:
            # NoKiss → refuser bosgan o'yinchi, receiver — sherigi
            refuser = spinner_p if press_by_spinner else target_p
            receiver = target_p if press_by_spinner else spinner_p
            await self._broadcast_refuse_pair_effects(table, refuser, receiver)
            advance_player = receiver

        table.reset_turn()
        await asyncio.sleep(Table.POST_RESOLVE_PAUSE_SEC)
        await self._advance_bottle(table, advance_player)


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
        self, sender_id: Optional[int], receiver_id: Optional[int]
    ):
        if not sender_id and not receiver_id:
            return
        try:
            async with self._db() as repo:
                if sender_id:
                    await repo.add_stat(sender_id, "kisses", 1)
                    await repo.add_stat(sender_id, "emotion", 1)
                # Qabul qiluvchiga ham emotion qo'shamiz
                if receiver_id:
                    await repo.add_stat(receiver_id, "emotion", 1)
        except Exception as e:
            log.error(f"Kiss stat DB xatosi: {e}")

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
        gift_type = data.get("gift_type", "")
        receiver_id = str(data.get("receiver_id", ""))
        # Narxni GIFT_PRICES dan, yo'q bo'lsa HAT_PRICES dan, default 5
        price = int(data.get("price", GIFT_PRICES.get(gift_type, HAT_PRICES.get(gift_type, 5))))
        receiver = table.get_player(receiver_id)

        # Gift yoki Hat bo'lishi mumkin (crown1 kabi sovg'alar hat kategoriyasida)
        valid = gift_type in GIFT_TYPES or gift_type in HAT_TYPES
        if not receiver or not valid:
            await self.send_to(
                player, {"type": "error", "msg": "Noto'g'ri sovg'a", "ts": self._ts()}
            )
            return


        ok = await self._spend_hearts(player, price, "gift", f"gift:{gift_type}")
        if not ok:
            return

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
                "magic": False,
                "random": stick_random,
                "ts": self._ts(),
            },
        )

        asyncio.create_task(self._save_gift_stats(player.db_id, receiver.db_id, price))

    async def _save_gift_stats(self, sender_id, receiver_id, price: int):
        try:
            async with self._db() as repo:
                if sender_id:
                    await repo.add_stat(sender_id, "expense", price)
                    await repo.add_stat(sender_id, "emotion", price // 5 + 1)
                if receiver_id:
                    await repo.add_stat(receiver_id, "importance", price)
        except Exception as e:
            log.error(f"Gift stat DB xatosi: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # DRINK
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_drink(self, table: Table, player: Player, data: dict):
        drink_type = data.get("drink_type", data.get("drink", ""))
        receiver_id = str(data.get("receiver_id", ""))
        price = int(data.get("price", DRINK_PRICES.get(drink_type, 10)))
        receiver = table.get_player(receiver_id)

        if not receiver or drink_type not in DRINK_TYPES:
            await self.send_to(
                player, {"type": "error", "msg": "Noto'g'ri ichimlik", "ts": self._ts()}
            )
            return

        ok = await self._spend_hearts(player, price, "drink", f"drink:{drink_type}")
        if not ok:
            return

        player.drink = drink_type
        player.drink_count += 1

        drink_rnd = random.randint(0, 1_000_000_000)
        await self.broadcast(
            table.table_id,
            {
                "type": "game_drink",
                "drink_type": drink_type,
                "user": player.to_short(),
                "receiver": receiver.to_short(),
                "price": price,
                "drink_random": drink_rnd,
                "random": drink_rnd,
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
        receiver = table.get_player(receiver_id)

        if not receiver or hat_type not in HAT_TYPES:
            await self.send_to(
                player, {"type": "error", "msg": "Noto'g'ri shapka", "ts": self._ts()}
            )
            return

        ok = await self._spend_hearts(player, price, "hat", f"hat:{hat_type}")
        if not ok:
            return

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
                await repo.spend_stars(db_id, amount, tx_type, description)
        except Exception as e:
            log.error(f"Stars spend DB xatosi: {e}")

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
        receiver = table.get_player(receiver_id)
        if not receiver:
            return

        price = 15
        ok = await self._spend_hearts(player, price, "random_gift")
        if not ok:
            return

        gift_type = random.choice(GIFT_TYPES)
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
        asyncio.create_task(self._save_gift_stats(player.db_id, receiver.db_id, price))

    # ════════════════════════════════════════════════════════════════════════
    # CHAT
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_chat(self, table: Table, player: Player, data: dict):
        body = str(data.get("body", "")).strip()[:500]
        receiver_id = str(data.get("receiver_id", ""))
        receiver = table.get_player(receiver_id)
        if not body:
            return
        if self._db_factory and str(table.table_id).isdigit():
            try:
                async with self._db() as repo:
                    await repo.append_table_chat_message(
                        int(table.table_id),
                        player.id,
                        player.username,
                        body,
                    )
            except Exception as e:
                log.warning(f"chat persist: {e}")
        await self.broadcast(
            table.table_id,
            {
                "type": "game_chat",
                "body": body,
                "user": player.to_short(),
                "receiver": receiver.to_short() if receiver else None,
                "receiver_name": data.get("receiver_name", ""),
                "timestamp": self._ts(),
                "ts": self._ts(),
            },
        )

    async def _handle_locked_message(self, table: Table, player: Player, data: dict):
        if not player.vip:
            await self.send_to(
                player, {"type": "error", "msg": "VIP kerak", "ts": self._ts()}
            )
            return
        body = str(data.get("body", "")).strip()[:500]
        receiver_id = str(data.get("receiver_id", ""))
        receiver = table.get_player(receiver_id)
        if not body or not receiver:
            return
        await self.send_to(
            receiver,
            {
                "type": "locked_message",
                "body": body,
                "user": player.to_short(),
                "ts": self._ts(),
            },
        )

    # ════════════════════════════════════════════════════════════════════════
    # MUSIC
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_music(self, table: Table, player: Player, data: dict):
        price = int(data.get("price", 5))
        ok = await self._spend_hearts(player, price, "music")
        if not ok:
            return

        player.dj_score += price
        asyncio.create_task(self._save_dj_stat(player.db_id, price))

        await self.broadcast(
            table.table_id,
            {
                "type": "game_music",
                "artist": data.get("artist", ""),
                "title": data.get("title", ""),
                "url": data.get("url", ""),
                "duration": data.get("duration", 0),
                "id": data.get("id", ""),
                "icon": data.get("icon", ""),
                "provider": data.get("provider", ""),
                "source": data.get("source", ""),
                "user": player.to_short(),
                "start_timestamp": self._ts(),
                "ts": self._ts(),
            },
        )

        # Yutuq tekshiruvi (DJ score)
        await self._check_achievements(player, "dj_score", player.dj_score)

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
    async def _harem_purchase_fail(
        self, player: Player, err: str = "not_enough_gold"
    ):
        await self.send_to(
            player,
            {"type": "harem_purchase", "error": err, "ts": self._ts()},
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
        target = table.get_player(target_id)
        if not target:
            uid = self._resolve_client_user_ref_to_db_id(target_id, player)
            if uid:
                target = next(
                    (pl for pl in table.players.values() if pl.db_id == uid),
                    None,
                )
        if not target or target.id == player.id:
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

        ok = await self._spend_hearts(player, price, "harem_purchase", f"harem:{target_id}")
        if not ok:
            await self._harem_purchase_fail(player, "not_enough_gold")
            return

        target.harem_owner_id = buyer_db
        target.harem_price = int(target.harem_price) + 1

        if target.db_id:
            await self._db_update_user(
                target.db_id,
                harem_owner_id=target.harem_owner_id,
                harem_price=target.harem_price,
            )

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

        await self.broadcast(table.table_id, hp)

        target_participant = target.to_participant()
        # Ikkala klient ham (legacy + welcome) uxajor ma'lumotini ko'rsin
        await self._attach_harem_owner_payload(target_participant, buyer_db)
        target_participant["harem_price"] = target.harem_price
        await self.broadcast(
            table.table_id,
            self._make_update_user_payload(target_participant),
        )

        log.info(
            f"HAREM: {player.username} → {target.username} price={price} new={target.harem_price}"
        )

        # Yutuq tekshiruvi — target uchun (mashhurlik o'sishi)
        await self._check_achievements(target, "harem_price", target.harem_price)

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
          • cancel — viewer hozirgi uxajor bo'lsa, o'zini olib tashlaydi.
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
            # Faqat hozirgi uxajor o'zini olib tashlay oladi
            if viewer_db and cur_owner == viewer_db:
                new_owner = 0
                new_price = max(1, cur_price)  # narx kamaytirilmaydi
                if live_target:
                    live_target.harem_owner_id = 0
                if target_db_id:
                    await self._db_update_user(
                        target_db_id, harem_owner_id=0
                    )
                cur_owner = 0
                log.info(
                    "LIKE/cancel: viewer=%s removed self as admirer of %s",
                    player.id,
                    raw_target,
                )
            else:
                log.info(
                    "LIKE/cancel ignore: viewer=%s is not current admirer (%s)",
                    player.id,
                    cur_owner,
                )

        else:  # action == "like"
            # Allaqachon shu odam uxajor — qaytadan to'lov olmaymiz
            if viewer_db and cur_owner == viewer_db:
                log.info("LIKE: viewer=%s already admirer", player.id)
            else:
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

                new_owner = viewer_db
                new_price = price + 1
                if live_target:
                    live_target.harem_owner_id = new_owner
                    live_target.harem_price = new_price
                if target_db_id:
                    await self._db_update_user(
                        target_db_id,
                        harem_owner_id=new_owner,
                        harem_price=new_price,
                    )
                cur_owner = new_owner
                cur_price = new_price
                log.info(
                    "LIKE: viewer=%s → target=%s price=%d new=%d",
                    player.id,
                    raw_target,
                    price,
                    new_price,
                )

                # Stol uchastniklariga ham eski-uy uchun update_user yuboramiz
                if live_target and live_target.table_id:
                    part = live_target.to_participant()
                    part["harem_owner_id"] = new_owner
                    part["harem_price"] = new_price
                    await self._attach_harem_owner_payload(part, new_owner)
                    await self.broadcast(
                        live_target.table_id,
                        self._make_update_user_payload(part),
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
        kiss_rank = 1
        total_music_count = int(extra.dj_score if extra else 0) or 0
        music_rank = 1
        # smile_count: umumiy iltifotlar (lifetime) + bank sikli (kamida)
        _life = int(getattr(extra, "compliments_lifetime", 0) or 0) if extra else 0
        _sent = int(getattr(extra, "compliments_sent", 0) or 0) if extra else 0
        total_smile_count = max(_life, _sent)
        smile_rank = 1
        top = False
        league_name = ""
        frame_name = (extra.frame if extra else "") or ""
        vip_color = getattr(extra, "vip_color", None)
        status = (extra.status if extra else "") or ""
        user_id_out = (
            (live_target.id if live_target else None)
            or (str(target_db_id) if target_db_id else raw_target)
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
            "like_price_rank": int(cur_price or 1),
            "ts": self._ts(),
        }

    # ════════════════════════════════════════════════════════════════════════
    # ACHIEVEMENTS (Yutiqlar) — kritik o'yin voqealarida unlock qiladi
    # ════════════════════════════════════════════════════════════════════════
    # Har bir yutiq uchun bosqichlar (1-based level). Klient `assets.json` dagi
    # `counters` ro'yxati bilan mos kelishi kerak.
    # `bonus`: o'sha darajaga yetganda berib yuboriladigan gold.
    ACHIEVEMENTS: dict[str, dict] = {
        # Ko'p o'pgan (kissing)
        "donjuan":   {"metric": "total_kisses", "counters": [5, 25, 100, 250, 500],     "bonus": 50},
        "captain":   {"metric": "total_kisses", "counters": [10, 20, 50, 100, 200],     "bonus": 30},
        # DJ
        "dj":        {"metric": "dj_score",     "counters": [5, 15, 50],                "bonus": 40},
        "recorder":  {"metric": "dj_score",     "counters": [10, 30, 100, 300, 1000, 3000, 10000], "bonus": 100},
        # Iltifot (compliment) yuborish
        "kindlysoul":{"metric": "compliments_sent", "counters": [15, 75, 300, 750, 1500], "bonus": 30},
        # Uxajivat narxi (harem_price)
        "celebrity": {"metric": "harem_price",  "counters": [20, 80, 250, 750, 1500],   "bonus": 50},
        # Bottle aylantirish (newcomer milestone)
        "newcomer":  {"metric": "spins",        "counters": [1, 5, 10, 50, 100],        "bonus": 20},
    }

    async def _check_achievements(
        self, player: Player, metric: str, total: int
    ) -> None:
        """Berilgan metrika bo'yicha mos yutuqlarni tekshiradi va kerakli
        bo'lsa `achievement_bonus` paketini yuboradi.
        """
        if total <= 0:
            return
        for key, cfg in self.ACHIEVEMENTS.items():
            if cfg["metric"] != metric:
                continue
            counters = cfg["counters"]
            cur_level = int(player.achievements.get(key, 0))
            # Eng yuqori erishilgan darajani topamiz
            new_level = cur_level
            for i, threshold in enumerate(counters):
                if total >= threshold:
                    new_level = max(new_level, i + 1)
            if new_level <= cur_level:
                continue
            player.achievements[key] = new_level
            bonus_amount = int(cfg.get("bonus", 20))
            log.info(
                "ACHIEVEMENT unlocked: %s lvl=%d for %s (%s=%d)",
                key, new_level, player.username, metric, total,
            )
            # DB ga saqlash (best-effort)
            if player.db_id:
                async def _persist():
                    try:
                        async with self._db() as repo:
                            await repo.upsert_user_achievement(
                                int(player.db_id), key, new_level
                            )
                    except Exception as e:
                        log.debug(f"achievement persist failed: {e}")
                asyncio.create_task(_persist())

            # Klientga `achievement_bonus` (modal) va `game_achievement`
            # (chatga xabar) yuboramiz.
            payload = {
                "type":          "achievement_bonus",
                "ts":            self._ts(),
                "timestamp":     self._ts(),
                "user":          player.to_short(),
                "achievement_id": key,
                "level":         new_level - 1,  # klient 0-based level kutadi
                "bonus":         bonus_amount,
            }
            await self.send_to(player, payload)
            if player.table_id:
                await self.broadcast(
                    player.table_id,
                    {**payload, "type": "game_achievement"},
                )

    async def _handle_claim_achievement_bonus(
        self, player: Player, data: dict
    ) -> None:
        """Klient yutuq mukofotini olishni so'raydi.

        `claim_achievement_bonus` paketida `achievement_id`, `bonus` (gold
        sonu) keladi. Biz bonus qiymatini ACHIEVEMENTS lug'atidan tekshirib,
        gold qo'shamiz va wallet yangilanadi.
        """
        key = str(data.get("achievement_id", "")).strip()
        shared = bool(data.get("shared", False))
        cfg = self.ACHIEVEMENTS.get(key)
        if not cfg:
            log.debug(f"claim_achievement_bonus: noma'lum id={key!r}")
            return
        bonus = int(cfg.get("bonus", 20))
        if shared:
            bonus *= 2  # ulashganda mukofot 2x
        await self._give_hearts(
            player, bonus, f"achievement:{key}", save_to_db=True
        )
        log.info(
            "ACHIEVEMENT claim: %s id=%s bonus=%d shared=%s",
            player.username, key, bonus, shared,
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
        from datetime import datetime, timezone

        db_fields = {}

        if "name" in data:
            player.username = str(data["name"])[:30]
            db_fields["display_name"] = player.username

        if "male" in data:
            player.male = bool(data["male"])
            player.gender = "male" if player.male else "female"
            db_fields["gender"] = player.gender

        if "locale" in data:
            player.locale = str(data["locale"])
            db_fields["language_code"] = player.locale

        if "status" in data:
            player.status = str(data["status"])[:100]
            db_fields["status_text"] = player.status

        # Klient (L1.toServerBirthdayFull): "YYYY.MM.DD" — profil zodiak uchun
        if "birthday_full" in data:
            ts_ms = parse_birth_date_ms(str(data["birthday_full"]).strip())
            if ts_ms:
                player.birthday_ts = ts_ms
                d_birth = datetime.fromtimestamp(
                    ts_ms / 1000, tz=timezone.utc
                ).date()
                db_fields["birth_date"] = d_birth.isoformat()
                today = datetime.now(timezone.utc).date()
                player.age = max(
                    0,
                    today.year
                    - d_birth.year
                    - (
                        (today.month, today.day) < (d_birth.month, d_birth.day)
                    ),
                )
                db_fields["age"] = player.age

        if "age" in data and "birthday_full" not in data:
            try:
                player.age = max(0, int(data["age"]))
                db_fields["age"] = player.age
            except (TypeError, ValueError):
                pass

        player.is_new = 0
        db_fields["level"] = player.level

        if db_fields and player.db_id:
            asyncio.create_task(self._db_update_user(player.db_id, **db_fields))

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

    async def _enrich_get_profile_payload(
        self, payload: dict, db_id: Optional[int]
    ) -> None:
        """Klient profil dialogi: «в рейтинге» va yutiq kubogi (achievements)."""
        if not db_id:
            payload.setdefault("achievements", [])
            return
        try:
            async with self._db() as repo:
                rk, _ = await repo.get_user_rank_by_column(db_id, "kisses")
                payload["total_kisses_rank"] = rk
                rk, _ = await repo.get_user_rank_by_column(db_id, "dj")
                payload["dj_score_rank"] = rk
                rk, _ = await repo.get_user_rank_by_column(db_id, "emotion")
                payload["gestures_rank"] = rk
                rk, _ = await repo.get_user_rank_by_column(db_id, "expense")
                payload["price_rank"] = rk
                rk, _ = await repo.get_user_rank_by_column(db_id, "harem_price")
                payload["harem_price_rank"] = rk

                ach = await repo.get_user_achievements(db_id)
                payload["achievements"] = [
                    {"achievement_id": k, "level": int(v or 0), "timestamp": 0}
                    for k, v in sorted(ach.items())
                ]
        except Exception as e:
            log.error(f"_enrich_get_profile_payload: {e}")
            payload.setdefault("achievements", [])

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
        player.frame = str(data.get("frame", ""))
        player.stone = str(data.get("stone", ""))
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
                if self._room_online_count(tid) >= 12:
                    continue
                available_ids.append(tid)
        except Exception as e:
            log.error(f"goto_random DB xatosi: {e}")

        if not available_ids:
            available_ids = [
                tid
                for tid, t in self.tables.items()
                if tid != player.table_id and t.player_count() < 12
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
                    "msg": "Bu stoldan haydalgansiz. 15 daqiqadan keyin qayta urinib ko'ring.",
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
        login_pl = await self._login_payload_with_friends(new_player, new_tid, ts)
        await self.send_to(new_player, login_pl)
        await asyncio.sleep(0.2)

        new_table = self.tables.get(new_tid)
        if new_table:
            await self._emit_game_enter_join_and_turn(new_player, new_table, ts)

        log.info(f"GOTO RANDOM: {old_user_id} -> {new_tid}")

    async def _handle_goto_user(self, ws: WebSocket, player: Player, data: dict):
        target_id = str(data.get("user_id", "")).strip()
        for tid, t in self.tables.items():
            if target_id in t.players and tid != player.table_id:
                if self._db_factory:
                    try:
                        rid = int(tid)
                        async with self._db() as repo:
                            row = await repo.get_table_by_id(rid)
                            if row:
                                cc = normalize_country_code(
                                    player.country or "UZBEKISTAN"
                                )
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
                                if not await self._room_id_is_visible_for_country(
                                    cc, tid
                                ):
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
                            "msg": "Bu stoldan haydalgansiz. 15 daqiqadan keyin qayta urinib ko'ring.",
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
                login_pl = await self._login_payload_with_friends(new_player, tid, ts)
                await self.send_to(new_player, login_pl)
                await asyncio.sleep(0.2)
                new_table = self.tables.get(tid)
                if new_table:
                    await self._emit_game_enter_join_and_turn(new_player, new_table, ts)
                return
        await self.send_to(
            player,
            {"type": "error", "msg": "Foydalanuvchi topilmadi", "ts": self._ts()},
        )

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
        next_kick = kickout_price_for_use_index(await self._kickout_effective_uses(player))
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
                    await repo.add_relation(
                        player.db_id, target_uid, "friend_request"
                    )
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
            log.warning("friend_request_answer: user_id DB ga map qilinmadi (%s)", tid_log)

        if player.db_id and target_uid and player.db_id != target_uid:
            try:
                async with self._db() as repo:
                    # (yuboruvchi → qabul qiluvchi) va teskarisi — har ikki yo'nalishdagi so'rovni tozalash
                    await repo.remove_relation(target_uid, player.db_id, "friend_request")
                    await repo.remove_relation(player.db_id, target_uid, "friend_request")
                    if accepted:
                        ok_a = await repo.add_relation(player.db_id, target_uid, "friend")
                        ok_b = await repo.add_relation(target_uid, player.db_id, "friend")
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
        await self.send_to(player, {"type": "ok", "invite_sent": bool(target), "ts": self._ts()})

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
        await self.send_to(
            player, {"type": "items_get", "items": player.items, "ts": self._ts()}
        )

    async def _handle_items_use(self, player: Player, data: dict):
        item = data.get("item", "")
        if item in BOOSTER_TYPES and player.items.get(item, 0) > 0:
            player.items[item] -= 1
            player.boosters.append(item)
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
        await self.send_to(
            player,
            {
                "type": "league_info",
                "league": 1,
                "league_state": "welcome",
                "max_league": 16,
                "finish_ms": self._ts() + 86400000,
                "frame": "",
                "gifts": ["heartangel", "brokenheart", "heartdevil"],
                "gold": [4, 3, 2, 1, 1, 1, 1, 1, 1, 1],
                "items": {},
                "kisses": player.total_kisses,
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
        "dj_score":     "dj",
        "harem_price":  "harem_price",
        "gestures":     "emotion",
        "price":        "expense",
        # Eski (UserStats) atashlar — backward compatibility uchun
        "kisses":     "kisses",
        "dj":         "dj",
        "expense":    "expense",
        "importance": "importance",
        "emotion":    "emotion",
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
                            "top": [], "self_rank": 0, "self_score": 0,
                            "top_reset_ms": 0,
                        }
                        continue
                    rows = await repo.get_top_by_user_column(col_name, limit=size)
                    top_items = []
                    for row in rows:
                        item = {
                            "id":        row["id"],
                            "male":      row["male"],
                            "name":      row["name"],
                            "username":  row["username"],
                            "photo_url": row["photo_url"],
                            top_type:    row["score"],
                        }
                        top_items.append(item)

                    self_rank, self_score = (0, 0)
                    if player.db_id:
                        self_rank, self_score = await repo.get_user_rank_by_column(
                            int(player.db_id), col_name
                        )

                    result[top_type] = {
                        "top":          top_items,
                        "self_rank":    self_rank,
                        "self_score":   self_score,
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
        await self.send_to(
            player,
            {
                "type": "translate",
                "req_id": data.get("req_id", 0),
                "ttext": data.get("text", "") + " [translated]",
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
            player.stars += 10
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
                await repo.add_stars(db_id, amount, tx_type)
        except Exception as e:
            log.error(f"Stars add DB xatosi: {e}")

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
            await self.send_to(
                player,
                {
                    "type": "update_vip",
                    "user_id": player.id,
                    "vip": True,
                    "tokens": wf["tokens"],
                    "ts": self._ts(),
                },
            )
            await self._push_wallet_sync(player)
            table = self.tables.get(player.table_id)
            if table:
                await self._broadcast_to_table(
                    table,
                    {"type": "user_vip_upgraded", "uid": player.id, "ts": self._ts()},
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
                ok, new_stars = await repo.purchase_vip_with_stars(
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
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": f"VIP uchun {price} STARS kerak.",
                    "ts": self._ts(),
                },
            )
            return

        player.stars = new_stars
        player.vip = True
        player.grant_default_owned_items()

        wf = player.wallet_for_client()
        await self.send_to(
            player,
            {
                "type": "update_vip",
                "user_id": player.id,
                "vip": True,
                "tokens": wf["tokens"],
                "ts": self._ts(),
            },
        )
        await self._push_wallet_sync(player)

        table = self.tables.get(player.table_id)
        if table:
            await self._broadcast_to_table(
                table,
                {"type": "user_vip_upgraded", "uid": player.id, "ts": self._ts()},
            )

    async def _handle_item_purchase(self, player: Player, data: dict):
        item = data.get("item", "")
        price = int(data.get("price", 30))

        ok = await self._spend_hearts(player, price, "item_purchase", f"item:{item}")
        if not ok:
            return

        if item in BOOSTER_TYPES:
            player.boosters.append(item)
        player.items[item] = player.items.get(item, 0) + 1

        await self.send_to(
            player,
            {
                "type": "item_purchase",
                "ok": True,
                "item": item,
                "items": player.items,
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

    async def _handle_pass_claim_level_reward(
        self, player: Player, data: dict
    ):
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
                "items": [
                    {"gold": 50, "tokens": 1},
                    {"gold": 100, "tokens": 2},
                    {"gold": 500, "tokens": 12},
                    {"gold": 1000, "tokens": 25},
                ],
                "ts": ts,
            },
        )

    async def _handle_gold2tokens(self, player: Player, data: dict):
        """gold → stars (tokens). Klient allaqachon goldni kamaytirgan; server authoritative."""
        gold = int(data.get("gold", 0) or 0)
        if gold <= 0:
            return
        rate = 50
        tokens_inc = max(1, gold // rate)
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
        player.stars += tokens_inc
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
        await self._give_hearts(
            player, bonus, "vk_quest_bonus", save_to_db=True
        )

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
        if player.stars < amount:
            wf = player.wallet_for_client()
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "Token yetarli emas",
                    "tokens": wf["tokens"],
                    "ts": self._ts(),
                },
            )
            return False
        player.stars -= amount
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
    ):
        player.hearts += amount

        if save_to_db and player.db_id and not getattr(player, "is_admin", False):
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

    async def _db_add_hearts(self, db_id: int, amount: int, tx_type: str):
        try:
            async with self._db() as repo:
                await repo.add_hearts(db_id, amount, tx_type)
        except Exception as e:
            log.error(f"Hearts add DB xatosi: {e}")

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
        Aks holda 'game_turn_offer' yuboradi.
        """
        if not table.players:
            return

        # 1. Jinslarni tekshirish
        has_male = any(p.gender == "male" for p in table.players.values())
        has_female = any(p.gender == "female" for p in table.players.values())

        if not has_female or not has_male:
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

        # 3. Avtomatik aylantirish (oldingi taymerni bekor qilib, yangisini qo'yamiz)
        table.schedule_auto_spin_task(self._auto_spin_timeout_task(table, spinner.id))

    async def _auto_spin_timeout_task(self, table: Table, spinner_id: str):
        """Kutadi; aylantirish bo'lmasa server bajaradi (qo'lda bosish uchun vaqt)."""
        try:
            await asyncio.sleep(45)
        except asyncio.CancelledError:
            return
        player = table.get_player(spinner_id)
        if player and table.state == STATE_WAIT and table.turn_seat == player.seat:
            log.info(f"AUTO-SPIN: {player.username} did not spin. Server doing it.")
            await self._handle_game_turn(player)

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
            await self.send_to(
                player,
                {
                    "type": "gm_hearts_purchase_success",
                    "amount": hearts_to_add,
                    "ts": self._ts(),
                },
            )
            return

        try:
            async with self._db() as repo:
                ok, new_stars, new_hearts = await repo.purchase_hearts_with_stars(
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
            await self.send_to(
                player,
                {"type": "error", "msg": "STARS yetarli emas", "ts": self._ts()},
            )
            return

        player.stars = new_stars
        player.hearts = new_hearts
        player.hearts_real = new_hearts

        await self._push_wallet_sync(player)

        await self.send_to(
            player,
            {
                "type": "gm_hearts_purchase_success",
                "amount": hearts_to_add,
                "ts": self._ts(),
            },
        )


# ── Singleton ────────────────────────────────────────────────────────────────
manager = GameManager()


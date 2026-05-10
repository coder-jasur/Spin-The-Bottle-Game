"""
GameManager — to'liq tuzatilgan va kengaytirilgan versiya.
Xonalar ro'yxati, real foydalanuvchi ma'lumotlari, to'liq statistika.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from fastapi import WebSocket

from src.app.api.game_session import game_sessions
from src.app.api.ws.constants import (
    BOOSTER_TYPES,
    BOTTLE_TYPES,
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
)
from src.app.api.ws.player import Player
from src.app.api.ws.table import Table
from src.app.api.ws.utils import prepare_packet
from src.app.database.repositories.game import GameRepository

log = logging.getLogger("spinbottle")


class GameManager:
    def __init__(self):
        self.tables: Dict[str, Table] = {}
        # ws → (table_id, user_id)
        self.ws_map: Dict[WebSocket, Tuple[str, str]] = {}
        self._db_factory = None
        self._tasks_started = False

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
    async def connect(self, ws: WebSocket, table_id: str, user_id: str) -> Player:
        """
        O'yinchi ulanadi.
        Real user_id bo'lsa DB dan ma'lumotlar yuklanadi.
        """
        if table_id not in self.tables:
            self.tables[table_id] = Table(table_id)

        table = self.tables[table_id]

        db_user = None
        real_uid = None

        # 1. user_id ni aniqlash (raqam yoki session token)
        if user_id and not user_id.startswith("guest"):
            try:
                # Agar to'g'ridan-to'g'ri raqam bo'lsa
                real_uid = int(user_id)
            except (ValueError, TypeError):
                # Agar session token bo'lsa
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
            # Muhim: player.id sifatida user_id (token) ni qoldiramiz,
            # chunki frontend buni taniydi. player.db_id ichida esa real UID bo'ladi.
            player = Player.from_db(ws, db_user)
            player.id = user_id
            log.info(
                f"[+] DB user: {player.username}({real_uid}) joined with token={user_id}"
            )
        else:
            g_name = (
                f"Mehmon_{user_id[-4:]}" if len(user_id) > 4 else f"Mehmon_{user_id}"
            )
            player = Player(ws, user_id, g_name)
            log.warning(f"[+] Guest: {user_id} → table={table_id}")

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

    # ════════════════════════════════════════════════════════════════════════
    # BROADCAST / SEND
    # ════════════════════════════════════════════════════════════════════════
    async def broadcast(self, table_id: str, msg: dict, exclude_id: str = None):
        table = self.tables.get(table_id)
        if not table:
            return
        pkt = prepare_packet(msg)
        for uid, player in list(table.players.items()):
            if uid == exclude_id:
                continue
            await self._safe_send(player.ws, pkt)

    async def send_to(self, player: Player, msg: dict):
        await self._safe_send(player.ws, prepare_packet(msg))

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
        if player.db_id:
            try:
                async with self._db() as repo:
                    friends = await repo.get_friends(player.db_id)
                    login_payload["friend_user_ids"] = [f.id for f in friends]
            except Exception as e:
                log.error(f"login friend_user_ids: {e}")
        return login_payload

    async def _emit_game_enter_join_and_turn(
        self, player: Player, tbl: Table, ts: int, with_packet: bool = True
    ):
        """Klient sp.start(t) — maydon: game_id (snake_case), scheduled/achievements — massiv."""
        ge: dict = {
            "type": "game_enter",
            "game_id": tbl.table_id,
            "tableId": tbl.table_id,
            "participants": tbl.all_participants(),
            "bottle_type": tbl.bottle_type,
            "scheduled": [],
            "achievements": [],
            "achievements_ms": 0,
            "ts": ts + 5,
        }
        if with_packet:
            ge["packet"] = 100
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
            await self._give_hearts(player, 20, t, save_to_db=True)
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
            "compliment_group",
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
        elif t in ("compliment_send", "harem_purchase"):
            await self._handle_harem_purchase(table, player, data)
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
        ts = self._ts()
        table_id = table.table_id

        log.info(
            f"LOGIN: {player.username}({player.id}) | "
            f"hearts={player.hearts} | stars={player.stars} | "
            f"vip={player.vip} | table={table_id} | "
            f"guest={'guest' in player.id}"
        )

        # 1–3. Login + game_enter + game_join + navbat (klient sp.start uchun game_id majburiy)
        login_payload = await self._login_payload_with_friends(player, table_id, ts)
        await self.send_to(player, login_payload)
        await asyncio.sleep(0.3)
        await self._emit_game_enter_join_and_turn(player, table, ts, with_packet=True)

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

            if bonus_stars > 0 and player.db_id:
                player.stars += bonus_stars
                asyncio.create_task(
                    self._db_add_stars(player.db_id, bonus_stars, "daily_vip_bonus")
                )

            # DB da belgilab qo'yamiz
            if player.db_id:
                asyncio.create_task(self._db_mark_bonus_claimed(player.db_id))

            player.can_claim_bonus = False

        # 5. Navbat/Kutish holatini tekshirish
        await asyncio.sleep(0.3)
        await self._check_and_broadcast_turn(table)

    # ════════════════════════════════════════════════════════════════════════
    # ROOMS LIST — yangi qo'shilgan
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_get_rooms(self, player: Player, data: dict):
        """
        Foydalanuvchi mamlakatiga mos xonalar ro'yxatini qaytaradi.
        Rasmda ko'rsatilgan data modellarga moslashtirildi.
        """
        country = data.get("country", player.country or "UZBEKISTAN")
        print(f"DEBUG: get_rooms request for country={country} from {player.username}")

        db_rooms = []
        try:
            async with self._db() as repo:
                db_rooms = await repo.get_rooms_by_country(country)
                if len([r for r in db_rooms if r.country_code == country.upper()]) < 3:
                    await repo.ensure_base_rooms(country, min_count=5)
                    db_rooms = await repo.get_rooms_by_country(country)
        except Exception as e:
            log.error(f"get_rooms DB xatosi: {e}")

        # Xonalarni formatlash
        tables_list = []
        for room in db_rooms:
            room_id_str = str(room.room_id)
            participants = []
            if room_id_str in self.tables:
                participants = self.tables[room_id_str].all_participants()

            # Jadvaldagi modelga 100% muvofiq (for ichida)
            tables_list.append(
                {
                    "tableId": room_id_str,
                    "tableUsers": participants,
                    "tablePresenter": {},
                    "tableBoosters": [],
                    "tableActions": [],
                    "tableView": {"id": room_id_str},
                    "tableScale": 1.0,
                    "tableUrl": "",
                }
            )

        # Fallback
        if not tables_list:
            for i in range(1, 6):
                rid = f"100{i}"
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
                        "name": f"Xona {rid}",
                    }
                )

        print(
            f"DEBUG: Sending {len(tables_list)} tables (strict model) to {player.username}"
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

        old_user_id = player.id
        await self.disconnect(ws)

        new_player = await self.connect(ws, new_room_id, old_user_id)
        ts = self._ts()

        login_pl = await self._login_payload_with_friends(new_player, new_room_id, ts)
        await self.send_to(new_player, login_pl)
        await asyncio.sleep(0.2)

        new_table = self.tables.get(new_room_id)
        if new_table:
            await self._emit_game_enter_join_and_turn(
                new_player, new_table, ts, with_packet=False
            )

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

        history_rows: List[dict] = []
        country = player.country or "UZBEKISTAN"
        try:
            async with self._db() as repo:
                db_rooms = await repo.get_rooms_by_country(country)
                for room in db_rooms[:24]:
                    tid = str(room.room_id)
                    live = self.tables.get(tid)
                    m, w = self._gender_counts_for_table(live)
                    bottle = live.bottle_type if live else "standart"
                    history_rows.append(
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
                "games_history": history_rows,
                "ts": ts,
            },
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

        await self.send_to(
            player,
            {
                "type": "get_wallet",
                "ok": True,
                "gold": player.hearts,
                "tokens": player.stars,
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
                f"SPIN: {player.username} (seat {player.seat}) cannot spin. Table state: {table.state}, Bottle seat: {table.bottle_seat}"
            )
            # Agar xato bo'lsa, foydalanuvchiga turn offerni qayta yuboramiz (UI yangilanishi uchun)
            await self._check_and_broadcast_turn(table)
            return

        ts = self._ts()
        target_seat = table.start_spin(player.id)
        log.info(f"SPIN: {player.username} started spin. Target seat: {target_seat}")

        target_p = table.get_player(table.current_target)

        # Burchakni hisoblash (12 o'rin uchun har biri 30 gradus + 5 ta to'liq aylanish)
        # Seat 0 = 0 deg, Seat 1 = 30 deg, ...
        rotations = 5
        angle = (rotations * 360) + (target_seat * (360 // 12))

        # 1. Butilka aylanishini hamma ko'radi
        await self.broadcast(
            table.table_id,
            {
                "type": "game_spin",
                "gameId": table.table_id,
                "tableId": table.table_id,
                "user": player.to_short(),
                "target": target_p.to_short() if target_p else None,
                "target_seat": target_seat,
                "target_id": table.current_target,
                "angle": angle,
                "bottle_type": table.bottle_type,
                "ts": ts,
            },
        )

        # 2. Aylanish animatsiyasi tugashini kutamiz
        await asyncio.sleep(Table.SPIN_DURATION)

        target = table.get_player(table.current_target)
        if not target:
            table.reset_turn()
            await self._check_and_broadcast_turn(table)
            return

        table.offer_turn()

        # 3. O'pish/Rad etish taklifi (NextTurn)
        await self.broadcast(
            table.table_id,
            {
                "type": "game_turn_offer",
                "gameId": table.table_id,
                "tableId": table.table_id,
                "user": player.to_short(),
                "receiver": target.to_short(),
                "ts": self._ts(),
            },
        )

        # 4. Tanlov uchun timeout (Tanlanmasa navbat almashadi)
        asyncio.create_task(self._turn_timeout(table, player.id))

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

    # ════════════════════════════════════════════════════════════════════════
    # KISS
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_game_kiss(self, table: Table, player: Player, data: dict):
        receiver_id = str(data.get("receiver_id", ""))
        receiver = table.get_player(receiver_id)
        if not receiver:
            return

        ts = self._ts()
        has_kiss_fire = "kiss_fire" in player.boosters

        await self.broadcast(
            table.table_id,
            {
                "type": "game_kiss",
                "user": player.to_short(),
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
                    "user_id": player.id,
                    "receiver_id": receiver_id,
                    "ts": self._ts(),
                },
            )
            player.boosters = [b for b in player.boosters if b != "kiss_fire"]

        # Statistika yangilash (in-memory)
        # League score qo'shish (boosterlar bilan)
        score_to_add = 1
        if "league_kiss2x" in player.boosters:
            score_to_add = 2

        player.kisses += 1
        player.total_kisses += 1
        player.league_score += score_to_add
        receiver.kisses += 1
        receiver.league_score += score_to_add
        table.room_kiss_count += 1

        # DB ga saqlash (background)
        asyncio.create_task(self._save_kiss_stats(player.db_id, receiver.db_id))

        # League score xabari
        await self.broadcast(
            table.table_id,
            {
                "type": "league_score",
                "user": player.to_short(),
                "user_id": player.id,
                "score": score_to_add,
                "assign": {"kisses": 1, "league_score": score_to_add},
                "kisses": player.kisses,
                "kisses_lim": 500,
                "ts": self._ts(),
            },
        )

        # Har 5 ta kissda hearts bonus
        if player.total_kisses % 5 == 0:
            await self._give_hearts(
                player, KISS_BONUS_GOLD, "kiss_bonus", save_to_db=True
            )

        table.reset_turn()
        await asyncio.sleep(1.5)
        await self._advance_bottle(table, receiver)

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
        receiver_id = str(data.get("receiver_id", ""))
        receiver = table.get_player(receiver_id)
        if not receiver:
            return

        has_refuse_slap = "refuse_slap" in player.boosters

        await self.broadcast(
            table.table_id,
            {
                "type": "game_refuse",
                "user": player.to_short(),
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
                    "user_id": player.id,
                    "receiver_id": receiver_id,
                    "ts": self._ts(),
                },
            )
            player.boosters = [b for b in player.boosters if b != "refuse_slap"]

        table.reset_turn()
        await asyncio.sleep(1.5)
        await self._advance_bottle(table, player)

    # ════════════════════════════════════════════════════════════════════════
    # ADVANCE BOTTLE
    # ════════════════════════════════════════════════════════════════════════
    async def _advance_bottle(self, table: Table, current: Player):
        players_list = sorted(table.players.values(), key=lambda p: p.seat)
        if not players_list:
            return
        idx = next((i for i, p in enumerate(players_list) if p.id == current.id), 0)
        next_p = players_list[(idx + 1) % len(players_list)]
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

        await self.broadcast(
            table.table_id,
            {
                "type": "game_gift",
                "gift_type": gift_type,
                "user": player.to_short(),
                "receiver": receiver.to_short(),
                "price": price,
                "magic": True,
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

        await self.broadcast(
            table.table_id,
            {
                "type": "game_drink",
                "drink_type": drink_type,
                "user": player.to_short(),
                "receiver": receiver.to_short(),
                "price": price,
                "drink_random": 0,
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

        await self.broadcast(
            table.table_id,
            {
                "type": "game_hat",
                "hat_type": hat_type,
                "user": player.to_short(),
                "receiver": receiver.to_short(),
                "price": price,
                "hat_random": 0,
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

        if player.stars < price:
            await self.send_to(
                player, {"type": "error", "msg": "Token yetarli emas", "ts": self._ts()}
            )
            return

        player.stars -= price
        asyncio.create_task(
            self._db_spend_stars(player.db_id, price, "gesture", gesture)
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

        await self.broadcast(
            table.table_id,
            {
                "type": "game_gift",
                "gift_type": gift_type,
                "user": player.to_short(),
                "receiver": receiver.to_short(),
                "price": price,
                "magic": True,
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

    async def _save_dj_stat(self, db_id, amount: int):
        if not db_id:
            return
        try:
            async with self._db() as repo:
                await repo.add_stat(db_id, "dj", amount)
        except Exception as e:
            log.error(f"DJ stat DB xatosi: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # COMPLIMENT / COURT (UXAJIVAT)
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_harem_purchase(self, table: Table, player: Player, data: dict):
        """Foydalanuvchi boshqa foydalanuvchiga e'tibor (court) qila boshlaydi."""
        target_id = str(data.get("user_id", ""))
        target = table.get_player(target_id)
        if not target:
            return

        price = target.harem_price
        ok = await self._spend_hearts(player, price, "compliment", f"court:{target_id}")
        if not ok:
            return

        # Yangi ownerni o'rnatamiz
        try:
            target.harem_owner_id = int(player.id)
        except (ValueError, TypeError):
            target.harem_owner_id = 0

        # Narxni +1 ga oshiramiz (Siz so'raganingizdek)
        target.harem_price += 1

        # DB ga saqlash (persistent bo'lishi uchun)
        if target.db_id:
            asyncio.create_task(
                self._db_update_user(
                    target.db_id,
                    harem_owner_id=target.harem_owner_id,
                    harem_price=target.harem_price,
                )
            )

        # 1. Animatsiya/Xabar yuborish
        await self.broadcast(
            table.table_id,
            {
                "type": "harem_purchase",
                "user": player.to_short(),
                "receiver": target.to_short(),
                "price": price,
                "harem_price": target.harem_price,
                "ts": self._ts(),
            },
        )

        # 2. Target (uxajivat qilingan odam)ga interaktiv xabarnoma yuborish
        await self.send_to(
            target,
            {
                "type": "compliment_send",  # Ba'zi klientlar buni notification sifatida taniydi
                "user": player.to_short(),
                "message": f"❤️ {player.username} sizni {price} ta heart evaziga uxajivat qilyapti!",
                "is_notification": True,
                "ts": self._ts(),
            },
        )

        # 3. Foydalanuvchi ma'lumotlarini yangilash (HaremOwner ob'ekti bilan)
        target_participant = target.to_participant()
        target_participant["harem_owner"] = player.to_short()

        await self.broadcast(
            table.table_id,
            {"type": "update_user", "user": target_participant, "ts": self._ts()},
        )

        log.info(
            f"COURT: {player.username} started courting {target.username} for {price}"
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

        player.is_new = 0
        db_fields["level"] = player.level

        if db_fields and player.db_id:
            asyncio.create_task(self._db_update_user(player.db_id, **db_fields))

        await self.broadcast(
            table.table_id,
            {"type": "update_user", "user": player.to_participant(), "ts": self._ts()},
        )
        await self.send_to(
            player, {"type": "update_profile", "ok": True, "ts": self._ts()}
        )

    async def _handle_get_profile(self, player: Player, data: dict):
        target_id = str(data.get("user_id", player.id))

        # Aktiv o'yinchilardan qidirish
        for t in self.tables.values():
            p = t.get_player(target_id)
            if p:
                payload = p.to_participant()
                # Uxajor ma'lumotlarini qo'shish
                if p.harem_owner_id:
                    owner_p = t.get_player(str(p.harem_owner_id))
                    if not owner_p:
                        # Oflayn bo'lsa DB dan yuklaymiz
                        try:
                            async with self._db() as repo:
                                db_owner = await repo.get_user_with_wallet(
                                    p.harem_owner_id
                                )
                                if db_owner:
                                    fake_p = Player.from_db(None, db_owner)
                                    payload["harem_owner"] = fake_p.to_short()
                        except Exception:
                            pass
                    else:
                        payload["harem_owner"] = owner_p.to_short()

                payload.update(
                    {
                        "type": "get_profile",
                        "ok": True,
                        "ts": self._ts(),
                        "achievements": [],
                    }
                )
                await self.send_to(player, payload)
                return

        # DB dan qidirish
        try:
            uid_int = int(target_id)
            async with self._db() as repo:
                db_user = await repo.get_user_with_wallet(uid_int)
                if db_user:
                    fake = Player.from_db(None, db_user)
                    payload = fake.to_participant()
                    payload.update(
                        {
                            "type": "get_profile",
                            "ok": True,
                            "ts": self._ts(),
                            "achievements": [],
                        }
                    )
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
        await self.broadcast(
            table.table_id,
            {"type": "update_user", "user": player.to_participant(), "ts": self._ts()},
        )

    async def _handle_reset_photo(self, table: Table, player: Player):
        player.photo_url = "/photos/no_img.png"
        if player.db_id:
            asyncio.create_task(self._db_update_user(player.db_id, avatar_url=None))
        await self.broadcast(
            table.table_id,
            {"type": "update_user", "user": player.to_participant(), "ts": self._ts()},
        )

    async def _db_update_user(self, db_id: int, **fields):
        try:
            async with self._db() as repo:
                await repo.update_user_fields(db_id, **fields)
        except Exception as e:
            log.error(f"User update DB xatosi: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # NAVIGATION
    # ════════════════════════════════════════════════════════════════════════
    async def _handle_goto_random(self, ws: WebSocket, player: Player):
        """O'yinchini tasodifiy boshqa xonaga o'tkazadi. DB dan qidiradi."""
        country = player.country or "UZBEKISTAN"
        available_ids = []

        try:
            async with self._db() as repo:
                db_rooms = await repo.get_rooms_by_country(country)
                available_ids = [
                    str(r.room_id)
                    for r in db_rooms
                    if str(r.room_id) != player.table_id
                ]
        except Exception as e:
            log.error(f"goto_random DB xatosi: {e}")

        # Agar bazada yo'q bo'lsa — xotiradagilardan (self.tables)
        if not available_ids:
            available_ids = [
                tid
                for tid, t in self.tables.items()
                if tid != player.table_id and t.player_count() < 12
            ]

        if available_ids:
            new_tid = random.choice(available_ids)
        else:
            # Fallback (biron bir raqamli ID)
            new_tid = "1001" if player.table_id != "1001" else "1002"

        print(f"DEBUG: goto_random for {player.username} -> new_tid={new_tid}")

        old_user_id = player.id
        await self.disconnect(ws)
        new_player = await self.connect(ws, new_tid, old_user_id)

        ts = self._ts()
        login_pl = await self._login_payload_with_friends(new_player, new_tid, ts)
        await self.send_to(new_player, login_pl)
        await asyncio.sleep(0.2)

        new_table = self.tables.get(new_tid)
        if new_table:
            await self._emit_game_enter_join_and_turn(
                new_player, new_table, ts, with_packet=False
            )

        log.info(f"GOTO RANDOM: {old_user_id} -> {new_tid}")

    async def _handle_goto_user(self, ws: WebSocket, player: Player, data: dict):
        target_id = str(data.get("user_id", "")).strip()
        for tid, t in self.tables.items():
            if target_id in t.players and tid != player.table_id:
                old_uid = player.id
                await self.disconnect(ws)
                new_player = await self.connect(ws, tid, old_uid)
                ts = self._ts()
                login_pl = await self._login_payload_with_friends(new_player, tid, ts)
                await self.send_to(new_player, login_pl)
                await asyncio.sleep(0.2)
                new_table = self.tables.get(tid)
                if new_table:
                    await self._emit_game_enter_join_and_turn(
                        new_player, new_table, ts, with_packet=False
                    )
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
        expected_price = int(data.get("expected_price", 10))
        target = table.get_player(target_id)
        if not target:
            return

        ok = await self._spend_hearts(player, expected_price, "kickout")
        if not ok:
            return

        kickout_ts = self._ts() + 30_000
        target.kickout_ts = kickout_ts

        await self.broadcast(
            table.table_id,
            {
                "ts": self._ts(),
            },
        )

        # 30 soniyadan keyin avtomatik haydash taymeri
        asyncio.create_task(self._kickout_timer(table, target, kickout_ts))

    async def _kickout_timer(self, table: Table, target: Player, kickout_ts: int):
        """30 soniya kutadi va agar user qutqarilmagan bo'lsa, stoldan chiqaradi."""
        await asyncio.sleep(30)
        # Hali ham o'sha stoldami va kickout_ts o'zgarmaganmi (qutqarilmaganmi)?
        if target.id in table.players and target.kickout_ts == kickout_ts:
            log.info(f"KICKOUT: {target.username} timed out. Disconnecting.")
            await self.send_to(
                target,
                {"type": "session_expired", "reason": "kicked", "ts": self._ts()},
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
        await self.broadcast(
            table.table_id,
            {
                "type": "user_save",
                "saviour_user": player.to_short(),
                "saved_user": target.to_short(),
                "kickout_info": {"price": 10, "refresh_ms": 60000},
                "ts": self._ts(),
            },
        )

    async def _handle_kickout_refresh(self, player: Player):
        await self.send_to(
            player,
            {
                "type": "kickout_refresh",
                "kickout_info": {"price": 10, "refresh_ms": self._ts() + 60_000},
                "ts": self._ts(),
            },
        )

    async def _handle_get_friends(self, player: Player, data: dict):
        """Do'stlar ro'yxatini qaytaradi (Privacy hisobga olingan)."""
        target_id = str(data.get("user_id", player.id))

        # Privacy check
        try:
            target_uid = int(target_id)
            async with self._db() as repo:
                target_user = await repo.get_user_with_wallet(target_uid)
                if (
                    target_user
                    and target_user.friends_privacy == "only_me"
                    and target_id != player.id
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

    async def _handle_friend_add(self, player: Player, data: dict):
        target_id = str(data.get("user_id", ""))
        target = self._find_player(target_id)
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
        target_id = str(data.get("user_id", ""))

        if player.db_id:
            try:
                target_uid = int(target_id)
                async with self._db() as repo:
                    await repo.remove_relation(player.db_id, target_uid, "friend")
                    await repo.remove_relation(target_uid, player.db_id, "friend")
            except Exception:
                pass

        target = self._find_player(target_id)
        if target:
            await self.send_to(
                target,
                {"type": "remove_friend", "user_id": player.id, "ts": self._ts()},
            )
        await self.send_to(player, {"type": "ok", "ts": self._ts()})

    async def _handle_friend_request_answer(self, player: Player, data: dict):
        accepted = data.get("accepted", False)
        target_id = str(data.get("user_id", ""))

        if accepted and player.db_id:
            try:
                target_uid = int(target_id)
                async with self._db() as repo:
                    # Ikki tomonlama do'stlik qo'shamiz
                    await repo.add_relation(player.db_id, target_uid, "friend")
                    await repo.add_relation(target_uid, player.db_id, "friend")
            except Exception:
                pass

        target = self._find_player(target_id)
        if target:
            t_type = "add_new_friends" if accepted else "friend_reject"
            await self.send_to(
                target, {"type": t_type, "user_id": player.id, "ts": self._ts()}
            )
        await self.send_to(player, {"type": "ok", "ts": self._ts()})

    def _find_player(self, user_id: str) -> Optional[Player]:
        for t in self.tables.values():
            p = t.get_player(user_id)
            if p:
                return p
        return None

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
                "kisses": player.kisses,
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

    async def _handle_get_tops(self, player: Player, data: dict):
        """Haqiqiy DB dan TOP ma'lumotlar."""
        tops_types = data.get("tops", ["kisses"])
        if isinstance(tops_types, str):
            tops_types = [tops_types]

        cat_map = {
            "kisses": "kisses",
            "dj": "dj",
            "expense": "expense",
            "importance": "importance",
            "emotion": "emotion",
        }

        result = {}
        try:
            async with self._db() as repo:
                for top_type in tops_types:
                    cat = cat_map.get(top_type, top_type)
                    rows = await repo.get_top(cat, period="all_time", limit=20)
                    result[top_type] = rows
        except Exception as e:
            log.error(f"get_tops DB xatosi: {e}")
            result = {t: [] for t in tops_types}

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
        player.stars += 10
        asyncio.create_task(self._db_add_stars(player.db_id, 10, "vip_tokens"))
        await self.send_to(
            player,
            {
                "type": "claim_vip_tokens",
                "tokens_vip_ms": tokens_ms,
                "tokens_inc": 10,
                "tokens": player.stars,
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
        from src.app.api.ws.constants import VIP_PRICE_STARS

        # 1. Balansni tekshirish
        if player.stars < VIP_PRICE_STARS:
            await self.send_to(player, {
                "type": "error",
                "msg": f"VIP sotib olish uchun {VIP_PRICE_STARS} STARS kerak!",
                "ts": self._ts()
            })
            return

        # 2. Yulduzlarni yechib olish
        player.stars -= VIP_PRICE_STARS
        if player.db_id:
            asyncio.create_task(self._db_spend_stars(player.db_id, VIP_PRICE_STARS, "vip_purchase"))

        # 3. VIP qilish va bonus berish
        player.vip = True
        bonus_stars = 50
        player.stars += bonus_stars

        if player.db_id:
            asyncio.create_task(self._db_update_user(player.db_id, vip_status=True))
            asyncio.create_task(self._db_add_stars(player.db_id, bonus_stars, "vip_bonus"))

        # 4. Javob yuborish
        await self.send_to(player, {
            "type": "update_vip",
            "vip": True,
            "tokens": player.stars,
            "ts": self._ts()
        })

        # Xonadagilarga xabar
        table = self.tables.get(player.table_id)
        if table:
            await self._broadcast_to_table(table, {
                "type": "user_vip_upgraded",
                "uid": player.uid,
                "ts": self._ts()
            })

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
        player.stars += tokens_inc
        if player.db_id:
            asyncio.create_task(
                self._db_add_stars(player.db_id, tokens_inc, "gold2tokens")
            )
        await self.send_to(
            player,
            {
                "type": "gold2tokens",
                "tokens_inc": tokens_inc,
                "tokens": player.stars,
                "gold": player.hearts,
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
        if player.hearts < amount:
            await self.send_to(
                player,
                {
                    "type": "error",
                    "msg": "Gold yetarli emas",
                    "gold": player.hearts,
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

        if save_to_db and player.db_id:
            asyncio.create_task(self._db_add_hearts(player.db_id, amount, tx_type))

        msg = {
            "type": tx_type,
            "gold": player.hearts,
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
            log.info(
                f"TABLE {table.table_id}: Waiting for opposite gender. (M:{has_male}, F:{has_female})"
            )
            return

        # 2. Navbat kimdaligini aniqlash (bottle_seat)
        spinner = next(
            (p for p in table.players.values() if p.seat == table.bottle_seat), None
        )
        if not spinner:
            # Agar o'sha o'rindiqda hech kim bo'lmasa, navbatdagi birinchi o'yinchi
            players_list = sorted(table.players.values(), key=lambda p: p.seat)
            spinner = players_list[0]
            table.bottle_seat = spinner.seat

        # Navbat taklifini hamma ko'rishi kerak
        await self.broadcast(
            table.table_id,
            {
                "type": "game_turn_offer",
                "user": spinner.to_short(),
                "ts": self._ts(),
            },
        )
        log.info(f"TABLE {table.table_id}: Turn offered to {spinner.username}")

        # 3. Avtomatik aylantirish taymeri (15 soniya)
        # Agar foydalanuvchi butilkani bosmasa, server o'zi aylantiradi
        asyncio.create_task(self._auto_spin_timeout(table, spinner.id))

    async def _auto_spin_timeout(self, table: Table, spinner_id: str):
        """15 soniya kutadi va agar aylantirilmagan bo'lsa, o'zi aylantiradi."""
        await asyncio.sleep(15)
        player = table.get_player(spinner_id)
        if player and table.state == STATE_WAIT and table.bottle_seat == player.seat:
            log.info(f"AUTO-SPIN: {player.username} did not spin. Server doing it.")
            # GameManager ning asosiy handle mantiqini chaqiramiz
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
        try:
            amount_stars = int(data.get('amount', 0))
        except:
            await self.send_to(player, {'type': 'error', 'msg': 'Miqdor xato', 'ts': self._ts()})
            return
        if amount_stars not in HEARTS_PACKAGES:
            await self.send_to(player, {'type': 'error', 'msg': 'Bunday paket mavjud emas', 'ts': self._ts()})
            return
        hearts_to_add = HEARTS_PACKAGES[amount_stars]
        if player.stars < amount_stars:
            await self.send_to(player, {'type': 'error', 'msg': 'STARS yetarli emas', 'ts': self._ts()})
            return
        player.stars -= amount_stars
        player.hearts += hearts_to_add
        player.hearts_real += hearts_to_add
        if player.db_id:
            asyncio.create_task(self._db_spend_stars(player.db_id, amount_stars, 'hearts_purchase'))
            asyncio.create_task(self._db_add_hearts(player.db_id, hearts_to_add, 'hearts_purchase'))
        await self.send_to(player, {
            'type': 'update_wallet',
            'hearts': player.hearts,
            'stars': player.stars,
            'ts': self._ts()
        })
        await self.send_to(player, {
            'type': 'gm_hearts_purchase_success',
            'amount': hearts_to_add,
            'ts': self._ts()
        })


# ── Singleton ────────────────────────────────────────────────────────────────
manager = GameManager()


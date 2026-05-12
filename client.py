"""
Spin the Bottle — WebSocket test klienti.

Protokol: server bilan bir xil — 2 bayt + {"data": "<CryptoJS AES>"} (prepare_packet).

Server marshrut: src.app.api.ws.game_manager.GameManager.handle()
"""
import asyncio
import base64
import json
import os
import websockets
from Crypto.Cipher import AES
from Crypto.Hash import MD5

DECRYPT_KEY = "050000000000"
SERVER_URL = "ws://localhost:8000"


def decrypt_cryptojs(encrypted_b64: str, password: str = DECRYPT_KEY) -> dict:
    password_bytes = password.encode("utf-8")
    encrypted = base64.b64decode(encrypted_b64)
    salt = encrypted[8:16]
    ciphertext = encrypted[16:]
    d, d_i = b"", b""
    while len(d) < 48:
        d_i = MD5.new(d_i + password_bytes + salt).digest()
        d += d_i
    cipher = AES.new(d[:32], AES.MODE_CBC, d[32:48])
    decrypted = cipher.decrypt(ciphertext)
    return json.loads(decrypted[:-decrypted[-1]].decode("utf-8"))


def encrypt_cryptojs(data_dict: dict, password: str = DECRYPT_KEY) -> str:
    password_bytes = password.encode("utf-8")
    salt = os.urandom(8)
    d, d_i = b"", b""
    while len(d) < 48:
        d_i = MD5.new(d_i + password_bytes + salt).digest()
        d += d_i
    key, iv = d[:32], d[32:48]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    data_bytes = json.dumps(data_dict, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    pad_len = 16 - (len(data_bytes) % 16)
    padded = data_bytes + bytes([pad_len] * pad_len)
    ciphertext = cipher.encrypt(padded)
    b64 = base64.b64encode(b"Salted__" + salt + ciphertext).decode("utf-8")
    return '"' + b64 + '"'


def _encrypt_aes_b64(data_dict: dict) -> str:
    """CryptoJS OpenSSL format, quotesiz base64 (server encrypt_aes bilan bir xil)."""
    s = encrypt_cryptojs(data_dict)
    return s.strip('"') if s.startswith('"') else s


def prepare_packet(data: dict) -> bytes:
    b64 = _encrypt_aes_b64(data)
    payload = json.dumps({"data": b64}, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    ln = len(payload)
    return bytes([(ln >> 8) & 0xFF, ln & 0xFF]) + payload


def parse_packet(raw: bytes) -> dict:
    if not raw:
        return {}
    payload = raw if raw[0] == 123 else raw[2:]
    try:
        obj = json.loads(payload.decode("utf-8", errors="ignore"))
        if isinstance(obj, dict) and "data" in obj:
            s = obj.get("data", "")
            if isinstance(s, str):
                return decrypt_cryptojs(s) or {}
        return obj if isinstance(obj, dict) else {}
    except Exception as e:
        print(f"[PARSE ERROR] {e}")
        return {}


class SpinBottleClient:
    def __init__(
        self,
        table_id: str = "1",
        user_id: str = "1001",
        username: str = "TestUser",
        photo_url: str = "/photos/no_img.png",
        male: int = 1,
    ):
        params = (
            f"?table_id={table_id}"
            f"&user_id={user_id}"
            f"&username={username}"
            f"&photo_url={photo_url}"
            f"&male={male}"
        )
        self.url = f"{SERVER_URL}/ws/{params}"
        self.user_id = user_id
        self.username = username
        self.ws = None
        self.gold = 0
        self.tokens = 0
        self.table_id = table_id

    async def connect(self):
        self.ws = await websockets.connect(self.url, subprotocols=["binary"])
        print(f"[+] Ulandi: {self.url}")

    async def disconnect(self):
        if self.ws:
            await self.ws.close()
            print("[-] Ulanish yopildi")

    async def send(self, data: dict):
        pkt = prepare_packet(data)
        await self.ws.send(pkt)
        print(f"[→] {data.get('type')} | {data}")

    async def recv(self) -> dict:
        raw = await self.ws.recv()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return parse_packet(raw)

    async def listen(self):
        async for raw in self.ws:
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            data = parse_packet(raw)
            if data:
                await self._on_message(data)

    async def _on_message(self, data: dict):
        t = data.get("type", "")

        if t == "login":
            self.gold = data.get("gold", 0)
            self.tokens = data.get("tokens", 0)
            print(f"[LOGIN] id={data.get('id')} gold={self.gold} tokens={self.tokens}")

        elif t == "game_enter":
            parts = data.get("participants", [])
            print(f"[ENTER] Stol={data.get('game_id')} | O'yinchilar: {len(parts)} ta")
            for p in parts:
                print(f"  • seat={p['seat']} {p['name']} ({p['id']})")

        elif t == "game_join":
            u = data.get("user", {})
            print(f"[JOIN] {u.get('name')} ({u.get('id')}) kirdi")

        elif t == "game_leave":
            u = data.get("user", {})
            print(f"[LEAVE] {u.get('name')} chiqdi")

        elif t == "game_turn":
            print(
                f"[TURN] Navbat: user_id={data.get('user_id')} seat={data.get('bottle_seat')}"
            )

        elif t == "game_spin":
            u = data.get("user") or {}
            tgt = data.get("target") or {}
            print(
                f"[SPIN] {u.get('name', data.get('user_id'))} → "
                f"seat={data.get('target_seat')} ({tgt.get('name', data.get('target_id'))}) "
                f"angle={data.get('angle')}"
            )

        elif t == "game_turn_offer":
            sp = data.get("user") or {}
            rc = data.get("receiver") or {}
            print(f"[OFFER] Navbat: {sp.get('name')} → {rc.get('name')} (kiss / rad)")

        elif t == "game_wait":
            print(f"[WAIT] Taklif vaqti tugadi, spinner={data.get('user_id')}")

        elif t == "game_bottle":
            print(
                f"[BOTTLE] type={data.get('bottle_type', data.get('bottle'))} "
                f"by={(data.get('user') or {}).get('name', '?')}"
            )

        elif t == "game_kiss":
            print(f"[KISS] {data['user']['name']} → {data['receiver']['name']} 💋")

        elif t == "game_refuse":
            print(f"[REFUSE] {data['user']['name']} rad etdi ❌")

        elif t == "game_gift":
            print(
                f"[GIFT] {data['user']['name']} → {data['receiver']['name']}: "
                f"{data.get('gift_type')} (💰{data.get('price')})"
            )

        elif t == "game_drink":
            print(
                f"[DRINK] {data['user']['name']} → {data['receiver']['name']}: "
                f"{data.get('drink_type')}"
            )

        elif t == "game_hat":
            print(
                f"[HAT] {data['user']['name']} → {data['receiver']['name']}: "
                f"{data.get('hat_type')}"
            )

        elif t == "game_gesture":
            print(f"[GESTURE] {data['user']['name']}: {data.get('gesture')}")

        elif t == "game_turn_booster":
            print(
                f"[BOOSTER] {data.get('booster')} "
                f"user={data.get('user_id')} → {data.get('receiver_id')}"
            )

        elif t == "league_score":
            u = data.get("user") or {}
            print(
                f"[LEAGUE+] {u.get('name')}: +{data.get('score')} (kisses={data.get('kisses')})"
            )

        elif t == "harem_purchase":
            u = data.get("user") or {}
            r = data.get("receiver") or {}
            print(f"[COURT] {u.get('name')} → {r.get('name')} @ {data.get('price')}♥")

        elif t == "compliment_send":
            print(f"[COMPLIMENT] {data.get('message', data)}")

        elif t == "game_chat":
            u = data.get("user", {})
            print(f"[CHAT] {u.get('name')}: {data.get('body')}")

        elif t == "locked_message":
            print(f"[VIP MSG] {data.get('user', {}).get('name')}: {data.get('body')}")

        elif t == "game_music":
            print(
                f"[MUSIC] {data.get('artist')} - {data.get('title')} "
                f"(by {data['user']['name']})"
            )

        elif t in (
            "welcome_bonus",
            "kiss_bonus",
            "gold_rewarded",
            "gold_retention_bonus",
            "claim_kiss_bonus",
            "achievement_bonus",
        ):
            self.gold = data.get("gold", self.gold)
            print(f"[BONUS] +{data.get('gold_diff')} gold | Jami: {self.gold} ({t})")

        elif t == "update_user":
            u = data.get("user", {})
            print(f"[UPDATE] {u.get('name')} yangilandi")

        elif t == "update_vip":
            print(f"[VIP] vip={data.get('vip')} tokens={data.get('tokens')}")

        elif t == "user_kickout":
            print(
                f"[KICKOUT] {data['kicked_user']['name']} chiqarildi "
                f"({data['kicker_user']['name']} tomonidan)"
            )

        elif t == "user_kicked":
            print(f"[KICKED] {data['kicked_user']['name']} admin tomonidan chiqarildi")

        elif t == "league_info":
            print(f"[LEAGUE] score={data.get('score')} rank={data.get('rank')}")

        elif t == "error":
            print(f"[ERROR] {data.get('msg', data.get('message'))}")

        elif t == "session_expired":
            print("[SESSION] Sessiya tugadi")

        elif t == "pong":
            print("[PONG] ✓")

        else:
            print(f"[MSG:{t}] {data}")

    # ── O'YIN BUYRUQLARI (server: GameManager.handle) ─────────────────────

    async def login(self, token_or_id: str | None = None, room_id: str | None = None):
        """
        Router: birinchi paket. id = JWT yoki session token; bo'lmasa guest.
        room_id — stol (default: 1, DB `table_rooms.id`).
        """
        payload: dict = {"type": "login", "id": token_or_id or self.user_id}
        if room_id is not None:
            payload["room_id"] = str(room_id)
        elif self.table_id:
            payload["room_id"] = str(self.table_id)
        await self.send(payload)

    async def spin(self):
        """Asosiy klient (main.be3d9225.js) aynan spin_bottle yuboradi."""
        await self.send({"type": "spin_bottle"})

    async def spin_legacy(self):
        """Eski nomlar: game_turn | game_spin | spin."""
        await self.send({"type": "game_turn"})

    async def spin_as(self, msg_type: str = "game_turn_spin"):
        await self.send({"type": msg_type})

    async def kiss(self, receiver_id: str):
        await self.send({"type": "game_kiss", "receiver_id": receiver_id})

    async def refuse(self, receiver_id: str):
        await self.send({"type": "game_refuse", "receiver_id": receiver_id})

    async def send_gift(
        self, receiver_id: str, gift_type: str = "air_kiss", price: int = 1
    ):
        await self.send(
            {
                "type": "game_gift",
                "receiver_id": receiver_id,
                "gift_type": gift_type,
                "price": price,
            }
        )

    async def send_drink(
        self, receiver_id: str, drink_type: str = "cola", price: int = 5
    ):
        await self.send(
            {
                "type": "game_drink",
                "receiver_id": receiver_id,
                "drink_type": drink_type,
                "price": price,
            }
        )

    async def send_hat(
        self, receiver_id: str, hat_type: str = "bosscap", price: int = 20
    ):
        await self.send(
            {
                "type": "game_hat",
                "receiver_id": receiver_id,
                "hat_type": hat_type,
                "price": price,
            }
        )

    async def send_gesture(self, gesture: str = "heart", price: int = 5):
        await self.send({"type": "game_gesture", "gesture": gesture, "price": price})

    async def send_random_gift(self, receiver_id: str):
        await self.send({"type": "game_random", "receiver_id": receiver_id})

    async def change_bottle(self, bottle_type: str = "standart", price: int = 10):
        """game_bottle | bottle_change | set_bottle | game:bottle"""
        await self.send(
            {"type": "game_bottle", "bottle_type": bottle_type, "price": price}
        )

    async def bottle_selected(self, item: str):
        await self.send({"type": "bottle_selected", "item": item})

    async def send_locked_message(self, receiver_id: str, body: str):
        await self.send(
            {"type": "locked_message", "receiver_id": receiver_id, "body": body}
        )

    async def send_turn_booster(self, booster: str, receiver_id: str = ""):
        await self.send(
            {
                "type": "game_turn_booster",
                "booster": booster,
                "receiver_id": receiver_id,
            }
        )

    async def chat(self, text: str, receiver_id: str = "", receiver_name: str = ""):
        await self.send(
            {
                "type": "game_chat_message",
                "body": text,
                "receiver_id": receiver_id,
                "receiver_name": receiver_name,
            }
        )

    async def send_music(
        self,
        artist: str,
        title: str,
        url: str,
        duration: int = 200,
        price: int = 5,
        song_id: str = "",
        icon: str = "",
        provider: str = "",
        source: str = "",
    ):
        await self.send(
            {
                "type": "game_music",
                "artist": artist,
                "title": title,
                "url": url,
                "duration": duration,
                "price": price,
                "id": song_id,
                "icon": icon,
                "provider": provider,
                "source": source,
            }
        )

    async def get_rooms(self, country: str = "UZBEKISTAN"):
        await self.send({"type": "get_rooms", "country": country})

    async def change_room(self, room_id: str):
        await self.send({"type": "change_room", "room_id": str(room_id)})

    async def goto_random(self):
        await self.send({"type": "goto_random"})

    async def goto_user(self, user_id: str, goto_type: str = "friend"):
        await self.send(
            {"type": "goto_user", "user_id": user_id, "goto_type": goto_type}
        )

    async def goto_history_table(self, game_id: str):
        await self.send({"type": "goto_history", "game_id": str(game_id)})

    async def goto_view_table(self, game_id: str):
        await self.send({"type": "goto_view_table", "game_id": str(game_id)})

    async def court_purchase(self, target_user_id: str):
        await self.send({"type": "harem_purchase", "user_id": target_user_id})

    async def invite_to_table(self, friend_user_id: str):
        await self.send(
            {"type": "invite_to_table", "user_id": str(friend_user_id)}
        )

    async def admirer_add(self, target_user_id: str):
        """DB: admirer munosabat + onlayn bo'lsa fellow_invite."""
        await self.send({"type": "admirer_add", "user_id": str(target_user_id)})

    async def answer_friend_request(self, user_id: str, accepted: bool):
        await self.send(
            {
                "type": "friend_request_answer",
                "user_id": str(user_id),
                "accepted": accepted,
            }
        )

    async def kickout_user(self, user_id: str, price: int = 10):
        await self.send(
            {"type": "user_kickout", "user_id": user_id, "expected_price": price}
        )

    async def block_user(self, user_id: str):
        await self.send({"type": "block_user", "user_id": user_id})

    async def unblock_user(self, user_id: str):
        await self.send({"type": "unblock_user", "user_id": user_id})

    async def add_friend(self, user_id: str):
        await self.send({"type": "friend_add", "user_id": user_id})

    async def remove_friend(self, user_id: str):
        await self.send({"type": "friend_remove", "user_id": user_id})

    async def get_friends(self):
        await self.send({"type": "get_friends"})

    async def update_profile(self, name: str = None, male: bool = None, locale: str = None):
        d: dict = {"type": "update_profile"}
        if name:
            d["name"] = name
        if male is not None:
            d["male"] = male
        if locale:
            d["locale"] = locale
        await self.send(d)

    async def get_profile(self, user_id: str = None):
        await self.send({"type": "get_profile", "user_id": user_id or self.user_id})

    async def set_decorations(self, frame: str = "", stone: str = ""):
        await self.send({"type": "set_decorations", "frame": frame, "stone": stone})

    async def claim_kiss_bonus(self):
        await self.send({"type": "claim_kiss_bonus"})

    async def claim_retention_bonus(self):
        await self.send({"type": "claim_retention_bonus"})

    async def claim_rewarded_video(self):
        await self.send({"type": "claim_rewarded_video_bonus"})

    async def claim_vip_tokens(self):
        await self.send({"type": "claim_vip_tokens"})

    async def get_league_info(self):
        await self.send({"type": "league_info"})

    async def buy_vip(self):
        await self.send({"type": "vip_purchase"})

    async def buy_item(self, item: str):
        await self.send({"type": "item_purchase", "item": item})

    async def get_tops(self):
        await self.send({"type": "get_tops"})

    async def get_stickers(self):
        await self.send({"type": "get_stickers"})

    async def ping(self):
        await self.send({"type": "ping"})

    async def get_wallet(self):
        await self.send({"type": "get_wallet"})

    async def user_save(self, **fields):
        await self.send({"type": "user_save", **fields})

    async def kickout_refresh(self):
        await self.send({"type": "kickout_refresh"})


async def _demo():
    c = SpinBottleClient(table_id="1", user_id="1001")
    await c.connect()
    await c.login()
    asyncio.create_task(c.listen())
    await asyncio.sleep(2)
    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(_demo())

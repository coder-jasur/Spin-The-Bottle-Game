"""
WebSocket Router — to'liq tuzatilgan va kengaytirilgan versiya.
"""
import json
import logging
import random
import traceback

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.app.api.ws.game_manager import manager
from src.app.api.ws.utils import parse_packet
from src.app.core.config import load_config
from src.app.core.jwt import verify_access_token
from src.app.core.security.rate_limit import check_rate, ws_client_ip

router = APIRouter(tags=["Game WebSocket"])
log = logging.getLogger("spinbottle")
log.setLevel(logging.INFO)


def _incoming_is_plain_json(raw: bytes) -> bool:
    """main.be3d9225.js kabi klientlar to'g'ridan-to'g'ri JSON yuboradi (wrap qilinmagan)."""
    if not raw:
        return False
    payload = raw if raw[0] == 123 else raw[2:]
    try:
        o = json.loads(payload.decode("utf-8"))
        return isinstance(o, dict) and "data" not in o
    except Exception:
        return False


def _user_id_from_token(token: str | None) -> int | None:
    """`token` → DB user_id. Sessiya tokeni yoki JWT bo'lishi mumkin."""
    if not token:
        return None
    try:
        from src.app.api.game_session import game_sessions

        uid = game_sessions.verify(token)
        if uid:
            return int(uid)
    except Exception as ex:
        log.error(f"Session verify xatosi: {ex}")
    try:
        payload = verify_access_token(token)
        if payload and payload.get("id"):
            return int(payload["id"])
    except Exception as ex:
        log.error(f"JWT verify xatosi: {ex}")
    return None


def _user_id_from_ws_cookies(ws: WebSocket) -> int | None:
    """Cookie'dagi JWT tokenlardan DB user_id ni aniqlash (zaxira yo'l).

    Server restart bo'lganda RAM'dagi sessiya tokeni yo'qoladi — bu yerda
    `accessToken` / `device_user_ids` cookie'sidagi JWT'dan foydalanuvchini
    qaytarib olamiz, shunda foydalanuvchi `mehmon` bo'lib qolmaydi.
    """
    cookies = ws.cookies or {}
    for name in ("accessToken", "device_user_ids", "refreshToken"):
        raw = cookies.get(name)
        if not raw:
            continue
        try:
            payload = verify_access_token(raw)
        except Exception:
            payload = None
        if payload and payload.get("id"):
            try:
                return int(payload["id"])
            except (TypeError, ValueError):
                pass
        # `device_user_ids` ba'zan `["123"]` ko'rinishida bo'ladi
        try:
            import json as _json
            import urllib.parse as _urllib

            decoded = _urllib.unquote(raw)
            if decoded.startswith("["):
                arr = _json.loads(decoded)
                if isinstance(arr, list) and arr:
                    return int(arr[0])
        except (ValueError, TypeError, _json.JSONDecodeError):
            pass
    return None


def _resolve_user_from_token(
    token: str, ws: WebSocket | None = None
) -> tuple[str, int | None]:
    """JWT / session token (+ ixtiyoriy WS cookie) → (user_id_str, db_uid_or_None)."""
    uid_int = _user_id_from_token(token)
    if uid_int:
        log.info(f"WS token resolved: user_id={uid_int}")
        return str(uid_int), uid_int

    # Cookie zaxirasi: server restart yoki eskirgan sessiya tokeni
    if ws is not None:
        cookie_uid = _user_id_from_ws_cookies(ws)
        if cookie_uid:
            try:
                from src.app.api.game_session import game_sessions

                game_sessions.create(cookie_uid)
            except Exception as ex:
                log.debug(f"cookie recover session create: {ex}")
            log.info(f"WS cookie recovered: user_id={cookie_uid}")
            return str(cookie_uid), cookie_uid

    guest_num = random.randint(10000, 99999)
    guest_id = f"guest_{guest_num}"
    log.warning(f"Guest sifatida kirdi: {guest_id} (token yaroqsiz)")
    return guest_id, None


@router.websocket("/ws/")
async def game_websocket(ws: WebSocket):
    """
    Asosiy WebSocket handler.
    Barcha paketlarni manager.handle() ga yo'naltiradi.
    HTML5: ?token=...&table_id=... query bilan ulanadi (xabar ketmaydi).
    """
    settings = getattr(ws.app.state, "settings", None) or load_config()
    ip = ws_client_ip(ws)
    redis_url = getattr(settings, "redis_url", "") or ""
    ws_msg_limit = int(getattr(settings, "ws_max_messages_per_10s", 80) or 80)

    if not await check_rate(f"wsconn:{ip}", 40, 60, redis_url=redis_url):
        await ws.close(code=1008, reason="too_many_connections")
        return

    await ws.accept(subprotocol="binary")

    # DB factory ulanishi
    if not manager._db_factory and hasattr(ws.app, "state") and hasattr(ws.app.state, "db"):
        manager.set_db_factory(ws.app.state.db.session_factory)

    user_id: str | None = None
    table_id: str = "1"

    try:
        # ── HTML5: URL token bilan darhol sessiya ─────────────────────────
        qp_token = ws.query_params.get("token")
        qp_room = (
            ws.query_params.get("table_id")
            or ws.query_params.get("tableId")
            or "1"
        )
        if qp_token:
            table_id = str(qp_room)
            uid_str, uid_int = _resolve_user_from_token(qp_token, ws)
            user_id = uid_str
            player = await manager.connect(ws, table_id, user_id)
            if not player:
                return
            player.plain_ws = True
            if uid_int:
                player.session_token = qp_token
            actual_tid, _uid = manager.ws_map.get(ws, (table_id, user_id))
            tbl = manager.tables.get(actual_tid)
            if not tbl:
                return
            await manager._handle_login(
                ws,
                tbl,
                player,
                {"type": "login", "id": qp_token, "room_id": actual_tid},
            )

        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            raw: bytes | None = None
            if message.get("bytes") is not None:
                raw = message["bytes"]
            elif message.get("text") is not None:
                raw = message["text"].encode("utf-8")
            if not raw:
                continue

            rate_key = f"wsmsg:{user_id or ip}"
            if not await check_rate(
                rate_key, ws_msg_limit, 10, redis_url=redis_url
            ):
                log.warning("WS rate limit: %s", rate_key)
                await ws.close(code=1008, reason="too_many_messages")
                break

            packet = parse_packet(raw)
            if not packet:
                continue

            ptype = packet.get("type", "unknown")

            # ── LOGIN (faqat query-token yo'lidan kelmaganda) ───────────────
            if ptype == "login":
                if user_id:
                    await manager.handle(ws, packet)
                    continue

                token = packet.get("id", "")
                uid_str, uid_int = _resolve_user_from_token(token, ws)
                user_id = uid_str

                table_id = str(packet.get("room_id", "1"))

                player = await manager.connect(ws, table_id, user_id)
                if not player:
                    return
                player.plain_ws = _incoming_is_plain_json(raw)
                if uid_int:
                    player.session_token = token

                await manager.handle(ws, packet)

            # ── BOSHQA PAKETLAR ────────────────────────────────────────────
            else:
                if not user_id:
                    continue
                await manager.handle(ws, packet)

    except WebSocketDisconnect:
        log.info(f"WebSocket uzildi: user_id={user_id}")
    except Exception as e:
        log.error(f"WebSocket XATOSI [{user_id}]: {e}")
        traceback.print_exc()
    finally:
        if user_id:
            await manager.disconnect(ws)

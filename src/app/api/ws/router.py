"""
WebSocket Router — to'liq tuzatilgan va kengaytirilgan versiya.
"""
import logging
import traceback
import random
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.app.api.ws.game_manager import GameManager
from src.app.api.ws.utils import parse_packet
from src.app.core.jwt import verify_access_token

router = APIRouter(tags=["Game WebSocket"])
log = logging.getLogger("spinbottle")

# Singleton manager
manager = GameManager()


@router.websocket("/ws/")
async def game_websocket(ws: WebSocket):
    """
    Asosiy WebSocket handler.
    Barcha paketlarni manager.handle() ga yo'naltiradi.
    """
    await ws.accept(subprotocol="binary")

    # DB factory ulanishi
    if not manager._db_factory and hasattr(ws.app, "state") and hasattr(ws.app.state, "db"):
        manager.set_db_factory(ws.app.state.db.session_factory)

    user_id: str | None = None
    table_id: str = "1003"

    try:
        async for raw in ws.iter_bytes():
            packet = parse_packet(raw)
            if not packet:
                continue

            ptype = packet.get("type", "unknown")

            # ── LOGIN ──────────────────────────────────────────────────────
            if ptype == "login":
                token = packet.get("id", "")
                uid_int: int | None = None

                # 1) Sessiya token tekshirish
                try:
                    from src.app.api.game_session import game_sessions
                    uid_int = game_sessions.verify(token)
                    if uid_int:
                        user_id = str(uid_int)
                        log.info(f"SESSION token: user_id={user_id}")
                except Exception as ex:
                    log.error(f"Session verify xatosi: {ex}")

                # 2) JWT token tekshirish (zapas)
                if not uid_int:
                    try:
                        payload = verify_access_token(token)
                        if payload and payload.get("id"):
                            uid_int = int(payload["id"])
                            user_id = str(uid_int)
                            log.info(f"JWT token: user_id={user_id}")
                    except Exception as ex:
                        log.error(f"JWT verify xatosi: {ex}")

                # 3) Guest fallback
                if not user_id or not uid_int:
                    guest_num = random.randint(10000, 99999)
                    user_id = f"guest_{guest_num}"
                    log.warning(f"Guest sifatida kirdi: {user_id}")

                # Room ID aniqlash
                table_id = str(packet.get("room_id", "1003"))

                # Manager ga ulash va login bajarish
                player = await manager.connect(ws, table_id, user_id)

                # Sessiya tokenni player ga saqlash
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
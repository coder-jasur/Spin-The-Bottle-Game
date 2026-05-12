"""
Tables HTTP Router — xonalar ro'yxatini HTTP orqali beradi.
Klient bu endpointni o'yin boshlanishidan oldin chaqiradi.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_db
from src.app.database.repositories.game import GameRepository

router = APIRouter(prefix="/api/tables", tags=["Tables"])


def _get_manager():
    """GameManager singleton ni import qilish."""
    from src.app.api.ws.game_manager import manager
    return manager


@router.get("/")
async def get_rooms(
    country: str = Query(default="UZBEKISTAN", description="Mamlakat kodi"),
    db: AsyncSession = Depends(get_db),
):
    """
    Berilgan mamlakat uchun aktiv xonalar ro'yxati (faqat ochiq/ochiladigan stollar).
    """
    manager = _get_manager()
    # DB sessiyasi seed uchun ishlatiladi; ro'yxat manager orqali
    repo = GameRepository(db)
    c = country.upper()
    await repo.seed_country_tables(c)
    await repo.seed_global_tables()

    rooms_out = await manager.http_tables_list_payload(country)

    return {"ok": True, "tables": rooms_out}


@router.get("/online")
async def get_online_stats():
    """
    Hozir online bo'lgan barcha o'yinchilar va xonalar statistikasi.
    """
    manager = _get_manager()

    total_players = 0
    rooms_info    = []

    for tid, table in manager.tables.items():
        count = table.player_count()
        total_players += count
        rooms_info.append({
            "room_id":    tid,
            "online":     count,
            "state":      table.state,
            "bottle":     table.bottle_type,
        })

    return {
        "ok":            True,
        "total_players": total_players,
        "total_rooms":   len(manager.tables),
        "rooms":         rooms_info,
    }


@router.get("/room/{room_id}")
async def get_room_detail(room_id: str):
    """Bitta xona haqida to'liq ma'lumot."""
    manager = _get_manager()
    table   = manager.tables.get(room_id)

    if not table:
        return {"ok": True, "room_id": room_id, "online": 0, "participants": []}

    return {
        "ok":          True,
        "room_id":     room_id,
        "online":      table.player_count(),
        "state":       table.state,
        "bottle_type": table.bottle_type,
        "participants": [
            {
                "id":        p.id,
                "name":      p.username,
                "photo_url": p.photo_url,
                "seat":      p.seat,
                "vip":       p.vip,
                "country":   p.country,
            }
            for p in table.players.values()
        ],
    }
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
    Berilgan mamlakat uchun aktiv xonalar ro'yxatini qaytaradi.
    Har bir xonadagi online o'yinchilar soni ham ko'rsatiladi.
    """
    manager = _get_manager()
    repo    = GameRepository(db)

    # DB dan xonalar
    db_rooms = await repo.get_rooms_by_country(country)

    # Kamida 5 ta xona bo'lsin
    specific = [r for r in db_rooms if r.country_code == country.upper()]
    if len(specific) < 5:
        await repo.ensure_base_rooms(country, min_count=5)
        db_rooms = await repo.get_rooms_by_country(country)

    rooms_out = []
    for room in db_rooms:
        rid_str      = str(room.room_id)
        online_count = 0
        if rid_str in manager.tables:
            online_count = manager.tables[rid_str].player_count()

        rooms_out.append({
            "id":             rid_str,
            "room_id":        room.room_id,
            "name":           room.name,
            "currentPlayers": online_count,
            "online":         online_count,
            "maxPlayers":     room.capacity,
            "capacity":       room.capacity,
            "is_vip":         room.is_vip,
            "min_level":      room.min_level,
            "country":        room.country_code,
        })

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
import asyncio
import os
import sys

# Loyiha ildizini path-ga qo'shamiz
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app.core.config import load_config
from src.app.database.base import Database
from src.app.database.models.table import TableRoom
from sqlalchemy import select

async def check_rooms():
    print("Bazadagi stollarni tekshirish...", flush=True)
    settings = load_config()
    dsn = settings.construct_postgresql_url()
    db = Database(dsn)

    async with db.session_factory() as session:
        stmt = select(TableRoom)
        result = await session.execute(stmt)
        rooms = result.scalars().all()
        
        print(f"Jami stollar soni: {len(rooms)}", flush=True)
        for r in rooms:
            print(f"ID: {r.room_id} | Name: {r.name} | Country: {r.country_code} | Active: {r.is_active}", flush=True)
    
    await db.engine.dispose()

if __name__ == "__main__":
    asyncio.run(check_rooms())

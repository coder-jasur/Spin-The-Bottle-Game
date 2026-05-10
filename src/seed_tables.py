import asyncio
import sys
import os

# Loyiha ildizini path-ga qo'shamiz
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app.core.config import load_config
from src.app.database.base import Database
from src.app.database.repositories.game import GameRepository

async def seed_tables():
    print("Dastlabki stollar yaratilmoqda...", flush=True)
    settings = load_config()
    dsn = settings.construct_postgresql_url()
    db = Database(dsn)

    async with db.session_factory() as session:
        repo = GameRepository(session)
        
        countries = ["UZBEKISTAN", "KAZAKHSTAN", "RUSSIA", "ALL"]
        
        for country in countries:
            print(f"Creating base rooms for {country}...", flush=True)
            await repo.ensure_base_rooms(country, min_count=3)
        
        await session.commit()
    
    print("Stollar muvaffaqiyatli yaratildi!", flush=True)
    await db.engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed_tables())

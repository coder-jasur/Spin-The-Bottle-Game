import asyncio
import sys
import os

# Loyiha ildizini path-ga qo'shamiz
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app.core.config import load_config
from src.app.database.base import Database, Base

# Modellarni import qilish shart, aks holda Base.metadata ularni ko'rmaydi
from src.app.database.models.user import User
from src.app.database.models.wallet import Wallet
from src.app.database.models.stats import UserStats
from src.app.database.models.transaction import Transaction
from src.app.database.models.achievement import Achievement, UserAchievement
from src.app.database.models.booster import UserBooster
from src.app.database.models.relation import UserRelation
from src.app.database.models.story import Story, StoryView, StoryLike
from src.app.database.models.table import TableRoom

async def recreate_db():
    print("DB qayta yaratilmoqda...", flush=True)
    settings = load_config()
    dsn = settings.construct_postgresql_url()
    db = Database(dsn)

    async with db.engine.begin() as conn:
        print("Eski jadvallar o'chirilmoqda...", flush=True)
        await conn.run_sync(Base.metadata.drop_all)
        print("Yangi jadvallar yaratilmoqda...", flush=True)
        await conn.run_sync(Base.metadata.create_all)
    
    print("DB muvaffaqiyatli qayta yaratildi!", flush=True)
    await db.engine.dispose()

if __name__ == "__main__":
    asyncio.run(recreate_db())

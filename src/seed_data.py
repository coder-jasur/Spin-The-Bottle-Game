import asyncio
import os
import sys
from pathlib import Path

# Fix PYTHONPATH
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Import models
from src.app.core.config import load_config
from src.app.database.base import Base
from src.app.database.models.stats import UserStats
from src.app.database.models.user import User
from src.app.database.models.wallet import Wallet


async def seed():
    settings = load_config()
    dsn = settings.construct_postgresql_url()
    engine = create_async_engine(dsn)
    AsyncSessionLocal = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with AsyncSessionLocal() as session:
        # User ID from JWT in URL
        user_id = 68149139

        # Check if user exists
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            print(f"Creating user {user_id}...")
            user = User(
                id=user_id,
                referral_id=f"ref_{user_id}",
                display_name="Antigravity Player",
                username="antigravity",
                login="antigravity",
                gender="qadın",
                level=50,
                xp=1250000,
                country="UZ",
                is_verified=True,
                vip_status=True,
            )
            session.add(user)
            await session.flush()
        else:
            print(f"Updating user {user_id}...")
            user.display_name = "Antigravity Player"
            user.level = 50
            user.xp = 1250000
            user.vip_status = True
            user.is_verified = True
            user.country = "UZ"
            user.gender = "qadın"

        # Wallet
        result = await session.execute(select(Wallet).where(Wallet.user_id == user_id))
        wallet = result.scalar_one_or_none()
        if not wallet:
            wallet = Wallet(user_id=user_id, hearts=500000, stars=1000, stars_coin=5000)
            session.add(wallet)
        else:
            wallet.hearts = 500000
            wallet.stars = 1000
            wallet.stars_coin = 5000

        await session.commit()
        print("Successfully seeded 'real data' for the user.")


if __name__ == "__main__":
    asyncio.run(seed())

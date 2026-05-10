from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from src.app.database.models.stats import UserStats
from src.app.database.models.user import User

class RankingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_top_users(self, category: str, period: str = "all_time", limit: int = 10):
        """
        Kategoriya va vaqt oralig'i bo'yicha top foydalanuvchilarni olish.
        period: daily, weekly, monthly, all_time
        category: kisses, dj, expense, importance, emotion
        """
        
        column_name = "total_value"
        if period == "daily": column_name = "daily_value"
        elif period == "weekly": column_name = "weekly_value"
        elif period == "monthly": column_name = "monthly_value"

        # UserStats va User jadvallarini bog'lab top foydalanuvchilarni olamiz
        stmt = (
            select(User.display_name, User.avatar_url, getattr(UserStats, column_name))
            .join(UserStats, User.id == UserStats.user_id)
            .where(UserStats.category == category)
            .order_by(desc(getattr(UserStats, column_name)))
            .limit(limit)
        )
        
        result = await self.session.execute(stmt)
        return result.all()

    async def update_stat(self, user_id: int, category: str, amount: int):
        """Foydalanuvchi statistikasini yangilash (ball qo'shish)"""
        stmt = select(UserStats).where(UserStats.user_id == user_id, UserStats.category == category)
        result = await self.session.execute(stmt)
        stat = result.scalar_one_or_none()
        
        if not stat:
            stat = UserStats(user_id=user_id, category=category)
            self.session.add(stat)
        
        stat.daily_value += amount
        stat.weekly_value += amount
        stat.monthly_value += amount
        stat.total_value += amount
        
        await self.session.commit()

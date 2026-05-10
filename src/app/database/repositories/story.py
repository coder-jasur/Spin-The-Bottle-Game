from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.database.models.story import Story, StoryLike, StoryView


class StoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_story(
        self,
        user_id: int,
        media_url: str,
        media_type: str,
        caption: Optional[str] = None,
    ) -> Story:
        """Yangi story qo'shish (24 soat amal qiladi)"""
        expires_at = datetime.utcnow() + timedelta(hours=24)
        story = Story(
            user_id=user_id,
            media_url=media_url,
            media_type=media_type,
            caption=caption,
            expires_at=expires_at,
        )
        self.session.add(story)
        await self.session.commit()
        await self.session.refresh(story)
        return story

    async def get_active_stories(self) -> List[Story]:
        """Barcha faol (vaqti o'tmagan) storylarni olish"""
        now = datetime.utcnow()
        query = (
            select(Story)
            .where(Story.expires_at > now)
            .options(
                selectinload(Story.user),
                selectinload(Story.views),
                selectinload(Story.likes),
            )
            .order_by(desc(Story.created_at))
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def add_view(self, story_id: int, user_id: int):
        """Story ko'rilganini belgilash (agar avval ko'rilmagan bo'lsa)"""
        try:
            story_id = int(story_id)
            user_id = int(user_id)
        except (ValueError, TypeError):
            return

        check_query = select(StoryView).where(
            StoryView.story_id == story_id, StoryView.user_id == user_id
        )
        existing = await self.session.execute(check_query)
        if not existing.scalar_one_or_none():
            view = StoryView(story_id=story_id, user_id=user_id)
            self.session.add(view)
            await self.session.commit()

    async def toggle_like(self, story_id: int, user_id: int) -> bool:
        """Like bosish yoki qaytarib olish"""
        try:
            story_id = int(story_id)
            user_id = int(user_id)
        except (ValueError, TypeError):
            return False

        query = select(StoryLike).where(
            StoryLike.story_id == story_id, StoryLike.user_id == user_id
        )
        result = await self.session.execute(query)
        like = result.scalar_one_or_none()

        if like:
            await self.session.delete(like)
            await self.session.commit()
            return False  # Unliked
        else:
            new_like = StoryLike(story_id=story_id, user_id=user_id)
            self.session.add(new_like)
            await self.session.commit()
            return True  # Liked

    async def get_story_by_id(self, story_id: int) -> Optional[Story]:
        query = (
            select(Story)
            .where(Story.id == story_id)
            .options(
                selectinload(Story.views).selectinload(StoryView.user),
                selectinload(Story.likes).selectinload(StoryLike.user),
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def delete_story(self, story_id: int, user_id: int) -> bool:
        """Foydalanuvchining o'z storysini o'chirish"""
        try:
            story_id = int(story_id)
            user_id = int(user_id)
        except (ValueError, TypeError):
            return False

        story = await self.get_story_by_id(story_id)
        # Faqat o'ziniki bo'lsa o'chira oladi
        if story and story.user_id == user_id:
            # 1. Avval ko'rishlar (views) tarixini o'chiramiz
            from sqlalchemy import delete

            from src.app.database.models.story import StoryLike, StoryView

            await self.session.execute(
                delete(StoryView).where(StoryView.story_id == story_id)
            )

            # 2. Keyin layklar (likes) tarixini o'chiramiz
            await self.session.execute(
                delete(StoryLike).where(StoryLike.story_id == story_id)
            )

            # 3. Va nihoyat hikoyaning o'zini o'chiramiz
            await self.session.delete(story)
            await self.session.commit()
            return True
        return False

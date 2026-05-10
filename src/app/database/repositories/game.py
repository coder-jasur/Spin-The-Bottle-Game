"""
GameRepository — O'yin uchun barcha DB operatsiyalari.
UserRepository, WalletRepository, RankingRepository ni birlashtiradi.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.database.models.user   import User
from src.app.database.models.wallet import Wallet
from src.app.database.models.stats  import UserStats
from src.app.database.models.transaction import Transaction
from src.app.database.models.table import TableRoom

log = logging.getLogger("spinbottle.db")


class GameRepository:
    """
    Barcha o'yin uchun DB operatsiyalari shu yerda.
    GameManager har WebSocket so'rovi uchun yangi session bilan ishlatadi.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ════════════════════════════════════════════════════════════════════════
    # USER
    # ════════════════════════════════════════════════════════════════════════

    async def get_user_with_wallet(self, user_id: int) -> Optional[User]:
        """User va walletni birgalikda yuklaydi (N+1 yo'q)."""
        stmt = (
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.wallet))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_user_fields(self, user_id: int, **fields) -> None:
        """Foydalanuvchi maydonlarini yangilaydi."""
        if not fields:
            return
        stmt = update(User).where(User.id == user_id).values(**fields)
        await self.session.execute(stmt)
        await self.session.commit()

    async def mark_bonus_claimed(self, user_id: int) -> None:
        """Foydalanuvchi bugun bonus olganini belgilaydi."""
        from datetime import datetime
        stmt = update(User).where(User.id == user_id).values(last_bonus_claimed_at=datetime.now())
        await self.session.execute(stmt)
        await self.session.commit()

    # ════════════════════════════════════════════════════════════════════════
    # WALLET
    # ════════════════════════════════════════════════════════════════════════

    async def get_wallet(self, user_id: int) -> Optional[Wallet]:
        stmt = select(Wallet).where(Wallet.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def ensure_wallet(self, user_id: int) -> Wallet:
        """Wallet yo'q bo'lsa yaratadi."""
        wallet = await self.get_wallet(user_id)
        if not wallet:
            wallet = Wallet(user_id=user_id, hearts=500, stars=50)
            self.session.add(wallet)
            await self.session.commit()
        return wallet

    async def add_hearts(self, user_id: int, amount: int,
                         tx_type: str, description: str = "") -> int:
        """Hearts qo'shadi va yangi balansni qaytaradi."""
        stmt = (
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(hearts=Wallet.hearts + amount)
            .returning(Wallet.hearts)
        )
        result = await self.session.execute(stmt)
        new_balance = result.scalar_one_or_none() or 0

        await self._save_tx(user_id, amount, "hearts", tx_type, description)
        await self.session.commit()
        return new_balance

    async def spend_hearts(self, user_id: int, amount: int,
                           tx_type: str, description: str = "") -> tuple[bool, int]:
        """
        Hearts sarflaydi.
        (muvaffaqiyat, yangi_balans) qaytaradi.
        """
        wallet = await self.get_wallet(user_id)
        if not wallet or wallet.hearts < amount:
            return False, wallet.hearts if wallet else 0

        stmt = (
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(hearts=Wallet.hearts - amount)
            .returning(Wallet.hearts)
        )
        result = await self.session.execute(stmt)
        new_balance = result.scalar_one_or_none() or 0

        await self._save_tx(user_id, -amount, "hearts", tx_type, description)
        await self.session.commit()
        return True, new_balance

    async def add_stars(self, user_id: int, amount: int,
                        tx_type: str, description: str = "") -> int:
        stmt = (
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(stars=Wallet.stars + amount)
            .returning(Wallet.stars)
        )
        result = await self.session.execute(stmt)
        new_balance = result.scalar_one_or_none() or 0
        await self._save_tx(user_id, amount, "stars", tx_type, description)
        await self.session.commit()
        return new_balance

    async def spend_stars(self, user_id: int, amount: int,
                          tx_type: str, description: str = "") -> tuple[bool, int]:
        wallet = await self.get_wallet(user_id)
        if not wallet or wallet.stars < amount:
            return False, wallet.stars if wallet else 0

        stmt = (
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(stars=Wallet.stars - amount)
            .returning(Wallet.stars)
        )
        result = await self.session.execute(stmt)
        new_balance = result.scalar_one_or_none() or 0
        await self._save_tx(user_id, -amount, "stars", tx_type, description)
        await self.session.commit()
        return True, new_balance

    async def _save_tx(self, user_id: int, amount: int,
                       currency: str, tx_type: str, description: str):
        tx = Transaction(
            user_id=user_id,
            amount=amount,
            currency=currency,
            type=tx_type,
            description=description,
        )
        self.session.add(tx)

    # ════════════════════════════════════════════════════════════════════════
    # STATS / RANKING
    # ════════════════════════════════════════════════════════════════════════

    async def add_stat(self, user_id: int, category: str, amount: int) -> None:
        """
        Statistikani qo'shadi (UserStats va User modelida).
        category: 'kisses' | 'dj' | 'expense' | 'importance' | 'emotion'
        """
        # 1. UserStats (Detailed) yangilash
        stmt = select(UserStats).where(
            UserStats.user_id == user_id,
            UserStats.category == category,
        )
        result = await self.session.execute(stmt)
        stat = result.scalar_one_or_none()

        if not stat:
            stat = UserStats(user_id=user_id, category=category,
                             daily_value=0, weekly_value=0,
                             monthly_value=0, total_value=0)
            self.session.add(stat)

        stat.daily_value   += amount
        stat.weekly_value  += amount
        stat.monthly_value += amount
        stat.total_value   += amount

        # 2. User (Direct) yangilash
        if hasattr(User, category):
            stmt_user = (
                update(User)
                .where(User.id == user_id)
                .values({category: getattr(User, category) + amount})
            )
            await self.session.execute(stmt_user)

        await self.session.commit()

    async def get_top(self, category: str,
                      period: str = "all_time", limit: int = 10) -> list:
        col_map = {
            "daily":    "daily_value",
            "weekly":   "weekly_value",
            "monthly":  "monthly_value",
            "all_time": "total_value",
        }
        col_name = col_map.get(period, "total_value")
        col = getattr(UserStats, col_name)

        from sqlalchemy import desc
        stmt = (
            select(
                User.id,
                User.display_name,
                User.username,
                User.avatar_url,
                col.label("score"),
            )
            .join(UserStats, User.id == UserStats.user_id)
            .where(UserStats.category == category)
            .order_by(desc(col))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        return [
            {
                "id":        str(r.id),
                "name":      r.display_name or r.username or f"user_{r.id}",
                "photo_url": r.avatar_url or "/photos/no_img.png",
                "score":     r.score or 0,
            }
            for r in rows
        ]

    # ════════════════════════════════════════════════════════════════════════
    # TABLES
    # ════════════════════════════════════════════════════════════════════════

    async def get_rooms_by_country(self, country_code: str) -> list[TableRoom]:
        """Berilgan davlat (masalan, 'UZBEKISTAN') yoki 'all' uchun aktiv stollarni qaytaradi."""
        c = country_code.upper()
        stmt = select(TableRoom).where(
            TableRoom.is_active == True,
            TableRoom.country_code.in_([c, "ALL"])
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_room(self, name: str, country_code: str = "ALL", is_vip: bool = False) -> Optional[TableRoom]:
        """Yangi o'yin xonasi yaratadi (raqamli ID diapazoni bilan)."""
        c = country_code.upper()
        ranges = {
            "UZBEKISTAN": 1000,
            "KAZAKHSTAN": 2500,
            "RUSSIA":     4000,
            "ALL":        5500,
            "VIP":        9000
        }
        start_id = ranges.get(c, 5500)
        if is_vip:
            start_id = 9000

        stmt = select(TableRoom.room_id).where(
            TableRoom.room_id >= start_id,
            TableRoom.room_id < start_id + 150
        )
        result = await self.session.execute(stmt)
        used_ids = set(result.scalars().all())

        new_room_id = None
        for i in range(start_id + 1, start_id + 151):
            if i not in used_ids:
                new_room_id = i
                break

        if not new_room_id:
            return None

        room = TableRoom(room_id=new_room_id, name=name, country_code=c, is_vip=is_vip)
        self.session.add(room)
        await self.session.commit()
        await self.session.refresh(room)
        return room

    async def ensure_base_rooms(self, country_code: str, min_count: int = 3):
        """Agar xonalar yetarli bo'lmasa, bazaviy xonalarni yaratadi."""
        c = country_code.upper()
        rooms = await self.get_rooms_by_country(c)
        # Faqat o'sha country_code ga tegishli bo'lganlarini sanaymiz
        specific_rooms = [r for r in rooms if r.country_code == c]

        if len(specific_rooms) < min_count:
            for i in range(len(specific_rooms) + 1, min_count + 1):
                name = f"{c} Room {i}"
                await self.create_room(name, c)

    # ════════════════════════════════════════════════════════════════════════
    # RELATIONS
    # ════════════════════════════════════════════════════════════════════════

    async def add_relation(self, user_id: int, target_id: int, relation_type: str) -> bool:
        """
        Foydalanuvchilar o'rtasida munosabat qo'shadi (admirer, friend).
        Muvaffaqiyatli qo'shilsa True, allaqachon mavjud bo'lsa False qaytaradi.
        """
        from src.app.database.models.relation import UserRelation
        from sqlalchemy.exc import IntegrityError

        try:
            # Allaqachon bor-yo'qligini tekshirish
            stmt = select(UserRelation).where(
                UserRelation.user_id == user_id,
                UserRelation.target_id == target_id,
                UserRelation.type == relation_type
            )
            result = await self.session.execute(stmt)
            if result.scalar_one_or_none():
                return False

            rel = UserRelation(user_id=user_id, target_id=target_id, type=relation_type)
            self.session.add(rel)
            await self.session.commit()
            return True
        except IntegrityError:
            await self.session.rollback()
            return False
        except Exception as e:
            log.error(f"add_relation error: {e}")
            await self.session.rollback()
            return False

    async def get_friends(self, user_id: int) -> list[User]:
        """Foydalanuvchining barcha do'stlarini yuklaydi."""
        from src.app.database.models.relation import UserRelation
        from src.app.database.models.user import User

        stmt = (
            select(User)
            .join(UserRelation, User.id == UserRelation.target_id)
            .where(UserRelation.user_id == user_id, UserRelation.type == "friend")
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_admirer_targets(self, user_id: int) -> list[User]:
        """user_id foydalanuvchi admirer sifatida belgilagan foydalanuvchilar (fellows)."""
        from src.app.database.models.relation import UserRelation
        from src.app.database.models.user import User

        stmt = (
            select(User)
            .join(UserRelation, User.id == UserRelation.target_id)
            .where(UserRelation.user_id == user_id, UserRelation.type == "admirer")
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def remove_relation(self, user_id: int, target_id: int, relation_type: str) -> None:
        """Munosabatni o'chiradi."""
        from src.app.database.models.relation import UserRelation
        from sqlalchemy import delete
        stmt = delete(UserRelation).where(
            UserRelation.user_id == user_id,
            UserRelation.target_id == target_id,
            UserRelation.type == relation_type
        )
        await self.session.execute(stmt)
        await self.session.commit()
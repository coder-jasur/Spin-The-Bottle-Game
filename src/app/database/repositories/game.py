"""
GameRepository — O'yin uchun barcha DB operatsiyalari.
UserRepository, WalletRepository, RankingRepository ni birlashtiradi.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.database.models.user   import User
from src.app.database.models.wallet import Wallet
from src.app.database.models.stats  import UserStats
from src.app.database.models.transaction import Transaction
from src.app.database.models.table import TableRoom
from src.app.database.models.table_chat import TableChatMessage
from src.app.database.models.achievement import Achievement, UserAchievement
from src.app.database.models.admin import Admins
from src.app.core.config import load_config
from src.app.core.room_policy import (
    COUNTRY_ROOM_SLOTS,
    GLOBAL_ROOM_SLOTS,
    normalize_country_code,
)

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

    async def is_admin_user(self, user_id: int) -> bool:
        """`.env` MAIN_ADMIN yoki `admins` jadvalidagi moderator/superadmin."""
        cfg = load_config()
        if user_id == cfg.main_admin_id:
            return True
        stmt = select(Admins).where(Admins.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

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
            wallet = Wallet(user_id=user_id, hearts=0, stars=0)
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

    async def purchase_hearts_with_stars(
        self,
        user_id: int,
        stars_cost: int,
        hearts_delta: int,
    ) -> tuple[bool, int, int]:
        """
        Yulduzdan yurak paketi. (ok, yangi_stars, yangi_hearts).
        """
        wallet = await self.get_wallet(user_id)
        if not wallet or wallet.stars < stars_cost:
            return False, wallet.stars if wallet else 0, wallet.hearts if wallet else 0

        new_stars = int(wallet.stars) - stars_cost
        new_hearts = int(wallet.hearts) + hearts_delta

        await self.session.execute(
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(stars=new_stars, hearts=new_hearts)
        )
        await self._save_tx(
            user_id, -stars_cost, "stars", "hearts_purchase", f"pack:{stars_cost}"
        )
        await self._save_tx(
            user_id, hearts_delta, "hearts", "hearts_purchase", f"pack:{stars_cost}"
        )
        await self.session.commit()
        return True, new_stars, new_hearts

    async def purchase_vip_with_stars(
        self,
        user_id: int,
        price_stars: int,
        bonus_stars: int,
        extend_days: int,
    ) -> tuple[bool, int]:
        """
        VIP: yulduz yechish, bonus yulduz, muddatni uzaytirish. (ok, yangi_stars).
        """
        wallet = await self.get_wallet(user_id)
        user = await self.get_user_with_wallet(user_id)
        if not wallet or not user:
            return False, wallet.stars if wallet else 0
        if wallet.stars < price_stars:
            return False, int(wallet.stars)

        now = datetime.now()
        base = now
        if user.vip_expires_at and user.vip_expires_at > now:
            base = user.vip_expires_at
        new_expires = base + timedelta(days=extend_days)
        new_stars = int(wallet.stars) - price_stars + bonus_stars

        await self.session.execute(
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(stars=new_stars)
        )
        await self._save_tx(user_id, -price_stars, "stars", "vip_purchase", "")
        if bonus_stars:
            await self._save_tx(user_id, bonus_stars, "stars", "vip_bonus", "")
        await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                vip_status=True,
                is_premium=True,
                vip_expires_at=new_expires,
            )
        )
        await self.session.commit()
        return True, new_stars

    async def convert_stars_coin_to_live(
        self,
        user_id: int,
        cost: int,
        live_delta: int,
    ) -> tuple[bool, int, int]:
        """
        GM (stars_coin) yechiladi, balance_live (jeton) qo'shiladi.
        (ok, yangi_stars_coin, yangi_balance_live).
        """
        wallet = await self.get_wallet(user_id)
        if not wallet or int(wallet.stars_coin or 0) < cost:
            return (
                False,
                int(wallet.stars_coin or 0) if wallet else 0,
                int(wallet.balance_live or 0) if wallet else 0,
            )
        new_sc = int(wallet.stars_coin) - cost
        new_live = int(wallet.balance_live or 0) + live_delta
        await self.session.execute(
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(stars_coin=new_sc, balance_live=new_live)
        )
        await self._save_tx(user_id, -cost, "stars", "jeton_convert", f"gm:{cost}")
        await self._save_tx(
            user_id, live_delta, "stars", "jeton_convert", f"live+:{live_delta}"
        )
        await self.session.commit()
        return True, new_sc, new_live

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
        category: 'kisses' | 'dj' | 'expense' | 'importance' | 'emotion' |
            'compliment' | 'bottle_spin' (User jadvalida ustun bo'lmasa faqat UserStats)
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

    async def get_stat_total_value(self, user_id: int, category: str) -> int:
        """UserStats.total_value (kategoriya bo'yicha); qator bo'lmasa 0."""
        stmt = select(UserStats.total_value).where(
            UserStats.user_id == user_id,
            UserStats.category == category,
        )
        result = await self.session.execute(stmt)
        v = result.scalar_one_or_none()
        return int(v or 0)

    async def get_top(self, category: str,
                      period: str = "all_time", limit: int = 10) -> list:
        """UserStats bo'yicha kategoriya + davr orqali reyting (eski usul)."""
        col_map = {
            "daily":    "daily_value",
            "weekly":   "weekly_value",
            "monthly":  "monthly_value",
            "all_time": "total_value",
            "total":    "total_value",
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
                User.gender,
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
                "username":  r.display_name or r.username or f"user_{r.id}",
                "male":      (r.gender or "male") == "male",
                "photo_url": r.avatar_url or "/photos/no_img.png",
                "score":     r.score or 0,
            }
            for r in rows
        ]

    async def get_top_by_user_column(
        self,
        column_name: str,
        limit: int = 50,
    ) -> list:
        """Foydalanuvchi jadvalidagi ustun (kisses/dj/expense/emotion/harem_price)
        bo'yicha to'g'ridan-to'g'ri reyting. UserStats jadvalida bo'lmasa ham
        ishlaydi (umuman ko'p odam uchun ishonchli).
        """
        from sqlalchemy import desc
        col = getattr(User, column_name, None)
        if col is None:
            return []
        stmt = (
            select(
                User.id,
                User.display_name,
                User.username,
                User.avatar_url,
                User.gender,
                col.label("score"),
            )
            .where(col > 0)
            .order_by(desc(col))
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "id":        str(r.id),
                "name":      r.display_name or r.username or f"user_{r.id}",
                "username":  r.display_name or r.username or f"user_{r.id}",
                "male":      (r.gender or "male") == "male",
                "photo_url": r.avatar_url or "/photos/no_img.png",
                "score":     int(r.score or 0),
            }
            for r in rows
        ]

    async def get_user_rank_by_column(
        self, user_id: int, column_name: str
    ) -> tuple[int, int]:
        """(rank, score) qaytaradi. Foydalanuvchi reytingda bo'lmasa (0, score)."""
        from sqlalchemy import desc, func as sa_func
        col = getattr(User, column_name, None)
        if col is None:
            return (0, 0)
        score_stmt = select(col).where(User.id == user_id)
        score = (await self.session.execute(score_stmt)).scalar() or 0
        if not score:
            return (0, 0)
        rank_stmt = select(sa_func.count()).where(col > score)
        higher = (await self.session.execute(rank_stmt)).scalar() or 0
        return (int(higher) + 1, int(score))

    # ════════════════════════════════════════════════════════════════════════
    # ACHIEVEMENTS
    # ════════════════════════════════════════════════════════════════════════

    async def _get_or_create_achievement(self, key: str) -> Achievement:
        stmt = select(Achievement).where(Achievement.key == key)
        ach = (await self.session.execute(stmt)).scalar_one_or_none()
        if ach:
            return ach
        ach = Achievement(key=key, name=key, description=None)
        self.session.add(ach)
        await self.session.flush()
        return ach

    async def get_user_achievements(self, user_id: int) -> dict[str, int]:
        """Yutuq kalit -> daraja (1-based) lug'atini qaytaradi."""
        stmt = (
            select(Achievement.key, UserAchievement.level)
            .join(Achievement, Achievement.id == UserAchievement.achievement_id)
            .where(UserAchievement.user_id == user_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {r.key: int(r.level or 0) for r in rows if r.key}

    async def upsert_user_achievement(
        self, user_id: int, key: str, level: int
    ) -> None:
        """Foydalanuvchining yutiq darajasini saqlaydi (yangilash yoki yaratish)."""
        ach = await self._get_or_create_achievement(key)
        stmt = select(UserAchievement).where(
            UserAchievement.user_id == user_id,
            UserAchievement.achievement_id == ach.id,
        )
        ua = (await self.session.execute(stmt)).scalar_one_or_none()
        if ua:
            if (ua.level or 0) < level:
                ua.level = level
                ua.status = "completed"
        else:
            ua = UserAchievement(
                user_id=user_id,
                achievement_id=ach.id,
                level=level,
                status="completed",
            )
            self.session.add(ua)
        await self.session.commit()

    # ════════════════════════════════════════════════════════════════════════
    # TABLES
    # ════════════════════════════════════════════════════════════════════════

    async def get_table_by_id(self, table_id: int) -> Optional[TableRoom]:
        stmt = select(TableRoom).where(
            TableRoom.id == table_id,
            TableRoom.is_active.is_(True),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def seed_country_tables(self, country_code: str) -> None:
        """Berilgan mamlakat uchun 150 ta stol (name: 1..150)."""
        c = normalize_country_code(country_code)
        if c == "ALL":
            await self.seed_global_tables()
            return
        stmt = select(func.count()).select_from(TableRoom).where(
            TableRoom.country_code == c,
            TableRoom.is_vip.is_(False),
        )
        n = int((await self.session.execute(stmt)).scalar_one() or 0)
        for i in range(n + 1, COUNTRY_ROOM_SLOTS + 1):
            self.session.add(
                TableRoom(
                    name=str(i),
                    country_code=c,
                    is_vip=False,
                )
            )
        await self.session.commit()

    async def seed_global_tables(self) -> None:
        """20 ta global stol (ALL)."""
        stmt = select(func.count()).select_from(TableRoom).where(
            func.upper(TableRoom.country_code) == "ALL",
            TableRoom.is_vip.is_(False),
        )
        n = int((await self.session.execute(stmt)).scalar_one() or 0)
        for i in range(n + 1, GLOBAL_ROOM_SLOTS + 1):
            self.session.add(
                TableRoom(
                    name=f"G{i}",
                    country_code="ALL",
                    is_vip=False,
                )
            )
        await self.session.commit()

    async def get_rooms_by_country(self, country_code: str) -> list[TableRoom]:
        """Berilgan davlat yoki global (ALL) uchun aktiv stollarni qaytaradi."""
        c = country_code.upper()
        stmt = select(TableRoom).where(
            TableRoom.is_active.is_(True),
            or_(
                TableRoom.country_code == c,
                func.upper(TableRoom.country_code) == "ALL",
            ),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_room(self, name: str, country_code: str = "ALL", is_vip: bool = False) -> Optional[TableRoom]:
        """Yangi o'yin xonasi (id avtomatik)."""
        c = normalize_country_code(country_code)
        if is_vip:
            stmt = select(func.count()).select_from(TableRoom).where(TableRoom.is_vip.is_(True))
            if int((await self.session.execute(stmt)).scalar_one() or 0) >= 150:
                return None
            room = TableRoom(name=name, country_code=c, is_vip=True)
        elif c == "ALL":
            stmt = select(func.count()).select_from(TableRoom).where(
                func.upper(TableRoom.country_code) == "ALL",
                TableRoom.is_vip.is_(False),
            )
            if int((await self.session.execute(stmt)).scalar_one() or 0) >= GLOBAL_ROOM_SLOTS:
                return None
            room = TableRoom(name=name, country_code="ALL", is_vip=False)
        else:
            stmt = select(func.count()).select_from(TableRoom).where(
                TableRoom.country_code == c,
                TableRoom.is_vip.is_(False),
            )
            if int((await self.session.execute(stmt)).scalar_one() or 0) >= COUNTRY_ROOM_SLOTS:
                return None
            room = TableRoom(name=name, country_code=c, is_vip=False)

        self.session.add(room)
        await self.session.commit()
        await self.session.refresh(room)
        return room

    async def ensure_base_rooms(self, country_code: str, min_count: int = 3) -> None:
        """Mamlakat (150) + global (20) stollarni yaratish."""
        await self.seed_country_tables(country_code)
        await self.seed_global_tables()

    async def append_table_chat_message(
        self, table_id: int, user_id: str, username: str, body: str
    ) -> None:
        self.session.add(
            TableChatMessage(
                table_id=table_id,
                user_id=str(user_id),
                username=username or "",
                body=body,
            )
        )
        await self.session.commit()
        await self._trim_table_chat_messages(table_id, keep=5)

    async def _trim_table_chat_messages(self, table_id: int, *, keep: int = 5) -> None:
        sub = (
            select(TableChatMessage.id)
            .where(TableChatMessage.table_id == table_id)
            .order_by(TableChatMessage.id.desc())
            .limit(keep)
        )
        keep_ids = list((await self.session.execute(sub)).scalars().all())
        if not keep_ids:
            return
        await self.session.execute(
            delete(TableChatMessage).where(
                TableChatMessage.table_id == table_id,
                TableChatMessage.id.not_in(keep_ids),
            )
        )
        await self.session.commit()

    async def get_recent_table_chat_messages(
        self, table_id: int, *, limit: int = 5
    ) -> list[dict]:
        stmt = (
            select(TableChatMessage)
            .where(TableChatMessage.table_id == table_id)
            .order_by(TableChatMessage.id.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        rows.reverse()
        out: list[dict] = []
        for m in rows:
            ts_ms = int(m.created_at.timestamp() * 1000) if m.created_at else 0
            created = (
                m.created_at.isoformat()
                if m.created_at
                else datetime.utcfromtimestamp(ts_ms / 1000.0).isoformat()
            )
            out.append(
                {
                    "id": m.id,
                    "user_id": m.user_id,
                    "game_username": m.username,
                    "message": m.body,
                    "level": 0,
                    "profile_picture": "/photos/no_img.png",
                    "gender": None,
                    "created_at": created,
                    "is_private": False,
                    # Qo'shimcha (boshqa istemollar)
                    "username": m.username,
                    "from_user": m.username,
                    "user": {
                        "id": m.user_id,
                        "userId": m.user_id,
                        "name": m.username,
                        "username": m.username,
                    },
                    "body": m.body,
                    "text": m.body,
                    "timestamp": ts_ms,
                    "ts": ts_ms,
                }
            )
        return out

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
            .options(selectinload(User.wallet))
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
            .options(selectinload(User.wallet))
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

    async def get_incoming_friend_requests(self, user_id: int) -> list[User]:
        """Menga yuborilgan kutilayotgan do'stlik so'rovlari (yuboruvchi profillari)."""
        from src.app.database.models.relation import UserRelation

        stmt = (
            select(User)
            .join(UserRelation, User.id == UserRelation.user_id)
            .where(
                UserRelation.target_id == user_id,
                UserRelation.type == "friend_request",
            )
            .options(selectinload(User.wallet))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
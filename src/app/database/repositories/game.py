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
            .options(
                selectinload(User.wallet),
                selectinload(User.achievements).selectinload(
                    UserAchievement.achievement
                ),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def sync_daily_login_streak(self, user: User) -> None:
        """HTTP /login dan tashqari ulanishlar (WS): ketma-ket kunlar bo'yicha streak."""
        from src.app.database.repositories.user import UserRepository

        ur = UserRepository(self.session)
        await ur.update_daily_streak(user)
        await self.session.commit()

    async def update_user_fields(self, user_id: int, **fields) -> None:
        """Foydalanuvchi maydonlarini yangilaydi."""
        if not fields:
            return
        stmt = update(User).where(User.id == user_id).values(**fields)
        await self.session.execute(stmt)
        await self.session.commit()

    async def clear_harem_owner_except(
        self, owner_db_id: int, except_user_id: int = 0
    ) -> list[int]:
        """owner_db_id ni uxajor qilgan barcha foydalanuvchilardan olib tashlaydi.

        except_user_id berilsa, shu foydalanuvchi saqlanadi (yangi nishon).
        """
        if not owner_db_id:
            return []
        conds = [User.harem_owner_id == owner_db_id]
        if except_user_id:
            conds.append(User.id != except_user_id)
        stmt = (
            update(User)
            .where(*conds)
            .values(harem_owner_id=0)
            .returning(User.id)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return [int(row[0]) for row in result.all()]

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
        from sqlalchemy.exc import IntegrityError

        from src.app.database.sequence_sync import (
            is_primary_key_violation,
            sync_all_sequences,
        )

        wallet = await self.get_wallet(user_id)
        if wallet:
            return wallet
        for attempt in range(2):
            wallet = Wallet(user_id=user_id)
            self.session.add(wallet)
            try:
                await self.session.flush()
                return wallet
            except IntegrityError as e:
                await self.session.rollback()
                existing = await self.get_wallet(user_id)
                if existing:
                    return existing
                if attempt == 0 and is_primary_key_violation(e):
                    await sync_all_sequences(self.session)
                    await self.session.commit()
                    continue
                raise
        wallet = await self.get_wallet(user_id)
        if wallet:
            return wallet
        raise RuntimeError(f"ensure_wallet failed for user_id={user_id}")

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

    async def get_tg_stars_revenue_stats(self) -> dict:
        """Telegram Stars to'lovlari (transactions.type = tg_stars_topup)."""
        now = datetime.utcnow()
        windows = {
            "day": now - timedelta(days=1),
            "week": now - timedelta(days=7),
            "month": now - timedelta(days=30),
            "year": now - timedelta(days=365),
        }

        async def _agg(since: datetime | None) -> tuple[int, int]:
            stmt = select(
                func.coalesce(func.sum(Transaction.amount), 0),
                func.count(Transaction.id),
            ).where(Transaction.type == "tg_stars_topup")
            if since is not None:
                stmt = stmt.where(Transaction.created_at >= since)
            row = (await self.session.execute(stmt)).one()
            return int(row[0] or 0), int(row[1] or 0)

        out: dict = {}
        for key, since in windows.items():
            stars, cnt = await _agg(since)
            out[key] = {"stars": stars, "payments": cnt}
        total_stars, total_cnt = await _agg(None)
        out["total"] = {"stars": total_stars, "payments": total_cnt}
        return out

    async def get_recent_tg_stars_payments(self, *, limit: int = 50) -> list[dict]:
        stmt = (
            select(Transaction, User)
            .join(User, User.id == Transaction.user_id)
            .where(Transaction.type == "tg_stars_topup")
            .order_by(Transaction.id.desc())
            .limit(max(1, min(int(limit), 200)))
        )
        rows = (await self.session.execute(stmt)).all()
        items: list[dict] = []
        for tx, user in rows:
            charge = ""
            desc = tx.description or ""
            if desc.startswith("tg_charge:"):
                charge = desc.split(":", 1)[1][:24]
            items.append(
                {
                    "id": tx.id,
                    "user_id": tx.user_id,
                    "username": user.username or user.login or f"ID {tx.user_id}",
                    "amount": int(tx.amount or 0),
                    "created_at": (
                        tx.created_at.isoformat(sep=" ", timespec="seconds")
                        if tx.created_at
                        else ""
                    ),
                    "charge_id": charge,
                }
            )
        return items

    async def tg_charge_already_processed(self, charge_id: str) -> bool:
        if not charge_id:
            return False
        tag = f"tg_charge:{charge_id}"
        stmt = (
            select(Transaction.id)
            .where(
                Transaction.type == "tg_stars_topup",
                Transaction.description == tag,
            )
            .limit(1)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def apply_tg_stars_topup(
        self,
        user_id: int,
        amount: int,
        charge_id: str,
    ) -> tuple[bool, int, int, int]:
        """
        Telegram Stars to'lovi: faqat stars_coin ga qo'shiladi.
        (yangi_yozuv, stars_coin, gift_tokens, hearts)
        """
        wallet = await self.get_wallet(user_id)
        if not wallet:
            return False, 0, 0, 0

        tag = f"tg_charge:{charge_id}"
        if await self.tg_charge_already_processed(charge_id):
            return (
                False,
                int(wallet.stars_coin or 0),
                int(wallet.gift_tokens or 0),
                int(wallet.hearts or 0),
            )

        amount = int(amount)
        new_sc = int(wallet.stars_coin or 0) + amount
        new_gt = int(wallet.gift_tokens or 0)
        new_hearts = int(wallet.hearts or 0)

        await self.session.execute(
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(stars_coin=new_sc)
        )
        await self._save_tx(user_id, amount, "stars_coin", "tg_stars_topup", tag)
        await self.session.commit()
        return True, new_sc, new_gt, new_hearts

    async def apply_tg_hearts_product_payment(
        self,
        user_id: int,
        hearts: int,
        stars_paid: int,
        charge_id: str,
    ) -> tuple[bool, int, int, int]:
        """Telegram Stars orqali to'g'ridan-to'g'ri ❤️ paket (hp: payload)."""
        wallet = await self.get_wallet(user_id)
        if not wallet:
            return False, 0, 0, 0

        tag = f"tg_charge:{charge_id}"
        if await self.tg_charge_already_processed(charge_id):
            return (
                False,
                int(wallet.stars_coin or 0),
                int(wallet.gift_tokens or 0),
                int(wallet.hearts or 0),
            )

        hearts = int(hearts)
        new_hearts = int(wallet.hearts or 0) + hearts
        new_sc = int(wallet.stars_coin or 0)
        new_gt = int(wallet.gift_tokens or 0)

        await self.session.execute(
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(hearts=new_hearts)
        )
        await self._save_tx(
            user_id,
            hearts,
            "hearts",
            "tg_hearts_product",
            f"{tag};stars={int(stars_paid)}",
        )
        await self.session.commit()
        return True, new_sc, new_gt, new_hearts

    async def add_gift_tokens(self, user_id: int, amount: int,
                              tx_type: str, description: str = "") -> int:
        stmt = (
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(gift_tokens=Wallet.gift_tokens + amount)
            .returning(Wallet.gift_tokens)
        )
        result = await self.session.execute(stmt)
        new_balance = result.scalar_one_or_none() or 0
        await self._save_tx(user_id, amount, "gift_tokens", tx_type, description)
        await self.session.commit()
        return new_balance

    async def spend_gift_tokens(self, user_id: int, amount: int,
                                tx_type: str, description: str = "") -> tuple[bool, int]:
        wallet = await self.get_wallet(user_id)
        if not wallet or int(wallet.gift_tokens or 0) < amount:
            return False, int(wallet.gift_tokens or 0) if wallet else 0

        stmt = (
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(gift_tokens=Wallet.gift_tokens - amount)
            .returning(Wallet.gift_tokens)
        )
        result = await self.session.execute(stmt)
        new_balance = result.scalar_one_or_none() or 0
        await self._save_tx(user_id, -amount, "gift_tokens", tx_type, description)
        await self.session.commit()
        return True, new_balance

    async def spend_stars_balance(
        self,
        user_id: int,
        amount: int,
        tx_type: str,
        description: str = "",
    ) -> tuple[bool, int, int]:
        """
        Stars sarflash: faqat stars_coin dan (gift_tokens tegilmaydi).
        (ok, yangi_stars_coin, yangi_gift_tokens).
        """
        wallet = await self.get_wallet(user_id)
        if not wallet:
            return False, 0, 0

        amount = int(amount)
        sc = int(wallet.stars_coin or 0)
        gt = int(wallet.gift_tokens or 0)
        if sc < amount:
            return False, sc, gt

        new_sc = sc - amount

        await self.session.execute(
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(stars_coin=new_sc)
        )
        await self._save_tx(
            user_id, -amount, "stars_coin", tx_type, description or ""
        )
        await self.session.commit()
        return True, new_sc, gt

    async def purchase_hearts_with_gift_tokens(
        self,
        user_id: int,
        token_cost: int,
        hearts_delta: int,
    ) -> tuple[bool, int, int, int]:
        """
        Yurak paketi: faqat stars_coin dan yechiladi.
        (ok, yangi_stars_coin, yangi_gift_tokens, yangi_hearts).
        """
        wallet = await self.get_wallet(user_id)
        if not wallet:
            return False, 0, 0, 0

        sc = int(wallet.stars_coin or 0)
        gt = int(wallet.gift_tokens or 0)
        if sc < token_cost:
            return False, sc, gt, int(wallet.hearts or 0)

        ok, new_sc, new_gt = await self.spend_stars_balance(
            user_id,
            token_cost,
            "hearts_purchase",
            f"pack:{token_cost}",
        )
        if not ok:
            return False, sc, gt, int(wallet.hearts or 0)

        new_hearts = int(wallet.hearts or 0) + hearts_delta
        await self.session.execute(
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(hearts=new_hearts)
        )
        await self._save_tx(
            user_id, hearts_delta, "hearts", "hearts_purchase", f"pack:{token_cost}"
        )
        await self.session.commit()
        return True, new_sc, new_gt, new_hearts

    async def purchase_vip_with_gift_tokens(
        self,
        user_id: int,
        price_tokens: int,
        bonus_tokens: int,
        extend_days: int,
    ) -> tuple[bool, int, int]:
        """
        VIP: faqat stars_coin dan yechiladi; bonus ham stars_coin ga.
        (ok, yangi_stars_coin, yangi_gift_tokens).
        """
        wallet = await self.get_wallet(user_id)
        user = await self.get_user_with_wallet(user_id)
        if not wallet or not user:
            return False, 0, 0

        sc = int(wallet.stars_coin or 0)
        gt = int(wallet.gift_tokens or 0)
        if sc < price_tokens:
            return False, sc, gt

        ok, new_sc, new_gt = await self.spend_stars_balance(
            user_id, price_tokens, "vip_purchase", ""
        )
        if not ok:
            return False, sc, gt

        now = datetime.now()
        base = now
        if user.vip_expires_at and user.vip_expires_at > now:
            base = user.vip_expires_at
        new_expires = base + timedelta(days=extend_days)
        new_sc = int(new_sc) + int(bonus_tokens)

        await self.session.execute(
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(stars_coin=new_sc)
        )
        if bonus_tokens:
            await self._save_tx(user_id, bonus_tokens, "stars_coin", "vip_bonus", "")
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
        return True, new_sc, new_gt

    async def convert_stars_coin_to_gift_tokens(
        self,
        user_id: int,
        cost: int,
        tokens_delta: int,
    ) -> tuple[bool, int, int]:
        """
        GM (stars_coin) yechiladi, gift_tokens qo'shiladi.
        (ok, yangi_stars_coin, yangi_gift_tokens).
        """
        wallet = await self.get_wallet(user_id)
        if not wallet or int(wallet.stars_coin or 0) < cost:
            return (
                False,
                int(wallet.stars_coin or 0) if wallet else 0,
                int(wallet.gift_tokens or 0) if wallet else 0,
            )
        new_sc = int(wallet.stars_coin) - cost
        new_gt = int(wallet.gift_tokens or 0) + tokens_delta
        await self.session.execute(
            update(Wallet)
            .where(Wallet.user_id == user_id)
            .values(stars_coin=new_sc, gift_tokens=new_gt)
        )
        await self._save_tx(user_id, -cost, "stars_coin", "gm_to_gift_tokens", f"gm:{cost}")
        await self._save_tx(
            user_id, tokens_delta, "gift_tokens", "gm_to_gift_tokens", f"gt+:{tokens_delta}"
        )
        await self.session.commit()
        return True, new_sc, new_gt

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
            'compliment' | 'bottle_spin' | 'donjuan' (Userda ustun bo'lmasa — faqat UserStats)
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
        self, user_id: int, key: str, level: int, *, exact: bool = False
    ) -> None:
        """Foydalanuvchining yutiq darajasini saqlaydi (yangilash yoki yaratish).

        exact=True: daraja statistikadan kelgan qiymatga majburan tenglanadi
        (masalan, donjuan — eski noto'g'ri DB darajasini tushirish).
        """
        ach = await self._get_or_create_achievement(key)
        stmt = select(UserAchievement).where(
            UserAchievement.user_id == user_id,
            UserAchievement.achievement_id == ach.id,
        )
        ua = (await self.session.execute(stmt)).scalar_one_or_none()
        if ua:
            if exact:
                if level <= 0:
                    await self.session.delete(ua)
                else:
                    ua.level = level
                    ua.status = "completed"
            elif (ua.level or 0) < level:
                ua.level = level
                ua.status = "completed"
        elif level > 0:
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
        """user_id — barqaror db_id (mavjud bo'lsa), aks holda WS player.id."""
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
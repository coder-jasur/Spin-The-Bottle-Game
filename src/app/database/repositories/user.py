from datetime import datetime, timedelta
from typing import AsyncGenerator

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.config import load_config
from src.app.database.models import User, Wallet, Admins

config = load_config()

class UserRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_admin_role(self, user_id: int) -> str | None:
        """Foydalanuvchi admin rolini qaytarish (superadmin, moderator yoki None)"""
        # 1. Asosiy Superadmin tekshiruvi (.env dan)
        if user_id == config.main_admin_id:
            return "superadmin"
            
        # 2. Boshqa adminlarni bazadan tekshirish
        stmt = select(Admins).where(Admins.user_id == user_id)
        result = await self.session.execute(stmt)
        admin_record = result.scalar_one_or_none()
        
        if admin_record:
            return admin_record.role
            
        return None

    async def update_daily_streak(self, user: User):
        """Foydalanuvchining kunlik kirish ketma-ketligini yangilash"""
        now = datetime.now()
        
        if not user.last_login_at:
            user.daily_streak = 1
        else:
            # Oxirgi kirgan sanasi va bugun o'rtasidagi farq
            last_login_date = user.last_login_at.date()
            today_date = now.date()
            
            diff = (today_date - last_login_date).days
            
            if diff == 1:
                # Kecha kirgan, streak davom etadi
                user.daily_streak += 1
                if user.daily_streak > 7:
                    user.daily_streak = 1
            elif diff > 1:
                # Orada kun qolib ketgan, reset
                user.daily_streak = 1
            # Agar diff == 0 bo'lsa (bugun allaqachon kirgan), streak o'zgarmaydi
            
        user.last_login_at = now
        await self.session.flush()

    async def can_claim_daily_bonus(self, user: User) -> bool:
        """Bugun bonus olishi mumkinligini tekshirish"""
        if not user.last_bonus_claimed_at:
            return True
            
        today_date = datetime.now().date()
        last_claimed_date = user.last_bonus_claimed_at.date()
        
        return today_date > last_claimed_date

    async def is_admin(self, user_id: int) -> bool:
        """Foydalanuvchi admin ekanligini tekshirish (qulaylik uchun qoldirildi)"""
        role = await self.get_admin_role(user_id)
        return role is not None

    async def _generate_referral_id(self, length: int = 6) -> str:
        """Takrorlanmas alfanumerik referral_id yaratish"""
        import random
        import string

        characters = string.ascii_uppercase + string.digits
        while True:
            ref_id = "".join(random.choices(characters, k=length))
            # Bazada bormi tekshirish
            stmt = select(User).where(User.referral_id == ref_id)
            result = await self.session.execute(stmt)
            if not result.scalar_one_or_none():
                return ref_id

    async def get_user_by_id(self, user_id: int):
        """ID (primary key) orqali foydalanuvchini topish"""
        stmt = select(User).where(User.id == user_id).options(selectinload(User.wallet))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_by_username(self, username: str):
        """Username orqali foydalanuvchini topish"""
        stmt = select(User).where(User.username == username).options(selectinload(User.wallet))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_by_login(self, login: str):
        """Login orqali foydalanuvchini topish"""
        stmt = select(User).where(User.login == login).options(selectinload(User.wallet))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_user(
        self,
        password: str = None,
        tg_id: int = None,
        referred_by_id: str = None,
        country: str = None,
        **kwargs,
    ):
        """Umumiy foydalanuvchi qo'shish metodi (TG yoki Web)"""
        # Yangi referral_id yaratish
        new_ref_id = await self._generate_referral_id()

        user = User(
            tg_id=tg_id,
            referral_id=new_ref_id,
            referred_by_id=referred_by_id,
            password=password,
            country=country,
            **kwargs,
        )
        self.session.add(user)
        await self.session.flush()  # User.id ni olish uchun

        # 👤 Avtomatik username generatsiya qilish
        if not user.username:
            user.username = f"user_{user.id}"
            await self.session.flush()


        # 💰 Hamyon yaratish
        wallet = Wallet(user_id=user.id)
        self.session.add(wallet)

        if referred_by_id is not None:
            await self.increment_invited_guests_by_ref_id(referred_by_id)

        try:
            await self.session.commit()
            await self.session.refresh(user, ["wallet"])  # Relationshipni yuklash
            return user
        except Exception:
            await self.session.rollback()
            raise

    async def get_user_by_login(self, login: str):
        """Login orqali foydalanuvchini topish"""
        stmt = (
            select(User).where(User.login == login).options(selectinload(User.wallet))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user(self, tg_id: int):
        stmt = (
            select(User).where(User.tg_id == tg_id).options(selectinload(User.wallet))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_by_username(self, username: str):
        if username.startswith("@"):
            username = username[1:]
        stmt = (
            select(User)
            .where(User.username == username)
            .options(selectinload(User.wallet))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_user(self):
        stmt = select(User).options(selectinload(User.wallet))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def increment_invited_guests_by_ref_id(self, referral_id: str):
        """Taklif qilgan odamning invited_guests'ini 1 taga oshirish"""
        stmt = (
            update(User)
            .where(User.referral_id == referral_id)
            .values(invited_guests=User.invited_guests + 1)
        )
        await self.session.execute(stmt)
        # Commit add_tg_user/add_web_user ichida qilinadi

    async def get_registration_stats(self):
        from datetime import datetime, timedelta

        now = datetime.now()
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)
        year_ago = now - timedelta(days=365)

        # Total
        stmt_total = select(func.count(User.id))
        total = (await self.session.execute(stmt_total)).scalar()

        # Day
        stmt_day = select(func.count(User.id)).where(User.created_at >= day_ago)
        day = (await self.session.execute(stmt_day)).scalar()

        # Month
        stmt_month = select(func.count(User.id)).where(User.created_at >= month_ago)
        month = (await self.session.execute(stmt_month)).scalar()

        # Week
        stmt_week = select(func.count(User.id)).where(User.created_at >= week_ago)
        week = (await self.session.execute(stmt_week)).scalar()

        # Year
        stmt_year = select(func.count(User.id)).where(User.created_at >= year_ago)
        year = (await self.session.execute(stmt_year)).scalar()

        # New Stats: Premium & Language
        stmt_premium = select(func.count(User.id)).where(User.is_premium == True)
        premium_count = (await self.session.execute(stmt_premium)).scalar()

        stmt_langs = (
            select(User.language_code, func.count(User.id))
            .group_by(User.language_code)
            .order_by(func.count(User.id).desc())
            .limit(5)
        )
        langs_result = (await self.session.execute(stmt_langs)).all()
        langs_stats = [
            {"code": row[0] or "unknown", "count": row[1]} for row in langs_result
        ]

        # Top Countries
        stmt_countries = (
            select(User.country, func.count(User.id))
            .group_by(User.country)
            .order_by(func.count(User.id).desc())
            .limit(10)
        )
        countries_result = (await self.session.execute(stmt_countries)).all()
        countries_stats = [
            {"name": row[0] or "unknown", "count": row[1]} for row in countries_result
        ]

        # New: Revenue and VIP counts
        # We fetch all users with non-empty payment history or active VIP
        # (For optimization in larger DBs, we'd use a separate Payment table)
        stmt_vip = select(func.count(User.id)).where(User.vip_status == True)
        active_vip_count = (await self.session.execute(stmt_vip)).scalar()

        # Revenue intervals
        revenue = {
            "day": {"uzs": 0, "stars": 0},
            "week": {"uzs": 0, "stars": 0},
            "month": {"uzs": 0, "stars": 0},
            "year": {"uzs": 0, "stars": 0},
            "total": {"uzs": 0, "stars": 0},
        }

        now = datetime.utcnow() + timedelta(hours=5)

        stmt_all = select(User.vip_payment_history).where(
            User.vip_payment_history != None
        )
        result = await self.session.execute(stmt_all)
        for history in result.scalars():
            if not history:
                continue
            if isinstance(history, dict):
                history = [history]
            for payment in history:
                amount = payment.get("amount", 0)
                currency = payment.get("currency", "").upper()
                date_str = payment.get("date", "")  # format: %d.%m.%Y %H:%M or %d.%m.%Y

                try:
                    if " " in date_str:
                        p_date = datetime.strptime(date_str, "%d.%m.%Y %H:%M")
                    else:
                        p_date = datetime.strptime(date_str, "%d.%m.%Y")
                except:
                    continue

                is_uzs = currency in ["UZS", "SUM"]
                is_stars = currency in ["XTR", "STARS"]

                def add_rev(key):
                    if is_uzs:
                        revenue[key]["uzs"] += amount
                    if is_stars:
                        revenue[key]["stars"] += amount

                add_rev("total")
                if p_date >= now - timedelta(days=1):
                    add_rev("day")
                if p_date >= now - timedelta(days=7):
                    add_rev("week")
                if p_date >= now - timedelta(days=30):
                    add_rev("month")
                if p_date >= now - timedelta(days=365):
                    add_rev("year")

        return {
            "total": total,
            "day": day,
            "week": week,
            "month": month,
            "year": year,
            "premium": premium_count,
            "languages": langs_stats,
            "countries": countries_stats,
            "active_vip": active_vip_count,
            "revenue": revenue,
        }

    async def update_user(self, tg_id: int, **kwargs):
        stmt = update(User).where(User.tg_id == tg_id).values(**kwargs)
        await self.session.execute(stmt)
        await self.session.commit()

    async def update_user_by_id(self, user_id: int, **kwargs):
        stmt = update(User).where(User.id == user_id).values(**kwargs)
        await self.session.execute(stmt)
        await self.session.commit()

    async def update_user_status(self, new_status: str, tg_id: int):
        await self.update_user(tg_id, status=new_status)

    async def increment_invited_guests(self, tg_id: int) -> int:
        from sqlalchemy import func as sa_func

        stmt = (
            update(User)
            .where(User.tg_id == tg_id)
            .values(invited_guests=sa_func.coalesce(User.invited_guests, 0) + 1)
            .returning(User.invited_guests)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        value = result.scalar()
        return value if value is not None else 0

    async def get_user_ids_batch(self, offset: int, limit: int = 5000) -> list[int]:
        stmt = select(User.tg_id).order_by(User.tg_id).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def iterate_user_ids(
        self, batch_size: int = 5000, exclude_vip: bool = False
    ) -> AsyncGenerator[tuple[list[int], int], None]:

        offset = 0

        while True:
            # We construct a custom get_user_ids_batch to filter VIPs
            stmt = select(User.tg_id).order_by(User.tg_id)
            if exclude_vip:
                stmt = stmt.where(
                    (User.vip_status == False) | (User.vip_status.is_(None))
                )
            stmt = stmt.offset(offset).limit(batch_size)
            result = await self.session.execute(stmt)
            user_ids = list(result.scalars().all())

            if not user_ids:
                break

            yield user_ids, offset
            offset += len(user_ids)

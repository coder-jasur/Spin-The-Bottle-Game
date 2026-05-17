from pathlib import Path
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from sqlalchemy.orm import selectinload
from src.app.api.deps import get_db
from src.app.core.jwt import verify_access_token
from src.app.database.repositories.user import UserRepository
from src.app.database.repositories.game import GameRepository
from src.app.database.repositories.referral import ReferralRepository
from src.app.database.models import User, AdminActionLog, BroadcastMessage, Wallet, Admins
from src.app.database.models.stats import UserStats
from src.app.api.ws.game_manager import manager as game_manager
from src.app.api.ws.constants import GIFT_LOVE_UNLIMITED_MIN

site_dir = Path(__file__).resolve().parents[2] / "site"
templates = Jinja2Templates(directory=str(site_dir))

router = APIRouter(tags=["Admin"])

async def get_current_admin_info(request: Request, session: AsyncSession = Depends(get_db)):
    token = request.cookies.get("device_user_ids")
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    payload = verify_access_token(token)
    # Fallback for legacy format
    if not payload:
        import urllib.parse
        import json
        try:
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if ids: payload = {"id": int(ids[0])}
        except: pass
    
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    user_repo = UserRepository(session)
    role = await user_repo.get_admin_role(payload.get("id"))
    if not role:
        raise HTTPException(status_code=403, detail="Forbidden: Admin access required")
    
    return {"id": payload.get("id"), "role": role}

async def get_current_admin(admin_info: dict = Depends(get_current_admin_info)):
    return admin_info["id"]

async def get_current_superadmin(admin_info: dict = Depends(get_current_admin_info)):
    if admin_info["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden: Superadmin access required")
    return admin_info["id"]

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, admin_info: dict = Depends(get_current_admin_info)):
    return templates.TemplateResponse(request, "admin.html")

@router.get("/api/admin/search_user")
async def search_admin_user(
    query: str,
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin)
):
    user_repo = UserRepository(session)
    user = None
    
    try:
        user = await user_repo.get_user_by_id(int(query))
    except ValueError:
        user = await user_repo.get_user_by_username(query.lstrip('@'))
        if not user:
            user = await user_repo.get_user_by_login(query)
            if not user:
                # Fallback to display_name search
                stmt = select(User).where(User.display_name == query).options(selectinload(User.wallet))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
            
    if not user:
        return {"success": False, "message": "Foydalanuvchi topilmadi"}
    vip_active = bool(user.vip_status) and (
        user.vip_expires_at is None or user.vip_expires_at > datetime.now()
    )
    return {
        "success": True,
        "user": {
            "id": user.id,
            "username": user.username or user.login or "Noma'lum",
            "complaints": user.number_of_complaints,
            "is_banned": user.is_banned,
            "gender": user.gender,
            "country": user.country,
            "avatar": user.avatar_url or "/photos/no_img.png",
            "vip": vip_active,
            "vip_expires_at": (
                user.vip_expires_at.isoformat() if user.vip_expires_at else None
            ),
        }
    }


@router.get("/api/admin/user-metrics")
async def admin_user_metrics(
    user_id: int,
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    """Diagnostika: DB'dagi umumiy metrikalar (reyting uchun)."""
    user = (
        await session.execute(select(User).where(User.id == int(user_id)))
    ).scalar_one_or_none()
    if not user:
        return JSONResponse({"success": False, "message": "User topilmadi"}, status_code=404)

    rows = (
        await session.execute(
            select(UserStats.category, UserStats.daily_value, UserStats.monthly_value, UserStats.total_value)
            .where(UserStats.user_id == int(user_id))
        )
    ).all()
    stats = {
        r.category: {
            "daily": int(r.daily_value or 0),
            "monthly": int(r.monthly_value or 0),
            "total": int(r.total_value or 0),
        }
        for r in rows
        if r and r.category
    }

    return {
        "success": True,
        "user_id": int(user.id),
        "user_columns": {
            "kisses": int(getattr(user, "kisses", 0) or 0),
            "dj": int(getattr(user, "dj", 0) or 0),
            "emotion": int(getattr(user, "emotion", 0) or 0),
            "expense": int(getattr(user, "expense", 0) or 0),
            "importance": int(getattr(user, "importance", 0) or 0),
            "harem_price": int(getattr(user, "harem_price", 0) or 0),
            "gift_love_stock": int(getattr(user, "gift_love_stock", 0) or 0),
        },
        "user_stats": stats,
    }

@router.get("/api/admin/users/complaints")
async def get_complained_users(
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin)
):
    stmt = select(User).where(User.number_of_complaints >= 10).order_by(desc(User.number_of_complaints), desc(User.id)).limit(100)
    result = await session.execute(stmt)
    users = result.scalars().all()
    
    return [{
        "id": u.id,
        "username": u.username or u.login or "Noma'lum",
        "complaints": u.number_of_complaints,
        "is_banned": u.is_banned,
        "country": u.country,
        "avatar": u.avatar_url or "/photos/no_img.png"
    } for u in users]

@router.post("/api/admin/add-balance")
async def add_balance(
    data: dict = Body(...),
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin)
):
    """Balans: gift_tokens, stars_coin, hearts, vip (kun). `operation`: `add` | `subtract`."""
    try:
        target_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "message": "Foydalanuvchi ID noto'g'ri"},
            status_code=400,
        )

    try:
        amount = int(data.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0

    currency = (data.get("currency") or "gift_tokens").strip().lower()
    if currency not in ("gift_tokens", "hearts", "stars_coin", "vip"):
        currency = "gift_tokens"

    operation = (data.get("operation") or data.get("direction") or "add").strip().lower()
    if operation not in ("add", "subtract"):
        operation = "add"

    if amount <= 0:
        return JSONResponse(
            {"success": False, "message": "Miqdor musbat butun son bo'lishi kerak"},
            status_code=400,
        )

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(target_id)
    if not user:
        return JSONResponse({"success": False, "message": "User topilmadi"}, status_code=404)

    if currency == "vip":
        now = datetime.now()
        if operation == "add":
            base = now
            if user.vip_expires_at and user.vip_expires_at > now:
                base = user.vip_expires_at
            new_expires = base + timedelta(days=amount)
            user.vip_status = True
            user.is_premium = True
            user.vip_expires_at = new_expires
            msg = (
                f"VIP +{amount} kun berildi. "
                f"Tugash: {new_expires.strftime('%Y-%m-%d %H:%M')}"
            )
            action = "grant_vip"
        else:
            if not user.vip_status or not user.vip_expires_at:
                return JSONResponse(
                    {"success": False, "message": "Foydalanuvchida faol VIP yo'q"},
                    status_code=400,
                )
            new_expires = user.vip_expires_at - timedelta(days=amount)
            if new_expires <= now:
                user.vip_status = False
                user.is_premium = False
                user.vip_expires_at = None
                msg = f"VIP olib tashlandi (-{amount} kun, muddati tugadi)"
            else:
                user.vip_expires_at = new_expires
                msg = (
                    f"VIP -{amount} kun. "
                    f"Qolgan muddat: {new_expires.strftime('%Y-%m-%d %H:%M')}"
                )
            action = "revoke_vip"

        admin_log = AdminActionLog(
            admin_id=admin_id,
            target_user_id=target_id,
            action=action,
            amount=amount if operation == "add" else -amount,
            details=f"Admin {admin_id} {operation} VIP {amount} days for user {target_id}",
        )
        session.add(admin_log)
        await session.commit()
        return {"success": True, "message": msg}

    if not user.wallet:
        user.wallet = Wallet(user_id=user.id)
        session.add(user.wallet)
        await session.flush()

    delta = amount if operation == "add" else -amount

    if currency == "gift_tokens":
        cur = int(user.wallet.gift_tokens or 0)
        user.wallet.gift_tokens = max(0, cur + delta)
    elif currency == "stars_coin":
        cur = int(user.wallet.stars_coin or 0)
        user.wallet.stars_coin = max(0, cur + delta)
    else:
        cur = int(user.wallet.hearts or 0)
        user.wallet.hearts = max(0, cur + delta)

    admin_log = AdminActionLog(
        admin_id=admin_id,
        target_user_id=target_id,
        action=f"{operation}_{currency}",
        amount=delta,
        details=f"Admin {admin_id} {operation} {abs(delta)} {currency} to user {target_id} (delta={delta})",
    )
    session.add(admin_log)

    await session.commit()
    if currency == "gift_tokens":
        label = "gift token"
    elif currency == "stars_coin":
        label = "Stars"
    else:
        label = "yurak (gold)"
    if operation == "subtract":
        return {"success": True, "message": f"-{amount} {label} ayirildi (minimum 0)"}
    return {"success": True, "message": f"+{amount} {label} qo'shildi"}


@router.post("/api/admin/grant-love-cocktail")
async def grant_love_cocktail(
    data: dict = Body(...),
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    """Mehebbet kokteyli (g_love): o'yin sovg'alar panelida ko'rinadi; >=999 cheksiz."""
    try:
        target_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "message": "Foydalanuvchi ID noto'g'ri"},
            status_code=400,
        )

    try:
        amount = int(data.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0

    operation = (data.get("operation") or "add").strip().lower()
    if operation not in ("add", "set", "subtract", "clear"):
        operation = "add"

    if operation != "clear" and amount <= 0:
        return JSONResponse(
            {"success": False, "message": "Miqdor musbat butun son bo'lishi kerak"},
            status_code=400,
        )

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(target_id)
    if not user:
        return JSONResponse({"success": False, "message": "User topilmadi"}, status_code=404)

    cur = int(getattr(user, "gift_love_stock", 0) or 0)
    if operation == "clear":
        new_stock = 0
        log_action = "revoke_love_cocktail_all"
    elif operation == "set":
        new_stock = amount
        log_action = "set_love_cocktail"
    elif operation == "subtract":
        new_stock = max(0, cur - amount)
        log_action = "revoke_love_cocktail"
    else:
        new_stock = cur + amount
        log_action = "grant_love_cocktail"

    user.gift_love_stock = new_stock

    admin_log = AdminActionLog(
        admin_id=admin_id,
        target_user_id=target_id,
        action=log_action,
        amount=new_stock,
        details=(
            f"Admin {admin_id} {operation} love_cocktail "
            f"(delta={amount if operation != 'clear' else cur}) "
            f"for user {target_id} (was {cur}, now {new_stock})"
        ),
    )
    session.add(admin_log)
    await session.commit()

    synced = await game_manager.admin_sync_gift_love_stock(target_id, new_stock)

    if new_stock >= GIFT_LOVE_UNLIMITED_MIN:
        display = "999+ (cheksiz)"
    elif new_stock <= 0:
        display = "0 (yo'q)"
    else:
        display = str(new_stock)

    if operation == "clear":
        msg = f"Kokteillar olib tashlandi (oldingi: {cur}, hozir: yo'q)"
    elif operation == "subtract":
        msg = f"Koktel ayirildi: −{amount} (oldingi {cur} → hozir {display})"
    elif operation == "set":
        msg = f"Koktel o'rnatildi: {display}"
    else:
        msg = f"Koktel berildi: +{amount} (hozir {display})"
    if synced:
        msg += " — onlayn o'yinchi yangilandi"
    return {
        "success": True,
        "message": msg,
        "gift_love_stock": new_stock,
        "synced_online": synced,
    }


@router.post("/api/admin/grant-vip")
async def grant_vip(
    data: dict = Body(...),
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    """Admin orqali VIP obuna berish (kun bo'yicha) yoki uzaytirish."""
    try:
        target_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "Foydalanuvchi ID noto'g'ri"}, status_code=400)

    try:
        days = int(data.get("days", 0))
    except (TypeError, ValueError):
        days = 0

    if days <= 0:
        return JSONResponse({"success": False, "message": "Kun soni musbat butun son bo'lishi kerak"}, status_code=400)

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(target_id)
    if not user:
        return JSONResponse({"success": False, "message": "User topilmadi"}, status_code=404)

    now = datetime.now()
    base = now
    if user.vip_expires_at and user.vip_expires_at > now:
        base = user.vip_expires_at
    new_expires = base + timedelta(days=days)

    user.vip_status = True
    user.is_premium = True
    user.vip_expires_at = new_expires

    log = AdminActionLog(
        admin_id=admin_id,
        target_user_id=target_id,
        action="grant_vip",
        amount=days,
        details=f"Admin {admin_id} granted VIP +{days} days to user {target_id} (expires_at={new_expires.isoformat()})",
    )
    session.add(log)

    await session.commit()
    return {
        "success": True,
        "message": f"VIP +{days} kun berildi. Tugash sanasi: {new_expires.strftime('%Y-%m-%d %H:%M')}",
        "expires_at": new_expires.isoformat(),
    }

@router.post("/api/admin/ban")
async def ban_user_endpoint(
    data: dict = Body(...),
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin)
):
    user_query = data.get("query")
    action = data.get("action") # "ban" or "unban"
    
    if not user_query or action not in ["ban", "unban"]:
        return JSONResponse({"success": False, "message": "Noto'g'ri so'rov"}, status_code=400)
        
    user_repo = UserRepository(session)
    target_user = None
    
    try:
        target_user = await user_repo.get_user_by_id(int(user_query))
    except ValueError:
        target_user = await user_repo.get_user_by_username(str(user_query).lstrip('@'))
        if not target_user:
            target_user = await user_repo.get_user_by_login(str(user_query))
            
    if not target_user:
        return JSONResponse({"success": False, "message": "Foydalanuvchi topilmadi"}, status_code=404)
        
    # Superadminni ban qilib bo'lmaydi
    target_role = await user_repo.get_admin_role(target_user.id)
    if target_role == "superadmin":
        return JSONResponse({"success": False, "message": "Superadminni bloklab bo'lmaydi"}, status_code=403)
        
    if action == "ban":
        duration_days = data.get("duration") # '1', '7', '15', '30', or 'forever'
        target_user.is_banned = True
        
        from datetime import datetime, timedelta
        if duration_days and duration_days != "forever":
            try:
                days = int(duration_days)
                target_user.ban_expires_at = datetime.now() + timedelta(days=days)
            except ValueError:
                target_user.ban_expires_at = None # default forever
        else:
            target_user.ban_expires_at = None
            
        details = f"Admin {admin_id} banned user {target_user.id} for {duration_days} days"
    else:
        # Unban
        target_user.is_banned = False
        target_user.ban_expires_at = None
        target_user.number_of_complaints = 0 # Shikoyatlar 0 ga tushiriladi
        details = f"Admin {admin_id} unbanned user {target_user.id}"
    
    log = AdminActionLog(
        admin_id=admin_id,
        target_user_id=target_user.id,
        action=action,
        details=details
    )
    session.add(log)
    
    await session.commit()
    
    status_text = "bloklandi" if action == "ban" else "blokdan chiqarildi"
    return {"success": True, "message": f"Foydalanuvchi muvaffaqiyatli {status_text}!"}

@router.post("/api/admin/broadcast")
async def broadcast_message(
    data: dict = Body(...),
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin)
):
    text = data.get("text")
    if not text:
        return JSONResponse({"success": False, "message": "Matn kiritilmadi"}, status_code=400)
    
    # Kelajakda bu xabar barcha foydalanuvchilarga bildirishnoma sifatida yuboriladi
    # Hozircha faqat tarixga saqlaymiz
    msg = BroadcastMessage(admin_id=admin_id, text=text)
    session.add(msg)
    
    # Log yozish
    log = AdminActionLog(
        admin_id=admin_id,
        action="broadcast",
        details=f"Broadcast message: {text[:50]}..."
    )
    session.add(log)
    
    await session.commit()
    return {"success": True, "message": "Xabar muvaffaqiyatli tarqatildi (Logga saqlandi)"}

@router.get("/api/admin/check")
async def check_admin_status(request: Request, session: AsyncSession = Depends(get_db)):
    try:
        info = await get_current_admin_info(request, session)
        return {"is_admin": True, "role": info["role"]}
    except Exception:
        return {"is_admin": False}

@router.get("/api/admin/list")
async def list_admins(
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_superadmin)
):
    stmt = select(Admins).options(selectinload(Admins.user))
    result = await session.execute(stmt)
    admins = result.scalars().all()
    
    # .env dagi superadminni ham qo'shamiz (agar u bazada bo'lmasa)
    from src.app.core.config import load_config
    config = load_config()
    
    admin_list = []
    found_main = False
    
    for a in admins:
        if a.user_id == config.main_admin_id:
            found_main = True
        admin_list.append({
            "user_id": a.user_id,
            "username": a.user.username if a.user else f"ID:{a.user_id}",
            "role": a.role
        })
        
    if not found_main:
        # Main adminni repo orqali yuklash
        user_repo = UserRepository(session)
        main_user = await user_repo.get_user_by_id(config.main_admin_id)
        admin_list.insert(0, {
            "user_id": config.main_admin_id,
            "username": main_user.username if main_user else f"ID:{config.main_admin_id}",
            "role": "superadmin"
        })
        
    return admin_list

@router.post("/api/admin/manage")
async def manage_admin(
    data: dict = Body(...),
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_superadmin)
):
    action = data.get("action") # "add", "delete", "update_role"
    target_id = data.get("user_id")
    username = data.get("username")
    role = data.get("role", "moderator")
    
    user_repo = UserRepository(session)
    target_user = None
    
    if target_id:
        try:
            target_user = await user_repo.get_user_by_id(int(target_id))
        except: pass
    elif username:
        target_user = await user_repo.get_user_by_username(username)
        
    if not target_user:
        return JSONResponse({"success": False, "message": "Foydalanuvchi topilmadi"}, status_code=404)
    
    if action == "add":
        # Tekshiramiz, u allaqachon adminmi?
        stmt = select(Admins).where(Admins.user_id == target_user.id)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.role = role
        else:
            new_admin = Admins(user_id=target_user.id, role=role)
            session.add(new_admin)
            
    elif action == "delete":
        # Main adminni o'chirib bo'lmaydi
        from src.app.core.config import load_config
        config = load_config()
        if target_user.id == config.main_admin_id:
            return JSONResponse({"success": False, "message": "Asosiy superadminni o'chirib bo'lmaydi"}, status_code=400)
            
        stmt = select(Admins).where(Admins.user_id == target_user.id)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            await session.delete(existing)
        else:
             return JSONResponse({"success": False, "message": "Foydalanuvchi admin emas"}, status_code=400)
            
    elif action == "update_role":
        stmt = select(Admins).where(Admins.user_id == target_user.id)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.role = role
        else:
            return JSONResponse({"success": False, "message": "Foydalanuvchi admin emas"}, status_code=400)
            
    await session.commit()
    return {"success": True, "message": f"Admin muvaffaqiyatli {action} qilindi"}

@router.get("/api/admin/stats")
async def get_stats(
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin)
):
    user_repo = UserRepository(session)
    stats = await user_repo.get_registration_stats()
    return stats


@router.get("/api/admin/live-dashboard")
async def admin_live_dashboard(
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    """Onlayn o'yinchilar + Telegram Stars daromadi (real vaqt)."""
    online = game_manager.get_online_presence_stats()
    repo = GameRepository(session)
    revenue = await repo.get_tg_stars_revenue_stats()
    return {
        "success": True,
        "online": online,
        "stars_revenue": revenue,
        "ts": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/api/admin/stars-payments")
async def admin_stars_payments(
    limit: int = 50,
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    """Oxirgi Telegram Stars to'lovlari ro'yxati."""
    repo = GameRepository(session)
    payments = await repo.get_recent_tg_stars_payments(limit=limit)
    revenue = await repo.get_tg_stars_revenue_stats()
    return {
        "success": True,
        "payments": payments,
        "summary": revenue,
    }


def _partner_display_name(partner) -> str:
    u = partner.user
    if not u:
        return f"#{partner.user_id}"
    return (u.display_name or u.username or u.login or f"user_{u.id}").strip()


def _serialize_partner(partner, *, daily_earned: int) -> dict:
    return {
        "id": partner.id,
        "user_id": partner.user_id,
        "display_name": _partner_display_name(partner),
        "partner_id": partner.partner_id,
        "invited_bonus": int(partner.invited_bonus or 0),
        "bonus_limit": int(partner.bonus_limit or 0),
        "invited_guests": int(partner.invited_guests or 0),
        "daily_earned": int(daily_earned),
        "is_active": bool(partner.is_active),
    }


@router.post("/api/admin/db/sync-sequences")
async def admin_sync_sequences(
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    """PostgreSQL id sequence — backup/merge dan keyin users_pkey / wallets_pkey xatosi."""
    from src.app.database.sequence_sync import sync_all_sequences

    n = await sync_all_sequences(session)
    await session.commit()
    return {"success": True, "message": f"Sequence yangilandi ({n} jadval)", "tables": n}


@router.get("/api/admin/referral/settings")
async def get_referral_settings(
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    repo = ReferralRepository(session)
    s = await repo.get_default_settings()
    return {
        "success": True,
        "settings": {
            "bonus_hearts": int(s.bonus_hearts),
            "bonus_limit": int(s.bonus_limit),
        },
    }


@router.put("/api/admin/referral/settings")
async def update_referral_settings(
    data: dict = Body(...),
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    repo = ReferralRepository(session)
    bonus = data.get("bonus_hearts")
    limit = data.get("bonus_limit")
    if bonus is None and limit is None:
        return JSONResponse(
            {"success": False, "message": "bonus_hearts yoki bonus_limit kerak"},
            status_code=400,
        )
    try:
        if bonus is not None:
            bonus = int(bonus)
        if limit is not None:
            limit = int(limit)
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "message": "Butun son kiriting"},
            status_code=400,
        )
    s = await repo.update_default_settings(
        bonus_hearts=bonus if bonus is not None else None,
        bonus_limit=limit if limit is not None else None,
    )
    log = AdminActionLog(
        admin_id=admin_id,
        action="referral_settings_update",
        details=f"bonus={s.bonus_hearts} limit={s.bonus_limit}",
    )
    session.add(log)
    await session.commit()
    return {
        "success": True,
        "message": "Sozlamalar saqlandi",
        "settings": {
            "bonus_hearts": int(s.bonus_hearts),
            "bonus_limit": int(s.bonus_limit),
        },
    }


@router.get("/api/admin/referral/partners")
async def list_referral_partners(
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    repo = ReferralRepository(session)
    partners = await repo.list_partners()
    items = []
    for p in partners:
        daily = await repo.get_daily_earned(p.user_id)
        items.append(_serialize_partner(p, daily_earned=daily))
    return {"success": True, "partners": items}


@router.post("/api/admin/referral/partners")
async def create_referral_partner(
    data: dict = Body(...),
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    user_repo = UserRepository(session)
    ref_repo = ReferralRepository(session)
    query = (data.get("user_query") or data.get("user_id") or "").strip()
    if not query:
        return JSONResponse(
            {"success": False, "message": "Foydalanuvchi ID yoki username kerak"},
            status_code=400,
        )
    user = None
    try:
        user = await user_repo.get_user_by_id(int(query))
    except (TypeError, ValueError):
        user = await user_repo.get_user_by_login_or_id(query.lstrip("@"))
    if not user:
        return JSONResponse(
            {"success": False, "message": "Foydalanuvchi topilmadi"},
            status_code=404,
        )
    partner_code = (data.get("partner_id") or "").strip() or None
    try:
        invited_bonus = int(data.get("invited_bonus", 50))
        bonus_limit = int(data.get("bonus_limit", 10000))
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "message": "Bonus va limit butun son bo'lishi kerak"},
            status_code=400,
        )
    try:
        partner = await ref_repo.create_partner(
            user_id=user.id,
            partner_id=partner_code,
            invited_bonus=invited_bonus,
            bonus_limit=bonus_limit,
        )
    except ValueError as e:
        return JSONResponse(
            {"success": False, "message": str(e)},
            status_code=400,
        )
    log = AdminActionLog(
        admin_id=admin_id,
        action="partner_create",
        details=f"user={user.id} code={partner.partner_id}",
    )
    session.add(log)
    await session.commit()
    partner = await ref_repo.get_partner_by_user_id(user.id) or partner
    daily = await ref_repo.get_daily_earned(partner.user_id)
    return {
        "success": True,
        "message": "Hamkor saqlandi",
        "partner": _serialize_partner(partner, daily_earned=daily),
    }


@router.patch("/api/admin/referral/partners/{partner_pk}")
async def patch_referral_partner(
    partner_pk: int,
    data: dict = Body(...),
    session: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin),
):
    ref_repo = ReferralRepository(session)
    kwargs = {}
    if "invited_bonus" in data:
        try:
            kwargs["invited_bonus"] = int(data["invited_bonus"])
        except (TypeError, ValueError):
            return JSONResponse(
                {"success": False, "message": "invited_bonus butun son"},
                status_code=400,
            )
    if "bonus_limit" in data:
        try:
            kwargs["bonus_limit"] = int(data["bonus_limit"])
        except (TypeError, ValueError):
            return JSONResponse(
                {"success": False, "message": "bonus_limit butun son"},
                status_code=400,
            )
    if "is_active" in data:
        kwargs["is_active"] = bool(data["is_active"])
    if not kwargs:
        return JSONResponse(
            {"success": False, "message": "Yangilash uchun maydon yo'q"},
            status_code=400,
        )
    partner = await ref_repo.update_partner(partner_pk, **kwargs)
    if not partner:
        return JSONResponse(
            {"success": False, "message": "Hamkor topilmadi"},
            status_code=404,
        )
    await session.refresh(partner, ["user"])
    log = AdminActionLog(
        admin_id=admin_id,
        action="partner_update",
        details=f"id={partner_pk} {kwargs}",
    )
    session.add(log)
    await session.commit()
    daily = await ref_repo.get_daily_earned(partner.user_id)
    return {
        "success": True,
        "partner": _serialize_partner(partner, daily_earned=daily),
    }

from pathlib import Path
from fastapi import APIRouter, Request, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from sqlalchemy.orm import selectinload
from src.app.api.deps import get_db
from src.app.core.jwt import verify_access_token
from src.app.database.repositories.user import UserRepository
from src.app.database.models import User, AdminActionLog, BroadcastMessage, Wallet, Admins

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
    return {
        "success": True,
        "user": {
            "id": user.id,
            "username": user.username or user.login or "Noma'lum",
            "complaints": user.number_of_complaints,
            "is_banned": user.is_banned,
            "gender": user.gender,
            "country": user.country,
            "avatar": user.avatar_url or "/photos/no_img.png"
        }
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
    """O'yinda ko'rinadigan balans: `Wallet.stars` (token/coin), `Wallet.hearts` (gold/yurak)."""
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

    currency = (data.get("currency") or "stars").strip().lower()
    if currency not in ("stars", "hearts"):
        currency = "stars"

    if amount <= 0:
        return JSONResponse(
            {"success": False, "message": "Miqdor musbat butun son bo'lishi kerak"},
            status_code=400,
        )

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(target_id)
    if not user:
        return JSONResponse({"success": False, "message": "User topilmadi"}, status_code=404)

    if not user.wallet:
        user.wallet = Wallet(user_id=user.id)
        session.add(user.wallet)
        await session.flush()

    if currency == "stars":
        # WS o'yini `Wallet.stars`; frames/load-assets esa `gm_coin` uchun `stars_coin` beradi — ikkalasini sinxron tutamiz.
        user.wallet.stars = int(user.wallet.stars or 0) + amount
        user.wallet.stars_coin = int(user.wallet.stars_coin or 0) + amount
    else:
        user.wallet.hearts = int(user.wallet.hearts or 0) + amount

    log = AdminActionLog(
        admin_id=admin_id,
        target_user_id=target_id,
        action=f"add_{currency}",
        amount=amount,
        details=f"Admin {admin_id} added {amount} {currency} to user {target_id}",
    )
    session.add(log)

    await session.commit()
    label = "yulduz (token)" if currency == "stars" else "yurak (gold)"
    return {"success": True, "message": f"+{amount} {label} qo'shildi"}

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

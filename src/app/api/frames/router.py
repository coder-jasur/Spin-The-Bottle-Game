import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_db

router = APIRouter(tags=["Frames"])


def _frames_jwt_payload(request: Request) -> dict | None:
    """Cookie yoki Authorization Bearer orqali JWT; legacy `["id"]` cookie."""
    from src.app.core.jwt import verify_access_token

    token = request.cookies.get("device_user_ids")
    if not token:
        auth = request.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    payload = verify_access_token(token) if token else None
    if not payload and token:
        try:
            import json
            import urllib.parse

            decoded = urllib.parse.unquote(token)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if isinstance(ids, list) and len(ids) > 0:
                    payload = {"id": int(ids[0])}
        except Exception:
            pass
    return payload


class EncryptedPayload(BaseModel):
    encrypted: str
    iv: str


@router.api_route("/api/frames/load-assets", methods=["GET", "POST"])
@router.api_route("/frames/load-assets", methods=["GET", "POST"])
async def load_assets(
    request: Request,
    data: Optional[EncryptedPayload] = None,
    session: AsyncSession = Depends(get_db),
):
    print(f">>> LOAD ASSETS BOSHLANDI", flush=True)
    from src.app.database.repositories.user import UserRepository

    token = request.cookies.get("device_user_ids")
    print(f">>> LOAD ASSETS TOKEN: {token}", flush=True)
    payload = _frames_jwt_payload(request)

    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_repo = UserRepository(session)
    user_id = payload.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = await user_repo.get_user_by_id(user_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    from src.app.api.ws.constants import ADMIN_DISPLAY_STARS

    is_admin = await user_repo.is_admin(int(user_id))

    print(f">>> LOAD ASSETS: User found {user.username}", flush=True)

    # Bazadan ma'lumotlarni olish
    reg_date = "01.01.2024"
    if user.created_at:
        try:
            # Frontend DD.MM.YYYY formatini kutyapti
            reg_date = user.created_at.strftime("%d.%m.%Y")
        except Exception:
            pass

    # Davlat kodini qisqartirish (Uzbekistan -> UZ)
    country_code = user.country
    if country_code == "Uzbekistan":
        country_code = "UZ"
    elif not country_code:
        country_code = "UZ"

    w = user.wallet
    if is_admin and w:
        floor = ADMIN_DISPLAY_STARS
        dirty = False
        if int(w.stars_coin or 0) < floor:
            w.stars_coin = floor
            dirty = True
        if int(w.gift_tokens or 0) < floor:
            w.gift_tokens = floor
            dirty = True
        if dirty:
            await session.commit()

    gift_t = int(w.gift_tokens or 0) if w else 0
    gm_coin_val = int(w.stars_coin or 0) if w else 0
    balance_tokens = gm_coin_val
    if is_admin:
        gift_t = max(gift_t, ADMIN_DISPLAY_STARS)
        gm_coin_val = max(gm_coin_val, ADMIN_DISPLAY_STARS)
        balance_tokens = gm_coin_val

    payload = {
        "success": True,
        "message": "Məlumat yükləndi",
        "ban": 1 if user.is_banned else 0,
        "balance": balance_tokens,
        "gift_tokens": gift_t,
        "gm_coin": gm_coin_val,
        "tokens": balance_tokens,
        "site": None,
        "chat_id": user.chat_id or None,
        "password_base64": None,
        "hasViewedDailyMessage": None, # Frontend null kutyapti
        "country": country_code,
        "registrationDate": reg_date,
    }
    return payload


@router.get("/api/frames/recent-statuses")
@router.get("/frames/recent-statuses")
@router.get("/api/frames/all-statuses")
@router.get("/frames/all-statuses")
async def recent_statuses(request: Request, session: AsyncSession = Depends(get_db)):
    from src.app.database.repositories.story import StoryRepository

    story_repo = StoryRepository(session)
    stories = await story_repo.get_active_stories()

    # Bazaviy URL'ni so'rovdan (request) dinamik aniqlaymiz
    base_url = f"{request.url.scheme}://{request.url.netloc}"

    # Tokendan userni aniqlaymiz (layk bosgan-bosmaganini bilish uchun)
    from src.app.core.jwt import verify_access_token

    token = request.cookies.get("device_user_ids")
    authorization = request.headers.get("Authorization")
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")
    payload = verify_access_token(token) if token else None
    current_user_id = payload.get("id") if payload else None

    statuses = []
    for s in stories:
        is_liked = False
        if current_user_id:
            for like in s.likes:
                if like.user_id == current_user_id:
                    is_liked = True
                    break

        statuses.append(
            {
                "id": s.id,
                "userId": s.user_id,
                "username": s.user.username
                or s.user.display_name
                or f"user_{s.user.id}",
                "profilePicture": s.user.avatar_url or "/photos/no_img.png",
                "mediaType": s.media_type,
                "mediaUrl": s.media_url,
                "text": s.caption or "",
                "views": len(s.views),
                "createdAt": s.created_at.isoformat() + "Z",
                "reactionCount": len(s.likes),
                "isLiked": is_liked,
            }
        )

    return {"success": True, "statuses": statuses}


@router.get("/api/frames/recent-text-statuses")
@router.get("/frames/recent-text-statuses")
async def recent_text_statuses(session: AsyncSession = Depends(get_db)):
    from src.app.database.models import BroadcastMessage
    from sqlalchemy import select, desc
    from sqlalchemy.orm import selectinload
    
    # Admin broadcastlarini bazadan olamiz (admin ma'lumotlari bilan birga)
    stmt = (
        select(BroadcastMessage)
        .options(selectinload(BroadcastMessage.admin))
        .order_by(desc(BroadcastMessage.id))
        .limit(20)
    )
    result = await session.execute(stmt)
    broadcasts = result.scalars().all()
    
    statuses = []
    for b in broadcasts:
        admin_name = "ADMIN"
        if b.admin:
            admin_name = b.admin.username or b.admin.login or f"admin_{b.admin_id}"
            
        statuses.append({
            "id": b.id,
            "userId": b.admin_id,
            "text": b.text,
            "createdAt": b.created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "gameUsername": admin_name,
        })
        
    # Agar bazada xabar bo'lmasa, bo'sh ro'yxat yoki statik ma'lumot qaytaramiz
    if not statuses:
        statuses = [
            {
                "id": 2895,
                "userId": 0,
                "text": "Xush kelibsiz!",
                "createdAt": "2026-05-08T03:46:38.000Z",
                "gameUsername": "Spin bottle",
            }
        ]
        
    return {
        "success": True,
        "statuses": statuses,
    }


def _horoscope_zodiac_sprite_position(index0: int) -> str:
    """Welcome /horoscope klienti `backgroundPosition` kutadi (12 belgi, 8+4 sprite taxmini)."""
    col = index0 if index0 < 8 else index0 - 8
    row = 0 if index0 < 8 else 1
    return f"{-col * 34}px {-row * 55}px"


def _horoscope_zodiac_entries() -> list[dict]:
    """Kunlik va umumiy burçlar — `description` + `position` klient JSX bilan mos."""
    rows = [
        ("Aries", "aries", "Your energy is peaking today, perfect for new beginnings!", "/images/zodiacs/aries.png"),
        ("Taurus", "taurus", "A great day for financial planning and stability.", "/images/zodiacs/taurus.png"),
        ("Gemini", "gemini", "Your communication skills will open new doors today.", "/images/zodiacs/gemini.png"),
        ("Cancer", "cancer", "Focus on your inner peace and family connections.", "/images/zodiacs/cancer.png"),
        ("Leo", "leo", "Your natural leadership will shine in social situations.", "/images/zodiacs/leo.png"),
        ("Virgo", "virgo", "Attention to detail will lead to significant progress.", "/images/zodiacs/virgo.png"),
        ("Libra", "libra", "Balance and harmony are your best allies today.", "/images/zodiacs/libra.png"),
        ("Scorpio", "scorpio", "Trust your intuition, it is particularly sharp today.", "/images/zodiacs/scorpio.png"),
        ("Sagittarius", "sagittarius", "Adventure is calling, be open to new experiences.", "/images/zodiacs/sagittarius.png"),
        ("Capricorn", "capricorn", "Your hard work and discipline will be recognized.", "/images/zodiacs/capricorn.png"),
        ("Aquarius", "aquarius", "Your innovative ideas will inspire those around you.", "/images/zodiacs/aquarius.png"),
        ("Pisces", "pisces", "Your creativity and imagination know no bounds today.", "/images/zodiacs/pisces.png"),
    ]
    zodiacs: list[dict] = []
    for i, (name, slug, comment, image) in enumerate(rows):
        zodiacs.append(
            {
                "id": i + 1,
                "name": name,
                "slug": slug,
                "comment": comment,
                "description": comment,
                "image": image,
                "position": _horoscope_zodiac_sprite_position(i),
            }
        )
    return zodiacs


@router.get("/api/frames/daily-horoscopes")
@router.get("/frames/daily-horoscopes")
async def daily_horoscopes():
    return {"success": True, "zodiacs": _horoscope_zodiac_entries()}


@router.get("/api/frames/general-horoscopes")
@router.get("/frames/general-horoscopes")
async def general_horoscopes():
    """Klient «Umumiy» tabi uchun; avval yo'q bo'lgani uchun bo'sh / xato holat bo'lardi."""
    return {"success": True, "zodiacs": _horoscope_zodiac_entries()}


@router.get("/api/frames/payment-rankings")
@router.get("/frames/payment-rankings")
async def payment_rankings():
    return {
        "success": True,
        "rankings": {
            "payments": [],
            "lastMonthTopSpender": None,
            "timeRemaining": "9 gün 11 saat",
            "lastMonthPeriod": {
                "startDatetime": "2026-04-26 00:00:00",
                "endDatetime": "2026-05-05 23:59:59",
            },
        },
    }


# ────────────────────────────────────────────────────────────────────────────
# Welcome client uchun reyting jadvali (frontda RatingPanel.tsx so'roviga javob).
# Klient `category=kiss|smile|music|mod|like_price`,
#         `period=total|monthly|daily|all` parametri bilan keladi va
# `rankings: { <category>: { daily: [...], monthly: [...], total: [...], userRank: {...} } }`
# shaklini kutadi.
# ────────────────────────────────────────────────────────────────────────────
_WELCOME_CATEGORY_COLUMN_MAP = {
    "kiss":       "kisses",
    "kisses":     "kisses",
    "music":      "dj",
    "smile":      "emotion",
    "gestures":   "emotion",
    "like_price": "harem_price",
    "harem_price": "harem_price",
    # «Самые дорогие» = uxajor narxi (sovg'a expense emas)
    "price":      "harem_price",
}


def _legacy_user_id_from_token(raw: str | None) -> int | None:
    """Cookie ichidagi `device_user_ids` ni JWT yoki ["123"] formatdan parse qiladi."""
    if not raw:
        return None
    from src.app.core.jwt import verify_access_token

    payload = verify_access_token(raw)
    if payload and payload.get("id"):
        try:
            return int(payload["id"])
        except (TypeError, ValueError):
            return None
    try:
        import json
        import urllib.parse

        decoded = urllib.parse.unquote(raw)
        if decoded.startswith("[") and decoded.endswith("]"):
            ids = json.loads(decoded)
            if ids and isinstance(ids, list):
                return int(ids[0])
    except Exception:
        return None
    return None


@router.get("/api/frames/rankings")
@router.get("/frames/rankings")
async def frames_rankings(
    request: Request,
    category: str = "kiss",
    period: str = "total",
    session: AsyncSession = Depends(get_db),
):
    """Welcome klient RatingPanel uchun reyting."""
    from sqlalchemy import desc, func as sa_func, select

    from src.app.database.models.user import User
    from src.app.database.models.stats import UserStats

    cat = (category or "kiss").lower()
    per = (period or "total").lower()
    column_name = _WELCOME_CATEGORY_COLUMN_MAP.get(cat)

    media_prefix = "/photos/"
    empty_payload = {
        "success": True,
        "rankings": {
            cat: {
                "daily": [],
                "monthly": [],
                "total": [],
                "userRank": None,
            }
        },
    }

    # Maxsus tab: moderatorlar ro'yxati (Mod tab).
    if cat == "mod":
        try:
            stmt = (
                select(User.id, User.display_name, User.username, User.avatar_url)
                .where(User.is_verified == True)  # noqa: E712
                .limit(50)
            )
            rows = (await session.execute(stmt)).all()
            mods = [
                {
                    "user_id":         int(r.id),
                    "username":        r.display_name or r.username or f"user_{r.id}",
                    "profile_picture": r.avatar_url or f"{media_prefix}no_img.png",
                    "rank":            i + 1,
                    "count":           0,
                }
                for i, r in enumerate(rows)
            ]
            return {"success": True, "rankings": {"mod": mods}}
        except Exception as e:
            print(f">>> rankings(mod) error: {e}", flush=True)
            return {"success": True, "rankings": {"mod": []}}

    if not column_name:
        return empty_payload

    col = getattr(User, column_name)

    def _period_col(p: str):
        m = {
            "daily":   UserStats.daily_value,
            "weekly":  UserStats.weekly_value,
            "monthly": UserStats.monthly_value,
            "total":   UserStats.total_value,
            "all":     UserStats.total_value,
        }
        return m.get(p, UserStats.total_value)

    async def _top_for(p: str) -> list:
        """`daily/monthly/total` davr uchun top-50 ro'yxati."""
        items: list = []
        try:
            if p == "total":
                # Total uchun User ustuni — eng aniq.
                stmt = (
                    select(
                        User.id,
                        User.display_name,
                        User.username,
                        User.avatar_url,
                        col.label("score"),
                    )
                    .where(col > 0)
                    .order_by(desc(col))
                    .limit(50)
                )
                rows = (await session.execute(stmt)).all()
            else:
                # daily/monthly — UserStats kategoriyasiga qarab.
                pcol = _period_col(p)
                stmt = (
                    select(
                        User.id,
                        User.display_name,
                        User.username,
                        User.avatar_url,
                        pcol.label("score"),
                    )
                    .join(UserStats, User.id == UserStats.user_id)
                    .where(UserStats.category == column_name)
                    .where(pcol > 0)
                    .order_by(desc(pcol))
                    .limit(50)
                )
                rows = (await session.execute(stmt)).all()
            for i, r in enumerate(rows):
                items.append({
                    "user_id":         int(r.id),
                    "username":        r.display_name or r.username or f"user_{r.id}",
                    "profile_picture": r.avatar_url or f"{media_prefix}no_img.png",
                    "rank":            i + 1,
                    "count":           int(r.score or 0),
                })
        except Exception as e:
            print(f">>> rankings({cat}/{p}) error: {e}", flush=True)
        return items

    daily_list = await _top_for("daily")
    monthly_list = await _top_for("monthly")
    total_list = await _top_for("total")

    # Foydalanuvchining o'z reytingi (cookie orqali).
    user_rank_block: dict | None = None
    token = request.cookies.get("device_user_ids") or request.cookies.get(
        "accessToken"
    )
    uid = _legacy_user_id_from_token(token)
    if uid:
        try:
            user_row = (
                await session.execute(
                    select(
                        User.id,
                        User.display_name,
                        User.username,
                        User.avatar_url,
                        col.label("score"),
                    ).where(User.id == uid)
                )
            ).first()
            if user_row:
                async def _rank_count(p: str) -> dict:
                    if p == "total":
                        higher = (
                            await session.execute(
                                select(sa_func.count()).where(col > (user_row.score or 0))
                            )
                        ).scalar() or 0
                        return {
                            "rank":  int(higher) + 1 if user_row.score else 0,
                            "count": int(user_row.score or 0),
                        }
                    pcol = _period_col(p)
                    row = (
                        await session.execute(
                            select(pcol.label("score"))
                            .where(UserStats.user_id == uid)
                            .where(UserStats.category == column_name)
                        )
                    ).first()
                    score = int(row.score) if row and row.score else 0
                    if not score:
                        return {"rank": 0, "count": 0}
                    higher = (
                        await session.execute(
                            select(sa_func.count())
                            .where(UserStats.category == column_name)
                            .where(pcol > score)
                        )
                    ).scalar() or 0
                    return {"rank": int(higher) + 1, "count": score}

                user_rank_block = {
                    "user_id":         int(user_row.id),
                    "username":        user_row.display_name
                                       or user_row.username
                                       or f"user_{user_row.id}",
                    "profile_picture": user_row.avatar_url
                                       or f"{media_prefix}no_img.png",
                    "daily":   await _rank_count("daily"),
                    "monthly": await _rank_count("monthly"),
                    "total":   await _rank_count("total"),
                }
        except Exception as e:
            print(f">>> rankings(userRank) error: {e}", flush=True)

    return {
        "success": True,
        "rankings": {
            cat: {
                "daily":    daily_list,
                "monthly":  monthly_list,
                "total":    total_list,
                "userRank": user_rank_block,
            }
        },
    }


@router.get("/api/frames/referrals")
@router.get("/frames/referrals")
async def referrals(request: Request, session: AsyncSession = Depends(get_db)):
    from src.app.core.jwt import verify_access_token
    from src.app.database.repositories.user import UserRepository

    token = request.cookies.get("device_user_ids")
    print(f">>> REFERRALS DEBUG: Raw Token from cookie: {token}", flush=True)
    
    payload = verify_access_token(token) if token else None

    # Agar JWT emas bo'lsa, legacy formatni tekshiramiz
    if not payload and token:
        try:
            import json
            import urllib.parse
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if ids and isinstance(ids, list):
                    payload = {"id": int(ids[0])}
                    print(f">>> REFERRALS DEBUG: Legacy user ID found: {payload['id']}", flush=True)
        except:
            print(">>> REFERRALS DEBUG: Token JWT emas va legacy formatda ham emas", flush=True)

    count = 0
    referral_id = None
    if payload and payload.get("id"):
        user_repo = UserRepository(session)
        user = await user_repo.get_user_by_id(payload.get("id"))
        if user:
            count = user.invited_guests
            referral_id = user.referral_id
            print(f">>> REFERRALS DEBUG: Found user {user.id}, invited_guests: {count}, referral_id: {referral_id}", flush=True)
        else:
            print(f">>> REFERRALS DEBUG: User ID {payload.get('id')} database dan topilmadi", flush=True)
    else:
        print(">>> REFERRALS DEBUG: User ID aniqlanmadi (token xato yoki yo'q)", flush=True)

    return {"success": True, "count": count, "referrals": [], "referral_id": referral_id}


@router.post("/api/frames/increment-status-view")
@router.post("/frames/increment-status-view")
async def increment_status_view(
    request: Request, session: AsyncSession = Depends(get_db)
):
    # Bu funksiya xato bermasligi uchun try-except ichida ishlaydi
    try:
        # 1. TOKENNI TEKSHIRISH
        from src.app.core.jwt import verify_access_token

        token = request.cookies.get("device_user_ids")
        authorization = request.headers.get("Authorization")
        if not token and authorization and authorization.startswith("Bearer "):
            token = authorization.replace("Bearer ", "")

        payload = verify_access_token(token) if token else None

        # FALLBACK: Agar token JWT bo'lmasa, ["1"] formatini tekshiramiz
        if not payload and token:
            try:
                import json
                import urllib.parse

                decoded = urllib.parse.unquote(token)
                if decoded.startswith("[") and decoded.endswith("]"):
                    ids = json.loads(decoded)
                    if isinstance(ids, list) and len(ids) > 0:
                        user_id = int(ids[0])
                        payload = {"id": user_id}
                        print(
                            f">>> VIEW FALLBACK: User ID {user_id} found in legacy cookie",
                            flush=True,
                        )
            except:
                pass

        if not payload:
            raise HTTPException(status_code=401, detail="Unauthorized")

        # 2. MA'LUMOTNI OLISH
        stories_id = None
        viewer_id = None

        try:
            body = await request.json()
            print(f">>> DEBUG VIEW: Body: {body}", flush=True)
            stories_id = (
                body.get("statusId")
                or body.get("status_id")
                or body.get("stories_Id")
                or body.get("storiesId")
                or body.get("stories_id")
                or body.get("story_id")
            )
            viewer_id = (
                body.get("userId") or body.get("user_id") or body.get("viewerId")
            )
        except:
            print(f">>> DEBUG VIEW: Body error or empty", flush=True)
            pass

        if not stories_id:
            stories_id = (
                request.query_params.get("statusId")
                or request.query_params.get("status_id")
                or request.query_params.get("stories_Id")
                or request.query_params.get("stories_id")
                or request.query_params.get("story_id")
            )

        if not viewer_id:
            viewer_id = (
                request.query_params.get("userId")
                or request.query_params.get("user_id")
                or request.query_params.get("viewerId")
            )

        # Yakuniy user_id ni aniqlash (Token ustun, agar u bo'lmasa Payload dagi ID)
        final_viewer_id = (payload.get("id") if payload else None) or viewer_id

        print(
            f">>> DEBUG VIEW: Final extraction - StoryID: {stories_id}, ViewerID: {final_viewer_id}",
            flush=True,
        )

        # 3. BAZAGA YOZISH (faqat ID bo'lsa)
        if stories_id and final_viewer_id:
            from src.app.database.repositories.story import StoryRepository

            story_repo = StoryRepository(session)
            await story_repo.add_view(int(stories_id), int(final_viewer_id))

    except Exception as e:
        print(f">>> View increment error (ignored): {e}")

    # Har qanday holatda ham success qaytaradi (422 ni oldini olish uchun)
    return {"success": True, "message": "Görüntülenme kaydedildi"}


@router.post("/api/frames/add-reaction")
@router.post("/frames/add-reaction")
async def add_reaction(request: Request, session: AsyncSession = Depends(get_db)):
    try:
        from src.app.core.jwt import verify_access_token

        token = request.cookies.get("device_user_ids")
        authorization = request.headers.get("Authorization")
        if not token and authorization and authorization.startswith("Bearer "):
            token = authorization.replace("Bearer ", "")

        payload = verify_access_token(token) if token else None

        # FALLBACK: ["user_id"] formatini tekshirish
        if not payload and token:
            try:
                import json
                import urllib.parse

                decoded = urllib.parse.unquote(token)
                if decoded.startswith("[") and decoded.endswith("]"):
                    ids = json.loads(decoded)
                    if isinstance(ids, list) and len(ids) > 0:
                        payload = {"id": int(ids[0])}
            except:
                pass

        if not payload:
            raise HTTPException(status_code=401, detail="Unauthorized")

        body = await request.json()
        print(f">>> DEBUG REACTION: Body: {body}", flush=True)

        stories_id = (
            body.get("status_id")
            or body.get("statusId")
            or body.get("stories_Id")
            or body.get("stories_id")
        )
        viewer_id = payload.get("id")

        if stories_id and viewer_id:
            from src.app.database.repositories.story import StoryRepository

            story_repo = StoryRepository(session)
            is_liked = await story_repo.toggle_like(int(stories_id), int(viewer_id))

            # Yangi layklar sonini olamiz
            story = await story_repo.get_story_by_id(int(stories_id))
            new_count = len(story.likes) if story else 0

            return {
                "success": True,
                "is_liked": is_liked,
                "isLiked": is_liked,
                "reactionCount": new_count,
                "likes": new_count,
                "reaction_count": new_count,
                "message": "Reaction added/removed",
            }

    except Exception as e:
        print(f">>> Reaction error: {e}", flush=True)

    return {"success": True}


@router.get("/api/frames/check-user-status")
@router.get("/frames/check-user-status")
async def check_user_status(user_id: int, session: AsyncSession = Depends(get_db)):
    from src.app.database.repositories.story import StoryRepository

    try:
        story_repo = StoryRepository(session)
        # Hamma aktiv hikoyalarni olamiz
        active_stories = await story_repo.get_active_stories()

        # O'sha ID li foydalanuvchida aktiv hikoya bormi?
        has_story = any(story.user_id == user_id for story in active_stories)

        # Frontend negadir "is_moderator" maydoni orqali hikoyasi bor-yo'qligini tushunadi :)
        return {"success": True, "is_moderator": has_story}
    except Exception as e:
        print(f">>> Error checking user status: {e}")
        return {"success": True, "is_moderator": False}


@router.get("/api/frames/status-viewers/{status_id}")
@router.get("/frames/status-viewers/{status_id}")
async def status_viewers(
    status_id: int, request: Request, session: AsyncSession = Depends(get_db)
):
    from src.app.database.repositories.story import StoryRepository

    try:
        story_repo = StoryRepository(session)
        story = await story_repo.get_story_by_id(status_id)

        viewers_list = []
        if story:
            liked_user_ids = {like.user_id for like in story.likes}
            for v in story.views:
                viewer_id = v.user.id if v.user else v.user_id
                viewers_list.append(
                    {
                        "id": viewer_id,
                        "username": (v.user.username or v.user.display_name) if v.user else f"user_{v.user_id}",
                        "profilePicture": (
                            v.user.avatar_url if (v.user and v.user.avatar_url) else "/photos/no_img.png"
                        ),
                        "viewedAt": v.created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "createdAt": v.created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "date": v.created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "reactedAt": v.created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "isLiked": viewer_id in liked_user_ids,
                        "hasLiked": viewer_id in liked_user_ids,
                        "reaction": viewer_id in liked_user_ids,
                    }
                )

        return {"success": True, "viewers": viewers_list}
    except Exception as e:
        print(f">>> Error fetching viewers: {e}")
        return {"success": False, "viewers": []}


@router.get("/api/frames/status-reactions/{status_id}")
@router.get("/frames/status-reactions/{status_id}")
async def status_reactions(
    status_id: int, request: Request, session: AsyncSession = Depends(get_db)
):
    from src.app.database.repositories.story import StoryRepository

    try:
        story_repo = StoryRepository(session)
        story = await story_repo.get_story_by_id(status_id)

        reactions_list = []
        if story:
            for like in story.likes:
                reactions_list.append(
                    {
                        "id": like.user.id if like.user else like.user_id,
                        "username": (like.user.username or like.user.display_name) if like.user else f"user_{like.user_id}",
                        "profilePicture": (
                            like.user.avatar_url if (like.user and like.user.avatar_url) else "/photos/no_img.png"
                        ),
                        "reactionAt": like.created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "createdAt": like.created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "date": like.created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "reactedAt": like.created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "isLiked": True,
                        "hasLiked": True,
                        "reaction": True
                    }
                )

        return {"success": True, "reactions": reactions_list, "viewers": reactions_list, "likes": reactions_list}
    except Exception as e:
        print(f">>> Error fetching reactions: {e}")
        return {"success": False, "reactions": []}


@router.post("/api/frames/delete-status")
@router.post("/frames/delete-status")
async def delete_status(request: Request, session: AsyncSession = Depends(get_db)):
    from src.app.core.jwt import verify_access_token
    from src.app.database.repositories.story import StoryRepository

    try:
        token = request.cookies.get("device_user_ids")
        authorization = request.headers.get("Authorization")
        if not token and authorization and authorization.startswith("Bearer "):
            token = authorization.replace("Bearer ", "")

        payload = verify_access_token(token) if token else None

        # FALLBACK
        if not payload and token:
            try:
                import json
                import urllib.parse

                decoded = urllib.parse.unquote(token)
                if decoded.startswith("[") and decoded.endswith("]"):
                    ids = json.loads(decoded)
                    if isinstance(ids, list) and len(ids) > 0:
                        payload = {"id": int(ids[0])}
            except:
                pass

        if not payload:
            raise HTTPException(status_code=401, detail="Unauthorized")

        body = await request.json()
        status_id = (
            body.get("statusId") or body.get("status_id") or body.get("stories_id")
        )
        user_id = payload.get("id")

        if status_id and user_id:
            story_repo = StoryRepository(session)
            success = await story_repo.delete_story(int(status_id), int(user_id))
            return {"success": success, "message": "Status deleted"}

        return {"success": False, "message": "Invalid parameters"}
    except Exception as e:
        print(f">>> Error deleting status: {e}")
        return {"success": False, "message": str(e)}

@router.post("/api/frames/convert_jeton")
@router.post("/frames/convert_jeton")
async def convert_jeton(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """GM (stars_coin) → gift_tokens. Klient `success` + `data` kutadi."""
    from src.app.database.repositories.game import GameRepository
    from src.app.api.ws.constants import HEARTS_PACKAGES

    payload = _frames_jwt_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        body = await request.json()
        amount = int(body.get("amount", 0))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid amount")

    if amount not in HEARTS_PACKAGES:
        raise HTTPException(status_code=400, detail="Invalid package")

    jeton_delta = HEARTS_PACKAGES[amount]
    user_id = int(payload.get("id"))

    repo = GameRepository(session)
    await repo.ensure_wallet(user_id)
    ok, new_sc, new_gt = await repo.convert_stars_coin_to_gift_tokens(
        user_id, amount, jeton_delta
    )
    if not ok:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": f"Yetarli Stars. Sizda: {new_sc}",
                "data": {},
            },
        )

    return {
        "success": True,
        "data": {
            "STARS_coin": new_sc,
            "gift_tokens": new_gt,
            "converted_from": amount,
            "converted_to": jeton_delta,
        },
    }


@router.post("/api/frames/convert")
@router.post("/frames/convert")
async def frames_convert_stars_to_hearts(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Gift token → yurak. Klient `success` + `data` (STARS_coin, balance, …)."""
    from src.app.database.repositories.game import GameRepository
    from src.app.api.ws.constants import HEARTS_PACKAGES

    payload = _frames_jwt_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        body = await request.json()
        amount = int(body.get("amount", 0))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid amount")

    if amount not in HEARTS_PACKAGES:
        raise HTTPException(status_code=400, detail="Invalid package")

    hearts_delta = HEARTS_PACKAGES[amount]
    user_id = int(payload.get("id"))

    repo = GameRepository(session)
    await repo.ensure_wallet(user_id)
    ok, new_sc, new_gt, _ = await repo.purchase_hearts_with_gift_tokens(
        user_id, amount, hearts_delta
    )
    if not ok:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": f"Yetarli Stars yo'q. Sizda: {int(new_sc or 0)}",
                "data": {},
            },
        )

    w = await repo.get_wallet(user_id)
    gm = int(w.stars_coin or 0) if w else 0
    gt = int(w.gift_tokens or 0) if w else 0

    return {
        "success": True,
        "data": {
            "STARS_coin": gm,
            "balance": gt,
            "tokens": gm,
            "converted_from": amount,
            "converted_to": hearts_delta,
        },
    }


@router.post("/api/frames/purchase-vip")
@router.post("/frames/purchase-vip")
async def frames_purchase_vip(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    from src.app.database.repositories.game import GameRepository
    from src.app.database.repositories.user import UserRepository
    from src.app.api.ws.constants import (
        VIP_BONUS_STARS,
        VIP_PLAN_DAYS,
        VIP_PLAN_STARS,
    )

    payload = _frames_jwt_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    auth_uid = int(payload.get("id"))
    try:
        body = await request.json()
        body_uid = int(body.get("user_id") or 0)
        duration = int(body.get("duration") or 7)
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Noto'g'ri so'rov"},
        )

    if body_uid != auth_uid:
        return JSONResponse(status_code=403, content={"error": "Ruxsat yo'q"})

    plan = "week" if duration == 7 else "month"
    price = VIP_PLAN_STARS[plan]
    extend_days = VIP_PLAN_DAYS[plan]

    repo = GameRepository(session)
    await repo.ensure_wallet(auth_uid)

    try:
        ok, new_sc, new_gt = await repo.purchase_vip_with_gift_tokens(
            auth_uid,
            price,
            VIP_BONUS_STARS,
            extend_days,
        )
    except Exception as e:
        print(f">>> purchase-vip DB: {e}", flush=True)
        return JSONResponse(
            status_code=500,
            content={"error": "VIP sotib olishda server xatosi"},
        )

    if not ok:
        return JSONResponse(
            status_code=400,
            content={"error": f"VIP uchun {price} token kerak."},
        )

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(auth_uid)
    exp = user.vip_expires_at if user else None
    exp_s = exp.isoformat() + "Z" if exp else None

    return {
        "expiry_vip": exp_s,
        "newBalance": int(new_sc or 0),
        "stars_coin": int(new_sc or 0),
        "gift_tokens": int(new_gt or 0),
    }


@router.get("/api/frames/status")
@router.get("/frames/status")
async def frames_user_status(
    request: Request,
    user_id: int,
    session: AsyncSession = Depends(get_db),
):
    from src.app.database.repositories.user import UserRepository

    payload = _frames_jwt_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if int(user_id) != int(payload.get("id")):
        raise HTTPException(status_code=403, detail="Forbidden")

    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(int(user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now()
    exp = user.vip_expires_at
    is_vip = bool(exp and exp > now)
    role = await user_repo.get_admin_role(int(user_id))
    is_moderator = role == "moderator"
    has_ever = is_vip or exp is not None or bool(user.vip_payment_history)

    return {
        "is_vip": is_vip,
        "is_moderator": is_moderator,
        "vip_color": None,
        "has_ever_been_vip": has_ever,
        "vip_expiry": exp.isoformat() + "Z" if exp else None,
        "status": user.status_text or "",
    }


@router.post("/api/frames/purchase-moderator")
@router.post("/frames/purchase-moderator")
async def frames_purchase_moderator(request: Request):
    payload = _frames_jwt_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return JSONResponse(
        status_code=400,
        content={"error": "Moderator paketi hozircha mavjud emas."},
    )


# ── Musiqa: klient `/frames/search-music`, `/frames/all-gallery`, … chaqiradi;
# upstream: MUSIC_API_BASE (default bottle host) / api_music/*; mahalliy JSON faqat MUSIC_USE_LOCAL_JSON=1. ──

_SITE_DATA = Path(__file__).resolve().parents[2] / "site" / "data"
_USER_GALLERY_JSON = _SITE_DATA / "user_music_gallery.json"
_MUSIC_PAGE_SIZE = 12
_user_gallery_lock = asyncio.Lock()


async def _http_get_music_list(url: str, params: list[tuple[str, str]]) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            r = await client.get(url, params=params)
        if r.status_code >= 400:
            return []
        data = r.json()
    except Exception:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return [x for x in data["results"] if isinstance(x, dict)]
    return []


def _row_video_id(row: dict[str, Any]) -> str:
    return str(row.get("video_id") or row.get("id") or "")


async def _popular_source_rows(count: int, user_country: str, mtype: str | None, platform: str | None) -> list[dict[str, Any]]:
    from src.app.api.music.router import (
        _FALLBACK_POPULAR,
        _LOCAL_POPULAR,
        _load_local_list,
        _upstream_popular,
    )

    n = min(200, max(1, count))
    local = _load_local_list(_LOCAL_POPULAR)
    if local is not None:
        return local[:n]
    pairs: list[tuple[str, str]] = [
        ("count", str(n)),
        ("user_country", (user_country or "UZ").strip() or "UZ"),
    ]
    if mtype:
        pairs.append(("type", mtype))
    if platform:
        pairs.append(("platform", platform))
    rows = await _http_get_music_list(_upstream_popular(), pairs)
    if rows:
        return rows[:n]
    return list(_FALLBACK_POPULAR)[:n]


async def _by_ids_source_rows(count: int) -> list[dict[str, Any]]:
    from src.app.api.music.router import (
        _FALLBACK_BY_IDS,
        _LOCAL_GET_BY_IDS,
        _load_local_list,
        _upstream_get_by_ids,
    )

    n = min(200, max(1, count))
    local = _load_local_list(_LOCAL_GET_BY_IDS)
    if local is not None:
        return local[:n]
    rows = await _http_get_music_list(_upstream_get_by_ids(), [("count", str(n))])
    if rows:
        return rows[:n]
    return list(_FALLBACK_BY_IDS)[:n]


def _map_search_item(row: dict[str, Any]) -> dict[str, Any]:
    vid = _row_video_id(row)
    thumb = row.get("thumbnail") or row.get("icon") or (
        f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else ""
    )
    return {
        "video_id": vid,
        "title": str(row.get("title") or ""),
        "channel": str(row.get("channel") or row.get("artist") or "YouTube"),
        "duration": int(row.get("duration") or 0),
        "thumbnail": str(thumb),
    }


def _map_all_gallery_item(row: dict[str, Any]) -> dict[str, Any]:
    vid = _row_video_id(row)
    thumb = row.get("thumbnail") or row.get("icon") or (
        f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else ""
    )
    rid = str(row.get("id") or vid)
    return {
        "id": rid,
        "video_id": vid,
        "title": str(row.get("title") or ""),
        "artist": str(row.get("artist") or row.get("channel") or ""),
        "duration": int(row.get("duration") or 0),
        "thumbnail": str(thumb),
    }


def _filter_rows_text(rows: list[dict[str, Any]], needle: str) -> list[dict[str, Any]]:
    q = needle.strip().lower()
    if not q:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        blob = " ".join(
            str(row.get(k) or "")
            for k in ("title", "artist", "channel", "id", "video_id")
        ).lower()
        if q in blob:
            out.append(row)
    return out


def _load_user_gallery_store_sync() -> dict[str, list[dict[str, Any]]]:
    if not _USER_GALLERY_JSON.is_file():
        return {}
    try:
        raw = json.loads(_USER_GALLERY_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for uid, items in raw.items():
        if isinstance(items, list):
            out[str(uid)] = [x for x in items if isinstance(x, dict)]
    return out


def _save_user_gallery_store_sync(data: dict[str, list[dict[str, Any]]]) -> None:
    _USER_GALLERY_JSON.parent.mkdir(parents=True, exist_ok=True)
    _USER_GALLERY_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@router.get("/api/frames/search-music")
@router.get("/frames/search-music")
async def frames_search_music(
    name: str = Query("", alias="name"),
    user_country: str = Query("UZ"),
    type: str | None = Query(None, alias="type"),
    count: int = Query(48, ge=1, le=200),
    platform: str | None = Query(None, alias="platform"),
):
    """Bo‘sh `name` — mashhur (movie) ro‘yxat; to‘ldirilgan bo‘lsa shu ro‘yxatdan filtr."""
    rows = await _popular_source_rows(count, user_country, type, platform)
    rows = _filter_rows_text(rows, name)
    return {"success": True, "results": [_map_search_item(r) for r in rows[:count]]}


@router.get("/api/frames/all-gallery")
@router.get("/frames/all-gallery")
async def frames_all_gallery(
    page: int = Query(1, ge=1),
    query: str = Query("", alias="query"),
):
    rows = await _by_ids_source_rows(120)
    rows = _filter_rows_text(rows, query)
    mapped = [_map_all_gallery_item(r) for r in rows]
    total_pages = max(1, (len(mapped) + _MUSIC_PAGE_SIZE - 1) // _MUSIC_PAGE_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * _MUSIC_PAGE_SIZE
    slice_ = mapped[start : start + _MUSIC_PAGE_SIZE]
    return {"success": True, "results": slice_, "totalPages": total_pages}


@router.get("/api/frames/user-gallery")
@router.get("/frames/user-gallery")
async def frames_user_gallery(
    request: Request,
    user_id: str = Query(..., alias="user_id"),
    page: int = Query(1, ge=1),
    query: str = Query("", alias="query"),
):
    payload = _frames_jwt_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if str(payload.get("id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    def _read() -> dict[str, list[dict[str, Any]]]:
        return _load_user_gallery_store_sync()

    store = await asyncio.to_thread(_read)
    items = list(store.get(str(user_id), []))

    def _norm(it: dict[str, Any]) -> dict[str, Any]:
        vid = str(it.get("video_id") or it.get("id") or "")
        return {
            "id": str(it.get("id") or vid),
            "video_id": vid,
            "title": str(it.get("title") or ""),
            "artist": str(it.get("artist") or "YouTube"),
            "duration": int(it.get("duration") or 0),
            "thumbnail": str(
                it.get("thumbnail")
                or (f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else "")
            ),
        }

    mapped = [_norm(x) for x in items]
    mapped = _filter_rows_text(mapped, query)
    total_pages = max(1, (len(mapped) + _MUSIC_PAGE_SIZE - 1) // _MUSIC_PAGE_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * _MUSIC_PAGE_SIZE
    return {
        "success": True,
        "results": mapped[start : start + _MUSIC_PAGE_SIZE],
        "totalPages": total_pages,
    }


class AddMp3Body(BaseModel):
    video_id: str
    title: str
    duration: int = 0
    user_id: str


class RemoveGalleryBody(BaseModel):
    user_id: str
    video_id: str


@router.post("/api/frames/add-mp3")
@router.post("/frames/add-mp3")
async def frames_add_mp3(request: Request, body: AddMp3Body):
    payload = _frames_jwt_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if str(payload.get("id")) != str(body.user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    vid = (body.video_id or "").strip()
    if not vid:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "video_id kerak."},
        )

    async with _user_gallery_lock:

        def _add() -> None:
            store = _load_user_gallery_store_sync()
            uid = str(body.user_id)
            cur = list(store.get(uid, []))
            if any(str(x.get("video_id") or x.get("id")) == vid for x in cur):
                return
            item = {
                "id": vid,
                "video_id": vid,
                "title": body.title or "",
                "duration": int(body.duration or 0),
                "artist": "YouTube",
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            }
            cur.insert(0, item)
            store[uid] = cur
            _save_user_gallery_store_sync(store)

        await asyncio.to_thread(_add)

    return {"success": True, "message": "ok"}


@router.post("/api/frames/remove-from-gallery")
@router.post("/frames/remove-from-gallery")
async def frames_remove_from_gallery(request: Request, body: RemoveGalleryBody):
    payload = _frames_jwt_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if str(payload.get("id")) != str(body.user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    vid = (body.video_id or "").strip()
    async with _user_gallery_lock:

        def _rm() -> None:
            store = _load_user_gallery_store_sync()
            uid = str(body.user_id)
            cur = [x for x in store.get(uid, []) if str(x.get("video_id") or x.get("id")) != vid]
            store[uid] = cur
            _save_user_gallery_store_sync(store)

        await asyncio.to_thread(_rm)

    return {"success": True}

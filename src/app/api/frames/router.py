from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_db

router = APIRouter(tags=["Frames"])


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
    from src.app.core.jwt import verify_access_token
    from src.app.database.repositories.user import UserRepository

    token = request.cookies.get("device_user_ids")
    print(f">>> LOAD ASSETS TOKEN: {token}", flush=True)
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
                        f">>> LOAD ASSETS FALLBACK: User ID {user_id} found in legacy cookie",
                        flush=True,
                    )
        except Exception as e:
            print(f">>> LOAD ASSETS FALLBACK ERROR: {e}", flush=True)

    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_repo = UserRepository(session)
    user_id = payload.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = await user_repo.get_user_by_id(user_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

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

    return {
        "success": True,
        "message": "Məlumat yükləndi",
        "ban": 1 if user.is_banned else 0,
        "balance": user.wallet.stars if user.wallet else 0,
        "balance_live": user.wallet.balance_live if user.wallet else 0,
        "gm_coin": user.wallet.stars_coin if user.wallet else 0, # Frontend gm_coin kutyapti
        "site": None,
        "chat_id": user.chat_id or None,
        "password_base64": None,
        "hasViewedDailyMessage": None, # Frontend null kutyapti
        "country": country_code,
        "registrationDate": reg_date,
    }


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


@router.get("/api/frames/daily-horoscopes")
@router.get("/frames/daily-horoscopes")
async def daily_horoscopes():
    zodiacs = [
        {
            "id": 1,
            "name": "Aries",
            "slug": "aries",
            "comment": "Your energy is peaking today, perfect for new beginnings!",
            "image": "/images/zodiacs/aries.png",
        },
        {
            "id": 2,
            "name": "Taurus",
            "slug": "taurus",
            "comment": "A great day for financial planning and stability.",
            "image": "/images/zodiacs/taurus.png",
        },
        {
            "id": 3,
            "name": "Gemini",
            "slug": "gemini",
            "comment": "Your communication skills will open new doors today.",
            "image": "/images/zodiacs/gemini.png",
        },
        {
            "id": 4,
            "name": "Cancer",
            "slug": "cancer",
            "comment": "Focus on your inner peace and family connections.",
            "image": "/images/zodiacs/cancer.png",
        },
        {
            "id": 5,
            "name": "Leo",
            "slug": "leo",
            "comment": "Your natural leadership will shine in social situations.",
            "image": "/images/zodiacs/leo.png",
        },
        {
            "id": 6,
            "name": "Virgo",
            "slug": "virgo",
            "comment": "Attention to detail will lead to significant progress.",
            "image": "/images/zodiacs/virgo.png",
        },
        {
            "id": 7,
            "name": "Libra",
            "slug": "libra",
            "comment": "Balance and harmony are your best allies today.",
            "image": "/images/zodiacs/libra.png",
        },
        {
            "id": 8,
            "name": "Scorpio",
            "slug": "scorpio",
            "comment": "Trust your intuition, it is particularly sharp today.",
            "image": "/images/zodiacs/scorpio.png",
        },
        {
            "id": 9,
            "name": "Sagittarius",
            "slug": "sagittarius",
            "comment": "Adventure is calling, be open to new experiences.",
            "image": "/images/zodiacs/sagittarius.png",
        },
        {
            "id": 10,
            "name": "Capricorn",
            "slug": "capricorn",
            "comment": "Your hard work and discipline will be recognized.",
            "image": "/images/zodiacs/capricorn.png",
        },
        {
            "id": 11,
            "name": "Aquarius",
            "slug": "aquarius",
            "comment": "Your innovative ideas will inspire those around you.",
            "image": "/images/zodiacs/aquarius.png",
        },
        {
            "id": 12,
            "name": "Pisces",
            "slug": "pisces",
            "comment": "Your creativity and imagination know no bounds today.",
            "image": "/images/zodiacs/pisces.png",
        },
    ]
    return {"success": True, "zodiacs": zodiacs}


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
    if payload and payload.get("id"):
        user_repo = UserRepository(session)
        user = await user_repo.get_user_by_id(payload.get("id"))
        if user:
            count = user.invited_guests
            print(f">>> REFERRALS DEBUG: Found user {user.id}, invited_guests: {count}", flush=True)
        else:
            print(f">>> REFERRALS DEBUG: User ID {payload.get('id')} database dan topilmadi", flush=True)
    else:
        print(">>> REFERRALS DEBUG: User ID aniqlanmadi (token xato yoki yo'q)", flush=True)

    return {"success": True, "count": count, "referrals": []}


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
    session: AsyncSession = Depends(get_db)
):
    from src.app.core.jwt import verify_access_token
    from src.app.database.repositories.game import GameRepository
    from src.app.api.ws.constants import HEARTS_PACKAGES

    token = request.cookies.get("device_user_ids")
    payload = verify_access_token(token) if token else None

    # FALLBACK
    if not payload and token:
        try:
            import json
            import urllib.parse
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("["):
                ids = json.loads(decoded)
                if ids: payload = {"id": int(ids[0])}
        except: pass

    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        body = await request.json()
        amount_stars = int(body.get("amount", 0))
    except:
        raise HTTPException(status_code=400, detail="Invalid amount")

    if amount_stars not in HEARTS_PACKAGES:
        raise HTTPException(status_code=400, detail="Invalid package")

    hearts_to_add = HEARTS_PACKAGES[amount_stars]
    user_id = payload.get("id")

    repo = GameRepository(session)
    # 1. Yulduzlarni tekshirish va yechib olish
    ok, current_stars = await repo.spend_stars(user_id, amount_stars)
    if not ok:
        return {"success": False, "message": f"Yetersiz STARS. Sizda: {current_stars}"}

    # 2. Yurakchalarni qo'shish
    new_hearts = await repo.add_hearts(user_id, hearts_to_add)
    await session.commit()

    return {
        "success": True,
        "message": f"{hearts_to_add} Hearts muvaffaqiyatli qo'shildi!",
        "balance": current_stars, # Yangi yulduzlar balansi
        "hearts": new_hearts
    }

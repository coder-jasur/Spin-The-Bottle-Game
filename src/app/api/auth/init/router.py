from fastapi import APIRouter, Request, Depends, Response
from pydantic import BaseModel
import json
from sqlalchemy.ext.asyncio import AsyncSession
from src.app.api.deps import get_db

router = APIRouter(tags=["Init"])

class InitModel(BaseModel):
    lang: str

@router.post("/api/auth/init")
@router.post("/auth/init")
async def auth_init(request: Request, data: InitModel):
    # Faqat lang qaytaramiz, boshqa ortiqcha narsa yo'q
    response_content = {
        "lang": data.lang
    }
    
    response = Response(content=json.dumps(response_content), media_type="application/json")
    # Tilni cookie sifatida ham saqlab qo'yamiz (ixtiyoriy)
    response.set_cookie(key="language", value=data.lang, path="/", max_age=3600*24*365)
    return response

@router.get("/api/auth/check-verification")
@router.get("/auth/check-verification")
async def check_verification(request: Request, session: AsyncSession = Depends(get_db)):
    from src.app.core.jwt import verify_access_token
    from src.app.database.repositories.user import UserRepository

    token = request.cookies.get("device_user_ids")
    payload = verify_access_token(token) if token else None

    # FALLBACK: Legacy cookie support
    if not payload and token:
        try:
            import json
            import urllib.parse
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if ids and isinstance(ids, list):
                    payload = {"id": int(ids[0])}
        except: pass

    is_verified = 0
    if payload and payload.get("id"):
        user_repo = UserRepository(session)
        user = await user_repo.get_user_by_id(payload.get("id"))
        if user and user.is_verified:
            is_verified = 1

    return {
        "apple_verified": 0,
        "google_verified": 0,
        "blue_tick": is_verified,
        "blue_tick_until": None
    }

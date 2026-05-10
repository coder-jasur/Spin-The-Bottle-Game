from fastapi import APIRouter, Header, Request

from src.app.core.jwt import verify_access_token

router = APIRouter(tags=["Verify"])

@router.get("/api/auth/verify")
@router.post("/api/auth/verify")
async def verify_token(
    request: Request,
    authorization: str = Header(None)
):
    
    token = None
    
    # 1. Headerdan qidirish
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")
    
    # 2. Cookiedan qidirish (agar headerda bo'lmasa)
    if not token:
        token = request.cookies.get("device_user_ids")
        
    if not token:
        return {"success": False, "message": "Token topilmadi"}

    payload = verify_access_token(token)
    
    if payload:
        return {
            "success": True, 
            "user_id": payload.get("id"),
            "message": "Token valid"
        }
    else:
        return {"success": False, "message": "Token xato yoki muddati o'tgan"}

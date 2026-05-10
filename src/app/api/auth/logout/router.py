from fastapi import APIRouter, Response

router = APIRouter(tags=["Auth"])


@router.post("/api/auth/logout")
@router.post("/auth/logout")
@router.get("/api/auth/logout")
@router.get("/auth/logout")
async def logout(response: Response):
    # O'chirilishi kerak bo'lgan barcha cookielar ro'yxati
    cookies_to_clear = [
        "accessToken", 
        "refreshToken", 
        "device_user_ids", 
        "language",
        "user_id",
        "Pycharm-17f1d1cd"
    ]
    
    for cookie in cookies_to_clear:
        # 1-usul: httponly=False bilan o'chirish
        response.delete_cookie(
            key=cookie,
            path="/",
            httponly=False,
            samesite="lax"
        )
        # 2-usul: httponly=True bilan o'chirish (ba'zan shunday o'rnatilgan bo'lishi mumkin)
        response.delete_cookie(
            key=cookie,
            path="/",
            httponly=True,
            samesite="lax"
        )
    
    return {"success": True, "message": "All session cookies have been aggressively cleared"}

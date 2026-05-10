import json

from fastapi import APIRouter, HTTPException, Request, Response

from src.app.core.jwt import create_access_token, verify_refresh_token

router = APIRouter(tags=["Auth"])


@router.post("/api/auth/refresh")
@router.post("/auth/refresh")
async def refresh_token(request: Request):
    # DEBUG: Headers
    print(f">>> DEBUG REFRESH: Headers: {dict(request.headers)}", flush=True)
    
    refresh_token = None
    
    # 1. Header'dan qidirish
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        refresh_token = auth_header.replace("Bearer ", "")
        print(f">>> DEBUG REFRESH: Token found in Header", flush=True)
    
    # 2. Body'dan qidirish (Xavfsiz usulda)
    if not refresh_token:
        try:
            body_bytes = await request.body()
            body_str = body_bytes.decode()
            print(f">>> DEBUG REFRESH: Body: {body_str[:100]}", flush=True)
            
            # JSON bo'lsa
            if request.headers.get("Content-Type") == "application/json":
                data = json.loads(body_str)
            else:
                # Form bo'lsa
                from urllib.parse import parse_qs
                data = {k: v[0] for k, v in parse_qs(body_str).items()}
                
            refresh_token = data.get("refreshToken") or data.get("token") or data.get("assetToken")
            if refresh_token: print(f">>> DEBUG REFRESH: Token found in Body", flush=True)
        except Exception as e:
            print(f">>> DEBUG REFRESH Body Error: {e}", flush=True)
            
    # 3. Cookie'dan qidirish
    if not refresh_token:
        refresh_token = request.cookies.get("refreshToken")
        if refresh_token: print(f">>> DEBUG REFRESH: Token found in Cookie", flush=True)
    
    if not refresh_token:
        print(f">>> DEBUG REFRESH: NO TOKEN FOUND AT ALL!", flush=True)
        raise HTTPException(status_code=401, detail="Refresh token topilmadi")
    
    payload = verify_refresh_token(refresh_token)
    print(f">>> DEBUG REFRESH: verify_refresh_token payload exists: {payload is not None}", flush=True)
    if not payload:
        raise HTTPException(
            status_code=401, detail="Refresh token yaroqsiz yoki muddati tugagan"
        )

    user_id = payload.get("id")
    # Yangi access token yaratamiz
    new_access_token = create_access_token(user_id)

    response_data = {
        "success": True,
        "accessToken": new_access_token,
        "device_user_ids": new_access_token,
    }

    response = Response(
        content=json.dumps(response_data), media_type="application/json"
    )

    # Barcha cookielarni yangilaymiz (100 yil)
    max_age_100_years = 3600 * 24 * 365 * 100
    cookie_params = {"httponly": False, "path": "/", "samesite": "lax", "max_age": max_age_100_years}
    
    response.set_cookie(key="device_user_ids", value=new_access_token, **cookie_params)
    response.set_cookie(key="accessToken", value=new_access_token, **cookie_params)
    response.set_cookie(key="refreshToken", value=refresh_token, **cookie_params)
    
    return response

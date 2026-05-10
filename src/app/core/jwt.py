import jwt
import uuid
import secrets
from datetime import datetime, timedelta
from typing import Optional

from src.app.core.config import load_config

# SOZLAMALAR
settings = load_config()
SECRET_KEY = settings.secret_key
ALGORITHM = settings.algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = 60 # 1 soat
REFRESH_TOKEN_EXPIRE_DAYS = 36500  # 100 yil

def create_access_token(user_id: int) -> str:
    """Qisqa muddatli Access Token yaratish"""
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # Randomizatsiya (Xavfsizlik uchun)
    nonce = secrets.token_urlsafe(16)
    device_ref = str(uuid.uuid4())
    
    to_encode = {
        "id": user_id,
        "nonce": nonce,
        "device_ref": device_ref,
        "type": "access",
        "exp": expire
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(user_id: int) -> str:
    """Uzoq muddatli Refresh Token yaratish"""
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    
    to_encode = {
        "id": user_id,
        "type": "refresh",
        "exp": expire,
        "jti": str(uuid.uuid4()) # Unique token identifier
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_access_token(token: str) -> Optional[dict]:
    """Access tokenni tekshirish (URL-decoded)"""
    import urllib.parse
    try:
        # Tokenni dekodlaymiz (agar u cookie'dan encoded bo'lib kelgan bo'lsa)
        clean_token = urllib.parse.unquote(token)
        payload = jwt.decode(clean_token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.PyJWTError:
        return None

def verify_refresh_token(token: str) -> Optional[dict]:
    """Refresh tokenni tekshirish (URL-decoded)"""
    import urllib.parse
    try:
        clean_token = urllib.parse.unquote(token)
        payload = jwt.decode(clean_token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        return payload
    except jwt.PyJWTError:
        return None
"""
GameSessionManager — o'yin sessiyasi tokenlarini RAM'da boshqaradi.

Xususiyatlari:
- Har "Start" bosganda yangi token, eskisi bekor bo'ladi
- 30 daqiqa muddati
- Xavfsiz (cryptographically secure) token
- Serverda DB xarajati yo'q (RAM-based)
"""

import secrets
import time
from typing import Dict, Optional


class GameSessionManager:
    """
    RAM-da saqlangan o'yin sessiya tokenlari.
    Har bir foydalanuvchi uchun FAQAT BITTA aktiv token bo'ladi.
    """

    def __init__(self, ttl_seconds: int = 3600):  # 30 daqiqa
        # token → {user_id, expires_at}
        self._store: Dict[str, dict] = {}
        # user_id → joriy aktiv token (eskini tezda topib o'chirish uchun)
        self._user_tokens: Dict[int, str] = {}
        self.ttl = ttl_seconds

    def create(self, user_id: int) -> str:
        """
        Foydalanuvchi uchun yangi sessiya tokeni yaratadi.
        Avvalgi token avtomatik bekor bo'ladi.
        """
        # Eski tokenni bekor qil
        old_token = self._user_tokens.get(user_id)
        if old_token:
            self._store.pop(old_token, None)

        # Yangi xavfsiz token (256-bit entropy)
        token = secrets.token_urlsafe(32)
        self._store[token] = {
            "user_id": user_id,
            "expires_at": time.time() + self.ttl,
        }
        self._user_tokens[user_id] = token

        # Eskirgan tokenlarni tozalash (har 100 ta yaratishda)
        if len(self._store) % 100 == 0:
            self._cleanup()

        return token

    def verify(self, token: str) -> Optional[int]:
        """
        Tokenni tekshiradi va user_id qaytaradi.
        Eskirgan yoki noto'g'ri token uchun None qaytaradi.
        """
        data = self._store.get(token)
        if not data:
            return None
        if time.time() > data["expires_at"]:
            # Eskirgan — o'chir
            self._store.pop(token, None)
            uid = data["user_id"]
            if self._user_tokens.get(uid) == token:
                self._user_tokens.pop(uid, None)
            return None
        return data["user_id"]

    def revoke(self, user_id: int):
        """Foydalanuvchining tokenini qo'lda bekor qilish."""
        token = self._user_tokens.pop(user_id, None)
        if token:
            self._store.pop(token, None)

    def _cleanup(self):
        """Eskirgan tokenlarni xotiradan tozalash."""
        now = time.time()
        expired = [t for t, d in self._store.items() if now > d["expires_at"]]
        for t in expired:
            uid = self._store[t]["user_id"]
            self._store.pop(t, None)
            if self._user_tokens.get(uid) == t:
                self._user_tokens.pop(uid, None)


# Yagona global instance (singleton)
game_sessions = GameSessionManager(ttl_seconds=1800)

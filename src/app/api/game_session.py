"""
GameSessionManager — o'yin sessiyasi tokenlarini boshqaradi.

Xususiyatlari:
- Har "Start" bosganda yangi token, eskisi bekor bo'ladi
- TTL (default: 30 daqiqa)
- Xavfsiz (cryptographically secure) token
- Diskka saqlanadi — server restart bo'lganda sessiyalar saqlanib qoladi.
  Aks holda RAM-only edi va foydalanuvchi "mehmon" bo'lib qolardi.
"""

import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("spinbottle")


def _default_persist_path() -> Path:
    """Loyiha root/.cache/game_sessions.json — gitignore qilingan vaqt-keshi."""
    # src/app/api/game_session.py → parents[3] = loyiha root
    root = Path(__file__).resolve().parents[3]
    return root / ".cache" / "game_sessions.json"


class GameSessionManager:
    """
    Disk-bilan saqlangan o'yin sessiya tokenlari.
    Har bir foydalanuvchi uchun FAQAT BITTA aktiv token bo'ladi.
    Server qayta ishga tushganda mavjud (eski tugamagan) tokenlar
    o'qib olinadi — shu sababli `accessToken` ni hech kim qayta yo'qotmaydi.
    """

    def __init__(
        self,
        ttl_seconds: int = 1800,
        persist_path: Optional[Path] = None,
    ):
        self._store: Dict[str, dict] = {}
        self._user_tokens: Dict[int, str] = {}
        self.ttl = ttl_seconds
        self._persist_path = Path(persist_path) if persist_path else _default_persist_path()
        self._load()

    # ── Persistence ─────────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            if not self._persist_path.exists():
                return
            with self._persist_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            store = data.get("store", {}) if isinstance(data, dict) else {}
            valid_count = 0
            for token, info in store.items():
                if not isinstance(info, dict):
                    continue
                exp = float(info.get("expires_at", 0))
                uid = info.get("user_id")
                if not uid or exp <= now:
                    continue
                self._store[token] = {"user_id": int(uid), "expires_at": exp}
                self._user_tokens[int(uid)] = token
                valid_count += 1
            if valid_count:
                log.info(
                    "GameSessions: %d ta tirik sessiya diskdan tiklandi (path=%s)",
                    valid_count,
                    self._persist_path,
                )
        except Exception as e:
            log.warning(f"GameSessions load xatosi (path={self._persist_path}): {e}")

    def _save(self) -> None:
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"store": self._store}
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=".gs-",
                suffix=".tmp",
                dir=str(self._persist_path.parent),
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, separators=(",", ":"))
                os.replace(tmp_path, self._persist_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            log.warning(f"GameSessions save xatosi: {e}")

    # ── Public API ──────────────────────────────────────────────────────────
    def create(self, user_id: int) -> str:
        """Yangi sessiya tokeni; eski token avtomatik bekor bo'ladi."""
        user_id = int(user_id)
        old_token = self._user_tokens.get(user_id)
        if old_token:
            self._store.pop(old_token, None)

        token = secrets.token_urlsafe(32)
        self._store[token] = {
            "user_id": user_id,
            "expires_at": time.time() + self.ttl,
        }
        self._user_tokens[user_id] = token

        if len(self._store) % 100 == 0:
            self._cleanup()

        self._save()
        return token

    def verify(self, token: str) -> Optional[int]:
        """Token → user_id; eskirgan/noto'g'ri → None."""
        if not token:
            return None
        data = self._store.get(token)
        if not data:
            return None
        if time.time() > data["expires_at"]:
            self._store.pop(token, None)
            uid = data["user_id"]
            if self._user_tokens.get(uid) == token:
                self._user_tokens.pop(uid, None)
            self._save()
            return None
        return data["user_id"]

    def touch(self, token: str) -> Optional[int]:
        """Tirik tokenning amal qilish muddatini yangilab, user_id qaytaradi."""
        uid = self.verify(token)
        if uid is None:
            return None
        self._store[token]["expires_at"] = time.time() + self.ttl
        self._save()
        return uid

    def revoke(self, user_id: int) -> None:
        """Foydalanuvchining tokenini qo'lda bekor qilish."""
        token = self._user_tokens.pop(int(user_id), None)
        if token:
            self._store.pop(token, None)
            self._save()

    def _cleanup(self) -> None:
        """Eskirgan tokenlarni tozalash."""
        now = time.time()
        expired = [t for t, d in self._store.items() if now > d["expires_at"]]
        for t in expired:
            uid = self._store[t]["user_id"]
            self._store.pop(t, None)
            if self._user_tokens.get(uid) == t:
                self._user_tokens.pop(uid, None)
        if expired:
            self._save()


game_sessions = GameSessionManager(ttl_seconds=1800)

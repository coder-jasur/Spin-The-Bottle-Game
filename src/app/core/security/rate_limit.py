"""HTTP rate limiting (Redis yoki xotira) — DDoS / bruteforce kamaytirish."""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

log = logging.getLogger("spinbottle.security.ratelimit")


def ws_client_ip(ws) -> str:
    """WebSocket ulanish IP (proxy orqali)."""
    headers = {k.decode().lower(): v.decode() for k, v in (ws.scope.get("headers") or [])}
    forwarded = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    client = ws.scope.get("client")
    if client and client[0]:
        return str(client[0])[:64]
    return "unknown"


def client_ip(request: Request) -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return "unknown"


class _MemoryLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_sec: float) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        cutoff = now - window_sec
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        # Eski kalitlarni vaqti-vaqti bilan tozalash
        if len(self._hits) > 50_000:
            stale = [k for k, v in self._hits.items() if not v or v[-1] < cutoff]
            for k in stale[:10_000]:
                self._hits.pop(k, None)
        return True


_memory = _MemoryLimiter()
_redis = None
_redis_checked = False


async def _redis_client(redis_url: str):
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    if not redis_url:
        return None
    try:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(redis_url, decode_responses=True)
        await _redis.ping()
        log.info("Rate limit: Redis")
    except Exception as e:
        log.warning("Rate limit: Redis yo'q, RAM fallback (%s)", e)
        _redis = None
    return _redis


async def _allow_redis(r, key: str, limit: int, window_sec: int) -> bool:
    """Sliding window — Redis INCR + TTL."""
    bucket = int(time.time() // window_sec)
    rk = f"rl:{key}:{bucket}"
    try:
        pipe = r.pipeline()
        pipe.incr(rk)
        pipe.expire(rk, window_sec + 1)
        count, _ = await pipe.execute()
        return int(count) <= limit
    except Exception:
        return _memory.allow(key, limit, float(window_sec))


async def check_rate(
    key: str,
    limit: int,
    window_sec: int,
    *,
    redis_url: str = "",
) -> bool:
    r = await _redis_client(redis_url)
    if r:
        return await _allow_redis(r, key, limit, window_sec)
    return _memory.allow(key, limit, float(window_sec))


def _rule_for_path(path: str) -> tuple[int, int] | None:
    """(limit, window_sec) yoki None = cheklanmaydi."""
    if path.startswith("/static") or path.startswith("/assets/"):
        return None
    if path in ("/favicon.ico", "/favicon.svg", "/favicon-96x96.png"):
        return None
    if path.startswith("/photos/") or path.startswith("/images/"):
        return None
    if path.startswith("/api/auth/register"):
        return 8, 60
    if path.startswith("/api/auth/login") or path.startswith("/auth/login"):
        return 15, 60
    if path.startswith("/api/auth/"):
        return 40, 60
    if path.startswith("/api/admin"):
        return 120, 60
    if path.startswith("/api_music/play"):
        return 90, 60
    if path.startswith("/api_music"):
        return 180, 60
    if path.startswith("/ws"):
        return None
    return 400, 60


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        redis_url: str = "",
    ) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.redis_url = redis_url

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled or request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path or ""
        rule = _rule_for_path(path)
        if rule is None:
            return await call_next(request)

        limit, window = rule
        ip = client_ip(request)
        key = f"{ip}:{path.split('?')[0][:80]}"

        if not await check_rate(key, limit, window, redis_url=self.redis_url):
            log.warning("Rate limit: %s %s", ip, path)
            return JSONResponse(
                status_code=429,
                content={"error": "too_many_requests", "retry_after_sec": window},
                headers={"Retry-After": str(window)},
            )

        return await call_next(request)

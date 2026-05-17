"""JWT: Bearer header + cookie."""
from __future__ import annotations

import json
import urllib.parse
from typing import Optional

from fastapi import Request

from src.app.core.jwt import verify_access_token


def resolve_auth_payload(request: Request) -> Optional[dict]:
    tokens: list[str] = []
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        t = auth.replace("Bearer ", "", 1).strip()
        if t:
            tokens.append(t)
    for key in ("device_user_ids", "accessToken"):
        v = request.cookies.get(key)
        if not v:
            continue
        s = str(v).strip()
        if s and s not in tokens:
            tokens.append(s)
    if not tokens:
        return None
    for token in tokens:
        p = verify_access_token(token)
        if p:
            return p
        try:
            decoded = urllib.parse.unquote(token)
            if decoded.startswith("[") and decoded.endswith("]"):
                ids = json.loads(decoded)
                if isinstance(ids, list) and ids:
                    return {"id": int(ids[0])}
        except Exception:
            pass
    return None

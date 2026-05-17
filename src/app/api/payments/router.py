"""Telegram Stars to'ldirish API (Mini App invoice havolasi)."""
from __future__ import annotations

import json
import logging
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_db
from src.app.core.jwt import verify_access_token
from src.app.database.repositories.user import UserRepository
from src.app.core.language import resolve_user_lang
from src.app.core.stars_support import build_stars_support_url, support_telegram_url
from src.app.services.telegram_payments import (
    MIN_TOPUP_STARS,
    MAX_TOPUP_STARS,
    create_stars_invoice_link,
    send_stars_invoice_to_chat,
)

log = logging.getLogger("spinbottle.payments")
router = APIRouter(tags=["Payments"])


class StarsTopupRequest(BaseModel):
    amount: int = Field(..., ge=MIN_TOPUP_STARS, le=MAX_TOPUP_STARS)
    send_to_chat: bool = True


def _auth_user_id(request: Request) -> int:
    """WS / Mini App: accessToken, device_user_ids, Bearer."""
    candidates: list[str] = []
    for name in ("accessToken", "device_user_ids", "refreshToken"):
        raw = request.cookies.get(name)
        if raw:
            candidates.append(raw)

    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        candidates.append(auth[7:].strip())

    for raw in candidates:
        try:
            payload = verify_access_token(raw)
        except Exception:
            payload = None
        if payload and payload.get("id"):
            return int(payload["id"])

        try:
            decoded = urllib.parse.unquote(raw)
            if decoded.startswith("["):
                arr = json.loads(decoded)
                if arr:
                    payload = verify_access_token(str(arr[0]))
                    if payload and payload.get("id"):
                        return int(payload["id"])
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/api/payments/stars/topup")
async def request_stars_topup(
    request: Request,
    body: StarsTopupRequest,
    session: AsyncSession = Depends(get_db),
):
    """
    Yetishmayotgan Stars uchun invoice.
    `send_to_chat=true` — bot Telegram chatiga chek yuboradi (tg_id bo'lsa).
    """
    user_id = _auth_user_id(request)
    user_repo = UserRepository(session)
    user = await user_repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    settings = getattr(request.app.state, "settings", None)
    support_user = (
        getattr(settings, "telegram_support_username", None)
        if settings
        else None
    )
    lang = resolve_user_lang(
        cookie_lang=request.cookies.get("language"),
        db_language_code=getattr(user, "language_code", None),
    )
    if not user.tg_id:
        return {
            "success": False,
            "needs_support": True,
            "amount": body.amount,
            "support_url": build_stars_support_url(
                request, settings, shortfall=body.amount, lang=lang
            ),
            "support_telegram": support_telegram_url(support_user),
            "tg_id": None,
        }

    invoice_url = await create_stars_invoice_link(user_id, body.amount, lang=lang)
    invoice_sent = False
    if body.send_to_chat and user.tg_id:
        invoice_sent = await send_stars_invoice_to_chat(
            int(user.tg_id),
            user_id,
            body.amount,
            lang=lang,
        )

    if not invoice_url and not invoice_sent:
        raise HTTPException(
            status_code=503,
            detail="To'lov xizmati hozir mavjud emas. Bot sozlamalarini tekshiring.",
        )

    return {
        "success": True,
        "amount": body.amount,
        "invoice_url": invoice_url,
        "invoice_sent": invoice_sent,
        "tg_id": user.tg_id,
    }

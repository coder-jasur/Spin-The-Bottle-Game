"""Admin broadcast — barcha Telegram foydalanuvchilarga xabar (Movie-Bot asosida)."""
from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any, Union

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import (
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.database.models.user import User
from src.app.database.repositories.user import UserRepository

log = logging.getLogger("spinbottle.broadcaster")

USER_NAME_PLACEHOLDER = "<user_name>"
_ALT_PLACEHOLDER = "{user_name}"
_DEFAULT_NAME = "do'st"

# HTML parse_mode da <user_name> teg sifatida yo'qolishi mumkin
_PLACEHOLDER_PATTERN = re.compile(
    r"<user_name>|&lt;user_name&gt;|<USER_NAME>|&lt;USER_NAME&gt;"
    r"|\{user_name\}|\{USER_NAME\}",
    re.IGNORECASE,
)


async def fetch_telegram_display_name(bot: Bot, tg_id: int) -> str:
    """Telegram API orqali: avval familiya, keyin ism."""
    try:
        chat = await bot.get_chat(int(tg_id))
        last = (getattr(chat, "last_name", None) or "").strip()
        if last:
            return last
        first = (getattr(chat, "first_name", None) or "").strip()
        if first:
            return first
    except Exception as e:
        log.debug("get_chat(%s): %s", tg_id, e)
    return ""


def normalize_template(template: str) -> str:
    return html.unescape(template or "")


def personalize_text(template: str, display_name: str) -> str:
    if not template:
        return template
    safe_name = html.escape(display_name or _DEFAULT_NAME)
    return _PLACEHOLDER_PATTERN.sub(safe_name, normalize_template(template))


def template_has_placeholder(template: str | None) -> bool:
    if not template:
        return False
    return _PLACEHOLDER_PATTERN.search(normalize_template(template)) is not None


class Broadcaster:
    def __init__(
        self,
        bot: Bot,
        session: AsyncSession,
        admin_id: int,
        *,
        from_chat_id: int,
        message_id: int,
        content_type: str,
        text_template: str = "",
        photo_id: str | None = None,
        video_id: str | None = None,
        animation_id: str | None = None,
        document_id: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
        batch_size: int = 5000,
        sleep_seconds: float = 0.05,
    ):
        self._bot = bot
        self._session = session
        self.admin_id = int(admin_id)
        self.from_chat_id = int(from_chat_id)
        self.message_id = int(message_id)
        self.content_type = str(content_type or "text")
        self.text_template = text_template or ""
        self.photo_id = photo_id
        self.video_id = video_id
        self.animation_id = animation_id
        self.document_id = document_id
        self.reply_markup = reply_markup
        self.batch_size = batch_size
        self.sleep_seconds = sleep_seconds
        self.personalize = template_has_placeholder(self.text_template)

        self.sent_messages_count = 0
        self.failed_messages_count = 0
        self.processed_batches = 0
        self.total_processed = 0

        self.blocked_users: list[int] = []
        self.deleted_users: list[int] = []
        self.deactivated_users: list[int] = []
        self.limited_users: list[int] = []

        self.total_blocked_users = 0
        self.total_deleted_users = 0
        self.total_deactivated_users = 0
        self.total_limited_users = 0

        self._name_cache: dict[int, str] = {}

    async def _resolve_display_name(self, tg_id: int) -> str:
        tid = int(tg_id)
        if tid in self._name_cache:
            return self._name_cache[tid]
        name = await fetch_telegram_display_name(self._bot, tid)
        if not name:
            repo = UserRepository(self._session)
            row = await repo.get_user(tid)
            if row:
                dn = (row.display_name or row.username or "").strip()
                if dn:
                    name = dn.split()[-1] if " " in dn else dn
        if not name:
            name = _DEFAULT_NAME
        self._name_cache[tid] = name
        return name

    async def _send_info_message(self, template: str) -> Message:
        return await self._bot.send_message(
            self.admin_id,
            template.format(
                sent=0,
                failed=0,
                blocked=0,
                deleted=0,
                limited=0,
                deactivated=0,
                batches=0,
            ),
            parse_mode=ParseMode.HTML,
        )

    async def _update_info_message(
        self, info_message: Message, template: str, *, include_total: bool = False
    ) -> None:
        try:
            text = template.format(
                sent=self.sent_messages_count,
                failed=self.failed_messages_count,
                blocked=len(self.blocked_users),
                deleted=len(self.deleted_users),
                limited=len(self.limited_users),
                deactivated=len(self.deactivated_users),
                batches=self.processed_batches,
            )
            if include_total:
                text += (
                    f"\n\n<b>Jami:</b> {self.total_processed} foydalanuvchi"
                )
            await info_message.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.debug("broadcast status edit: %s", e)

    async def broadcast(self) -> None:
        status_tpl = (
            "<b>📣 Broadcast</b>\n\n"
            "Yuborildi: <b>{sent}</b>\n"
            "Xato: <b>{failed}</b>\n"
            "Bloklagan: <b>{blocked}</b>\n"
            "O'chirilgan: <b>{deleted}</b>\n"
            "Cheklangan: <b>{limited}</b>\n"
            "Deaktiv: <b>{deactivated}</b>\n"
            "Pachkalar: <b>{batches}</b>"
        )
        info_message = await self._send_info_message(status_tpl)
        repo = UserRepository(self._session)

        try:
            async for user_ids, _offset in repo.iterate_user_ids(self.batch_size):
                user_ids = [int(x) for x in user_ids if x]
                await self._process_batch(user_ids, info_message, status_tpl)
                self.processed_batches += 1
                self.total_processed += len(user_ids)
                await self._update_info_message(info_message, status_tpl)
                if (
                    self.blocked_users
                    or self.deleted_users
                    or self.limited_users
                    or self.deactivated_users
                ):
                    await self._mark_user_statuses()
                    self.total_blocked_users += len(self.blocked_users)
                    self.total_deleted_users += len(self.deleted_users)
                    self.total_limited_users += len(self.limited_users)
                    self.total_deactivated_users += len(self.deactivated_users)
                    self.blocked_users = []
                    self.deleted_users = []
                    self.limited_users = []
                    self.deactivated_users = []
        except Exception as e:
            log.exception("broadcast failed")
            await self._bot.send_message(
                self.admin_id, f"❌ Broadcast xatosi: {e}", parse_mode=ParseMode.HTML
            )
        finally:
            try:
                await self._update_info_message(
                    info_message, status_tpl, include_total=True
                )
            except Exception:
                pass
            await self._delete_preview()
            if (
                self.blocked_users
                or self.deleted_users
                or self.limited_users
                or self.deactivated_users
            ):
                await self._mark_user_statuses()

    async def _process_batch(
        self, user_ids: list[int], info_message: Message, status_tpl: str
    ) -> None:
        for raw_id in user_ids:
            if raw_id is None or isinstance(raw_id, bool):
                continue
            try:
                user_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if user_id <= 0:
                continue

            result = await self._send_to_user(user_id)
            if result is True:
                self.sent_messages_count += 1
            else:
                self.failed_messages_count += 1
                # bool is a subclass of int in Python — exclude False/True
                if type(result) is int:
                    self.blocked_users.append(result)
                elif result == "deactivated":
                    self.deactivated_users.append(user_id)
                elif result == "limited":
                    self.limited_users.append(user_id)
                elif result == "deleted":
                    self.deleted_users.append(user_id)
            await asyncio.sleep(self.sleep_seconds)

    async def _send_to_user(self, user_id: int) -> Union[bool, int, str]:
        try:
            if self.personalize:
                await self._send_personalized(user_id)
            else:
                await self._bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=self.from_chat_id,
                    message_id=self.message_id,
                    reply_markup=self.reply_markup,
                )
            return True
        except TelegramForbiddenError as e:
            err = str(e).lower()
            if "deactivated" in err:
                return "deactivated"
            if "limited" in err:
                return "limited"
            if "not found" in err:
                return "deleted"
            return user_id
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "chat not found" in err or "user not found" in err:
                return "deleted"
            log.warning("broadcast bad request uid=%s: %s", user_id, e)
            return False
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            return await self._send_to_user(user_id)
        except TelegramAPIError as e:
            log.warning("broadcast api uid=%s: %s", user_id, e)
            return False
        except Exception as e:
            log.warning("broadcast uid=%s: %s", user_id, e)
            return False

    async def _send_personalized(self, user_id: int) -> None:
        name = await self._resolve_display_name(user_id)
        body = personalize_text(self.text_template, name)
        kwargs: dict[str, Any] = {
            "chat_id": user_id,
            "parse_mode": ParseMode.HTML,
        }
        if self.reply_markup is not None:
            kwargs["reply_markup"] = self.reply_markup

        ct = self.content_type
        if ct == "text" and body:
            await self._bot.send_message(text=body, **kwargs)
            return
        if ct == "photo" and self.photo_id:
            await self._bot.send_photo(photo=self.photo_id, caption=body or None, **kwargs)
            return
        if ct == "video" and self.video_id:
            await self._bot.send_video(video=self.video_id, caption=body or None, **kwargs)
            return
        if ct == "animation" and self.animation_id:
            await self._bot.send_animation(
                animation=self.animation_id, caption=body or None, **kwargs
            )
            return
        if ct == "document" and self.document_id:
            await self._bot.send_document(
                document=self.document_id, caption=body or None, **kwargs
            )
            return
        if body:
            await self._bot.send_message(text=body, **kwargs)
            return
        await self._bot.copy_message(
            chat_id=user_id,
            from_chat_id=self.from_chat_id,
            message_id=self.message_id,
            reply_markup=self.reply_markup,
        )

    @staticmethod
    def _valid_tg_ids(ids: list) -> list[int]:
        out: list[int] = []
        for x in ids:
            if x is None or isinstance(x, bool):
                continue
            try:
                tid = int(x)
            except (TypeError, ValueError):
                continue
            if tid > 0:
                out.append(tid)
        return out

    async def _mark_user_statuses(self) -> None:
        try:
            blocked = self._valid_tg_ids(self.blocked_users)
            deleted = self._valid_tg_ids(self.deleted_users)
            limited = self._valid_tg_ids(self.limited_users)
            deactivated = self._valid_tg_ids(self.deactivated_users)

            if blocked:
                await self._session.execute(
                    update(User)
                    .where(User.tg_id.in_(blocked))
                    .values(status="blocked")
                )
            if deleted:
                await self._session.execute(
                    update(User)
                    .where(User.tg_id.in_(deleted))
                    .values(status="deleted")
                )
            if limited:
                await self._session.execute(
                    update(User)
                    .where(User.tg_id.in_(limited))
                    .values(status="limited")
                )
            if deactivated:
                await self._session.execute(
                    update(User)
                    .where(User.tg_id.in_(deactivated))
                    .values(status="deactivated")
                )
            await self._session.commit()
        except Exception as e:
            log.error("mark user statuses: %s", e)
            await self._session.rollback()

    async def _delete_preview(self) -> None:
        try:
            await self._bot.delete_message(
                chat_id=self.admin_id, message_id=self.message_id
            )
        except Exception:
            pass


async def run_broadcast_task(
    bot: Bot,
    session_pool: async_sessionmaker,
    admin_id: int,
    payload: dict[str, Any],
) -> None:
    async with session_pool() as session:
        markup = None
        raw_mk = payload.get("reply_markup")
        if raw_mk:
            try:
                markup = InlineKeyboardMarkup.model_validate(raw_mk)
            except Exception:
                markup = None
        bc = Broadcaster(
            bot,
            session,
            admin_id,
            from_chat_id=int(payload["from_chat_id"]),
            message_id=int(payload["message_id"]),
            content_type=str(payload.get("content_type") or "text"),
            text_template=str(payload.get("text_template") or ""),
            photo_id=payload.get("photo_id"),
            video_id=payload.get("video_id"),
            animation_id=payload.get("animation_id"),
            document_id=payload.get("document_id"),
            reply_markup=markup,
        )
        await bc.broadcast()

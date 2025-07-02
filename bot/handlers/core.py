"""Core message handler: detects keywords and sends responses."""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Optional

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from sqlalchemy import select

from ..anti_spam import check_spam
from ..cache import file_cache
from ..database import async_session
from ..models import GroupSetting, Keyword

logger = logging.getLogger(__name__)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.message is None:
        return

    # Ignore if command
    if update.message.text and update.message.text.startswith("/"):
        return

    # Anti-spam
    async with async_session() as session:
        stmt = select(GroupSetting).where(GroupSetting.chat_id == update.effective_chat.id)
        grp: Optional[GroupSetting] = await session.scalar(stmt)

    if await check_spam(update, context, grp):
        return

    text = (update.message.text or "").lower()
    if not text:
        return

    async with async_session() as session:
        kw_stmt = (
            select(Keyword)
            .join(GroupSetting)
            .where(GroupSetting.chat_id == update.effective_chat.id)
        )
        kw_rows: Iterable[Keyword] = (await session.scalars(kw_stmt)).all()
        for kw in kw_rows:
            if kw.phrase in text:
                await _respond(update, context, kw)
                break  # respond first match only


async def _respond(update: Update, context: ContextTypes.DEFAULT_TYPE, kw: Keyword) -> None:
    if kw.response_text:
        await update.message.reply_text(kw.response_text)
        return

    if kw.response_file_id and kw.response_file_type:
        try:
            if kw.response_file_type == "photo":
                await update.message.reply_photo(kw.response_file_id)
            elif kw.response_file_type == "video":
                await update.message.reply_video(kw.response_file_id)
            elif kw.response_file_type == "document":
                await update.message.reply_document(kw.response_file_id)
        except Exception as e:
            logger.warning("Failed to send media: %s", e)

        # refresh cache
        file_cache[kw.response_file_id] = kw.response_file_type

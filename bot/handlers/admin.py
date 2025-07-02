"""Admin handlers: manage keywords and settings via commands & inline buttons.
Simplified scaffold: /addkeyword, /listkeywords, /unban
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Iterable

from sqlalchemy import select

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters

from ..database import async_session
from ..models import GroupSetting, Keyword
from ..cache import file_cache

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def _ensure_group(session, chat_id: int) -> GroupSetting:
    # Retrieve group by chat_id (not primary key)
    stmt = select(GroupSetting).where(GroupSetting.chat_id == chat_id)
    grp: GroupSetting | None = await session.scalar(stmt)
    if not grp:
        grp = GroupSetting(chat_id=chat_id)
        session.add(grp)
        await session.commit()
        await session.refresh(grp)
    return grp


a_sync = asynccontextmanager


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_chat is None or update.effective_user is None:
        return False
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    member = await context.bot.get_chat_member(chat_id, user_id)
    return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}


# -------- commands -------- #


async def add_keyword_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /addkeyword <phrase> (reply with response text or media)"""
    if not await is_admin(update, context):
        return
    if update.effective_chat is None or update.message is None:
        return

    args = context.args
    if not args:
        await update.message.reply_text("Использование: /addkeyword <фраза> (ответом отправьте контент)")
        return

    phrase = " ".join(args).strip().lower()

    await update.message.reply_text(
        "Отправьте сообщение-ответ (текст / фото / видео / документ) — это будет ответом бота.",
    )
    logger.info("Registered temporary reply handler for add_keyword, command_msg_id=%s", update.message.message_id)

    # Wait for next message from admin
    # Create a one-time handler inside context
    def check_reply(msg_update: Update) -> bool:
        return (
            msg_update.message is not None
            and msg_update.message.reply_to_message is not None
            and msg_update.message.reply_to_message.message_id == update.message.message_id
            and msg_update.effective_user.id == update.effective_user.id
        )

    async def save_reply(msg_update: Update, msg_context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info("save_reply triggered: msg_id=%s reply_to=%s expected_cmd_id=%s", msg_update.message.message_id if msg_update.message else None, msg_update.message.reply_to_message.message_id if msg_update.message and msg_update.message.reply_to_message else None, update.message.message_id)
        if not check_reply(msg_update):
            logger.info("Message did not pass check_reply filter.")
            return
        async with async_session() as session:
            grp = await _ensure_group(session, update.effective_chat.id)

            kw = Keyword(
                group_id=grp.id,
                phrase=phrase,
                lang="ru",  # default lang; could parse arg later
            )

            # Determine response type and content
            if msg_update.message.text:
                kw.response_text = msg_update.message.text
            elif msg_update.message.photo:
                kw.response_file_type = "photo"
                kw.response_file_id = msg_update.message.photo[-1].file_id
            elif msg_update.message.video:
                kw.response_file_type = "video"
                kw.response_file_id = msg_update.message.video.file_id
            elif msg_update.message.document:
                kw.response_file_type = "document"
                kw.response_file_id = msg_update.message.document.file_id
            else:
                await msg_update.message.reply_text("Тип контента не поддерживается.")
                return

            session.add(kw)
            await session.commit()

            # Cache file_id if provided
            if kw.response_file_id:
                file_cache[kw.response_file_id] = kw.response_file_type or "file"

            await msg_update.message.reply_text("Ключевое слово добавлено! ✨")

        # Remove this handler after one use
        application = msg_context.application
        application.remove_handler(tmp_handler)

    from telegram.ext import MessageHandler as MH, filters as fl

    # Capture only replies (to reduce noise)
    tmp_handler = MH(fl.REPLY & (~fl.COMMAND), save_reply, block=False)
    context.application.add_handler(tmp_handler, 0)


async def list_keywords_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return
    if update.effective_chat is None or update.message is None:
        return

    async with async_session() as session:
        # Fetch all keyword phrases for this chat via explicit select to avoid lazy loading
        stmt = (
            select(Keyword.phrase)
            .join(GroupSetting)
            .where(GroupSetting.chat_id == chat_id)
            .order_by(Keyword.phrase)
        )
        scalar_result = await session.scalars(stmt)
        phrases = scalar_result.all()

    if not phrases:
        await update.message.reply_text("Список ключевых слов пуст.")
        return

    text = "\n".join(f"• {p}" for p in phrases)
    await update.message.reply_text(f"Ключевые слова:\n{text}")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ..anti_spam import unblock_user

    if not await is_admin(update, context):
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /unban <user_id>")
        return

    user_id = int(context.args[0])
    chat_id = update.effective_chat.id
    await unblock_user(chat_id, user_id, context)
    await update.message.reply_text("Пользователь разблокирован.")


# -------- inline panel -------- #

async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show admin control panel with inline buttons."""
    if not await is_admin(update, context):
        return
    buttons = [
        [InlineKeyboardButton("📋 Список ключевых", callback_data="panel:list")],
        [InlineKeyboardButton("🚫 spam_limit", callback_data="panel:spam_dec"),
         InlineKeyboardButton("➕ spam_limit", callback_data="panel:spam_inc")],
    ]
    await update.message.reply_text("Панель управления", reply_markup=InlineKeyboardMarkup(buttons))


async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline panel button callbacks."""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    if not await is_admin(update, context):
        return

    data = query.data or ""
    chat_id = query.message.chat_id if query.message else None

    # --- keyword list ---
    if data == "panel:list" and chat_id:
        try:
            async with async_session() as session:
                stmt = (
                    select(Keyword.phrase)
                    .join(GroupSetting)
                    .where(GroupSetting.chat_id == chat_id)
                    .order_by(Keyword.phrase)
                )
                phrases = (await session.scalars(stmt)).all()

            if not phrases:
                await query.edit_message_text("Список ключевых слов пуст.")
                return

            text_full = "\n".join(f"• {p}" for p in phrases)
            if len(text_full) <= 4000:
                await query.edit_message_text(f"Ключевые слова:\n{text_full}")
            else:
                await query.edit_message_text(
                    f"Ключевых слов: {len(phrases)}. Отправляю списком…"
                )
                chunk, size = [], 0
                for line in text_full.splitlines():
                    if size + len(line) + 1 > 4000:
                        await context.bot.send_message(chat_id=chat_id, text="\n".join(chunk))
                        chunk, size = [], 0
                    chunk.append(line)
                    size += len(line) + 1
                if chunk:
                    await context.bot.send_message(chat_id=chat_id, text="\n".join(chunk))
        except Exception as e:
            logger.exception("Failed to show keyword list: %s", e)
            await query.edit_message_text("Ошибка при выводе списка ключевых.")
        return

    # --- spam limit adjust ---
    if data in {"panel:spam_inc", "panel:spam_dec"} and chat_id:
        async with async_session() as session:
            grp = await _ensure_group(session, chat_id)
            delta = 1 if data.endswith("inc") else -1
            new_limit = max(1, grp.spam_limit + delta)
            grp.spam_limit = new_limit
            await session.commit()
        await query.edit_message_text(f"Новый spam_limit: {new_limit}")
    query = update.callback_query
    if not query:
        return
    await query.answer()
    logger.info("panel callback data=%s chat=%s user=%s", query.data, query.message.chat_id if query.message else None, update.effective_user.id if update.effective_user else None)
    if not await is_admin(update, context):
        return

    data = query.data or ""
    chat_id = query.message.chat_id if query.message else None

    if data == "panel:list" and chat_id:
        # Show keyword list for this chat
        try:
            async with async_session() as session:
                stmt = (
                    select(Keyword.phrase)
                    .join(GroupSetting)
                    .where(GroupSetting.chat_id == chat_id)
                    .order_by(Keyword.phrase)
                )
                phrases = (await session.scalars(stmt)).all()

            if not phrases:
                await query.edit_message_text("Список ключевых слов пуст.")
                return

            text_full = "\n".join(f"• {p}" for p in phrases)
            if len(text_full) <= 4000:
                await query.edit_message_text(f"Ключевые слова:\n{text_full}")
            else:
                await query.edit_message_text(f"Ключевых слов: {len(phrases)}. Отправляю списком…")
                chunk, size = [], 0
                for line in text_full.splitlines():
                    if size + len(line) + 1 > 4000:
                        await context.bot.send_message(chat_id=chat_id, text="\n".join(chunk))
                        chunk, size = [], 0
                    chunk.append(line)
                    size += len(line) + 1
                if chunk:
                    await context.bot.send_message(chat_id=chat_id, text="\n".join(chunk))
        except Exception as e:
            logger.exception("Failed to show keyword list: %s", e)
            await query.edit_message_text("Ошибка при выводе списка ключевых.")
        return
    chat_id = query.message.chat_id if query.message else None
    if data == "panel:list" and chat_id:
        logger.info("panel:list clicked by %s in chat %s", update.effective_user.id if update.effective_user else None, chat_id)
        try:
            async with async_session() as session:
                stmt = (
                    select(Keyword.phrase)
                    .join(GroupSetting)
                    .where(GroupSetting.chat_id == chat_id)
                    .order_by(Keyword.phrase)
                )
                phrases = (await session.scalars(stmt)).all()

            if not phrases:
                await query.edit_message_text("Список ключевых слов пуст.")
                return

            text_full = "\n".join(f"• {p}" for p in phrases)
            if len(text_full) <= 4000:
                await query.edit_message_text(f"Ключевые слова:\n{text_full}")
            else:
                await query.edit_message_text(f"Ключевых слов: {len(phrases)}. Отправляю списком…")
                chunk, size = [], 0
                for line in text_full.splitlines():
                    if size + len(line) + 1 > 4000:
                        await context.bot.send_message(chat_id=chat_id, text="\n".join(chunk))
                        chunk, size = [], 0
                    chunk.append(line)
                    size += len(line) + 1
                if chunk:
                    await context.bot.send_message(chat_id=chat_id, text="\n".join(chunk))
        except Exception as e:
            logger.exception("Failed to show keyword list: %s", e)
            await query.edit_message_text("Ошибка при выводе списка ключевых.")
        logger.info("panel:list clicked by %s in chat %s", update.effective_user.id if update.effective_user else None, update.effective_chat.id if update.effective_chat else None)
        # Fetch and show list here because update.message is None in callback
        async with async_session() as session:
            stmt = (
                select(Keyword.phrase)
                .join(GroupSetting)
                .where(GroupSetting.chat_id == chat_id)
                .order_by(Keyword.phrase)
            )
            scalar_result = await session.scalars(stmt)
            phrases = scalar_result.all()
            if not phrases:
                await query.edit_message_text("Список ключевых слов пуст.")
            else:
                text_full = "\n".join(f"• {p}" for p in phrases)
                # Telegram limit 4096 chars; split if longer
                if len(text_full) <= 4000:
                    await query.edit_message_text(f"Ключевые слова:\n{text_full}")
                else:
                    await query.edit_message_text("Слишком много ключевых слов (>{len(phrases)}). Отправляю списком …")
                chunk = []
                size = 0
                for line in [f"• {p}" for p in phrases]:
                    if size + len(line) + 1 > 4000:
                        await context.bot.send_message(chat_id=chat_id, text="\n".join(chunk))
                        chunk = []
                        size = 0
                    chunk.append(line)
                    size += len(line) + 1
                if chunk:
                    await context.bot.send_message(chat_id=chat_id, text="\n".join(chunk))

    elif data in {"panel:spam_inc", "panel:spam_dec"}:
        async with async_session() as session:
            grp = await _ensure_group(session, update.effective_chat.id)
            delta = 1 if data.endswith("inc") else -1
            new_limit = max(1, grp.spam_limit + delta)
            grp.spam_limit = new_limit
            await session.commit()
            await query.edit_message_text(f"Новый spam_limit: {new_limit}")

# -------- registration helper -------- #

def admin_handlers() -> Iterable[CommandHandler]:
    return [
        CommandHandler("addkeyword", add_keyword_command),
        CommandHandler("listkeywords", list_keywords_command),
        CommandHandler("unban", unban_command),
        CommandHandler("panel", panel_command),
        CallbackQueryHandler(panel_callback, pattern="^panel:"),
    ]

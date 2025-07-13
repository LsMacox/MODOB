"""Handlers for group membership events and group updates."""
from __future__ import annotations

import logging
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes, ChatMemberHandler
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import async_session
from .db_utils import ensure_group

logger = logging.getLogger(__name__)

async def chat_member_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик изменений статуса участников чата.
    Если бот был добавлен в группу или его статус изменился, обновляет информацию о группе в базе.
    """
    logger.info("chat_member_update_handler called")
    if update.my_chat_member is None:
        logger.debug("No my_chat_member in update, skipping.")
        return
    
    # Получаем информацию о чате
    chat = update.effective_chat
    if chat is None:
        logger.warning("Chat is None in chat_member_update_handler")
        return
    
    if chat.type not in ('group', 'supergroup'):
        logger.debug(f"Update is not for a group chat (type: {chat.type}), skipping.")
        return
    
    # Проверяем, относится ли обновление к боту
    bot_id = context.bot.id
    member_update = update.my_chat_member

    logger.debug(f"Chat member update for user {member_update.new_chat_member.user.id} in chat {chat.id}")

    # Мы заинтересованы только в изменениях, которые касаются нашего бота
    if member_update.new_chat_member.user.id != bot_id:
        logger.debug(f"Update is not for our bot (our id: {bot_id}), skipping.")
        return
    
    # Проверяем статус бота в группе
    new_status = member_update.new_chat_member.status
    old_status = member_update.old_chat_member.status

    logger.info(f"Bot status changed in chat {chat.id} ('{chat.title}'): {old_status} -> {new_status}")
    
    # Если бота добавили, обновим или создадим запись о группе
    try:
        async with async_session() as session:
            if new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR]:
                logger.info(f"Bot was added or promoted in chat {chat.id}. Upserting to DB.")
                # Бот был добавлен в группу или его права изменились
                await ensure_group(session, chat.id)
                logger.info(f"ensure_group called for {chat.id}.")
            elif new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                logger.info(f"Bot was removed from chat {chat.id}.")
                # Бот был удален из группы
                logger.info(f"Bot was removed from group {chat.id}")
                # Здесь можно добавить логику удаления группы из базы, если нужно
            await session.commit()
            logger.info(f"Transaction for group {chat.id} committed successfully.")
    except Exception as e:
        logger.error(f"!!! FAILED to update database for group {chat.id}: {e}", exc_info=True)
        await session.rollback()
    
def get_group_event_handlers():
    """Return all handlers for group events."""
    return [
        ChatMemberHandler(chat_member_update_handler, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER)
    ]
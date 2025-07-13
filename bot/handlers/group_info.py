"""Handlers for retrieving group information."""
from __future__ import annotations

import logging
from telegram import Update, ChatMemberAdministrator
from telegram.ext import ContextTypes, CommandHandler
from telegram.error import TelegramError
from ..config import settings

logger = logging.getLogger(__name__)

async def get_members_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Command handler to get information about group members.
    Usage: /members
    """
    chat = update.effective_chat
    if not chat or chat.type not in ('group', 'supergroup'):
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    
    # Check if user is a group admin
    user_id = update.effective_user.id
    try:
        # Only allow group admins to use this command
        chat_member = await context.bot.get_chat_member(chat.id, user_id)
        is_admin = isinstance(chat_member, ChatMemberAdministrator) or chat_member.status == 'creator'
        
        if not is_admin:
            # Silently ignore the command for non-admins
            return
            
        # Get admin list - this always works
        admins = await context.bot.get_chat_administrators(chat.id)
        admin_info = [f"- {admin.user.full_name} (ID: {admin.user.id})" for admin in admins]
        
        admin_message = "Администраторы группы:\n" + "\n".join(admin_info)
        
        # Get member count
        member_count = await context.bot.get_chat_member_count(chat.id)
        
        # Send combined info
        await update.message.reply_text(
            f"{admin_message}\n\nВсего участников: {member_count}"
        )
        
    except TelegramError as e:
        logger.error(f"Error getting group members: {e}")
        await update.message.reply_text(f"Ошибка при получении информации: {e}")

def get_group_info_handlers():
    """Return all handlers for group info commands."""
    return [
        CommandHandler("members", get_members_command)
    ]

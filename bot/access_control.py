"""Access control utilities for the bot."""
from __future__ import annotations

import functools
import logging
from typing import Callable, Any, List, Optional

from telegram import Update
from telegram.ext import ContextTypes

from .config import settings
from .database import async_session

logger = logging.getLogger(__name__)


def restricted(func: Callable) -> Callable:
    """
    Decorator to restrict access to the bot based on user ID whitelist.
    
    Args:
        func: The handler function to wrap.
    
    Returns:
        The wrapped function that checks for user permission.
    """
    @functools.wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs) -> Any:
        # Check if the user is in the whitelist
        if not update.effective_user:
            logger.warning("No user in update, denying access")
            return
            
        user_id = update.effective_user.id
        
        # Check if user is in whitelist
        if not settings.ALLOWED_USERS or user_id not in settings.ALLOWED_USERS:
            logger.warning(f"Unauthorized access attempt by user {user_id}")
            
            # Only respond to unauthorized users in private chats
            # In group chats, silently ignore to avoid spam
            if update.effective_chat and update.effective_chat.type == "private":
                await update.message.reply_text(settings.UNAUTHORIZED_MESSAGE)
                
                # Notify admins about unauthorized access attempt if enabled
                if settings.NOTIFY_ON_UNAUTHORIZED:
                    await notify_unauthorized_access(
                        context, 
                        user_id=user_id,
                        username=update.effective_user.username,
                        first_name=update.effective_user.first_name
                    )
            return None
            
        # User is authorized, proceed with the handler
        return await func(update, context, *args, **kwargs)
        
    return wrapped


def is_user_authorized(user_id: int) -> bool:
    """
    Check if a user ID is in the whitelist.
    
    Args:
        user_id: The Telegram user ID to check.
        
    Returns:
        True if the user is authorized, False otherwise.
    """
    return bool(settings.ALLOWED_USERS) and user_id in settings.ALLOWED_USERS


async def notify_unauthorized_access(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None
) -> None:
    """
    Notify all admins (users in ALLOWED_USERS) about unauthorized access attempt.
    
    Args:
        context: The context object for the current update.
        user_id: The ID of the user who attempted unauthorized access.
        username: The username of the user (if available).
        first_name: The first name of the user (if available).
    """
    if not settings.ALLOWED_USERS:
        logger.warning("No administrators defined in ALLOWED_USERS, can't send notification")
        return
    
    # Prepare notification message
    user_info = f"ID: {user_id}"
    if username:
        user_info += f", Username: @{username}"
    if first_name:
        user_info += f", Name: {first_name}"
    
    notification = f"⚠️ Попытка несанкционированного доступа к боту:\n{user_info}"
    
    # Send notification to all admins
    for admin_id in settings.ALLOWED_USERS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=notification)
            logger.info(f"Sent unauthorized access notification to admin {admin_id}")
        except Exception as e:
            logger.error(f"Failed to send notification to admin {admin_id}: {e}")


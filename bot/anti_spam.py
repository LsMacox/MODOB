"""Simple anti-spam manager per chat/user.
Blocks user if exceeds configured thresholds.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from cachetools import TTLCache
from telegram import Update, User
from telegram.ext import ContextTypes

from .config import settings
from .models import GroupSetting

# (chat_id, user_id) -> timestamps deque
_message_history: Dict[Tuple[int, int], Deque[float]] = defaultdict(lambda: deque(maxlen=20))

# ban cache: (chat_id, user_id) -> until_timestamp
_ban_cache: TTLCache = TTLCache(maxsize=1024, ttl=60 * 60)  # TTL updated per ban duration


async def check_spam(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    group_conf: GroupSetting | None,
) -> bool:
    """Returns True if user is blocked (spam)."""
    if update.effective_chat is None or update.effective_user is None:
        return False

    chat_id = update.effective_chat.id
    user: User = update.effective_user
    user_id = user.id

    # if already banned
    if (chat_id, user_id) in _ban_cache:
        return True

    limit = group_conf.spam_limit if group_conf else settings.SPAM_LIMIT
    interval = group_conf.spam_interval if group_conf else settings.SPAM_INTERVAL

    # history tracking
    history = _message_history[(chat_id, user_id)]
    now = time.time()
    history.append(now)

    # drop old entries
    while history and now - history[0] > interval:
        history.popleft()

    if len(history) > limit:
        # ban user
        ban_seconds = 60 * 5  # default 5 minutes; could be in settings
        _ban_cache[(chat_id, user_id)] = now + ban_seconds
        try:
            await context.bot.restrict_chat_member(chat_id, user_id, until_date=int(now + ban_seconds))
        except Exception:
            pass
        return True

    return False


async def unblock_user(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual unblock via admin command."""
    _ban_cache.pop((chat_id, user_id), None)
    try:
        await context.bot.restrict_chat_member(chat_id, user_id, permissions=context.bot.get_chat(chat_id).permissions)
    except Exception:
        pass

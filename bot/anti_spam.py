"""Simple anti-spam manager per chat/user.
Blocks user if exceeds configured thresholds or sends too many links.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import deque, defaultdict
import logging
import re
import time
from datetime import timedelta
from cachetools import TTLCache
from telegram import Update, User, ChatMemberAdministrator, ChatMemberOwner
from telegram.ext import ContextTypes

from .config import settings
from .models import GroupSetting

# (chat_id, user_id) -> timestamps deque
_message_history: Dict[Tuple[int, int], Deque[float]] = defaultdict(lambda: deque(maxlen=20))

# (chat_id, user_id) -> link timestamps deque
_link_history: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=100))

# ban cache: (chat_id, user_id) -> (until_timestamp, ban_count)
# Сохраняем как время окончания бана, так и счетчик банов для эскалации
_ban_cache = TTLCache(maxsize=1000, ttl=60 * 60 * 24)  # Cache for 24 hours

# Exponential ban durations
BAN_DURATIONS = (60, 300, 1800, 3600, 21600)  # 1m, 5m, 30m, 1h, 6h

# Регулярное выражение для обнаружения ссылок
_url_pattern = re.compile(r'https?://\S+|www\.\S+|t\.me/\S+|@\w+', re.IGNORECASE)


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

    # Проверяем, является ли пользователь администратором группы
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if isinstance(chat_member, (ChatMemberAdministrator, ChatMemberOwner)):
            # Администраторы и владельцы не подпадают под проверку антиспама
            return False
    except Exception:
        # Если не удалось проверить статус, продолжаем проверку спама
        pass

    # Проверяем, забанен ли пользователь
    ban_key = (chat_id, user_id)
    if ban_key in _ban_cache:
        ban_data = _ban_cache[ban_key]
        
        # Проверяем формат данных - может быть кортежем (новый формат) или просто временем (старый формат)
        if isinstance(ban_data, tuple):
            ban_until, ban_count = ban_data
        else:
            ban_until = ban_data
            ban_count = 1  # Если старый формат, предполагаем счетчик 1
        
        now = time.time()
        
        if now < ban_until:
            # Пользователь всё ещё забанен
            return True
        else:
            # Бан истёк, но сохраняем счетчик банов в кэше для эскалации при повторных нарушениях
            # Важно: обновляем запись в кэше, чтобы показать, что бан истёк, но счетчик остаётся
            logging.info(f"Ban expired for user {user_id} in chat {chat_id}. Ban count remains: {ban_count}")
            _ban_cache[ban_key] = (0, ban_count)  # Устанавливаем время окончания бана в 0, чтобы показать, что он истёк

    limit = group_conf.spam_limit if group_conf else settings.SPAM_LIMIT
    interval = group_conf.spam_interval if group_conf else settings.SPAM_INTERVAL

    # history tracking
    history = _message_history[(chat_id, user_id)]
    now = time.time()
    history.append(now)

    # drop old entries
    while history and now - history[0] > interval:
        history.popleft()

    # Проверка на превышение лимита сообщений
    if len(history) > limit:
        await _ban_user(chat_id, user_id, context, "слишком частую отправку сообщений")
        return True
    
    # Проверка на наличие ссылок в сообщении
    if update.message and update.message.text:
        # Получаем настройки блокировки за ссылки с проверкой наличия атрибутов
        link_spam_enabled = False
        link_spam_limit = 3
        
        # Безопасно получаем настройки, учитывая возможность отсутствия полей в БД
        if group_conf:
            link_spam_enabled = getattr(group_conf, 'link_spam_enabled', False)
            link_spam_limit = getattr(group_conf, 'link_spam_limit', 3)
        else:
            link_spam_enabled = settings.LINK_SPAM_ENABLED
            link_spam_limit = settings.LINK_SPAM_LIMIT
        
        if link_spam_enabled:
            # Проверяем наличие ссылок в сообщении
            if _url_pattern.search(update.message.text):
                # Отслеживаем историю отправки ссылок
                link_history = _link_history[(chat_id, user_id)]
                link_history.append(now)
                
                # Удаляем старые записи (используем тот же временной интервал)
                while link_history and now - link_history[0] > interval:
                    link_history.popleft()
                
                # Если превышен лимит ссылок, блокируем пользователя
                if len(link_history) >= link_spam_limit:
                    await _ban_user(chat_id, user_id, context, "отправку слишком большого количества ссылок")
                    return True

    return False


async def _ban_user(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, reason: str) -> None:
    """Restrict a user from sending messages (mute) with exponential duration."""
    ban_key = (chat_id, user_id)
    
    # Получаем текущий счетчик банов из кэша или начинаем с 0
    current_ban_data = _ban_cache.get(ban_key, (0, 0))
    current_ban_count = current_ban_data[1] if isinstance(current_ban_data, tuple) else 0
    current_ban_count += 1

    duration_index = min(current_ban_count - 1, len(BAN_DURATIONS) - 1)
    ban_seconds = BAN_DURATIONS[duration_index]

    now = time.time()
    # Сохраняем в кэше как время окончания бана, так и счетчик для эскалации
    _ban_cache[ban_key] = (now + ban_seconds, current_ban_count)

    try:
        from telegram import ChatPermissions
        permissions = ChatPermissions(can_send_messages=False)

        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=permissions,
            until_date=int(now + ban_seconds)
        )

        # Convert seconds to minutes or hours and format with correct Russian plurals
        if ban_seconds < 3600:  # Less than an hour
            minutes = ban_seconds // 60
            if minutes == 1:
                duration_text = "1 минуту"
            elif minutes in [2, 3, 4]:
                duration_text = f"{minutes} минуты"
            else:
                duration_text = f"{minutes} минут"
        else:  # Hour or more
            hours = ban_seconds // 3600
            if hours == 1:
                duration_text = "1 час"
            elif hours in [2, 3, 4]:
                duration_text = f"{hours} часа"
            else:
                duration_text = f"{hours} часов"
        try:
            user_info = await context.bot.get_chat_member(chat_id, user_id)
            user_name = user_info.user.first_name
            if user_info.user.username:
                user_name += f" (@{user_info.user.username})"
            ban_message = f"Пользователю {user_name} запрещено писать на {duration_text} за {reason}."
        except Exception:
            ban_message = f"Пользователю запрещено писать на {duration_text} за {reason}."

        await context.bot.send_message(chat_id=chat_id, text=ban_message)

    except Exception as e:
        logging.error(f"Error restricting user {user_id} in chat {chat_id}: {e}")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Не удалось ограничить пользователя. Убедитесь, что у бота есть права администратора."
            )
        except Exception:
            pass


async def unblock_user(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual unblock via admin command. Lifts messaging restrictions."""
    ban_key = (chat_id, user_id)
    
    # Проверяем существование записи о бане перед удалением
    had_ban_entry = ban_key in _ban_cache
    if had_ban_entry:
        old_data = _ban_cache[ban_key]
        ban_count = old_data[1] if isinstance(old_data, tuple) else 1
        logging.info(f"Removing ban data for user {user_id} in chat {chat_id}. Ban count was: {ban_count}")
    
    # Полностью удаляем из кэша, что сбрасывает счетчик банов
    _ban_cache.pop(ban_key, None)
    _message_history.pop(ban_key, None)
    _link_history.pop(ban_key, None)

    try:
        from telegram import ChatPermissions
        # Restore default permissions for a regular user.
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_invite_users=True,
            # These are usually admin-only, so we keep them False.
            can_change_info=False,
            can_pin_messages=False
        )
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=permissions
        )
        logging.info(f"Successfully lifted restrictions for user {user_id} in chat {chat_id}.")
    except Exception as e:
        logging.error(f"Error lifting restrictions for user {user_id} in chat {chat_id}: {e}")
        

def get_banned_users(chat_id: int = None) -> list:
    """Get list of banned users, optionally filtered by chat_id.
    
    Args:
        chat_id: Optional chat ID to filter by
        
    Returns:
        List of dicts with user_id, chat_id, ban_until timestamp and ban_count
    """
    current_time = time.time()
    banned_users = []
    
    # Фильтруем активные баны (где время окончания бана > текущее время)
    for (ban_chat_id, ban_user_id), ban_data in list(_ban_cache.items()):
        # Проверяем формат данных - может быть кортежем или просто временем окончания
        if isinstance(ban_data, tuple):
            ban_until, ban_count = ban_data
        else:
            ban_until = ban_data
            ban_count = 1  # Если старый формат, предполагаем счетчик 1
            
        if ban_until > current_time:  # Бан еще активен
            if chat_id is None or ban_chat_id == chat_id:
                banned_users.append({
                    "user_id": ban_user_id,
                    "chat_id": ban_chat_id,
                    "ban_until": ban_until,
                    "ban_count": ban_count
                })
    
    return banned_users

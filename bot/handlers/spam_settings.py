"""Spam settings handlers: manage spam limits via commands."""
from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any

from sqlalchemy import select
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import CommandHandler, ContextTypes, BaseHandler, ConversationHandler, CallbackQueryHandler
from telegram.constants import ChatMemberStatus

from ..database import async_session
from ..models import GroupSetting
from .db_utils import ensure_group
from ..anti_spam import BAN_DURATIONS

logger = logging.getLogger(__name__)


async def show_spam_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current spam settings for a specified group in private chat."""
    if not update.callback_query:
        return
        
    query = update.callback_query
    
    # Получаем chat_id из user_data
    chat_id = context.user_data.get("selected_chat_id")
    if not chat_id:
        await query.edit_message_text("Ошибка: не выбрана группа.")
        return
    
    async with async_session() as session:
        stmt = select(GroupSetting).where(GroupSetting.chat_id == chat_id)
        grp: Optional[GroupSetting] = await session.scalar(stmt)
        
        if not grp:
            await query.edit_message_text("Настройки группы не найдены.")
            return
        
        # Получаем информацию о чате
        try:
            chat = await context.bot.get_chat(chat_id=chat_id)
            chat_title = chat.title
        except Exception:
            chat_title = f"ID: {chat_id}"
        
        # Создаем клавиатуру с кнопками управления
        keyboard = [
            [InlineKeyboardButton("Изменить лимит спама", callback_data=f"spam_limit:{chat_id}")],
            [InlineKeyboardButton("Изменить интервал спама", callback_data=f"spam_interval:{chat_id}")],
            [InlineKeyboardButton(f"{'🟢' if grp.link_spam_enabled else '🔴'} Блокировка за ссылки", callback_data=f"link_spam_toggle:{chat_id}")],
            [InlineKeyboardButton("Изменить лимит ссылок", callback_data=f"link_spam_limit:{chat_id}")],
            [InlineKeyboardButton("Разбанить пользователей", callback_data=f"show_banned:{chat_id}")],
            [InlineKeyboardButton("« Назад к управлению", callback_data=f"private:manage:{chat_id}")]
        ]
        
        settings_text = (
            f"📊 **Настройки анти-спама для {chat_title}:**\n\n"
            f"• Лимит спам-сообщений: {grp.spam_limit}\n"
            f"• Интервал спам-сообщений: {grp.spam_interval} сек\n"
            f"• Лимит повторяющихся сообщений: {grp.repeat_limit}\n"
            f"• Интервал повторяющихся сообщений: {grp.repeat_interval} сек\n"
            f"• Блокировка за ссылки: {'Включена ✅' if grp.link_spam_enabled else 'Отключена ❌'}\n"
            f"• Лимит ссылок: {grp.link_spam_limit}\n\n"
            "Выберите настройку, которую хотите изменить:"
        )
        
        await query.edit_message_text(
            settings_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )


# Callback для обработки кнопок изменения настроек спама
async def spam_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, override_data: str = None) -> None:
    """
    Handle callbacks for spam settings buttons in private chat.
    Callbacks: spam_limit:{chat_id}, spam_interval:{chat_id}, etc.
    
    Args:
        update: The update object
        context: The context object
        override_data: Optional override for callback data (used for programmatic calls)
    """
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    
    # Используем переданные данные или данные из callback_query
    data = override_data or query.data
    if not data:
        return
    
    parts = data.split(":")
    if len(parts) < 2:
        return
        
    action = parts[0]
    try:
        chat_id = int(parts[1])
    except (ValueError, IndexError):
        await query.edit_message_text("Ошибка: некорректный формат данных.")
        return
    
    # Сохраняем chat_id в user_data
    context.user_data["selected_chat_id"] = chat_id
    
    # Получаем информацию о чате
    try:
        chat = await context.bot.get_chat(chat_id)
        chat_title = chat.title
    except Exception:
        chat_title = f"ID: {chat_id}"
    
    if action == "spam_limit":
        # Показываем диалог изменения лимита спама
        keyboard = []
        for value in [3, 5, 10, 15, 20]:
            keyboard.append([InlineKeyboardButton(
                f"{value}", 
                callback_data=f"set_spam_limit:{chat_id}:{value}")])
        
        keyboard.append([InlineKeyboardButton(
            "« Назад к настройкам", 
            callback_data=f"private:spam:{chat_id}")])
        
        await query.edit_message_text(
            f"Выберите новый лимит спам-сообщений для {chat_title}:\n\n"
            "После достижения этого лимита пользователь будет забанен.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif action == "spam_interval":
        # Показываем диалог изменения интервала спама
        keyboard = []
        for value in [30, 60, 120, 300, 600]:
            keyboard.append([InlineKeyboardButton(
                f"{value} сек", 
                callback_data=f"set_spam_interval:{chat_id}:{value}")])
        
        keyboard.append([InlineKeyboardButton(
            "« Назад к настройкам", 
            callback_data=f"private:spam:{chat_id}")])
        
        await query.edit_message_text(
            f"Выберите новый интервал спам-сообщений для {chat_title}:\n\n"
            "Это временной интервал, в течение которого считаются сообщения для лимита спама.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif action == "set_spam_limit":
        # Устанавливаем новое значение лимита спама
        if len(parts) < 3:
            await query.edit_message_text("Ошибка: некорректный формат данных.")
            return
            
        try:
            new_limit = int(parts[2])
            if new_limit < 1:
                await query.edit_message_text("Лимит должен быть положительным числом.")
                return
                
            async with async_session() as session:
                from .db_utils import ensure_group
                grp = await ensure_group(session, chat_id)
                grp.spam_limit = new_limit
                await session.commit()
                
            # Возвращаемся к настройкам спама
            await query.edit_message_text(
                f"✅ Лимит спам-сообщений установлен на {new_limit}.\n\nВозвращаемся к настройкам..."
            )
            
            # Переходим обратно к настройкам спама через 1 секунду
            from asyncio import sleep
            await sleep(1)
            await show_spam_settings(update, context)
            
        except ValueError:
            await query.edit_message_text("Ошибка: некорректное значение.")
            return
    
    elif action == "set_spam_interval":
        # Устанавливаем новое значение интервала спама
        if len(parts) < 3:
            await query.edit_message_text("Ошибка: некорректный формат данных.")
            return
            
        try:
            new_interval = int(parts[2])
            if new_interval < 1:
                await query.edit_message_text("Интервал должен быть положительным числом.")
                return
                
            async with async_session() as session:
                from .db_utils import ensure_group
                grp = await ensure_group(session, chat_id)
                grp.spam_interval = new_interval
                await session.commit()
                
            # Возвращаемся к настройкам спама
            await query.edit_message_text(
                f"✅ Интервал спам-сообщений установлен на {new_interval} секунд.\n\nВозвращаемся к настройкам..."
            )
            
            # Переходим обратно к настройкам спама через 1 секунду
            from asyncio import sleep
            await sleep(1)
            await show_spam_settings(update, context)
            
        except ValueError:
            await query.edit_message_text("Ошибка: некорректное значение.")
            return
    
    elif action == "show_banned":
        # Показываем список забаненных пользователей
        try:
            # Получаем информацию о чате
            chat = await context.bot.get_chat(chat_id=chat_id)
            chat_title = chat.title
            
            # Получаем список забаненных пользователей из анти-спам модуля
            from bot.anti_spam import get_banned_users
            banned_list = get_banned_users(chat_id)
            
            # Преобразуем формат данных для отображения
            formatted_banned_list = []
            for ban_info in banned_list:
                # Проверяем наличие всех необходимых ключей
                if 'user_id' not in ban_info or 'ban_until' not in ban_info:
                    logging.error(f"Missing required keys in ban_info: {ban_info}")
                    continue
                    
                # Добавляем информацию о пользователе, которую можно получить из API
                try:
                    # Пытаемся получить информацию о пользователе
                    user_info = await context.bot.get_chat_member(chat_id, ban_info['user_id'])
                    user = user_info.user
                    
                    # Добавляем информацию о счетчике бана, если она есть
                    ban_count = ban_info.get('ban_count', 1)
                    ban_duration = "неизвестно"
                    if ban_count > 0 and ban_count <= len(BAN_DURATIONS):
                        ban_seconds = BAN_DURATIONS[min(ban_count - 1, len(BAN_DURATIONS) - 1)]
                        if ban_seconds < 3600:  # Меньше часа
                            ban_duration = f"{ban_seconds // 60} мин."
                        else:  # Час или больше
                            ban_duration = f"{ban_seconds // 3600} час."
                    
                    formatted_banned_list.append({
                        "user_id": ban_info['user_id'],
                        "username": user.username or "",
                        "first_name": user.first_name or "",
                        "ban_until": ban_info['ban_until'],
                        "ban_count": ban_count,
                        "ban_duration": ban_duration
                    })
                except Exception as e:
                    logging.error(f"Error getting user info: {e}")
                    # Если не удалось получить информацию, добавляем только ID
                    formatted_banned_list.append({
                        "user_id": ban_info['user_id'],
                        "username": "",
                        "first_name": f"ID: {ban_info['user_id']}",
                        "ban_until": ban_info['ban_until'],
                        "ban_count": ban_info.get('ban_count', 1),
                        "ban_duration": "неизвестно"
                    })
            
            banned_list = formatted_banned_list
            
            if not banned_list:
                # Нет забаненных пользователей
                keyboard = [
                    [InlineKeyboardButton(
                        "« Назад к настройкам", 
                        callback_data=f"private:spam:{chat_id}"
                    )]
                ]
                
                await query.edit_message_text(
                    f"🔍 В группе {chat_title} нет забаненных пользователей.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            # Создаем клавиатуру с кнопками для разбана
            keyboard = []
            for banned in banned_list:
                import datetime
                ban_until = datetime.datetime.fromtimestamp(banned['ban_until']).strftime('%H:%M:%S')
                display_name = banned['first_name']
                if banned.get('username'):
                    display_name += f" (@{banned['username']})"
                
                # Добавляем информацию о текущем уровне бана и длительности
                ban_count = banned.get('ban_count', 1)
                ban_duration = banned.get('ban_duration', "неизвестно")
                
                keyboard.append([InlineKeyboardButton(
                    f"{display_name} [До {ban_until}] [Бан #{ban_count}, {ban_duration}] ❌", 
                    callback_data=f"unban_user:{chat_id}:{banned['user_id']}"
                )])
            
            keyboard.append([
                InlineKeyboardButton(
                    "« Назад к настройкам", 
                    callback_data=f"private:spam:{chat_id}"
                )
            ])
            
            message_text = f"🔒 **Забаненные пользователи в {chat_title}:**\n\n"
            for i, banned in enumerate(banned_list, 1):
                message_text += f"{i}. {banned['first_name']} (@{banned['username']})\n"
            
            message_text += "\nВыберите пользователя для разбана:"
            
            await query.edit_message_text(
                message_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Error showing banned users: {e}")
            await query.edit_message_text(
                f"Не удалось получить список забаненных пользователей: {str(e)}"
            )
            return
    
    elif action == "unban_user":
        # Разбан пользователя
        if len(parts) < 3:
            await query.edit_message_text("Ошибка: некорректный формат данных.")
            return
            
        try:
            user_id = int(parts[2])
            
            # Пытаемся получить информацию о пользователе
            try:
                user_info = await context.bot.get_chat_member(chat_id, user_id)
                user = user_info.user
                user_name = f"{user.first_name} {user.last_name or ''}".strip()
                if user.username:
                    user_name += f" (@{user.username})"
            except Exception as e:
                user_name = f"ID: {user_id}"
            
            # Вызываем функцию разбана пользователя
            from ..anti_spam import unblock_user
            await unblock_user(chat_id, user_id, context)
            
            # Отправляем сообщение о успешном разбане
            await query.edit_message_text(
                f"✅ Пользователь {user_name} успешно разбанен.\n\nВозвращаемся к настройкам..."
            )
            
            # Переходим обратно к настройкам спама через 1 секунду
            from asyncio import sleep
            await sleep(1)
            await show_spam_settings(update, context)
            
        except Exception as e:
            logger.error(f"Error unbanning user: {e}")
            await query.edit_message_text(
                f"Не удалось разбанить пользователя: {str(e)}"
            )
            return
            
    elif action == "link_spam_toggle":
        # Переключение блокировки за ссылки
        try:
            async with async_session() as session:
                stmt = select(GroupSetting).where(GroupSetting.chat_id == chat_id)
                grp: Optional[GroupSetting] = await session.scalar(stmt)
                
                if grp:
                    # Инвертируем текущее значение
                    grp.link_spam_enabled = not grp.link_spam_enabled
                    await session.commit()
                    
                    # Подтверждение успешного обновления
                    status = "включена" if grp.link_spam_enabled else "отключена"
                    await query.answer(f"Блокировка за ссылки {status}.")
                    # Возвращаемся к настройкам спама
                    await show_spam_settings(update, context)
                else:
                    await query.answer("Ошибка: настройки группы не найдены.")
            
        except Exception as e:
            logger.error(f"Error toggling link spam: {e}")
            await query.answer(f"Произошла ошибка: {str(e)}")
    
    elif action == "link_spam_limit":
        # Запрашиваем новый лимит ссылок
        try:
            async with async_session() as session:
                stmt = select(GroupSetting).where(GroupSetting.chat_id == chat_id)
                grp: Optional[GroupSetting] = await session.scalar(stmt)
                
                if grp:
                    # Создаем клавиатуру для выбора лимита
                    keyboard = []
                    for value in [1, 2, 3, 5, 10]:
                        keyboard.append([InlineKeyboardButton(
                            f"{value}", 
                            callback_data=f"set_link_spam_limit:{chat_id}:{value}")])
                    
                    keyboard.append([InlineKeyboardButton(
                        "« Назад к настройкам", 
                        callback_data=f"private:spam:{chat_id}")])
                    
                    await query.edit_message_text(
                        f"Текущий лимит ссылок: {grp.link_spam_limit}\n\n"
                        "Выберите новый лимит ссылок для группы:\n"
                        "Пользователь будет заблокирован, если отправит больше или равно указанного количества сообщений с ссылками "
                        "в течение интервала спама.",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    await query.answer("Ошибка: настройки группы не найдены.")
        except Exception as e:
            await query.answer(f"Произошла ошибка: {str(e)}")
    
    elif action == "set_link_spam_limit":
        # Устанавливаем новый лимит ссылок
        if len(parts) < 3:
            await query.edit_message_text("Ошибка: некорректный формат данных.")
            return
            
        try:
            new_limit = int(parts[2])
            if new_limit < 1:
                await query.edit_message_text("Лимит должен быть положительным числом.")
                return
                
            async with async_session() as session:
                from .db_utils import ensure_group
                grp = await ensure_group(session, chat_id)
                grp.link_spam_limit = new_limit
                await session.commit()
                
            # Возвращаемся к настройкам спама
            await query.edit_message_text(
                f"✅ Лимит ссылок установлен на {new_limit}.\n\nВозвращаемся к настройкам..."
            )
            
            # Переходим обратно к настройкам спама через 1 секунду
            from asyncio import sleep
            await sleep(1)
            await show_spam_settings(update, context)
            
        except ValueError:
            await query.edit_message_text("Ошибка: некорректное значение.")
            return


def get_spam_settings_handlers() -> List[BaseHandler]:
    """Return all handlers for spam settings."""
    return [
        # Обработчики для кнопок в интерфейсе настроек спама
        CallbackQueryHandler(spam_settings_callback, pattern=r"^(spam_limit|spam_interval|set_spam_limit|set_spam_interval|link_spam_toggle|link_spam_limit|set_link_spam_limit|show_banned|unban_user):"),
    ]

"""Handlers for private chat interactions with the bot."""
from __future__ import annotations

import logging

from sqlalchemy import select, func
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, filters, CallbackQueryHandler
from telegram.error import TelegramError

from ..database import async_session
from ..models import GroupSetting, Keyword
from .keyword_management import list_keywords_private
from ..access_control import restricted

logger = logging.getLogger(__name__)






@restricted
async def list_user_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all groups where the bot is present and the user is an admin."""
    try:
        if update.effective_chat is None or update.message is None or update.effective_user is None:
            logger.warning("Missing essential update fields")
            return
        logger.info(f"Chat type: {update.effective_chat.type}")
        logger.info(f"User ID: {update.effective_user.id}")
        logger.info(f"Message ID: {update.message.message_id}")
    
        # Only process in private chats
        if update.effective_chat.type != "private":
            logger.info(f"Команда вызвана не в личном чате, а в {update.effective_chat.type}")
            await update.message.reply_text("Эта команда доступна только в личных сообщениях с ботом.")
            return
        
        logger.info("Подтверждено: команда /groups вызвана в личном чате")
    
        user_id = update.effective_user.id
        bot_info = await context.bot.get_me()
        bot_id = bot_info.id
        logger.info(f"User ID: {user_id}, Bot ID: {bot_id}")
    
        # Get all groups where the bot is a member
        logger.info("Запрос к БД для получения групп")
        async with async_session() as session:
            groups_query = select(GroupSetting)
            groups = (await session.scalars(groups_query)).all()
        
        logger.info(f"Найдено групп в БД: {len(groups)}")
        if not groups:
            logger.info("Нет групп в базе данных, отправляем сообщение пользователю.")
            await update.message.reply_text("Я не состою ни в одной группе. Добавьте меня в группу, чтобы начать работу.")
            return
        
        logger.info(f"Группы в БД: {[group.chat_id for group in groups]}")
    
        # For each group, check if the user is an admin
        user_admin_groups = []
        logger.info("Начинаем проверку статуса администратора в группах")
    
        for group in groups:
            try:
                # Try to get chat member info
                logger.info(f"Проверяем группу: {group.chat_id}")
                member = await context.bot.get_chat_member(chat_id=group.chat_id, user_id=user_id)
                chat_info = await context.bot.get_chat(chat_id=group.chat_id)
                
                # Check if user is admin or owner in this group
                logger.info(f"Статус пользователя в группе {group.chat_id}: {member.status}")
                if member.status in ["administrator", "creator"]:
                    # Count keywords for this group
                    async with async_session() as session:
                        keyword_count = await session.scalar(
                            select(func.count()).select_from(Keyword).where(Keyword.group_id == group.id)
                        )
                    
                    user_admin_groups.append({
                        "id": group.id,
                        "chat_id": group.chat_id,
                        "title": chat_info.title,
                        "keyword_count": keyword_count or 0,
                    })
                    logger.info(f"Группа {group.chat_id} добавлена в список администрируемых")
            except TelegramError as e:
                # Bot might have been removed from the group or other error
                logger.warning(f"Couldn't get info for chat {group.chat_id}: {e}")
                continue
    
        if not user_admin_groups:
            logger.info("Пользователь не является администратором ни в одной группе с ботом")
            await update.message.reply_text("Вы не являетесь администратором ни в одной группе, где я состою.")
            return
        
        logger.info(f"Пользователь является администратором в {len(user_admin_groups)} группах")
    
        # Create a message with group list and management buttons
        message_text = "🔍 Группы, где вы администратор:\n\n"
        keyboard = []
        
        for idx, group in enumerate(user_admin_groups, start=1):
            message_text += f"{idx}. {group['title']} - {group['keyword_count']} ключевых слов\n"
            keyboard.append([
                InlineKeyboardButton(f"Управление {group['title']}", callback_data=f"private:manage:{group['chat_id']}")
            ])
        
        logger.info(f"Отправляем сообщение со списком {len(user_admin_groups)} групп")
        await update.message.reply_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info("Сообщение со списком групп отправлено")
    except Exception as e:
        logger.error(f"Ошибка в list_user_groups: {e}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)[:100]}")


@restricted
async def private_chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from private chat inline keyboards."""
    if not update.callback_query or not update.effective_user:
        return
        
    query = update.callback_query
    await query.answer()
    
    data = query.data or ""
    
    # Handle group management callbacks
    if data.startswith("private:manage:"):
        # Extract chat_id from callback data
        try:
            chat_id = int(data.split(":")[2])
        except (IndexError, ValueError):
            await query.edit_message_text("Произошла ошибка. Попробуйте снова.")
            return
        
        # Check if the user is still admin in this group
        try:
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=update.effective_user.id)
            if member.status not in ["administrator", "creator"]:
                await query.edit_message_text("Вы больше не администратор в этой группе.")
                return
        except TelegramError:
            await query.edit_message_text("Не удалось получить информацию о группе. Возможно, я был удален из группы.")
            return
        
        # Get group information
        chat = await context.bot.get_chat(chat_id=chat_id)
        
        # Get keyword count for this group
        async with async_session() as session:
            group = await session.scalar(
                select(GroupSetting).where(GroupSetting.chat_id == chat_id)
            )
            
            if not group:
                await query.edit_message_text("Данные о группе не найдены в базе данных.")
                return
                
            keyword_count = await session.scalar(
                select(func.count()).select_from(Keyword).where(Keyword.group_id == group.id)
            )
        
        # Create management keyboard for this group
        keyboard = [
            [InlineKeyboardButton("📝 Ключевые слова", callback_data=f"private:keywords:{chat_id}")],
            [InlineKeyboardButton("🛡️ Настройки антиспама", callback_data=f"private:spam:{chat_id}")],
            [InlineKeyboardButton("❓ Справка по группе", callback_data=f"private:help:{chat_id}")],
            [InlineKeyboardButton("« Назад к списку групп", callback_data="private:back_to_groups")]
        ]
        
        # Create group info message
        message_text = f"📣 Управление группой: {chat.title}\n\n"
        
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    # Return to group list
    elif data == "private:back_to_list" or data == "private:back_to_groups":
        await query.edit_message_text("Обновляю список групп...")
        
        # Получение списка групп напрямую, а не через list_user_groups
        # так как оригинальная функция ожидает message, а не callback_query
        user_id = update.effective_user.id
        bot_info = await context.bot.get_me()
        
        # Get all groups where the bot is a member
        async with async_session() as session:
            groups_query = select(GroupSetting)
            groups = (await session.scalars(groups_query)).all()
        
        if not groups:
            await query.edit_message_text("Я не состою ни в одной группе. Добавьте меня в группу, чтобы начать работу.")
            return
        
        # For each group, check if the user is an admin
        user_admin_groups = []
        
        for group in groups:
            try:
                # Try to get chat member info
                member = await context.bot.get_chat_member(chat_id=group.chat_id, user_id=user_id)
                chat_info = await context.bot.get_chat(chat_id=group.chat_id)
                
                # Check if user is admin or owner in this group
                if member.status in ["administrator", "creator"]:
                    # Count keywords for this group
                    async with async_session() as session:
                        keyword_count = await session.scalar(
                            select(func.count()).select_from(Keyword).where(Keyword.group_id == group.id)
                        )
                    
                    user_admin_groups.append({
                        "id": group.id,
                        "chat_id": group.chat_id,
                        "title": chat_info.title,
                        "keyword_count": keyword_count or 0,
                    })
            except TelegramError:
                # Bot might have been removed from the group or other error
                logger.warning(f"Couldn't get info for chat {group.chat_id}, might have been removed")
                continue
        
        if not user_admin_groups:
            await query.edit_message_text("Вы не являетесь администратором ни в одной группе, где я состою.")
            return
        
        # Create a message with group list and management buttons
        message_text = "🔍 Группы, где вы администратор:\n\n"
        keyboard = []
        
        for idx, group in enumerate(user_admin_groups, start=1):
            message_text += f"{idx}. {group['title']} - {group['keyword_count']} ключевых слов\n"
            keyboard.append([
                InlineKeyboardButton(f"Управление {group['title']}", callback_data=f"private:manage:{group['chat_id']}")
            ])
        
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    # Handle keyword management for a specific group
    elif data.startswith("private:keywords:"):
        try:
            chat_id = int(data.split(":")[2])
            
            # Set the chat_id in user_data so list_keywords_private knows which group to show
            context.user_data["selected_chat_id"] = chat_id
            await list_keywords_private(update, context)
            
        except (IndexError, ValueError) as e:
            logger.error(f"Error processing keyword callback: {e}")
            await query.edit_message_text("Произошла ошибка. Попробуйте снова.")
    
    # Handle spam settings for a specific group
    elif data.startswith("private:spam:"):
        try:
            chat_id = int(data.split(":")[2])
            chat = await context.bot.get_chat(chat_id=chat_id)
            
            # Call the spam settings functionality
            from .spam_settings import show_spam_settings
            
            # Set the chat_id in user_data so spam settings knows which group to show
            context.user_data["selected_chat_id"] = chat_id
            await show_spam_settings(update, context)
            
        except (IndexError, ValueError, TelegramError) as e:
            logger.error(f"Error processing spam settings callback: {e}")
            await query.edit_message_text("Произошла ошибка. Попробуйте снова.")
    
    # Handle help for a specific group
    elif data.startswith("private:help:"):
        try:
            chat_id = int(data.split(":")[2])
            chat = await context.bot.get_chat(chat_id=chat_id)
            
            # Get group settings and keyword count
            async with async_session() as session:
                group = await session.scalar(
                    select(GroupSetting).where(GroupSetting.chat_id == chat_id)
                )
                
                if not group:
                    await query.edit_message_text("Данные о группе не найдены в базе данных.")
                    return
                    
                keyword_count = await session.scalar(
                    select(func.count()).select_from(Keyword).where(Keyword.group_id == group.id)
                )
            
            # Create help keyboard with back button
            keyboard = [
                [InlineKeyboardButton("« Назад", callback_data=f"private:manage:{chat_id}")]
            ]
            
            # Prepare help text (используем HTML вместо Markdown для более надежного форматирования)
            help_text = f"<b>Настройки группы: {chat.title}</b>\n\n"
            
            # Добавляем все настройки группы в один раздел
            help_text += f"• Количество ключевых слов: {keyword_count or 0}\n"
            help_text += f"• Лимит спама: {group.spam_limit}\n"
            help_text += f"• Интервал спама: {group.spam_interval} сек.\n"
            help_text += f"• Лимит повторяющихся сообщений: {group.repeat_limit}\n"
            help_text += f"• Интервал повторяющихся сообщений: {group.repeat_interval} сек.\n"
            help_text += f"• Блокировка за ссылки: {'Включена ✅' if group.link_spam_enabled else 'Отключена ❌'}\n"
            help_text += f"• Лимит ссылок: {group.link_spam_limit}\n"
            
            # Используем HTML вместо Markdown
            await query.edit_message_text(
                help_text, 
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            
        except (IndexError, ValueError, TelegramError) as e:
            logger.error(f"Error processing help callback: {e}")
            await query.edit_message_text("Произошла ошибка. Попробуйте снова.")


def get_private_chat_handlers():
    """Return handlers for private chat functionality."""
    handlers = [
        CommandHandler("groups", list_user_groups, filters=filters.ChatType.PRIVATE),
        CallbackQueryHandler(private_chat_callback, pattern="^private:"),
    ]
    return handlers

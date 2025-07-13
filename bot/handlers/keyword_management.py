"""Keyword management system: add, remove, edit keywords via commands & inline buttons."""
from __future__ import annotations

import logging
from typing import Optional, List, Tuple

from sqlalchemy import select, delete
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from ..database import async_session
from ..models import GroupSetting, Keyword

logger = logging.getLogger(__name__)

# Conversation states
AWAITING_KEYWORD_PHRASE = 1
AWAITING_NEW_RESPONSE = 2
CONFIRM_DELETE = 3

# Callback data prefixes
CALLBACK_KEYWORD_PREFIX = "kw:"  # General prefix for keyword operations
CALLBACK_DELETE_PREFIX = "kw_del:"  # For delete operations
CALLBACK_EDIT_PREFIX = "kw_edit:"  # For edit operations

async def start_add_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation to add a new keyword."""
    query = update.callback_query
    await query.answer()

    try:
        chat_id = int(query.data.split(":")[2])
        context.user_data['selected_chat_id'] = chat_id
        chat = await context.bot.get_chat(chat_id)
    except (IndexError, ValueError, TelegramError) as e:
        logger.error(f"Error starting add_keyword conversation: {e}")
        await query.edit_message_text("Ошибка: не удалось определить группу. Попробуйте снова.")
        return ConversationHandler.END

    await query.edit_message_text(
        f"Добавление ключевого слова в группу <b>{chat.title}</b>.\n\n"
        f"Пожалуйста, отправьте фразу, на которую бот должен реагировать.",
        parse_mode="HTML"
    )
    return AWAITING_KEYWORD_PHRASE


async def get_keyword_phrase(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the keyword phrase and asks for the response."""
    if not update.message or not update.message.text:
        await update.message.reply_text("Это не похоже на текстовую фразу. Пожалуйста, попробуйте снова.")
        return AWAITING_KEYWORD_PHRASE

    phrase = update.message.text.strip().lower()
    context.user_data['new_keyword_phrase'] = phrase

    await update.message.reply_text(
        f"Отлично! Фраза '<b>{phrase}</b>' принята.\n\n"
        f"Теперь отправьте ответное сообщение (текст, фото, видео, документ), которое бот будет присылать.",
        parse_mode="HTML"
    )
    return AWAITING_NEW_RESPONSE


async def get_keyword_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the response, saves the keyword, and ends the conversation."""
    chat_id = context.user_data.get('selected_chat_id')
    phrase = context.user_data.get('new_keyword_phrase')
    message = update.message

    if not chat_id or not phrase or not message:
        await update.message.reply_text("Произошла ошибка, не хватает данных. Попробуйте начать заново.")
        return ConversationHandler.END

    async with async_session() as session:
        group_setting = await session.scalar(select(GroupSetting).where(GroupSetting.chat_id == chat_id))
        if not group_setting:
            await update.message.reply_text("Не удалось найти настройки для этой группы.")
            return ConversationHandler.END

        kw = Keyword(group_id=group_setting.id, phrase=phrase, lang="ru")

        if message.text:
            kw.response_text = message.text
        elif message.photo:
            kw.response_file_type = "photo"
            kw.response_file_id = message.photo[-1].file_id
        elif message.video:
            kw.response_file_type = "video"
            kw.response_file_id = message.video.file_id
        elif message.document:
            kw.response_file_type = "document"
            kw.response_file_id = message.document.file_id
        else:
            await message.reply_text("Этот тип контента не поддерживается. Пожалуйста, отправьте текст, фото, видео или документ.")
            return AWAITING_NEW_RESPONSE

        session.add(kw)
        await session.commit()

    keyboard = [[InlineKeyboardButton("« К управлению группой", callback_data=f"private:manage:{chat_id}")]]
    await message.reply_text("✅ Ключевое слово успешно добавлено!", reply_markup=InlineKeyboardMarkup(keyboard))

    # Clean up user_data
    context.user_data.pop('selected_chat_id', None)
    context.user_data.pop('new_keyword_phrase', None)

    return ConversationHandler.END


async def cancel_add_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the keyword adding process."""
    chat_id = context.user_data.get('selected_chat_id')
    await update.message.reply_text("Добавление ключевого слова отменено.")
    
    # Clean up user_data
    context.user_data.pop('selected_chat_id', None)
    context.user_data.pop('new_keyword_phrase', None)

    return ConversationHandler.END

async def receive_new_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receiving the new response for an edited keyword"""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    
    if not user or not chat or not message:
        return ConversationHandler.END
    
    keyword_id = context.user_data.get("editing_keyword_id")
    keyword_phrase = context.user_data.get("editing_keyword_phrase")
    
    if not keyword_id or not keyword_phrase:
        await message.reply_text("Ошибка: информация о редактируемом ключевом слове не найдена.")
        return ConversationHandler.END
    
    # Process the message based on its type
    content_type = None
    file_id = None
    text = None
    
    # Check for text message
    if message.text:
        content_type = "text"
        text = message.text
    # Check for photo
    elif message.photo:
        content_type = "photo"
        file_id = message.photo[-1].file_id  # Get the largest photo
    # Check for video
    elif message.video:
        content_type = "video"
        file_id = message.video.file_id
    # Check for document
    elif message.document:
        content_type = "document"
        file_id = message.document.file_id
    # Check for animation (GIF)
    elif message.animation:
        content_type = "animation"
        file_id = message.animation.file_id
    # Check for audio
    elif message.audio:
        content_type = "audio"
        file_id = message.audio.file_id
    # Check for voice message
    elif message.voice:
        content_type = "voice"
        file_id = message.voice.file_id
    # Check for sticker
    elif message.sticker:
        content_type = "sticker"
        file_id = message.sticker.file_id
    else:
        await message.reply_text("Неподдерживаемый тип сообщения. Пожалуйста, используйте текст, фото, видео или документ.")
        return AWAITING_NEW_RESPONSE
    
    # Now update the keyword response in the database
    async with async_session() as session:
        keyword = await session.get(Keyword, keyword_id)
        
        if not keyword:
            await message.reply_text(f"Ошибка: ключевое слово '{keyword_phrase}' не найдено.")
            return ConversationHandler.END
        
        # Update keyword response
        if content_type == "text":
            keyword.response_text = text
            keyword.response_file_id = None
            keyword.response_file_type = None
        else:
            keyword.response_text = None
            keyword.response_file_id = file_id
            keyword.response_file_type = content_type
        
        await session.commit()
        
        # Создаем временное сообщение с информацией об обновлении
        temp_message = await message.reply_text(
            f"Ответ для ключевого слова <b>{keyword_phrase}</b> успешно обновлен.",
            parse_mode="HTML"
        )
        
        # Create a dummy update to refresh the keywords list
        # We need to check if there's an existing callback query that started this operation
        for key, value in context.bot_data.items():
            if isinstance(key, str) and key.startswith('last_kw_query_') and value.get('user_id') == user.id:
                dummy_update = Update(0, value.get('callback_query'))
                await refresh_keywords_list(dummy_update, context)
                break
    
    # Clear the editing state
    if "editing_keyword_id" in context.user_data:
        del context.user_data["editing_keyword_id"]
    if "editing_keyword_phrase" in context.user_data:
        del context.user_data["editing_keyword_phrase"]
    
    return ConversationHandler.END

async def toggle_keyword_option(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword_id: int, option_name: str) -> None:
    """Toggle a boolean option for a keyword and refresh the edit view"""
    query = update.callback_query
    if not query:
        return
        
    
    await query.answer(f"Переключение опции {option_name}...")
    
    async with async_session() as session:
        keyword = await session.get(Keyword, keyword_id)
        if not keyword:
            await query.answer("Ключевое слово не найдено или уже удалено.")
            await refresh_keywords_list(update, context)
            return
        
        # Переключаем нужную опцию
        if option_name == "pattern":
            keyword.is_pattern = not keyword.is_pattern
        elif option_name == "case":
            keyword.case_sensitive = not keyword.case_sensitive
        elif option_name == "translit":
            keyword.transliterate_enabled = not keyword.transliterate_enabled
        elif option_name == "fuzzy":
            keyword.fuzzy_enabled = not keyword.fuzzy_enabled
        
        await session.commit()
    
    # Обновляем интерфейс
    await start_edit_keyword(update, context, keyword_id)

async def keyword_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all keyword-related callback queries"""
    query = update.callback_query
    await query.answer()
        
    data = query.data
    logger.info(f"Received keyword callback with data: {data}")
    
    # Handle back to keyword list action
    if data == "kw:back_to_list":
        logger.debug("Processing back to keyword list action")
        await refresh_keywords_list(update, context)
        return ConversationHandler.END  # Exit conversation if active
    
    # Handle cancel action (legacy support)
    if data == f"{CALLBACK_KEYWORD_PREFIX}cancel":
        logger.debug("Processing cancel action")
        await query.edit_message_text("Операция отменена.")
        return ConversationHandler.END  # Exit conversation
        
    # Handle delete operations
    if data.startswith(CALLBACK_DELETE_PREFIX):
        logger.debug(f"Processing delete operation with data: {data}")
        try:
            keyword_id = int(data[len(CALLBACK_DELETE_PREFIX):])
            logger.info(f"Attempting to delete keyword with ID: {keyword_id}")
            await delete_keyword_by_id(update, context, keyword_id)
            return ConversationHandler.END
        except (ValueError, IndexError) as e:
            logger.error(f"Error extracting keyword_id for deletion: {e}")
            await query.answer("Ошибка при удалении ключевого слова")
            return ConversationHandler.END
        
    # Handle edit operations
    if data.startswith(CALLBACK_EDIT_PREFIX):
        logger.debug(f"Processing edit operation with data: {data}")
        try:
            keyword_id = int(data[len(CALLBACK_EDIT_PREFIX):])
            logger.info(f"Attempting to edit keyword with ID: {keyword_id}")
            await start_edit_keyword(update, context, keyword_id)
            return
        except (ValueError, IndexError) as e:
            logger.error(f"Error extracting keyword_id for editing: {e}")
            await query.answer("Ошибка при редактировании ключевого слова")
            return
    
    # Обрабатываем новые опции переключения
    if data.startswith("kw_toggle_pattern:"):
        logger.debug("Processing toggle pattern option")
        keyword_id = int(data[len("kw_toggle_pattern:"):])
        await toggle_keyword_option(update, context, keyword_id, "pattern")
        return
        
    if data.startswith("kw_toggle_case:"):
        logger.debug("Processing toggle case option")
        keyword_id = int(data[len("kw_toggle_case:"):])
        await toggle_keyword_option(update, context, keyword_id, "case")
        return
        
    if data.startswith("kw_toggle_translit:"):
        logger.debug("Processing toggle translit option")
        keyword_id = int(data[len("kw_toggle_translit:"):])
        await toggle_keyword_option(update, context, keyword_id, "translit")
        return
        
    if data.startswith("kw_toggle_fuzzy:"):
        logger.debug("Processing toggle fuzzy option")
        keyword_id = int(data[len("kw_toggle_fuzzy:"):])
        await toggle_keyword_option(update, context, keyword_id, "fuzzy")
        return
        
    # Handle edit response button - now handled via entry point in conversation handler
    if data.startswith("kw_edit_resp:"):
        logger.debug("Processing edit response button")
        try:
            keyword_id = int(data[len("kw_edit_resp:"):])
            logger.info(f"Attempting to edit response for keyword with ID: {keyword_id}")
            return await start_edit_response(update, context, keyword_id)
        except (ValueError, IndexError) as e:
            logger.error(f"Error extracting keyword_id for response editing: {e}")
            await query.answer("Ошибка при редактировании ответа")
            return
            
    logger.warning(f"Unhandled keyword callback data: {data}")

async def delete_keyword_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword_id: int) -> None:
    """Delete a keyword by its ID and refresh the keywords list"""
    query = update.callback_query
    if not query or not query.message or not update.effective_chat:
        return
        
    
    async with async_session() as session:
        keyword = await session.get(Keyword, keyword_id)
        if not keyword:
            await query.answer("Ключевое слово не найдено или уже удалено.")
            # Refresh keyword list
            await refresh_keywords_list(update, context)
            return
        
        # Получаем данные о ключевом слове перед удалением
        phrase = keyword.phrase
        chat_id = None
        
        # Получаем chat_id от группы, которой принадлежит ключевое слово
        group = await session.get(GroupSetting, keyword.group_id)
        if group:
            chat_id = group.chat_id
        
        # Удаляем ключевое слово
        await session.delete(keyword)
        await session.commit()
        
        # Сохраняем chat_id в контексте для обновления списка
        if chat_id:
            context.user_data["selected_chat_id"] = chat_id
        
        # Show quick notification without changing the message
        await query.answer(f"Ключевое слово '{phrase}' удалено")
        
        try:
            # Если обрабатываем в приватном чате
            if update.effective_chat.type == "private" and chat_id:
                await list_keywords_private(update, context)
            else:
                # Иначе обычное обновление списка
                await refresh_keywords_list(update, context)
        except Exception as e:
            logger.error(f"Error updating keywords list after deletion: {e}")
            # Fallback to standard refresh
            await refresh_keywords_list(update, context)

async def start_edit_response(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword_id: int) -> int:
    """Start the process of editing a keyword response"""
    query = update.callback_query
    if not query or not query.message or not update.effective_user:
        return ConversationHandler.END
        
    
    async with async_session() as session:
        keyword = await session.get(Keyword, keyword_id)
        if not keyword:
            await query.answer("Ключевое слово не найдено или уже удалено.")
            return ConversationHandler.END
        
        # Сохраняем информацию о группе для корректного обновления списка
        group = await session.get(GroupSetting, keyword.group_id)
        if group and group.chat_id:
            context.user_data["selected_chat_id"] = group.chat_id
        
        # Store keyword information in user_data for later processing
        context.user_data["editing_keyword_id"] = keyword_id
        context.user_data["editing_keyword_phrase"] = keyword.phrase
        
        # Определяем текущий ответ для отображения
        if keyword.response_text:
            current_response = keyword.response_text
        elif keyword.response_file_type:
            current_response = f'[{keyword.response_file_type.upper()}]'
        else:
            current_response = '[пусто]'
        
        # Сохраняем текущий чат в контексте
        if hasattr(query.message, 'chat') and query.message.chat:
            context.user_data["edit_chat_id"] = query.message.chat.id
        
        # Сохраняем информацию о сообщении для последующего обновления
        context.user_data["edit_message_id"] = query.message.message_id
        
        await query.edit_message_text(
            f"Введите новый ответ для ключевого слова \"{keyword.phrase}\"."
            f"\n\nТекущий ответ: {current_response}"
            f"\n\nВы можете отправить текст, фото, видео или документ как новый ответ."
            f"\n\nДля отмены напишите /cancel"
        )
        
        # Отмечаем, что ответ на кнопку был обработан
        await query.answer()
        
        # Сохраняем callback query для последующего обновления списка
        context.bot_data[f'last_kw_query_{update.effective_user.id}'] = {
            'callback_query': query,
            'user_id': update.effective_user.id,
            'keyword_id': keyword_id
        }
        
        # Возвращаем состояние ожидания нового ответа
        await query.edit_message_text(
            f"Редактирование ответа для ключевого слова <b>{phrase}</b>\n\n"
            f"Отправьте новое сообщение-ответ (текст/фото/видео/документ).",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        
        # Set conversation state to wait for new response
        return AWAITING_NEW_RESPONSE

async def start_edit_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword_id: int) -> None:
    """Start the process of editing a keyword by its ID"""
    query = update.callback_query
    if not query or not query.message or not update.effective_user:
        return
        

    async with async_session() as session:
        keyword = await session.get(Keyword, keyword_id)
        if not keyword:
            await query.answer("Ключевое слово не найдено или уже удалено.")
            await refresh_keywords_list(update, context)
            return
        
        phrase = keyword.phrase
        response_text = keyword.response_text or ''
        response_file_type = keyword.response_file_type
        
        # Определяем текст для отображения
        if response_text:
            response_display = response_text
        elif response_file_type:
            response_display = f"[{response_file_type.upper()}]"
        else:
            response_display = "[пустой ответ]"
        
        # Store in context for the conversation
        context.user_data["editing_keyword_id"] = keyword_id
        context.user_data["editing_keyword_phrase"] = phrase
        
        # Текст состояния для опций
        pattern_status = "✅" if keyword.is_pattern else "❌"
        case_status = "✅" if keyword.case_sensitive else "❌"
        translit_status = "✅" if keyword.transliterate_enabled else "❌"
        fuzzy_status = "✅" if keyword.fuzzy_enabled else "❌"
        
        # Create keyboard with options
        keyboard = [
            [InlineKeyboardButton("Редактировать ответ", callback_data=f"kw_edit_resp:{keyword_id}")],
            [InlineKeyboardButton(f"Паттерн: {pattern_status}", callback_data=f"kw_toggle_pattern:{keyword_id}")],
            [InlineKeyboardButton(f"Учитывать регистр: {case_status}", callback_data=f"kw_toggle_case:{keyword_id}")],
            [InlineKeyboardButton(f"Транслитерация: {translit_status}", callback_data=f"kw_toggle_translit:{keyword_id}")],
            [InlineKeyboardButton(f"Нечеткий поиск: {fuzzy_status}", callback_data=f"kw_toggle_fuzzy:{keyword_id}")],
            [InlineKeyboardButton("Удалить ключевое слово", callback_data=f"{CALLBACK_DELETE_PREFIX}{keyword_id}")],
            [InlineKeyboardButton("« Назад к списку", callback_data="kw:back_to_list")]
        ]
        
        # Текст для отображения расширенных настроек
        settings_text = (
            f"\nНастройки:\n"
            f"• Паттерн: {pattern_status}\n"
            f"• Учитывать регистр: {case_status}\n"
            f"• Транслитерация: {translit_status}\n"
            f"• Нечеткий поиск: {fuzzy_status}"
        )
        
        # Show keyword details and options
        await query.edit_message_text(
            f"Ключевое слово: <b>{phrase}</b>\n\n" 
            f"Текущий ответ: {response_display[:100]}{'...' if len(response_display) > 100 else ''}\n"
            f"{settings_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

# Helper functions for keyword operations

async def refresh_keywords_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh the keywords list inline without creating a new message"""
    query = update.callback_query
    if not query or not query.message:
        return
    
    # Use selected_chat_id from user_data if available, otherwise use effective_chat.id
    chat_id = context.user_data.get("selected_chat_id") or (update.effective_chat.id if update.effective_chat else None)
    if not chat_id:
        await query.answer("Ошибка: не удалось определить ID чата")
        return

    async with async_session() as session:
        keywords = await get_all_keywords(session, chat_id)
        
        if not keywords:
            # If no keywords left, show empty message
            await query.edit_message_text(
                "Список ключевых слов пуст.", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="panel:back")]])
            )
            return
        
        # Create inline keyboard with action buttons
        keyboard = []
        for kw in keywords:
            keyboard.append([
                InlineKeyboardButton(f"{kw.phrase}", callback_data=f"{CALLBACK_EDIT_PREFIX}{kw.id}"),
                InlineKeyboardButton("❌", callback_data=f"{CALLBACK_DELETE_PREFIX}{kw.id}")
            ])
        
        # Add back button
        keyboard.append([InlineKeyboardButton("« Назад", callback_data="panel:back")])
        
        await query.edit_message_text(
            "Управление ключевыми словами:\nНажмите на слово для редактирования или на ❌ для удаления",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def find_keyword(session, chat_id: int, phrase: str) -> Optional[Keyword]:
    """Find a keyword by phrase in the given chat"""
    stmt = (
        select(Keyword)
        .join(GroupSetting)
        .where(GroupSetting.chat_id == chat_id, Keyword.phrase == phrase)
    )
    return await session.scalar(stmt)

async def get_all_keywords(session, chat_id: int) -> List[Keyword]:
    """Get all keywords for the given chat"""
    stmt = (
        select(Keyword)
        .join(GroupSetting)
        .where(GroupSetting.chat_id == chat_id)
        .order_by(Keyword.phrase)
    )
    return (await session.scalars(stmt)).all()

# Handlers setup

# Wrapper function to extract keyword_id from callback data
async def handle_edit_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Extract keyword_id from callback data and pass to start_edit_response"""
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    
    # Extract keyword_id from callback data (format: "kw_edit_resp:ID")
    try:
        keyword_id = int(query.data.split(":", 1)[1])
        return await start_edit_response(update, context, keyword_id)
    except (ValueError, IndexError):
        await query.answer("Invalid keyword ID")
        return ConversationHandler.END

def get_edit_keyword_conversation_handler() -> ConversationHandler:
    """Create conversation handler for editing keywords via inline buttons"""
    return ConversationHandler(
        entry_points=[
            # Entry points for both viewing keyword details and editing response
            CallbackQueryHandler(keyword_button_callback, pattern=f"^{CALLBACK_EDIT_PREFIX}"),
            # Add the edit response button pattern as an entry point too
            CallbackQueryHandler(handle_edit_response, pattern=f"^kw_edit_resp:")
        ],
        states={
            AWAITING_NEW_RESPONSE: [
                MessageHandler(
                    # Используем общий фильтр для поддержки всех типов сообщений
                    filters.ALL & ~filters.COMMAND, 
                    receive_new_response
                )
            ],
        },
        fallbacks=[
            CallbackQueryHandler(keyword_button_callback, pattern=f"^{CALLBACK_KEYWORD_PREFIX}cancel"),
            CallbackQueryHandler(keyword_button_callback, pattern="^kw:back_to_list"),
            CommandHandler('cancel', cancel_add_keyword)
        ],
        name="edit_keyword_conversation",
        persistent=False,
        allow_reentry=True
    )

async def list_keywords_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List keywords for a specific group when accessed through private chat."""
    if not update.effective_user or not update.callback_query:
        return
    
    # Get the selected chat_id from user_data
    chat_id = context.user_data.get("selected_chat_id")
    if not chat_id:
        await update.callback_query.edit_message_text(
            "Ошибка: не выбрана группа для управления ключевыми словами."
        )
        return
    
    # Verify the user is still an admin in this chat
    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=update.effective_user.id)
        if member.status not in ["administrator", "creator"]:
            await update.callback_query.edit_message_text("Вы больше не администратор в этой группе.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.callback_query.edit_message_text("Не удалось проверить права администратора в группе.")
        return
    
    # Get the chat title
    chat = await context.bot.get_chat(chat_id=chat_id)
    
    # Get keywords for this group
    async with async_session() as session:
        group = await session.scalar(
            select(GroupSetting).where(GroupSetting.chat_id == chat_id)
        )
        if not group:
            await update.callback_query.edit_message_text("Данные о группе не найдены в базе данных.")
            return
            
        keywords = await get_all_keywords(session, chat_id)
        
        # Create inline keyboard with edit/delete buttons
        keyboard = []
        for kw in keywords:
            keyboard.append([
                InlineKeyboardButton(f"{kw.phrase}", callback_data=f"{CALLBACK_EDIT_PREFIX}{kw.id}"),
                InlineKeyboardButton("❌", callback_data=f"{CALLBACK_DELETE_PREFIX}{kw.id}")
            ])
        
        # Add button to add a new keyword and to return to group management
        keyboard.append([InlineKeyboardButton("➕ Добавить слово", callback_data=f"kw:add_start:{chat_id}")])
        keyboard.append([InlineKeyboardButton("« Назад к управлению группой", callback_data=f"private:manage:{chat_id}")])
        
        # Prepare message text
        message_text = f"Ключевые слова группы {chat.title}:\n"
        message_text += "Нажмите на слово для редактирования или на ❌ для удаления"
        if not keywords:
            message_text += "\n\nСписок ключевых слов пуст."
        
        # Edit the message with new content
        await update.callback_query.edit_message_text(
            text=message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


def get_keyword_management_handlers():
    """Return all handlers for keyword management"""
    add_keyword_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_keyword, pattern="^kw:add_start:")],
        states={
            AWAITING_KEYWORD_PHRASE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_keyword_phrase)],
            AWAITING_NEW_RESPONSE: [MessageHandler(filters.ALL & ~filters.COMMAND, get_keyword_response)],
        },
        fallbacks=[CommandHandler('cancel', cancel_add_keyword)],
        per_message=False,
        name="add_keyword_conversation",
    )

    # This handler will manage all non-conversation callbacks for keywords
    # (delete, toggle, back, etc.)
    keyword_callbacks = CallbackQueryHandler(
        keyword_button_callback, 
        pattern=f"^({CALLBACK_DELETE_PREFIX}|{CALLBACK_EDIT_PREFIX}|kw_toggle_pattern:|kw_toggle_case:|kw_toggle_translit:|kw_toggle_fuzzy:|kw:back_to_list)"
    )

    return [
        add_keyword_conv_handler,
        get_edit_keyword_conversation_handler(), # Handles its own entry point
        keyword_callbacks, # Handles simple button presses
    ]

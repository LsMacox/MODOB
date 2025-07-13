"""Core message handler: detects keywords and sends responses."""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
from difflib import SequenceMatcher
from typing import Iterable, Optional, Dict, Tuple

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from sqlalchemy import select

from ..anti_spam import check_spam
from ..cache import file_cache
from ..database import async_session
from ..models import GroupSetting, Keyword

logger = logging.getLogger(__name__)

# Словарь для транслитерации русских букв в английские
RU_TO_EN: Dict[str, str] = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
    'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
}

# Словарь для транслитерации английских букв в русские
EN_TO_RU: Dict[str, str] = {
    'a': 'а', 'b': 'б', 'c': 'к', 'd': 'д', 'e': 'е', 'f': 'ф', 'g': 'г',
    'h': 'х', 'i': 'и', 'j': 'дж', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н',
    'o': 'о', 'p': 'п', 'q': 'к', 'r': 'р', 's': 'с', 't': 'т', 'u': 'у',
    'v': 'в', 'w': 'в', 'x': 'кс', 'y': 'й', 'z': 'з',
    'ch': 'ч', 'sh': 'ш', 'sch': 'щ', 'zh': 'ж', 'ts': 'ц',
    'yu': 'ю', 'ya': 'я', 'yo': 'ё'
}

def transliterate_ru_to_en(text: str) -> str:
    """Транслитерация русского текста в английский"""
    result = ''
    for char in text.lower():
        if char in RU_TO_EN:
            result += RU_TO_EN[char]
        else:
            result += char
    return result

def transliterate_en_to_ru(text: str) -> str:
    """Транслитерация английского текста в русский"""
    # Сначала обрабатываем двойные символы
    for en_combo, ru_char in [(k, v) for k, v in EN_TO_RU.items() if len(k) > 1]:
        text = text.replace(en_combo, ru_char)
    
    result = ''
    i = 0
    while i < len(text):
        char = text[i].lower()
        if char in EN_TO_RU and len(char) == 1:
            result += EN_TO_RU[char]
        else:
            result += char
        i += 1
    return result

def match_with_pattern(text: str, pattern: str) -> bool:
    """Проверяет, соответствует ли текст заданному паттерну"""
    # Используем fnmatch для поддержки простых шаблонов (* и ?)
    return fnmatch.fnmatch(text, pattern)

def fuzzy_match(text: str, phrase: str, threshold: float = 0.9) -> bool:
    """Нечеткое сравнение текста с фразой с заданным порогом схожести"""
    # Используем SequenceMatcher для нечеткого сравнения
    matcher = SequenceMatcher(None, text, phrase)
    similarity = matcher.ratio()
    return similarity >= threshold

def match_keyword(text: str, keyword: Keyword) -> Tuple[bool, float]:
    """Проверяет, соответствует ли текст ключевому слову с учетом всех опций"""
    phrase = keyword.phrase
    original_text = text
    
    # Применяем чувствительность к регистру
    if not keyword.case_sensitive:
        text = text.lower()
        phrase = phrase.lower()
    
    # Стандартная проверка на включение
    if phrase in text:
        return True, 1.0
    
    # Если включен паттерн, проверяем соответствие паттерну
    if keyword.is_pattern and match_with_pattern(text, phrase):
        return True, 1.0
    
    # Проверяем транслитерацию, если она включена
    if keyword.transliterate_enabled:
        # Пробуем транслитерировать русский текст в английский
        trans_text_en = transliterate_ru_to_en(text)
        if phrase in trans_text_en:
            return True, 1.0
            
        # Пробуем транслитерировать английский текст в русский
        trans_text_ru = transliterate_en_to_ru(text)
        if phrase in trans_text_ru:
            return True, 1.0
    
    # Проверяем нечеткое совпадение, если оно включено
    if keyword.fuzzy_enabled:
        # Порог схожести установлен на 90%
        threshold = 0.9
        
        # Проверяем схожесть с оригинальным текстом
        if fuzzy_match(text, phrase, threshold):
            return True, 1.0
            
        # Если включена транслитерация, проверяем схожесть с транслитерированным текстом
        if keyword.transliterate_enabled:
            if fuzzy_match(transliterate_ru_to_en(text), phrase, threshold):
                return True, 1.0
            if fuzzy_match(transliterate_en_to_ru(text), phrase, threshold):
                return True, 1.0
    
    return False, 0.0


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

    # Получаем текст сообщения (не приводим к нижнему регистру сразу, это делается в match_keyword при необходимости)
    text = update.message.text or ""
    if not text:
        return

    async with async_session() as session:
        kw_stmt = (
            select(Keyword)
            .join(GroupSetting)
            .where(GroupSetting.chat_id == update.effective_chat.id)
        )
        kw_rows: Iterable[Keyword] = (await session.scalars(kw_stmt)).all()
        
        # Поиск соответствующего ключевого слова с учетом всех опций
        best_match = None
        best_match_score = 0.0
        
        for kw in kw_rows:
            is_match, score = match_keyword(text, kw)
            if is_match and score > best_match_score:
                best_match = kw
                best_match_score = score
                # Если нашли точное совпадение (score = 1.0), можно прекратить поиск
                if score >= 1.0:
                    break
        
        # Отвечаем на первое найденное совпадение
        if best_match:
            await _respond(update, context, best_match)


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

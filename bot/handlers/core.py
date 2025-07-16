"""Core message handler: detects keywords and sends responses."""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
from difflib import SequenceMatcher
from typing import Iterable, Optional, Dict, Tuple, List

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from sqlalchemy import select

from ..anti_spam import check_spam
from ..cache import file_cache
from ..database import async_session
from ..models import GroupSetting, Keyword

# Максимальный размер скользящего окна для проверки очень длинных сообщений
MAX_WINDOW_SIZE = 200  # символов

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
    # Для длинных текстов используем оптимизированный подход
    if len(text) > 500:  # Порог для оптимизации
        return optimized_pattern_match(text, pattern)
    
    # Для коротких текстов используем обычный fnmatch
    return fnmatch.fnmatch(text, pattern)

def optimized_pattern_match(text: str, pattern: str) -> bool:
    """Оптимизированная версия проверки шаблонов для длинных текстов"""
    # Если в шаблоне нет специальных символов (* и ?), то просто проверяем наличие подстроки
    if '*' not in pattern and '?' not in pattern:
        return pattern in text
    
    # Если шаблон начинается и заканчивается звездочками (*pattern*), ищем только середину
    if pattern.startswith('*') and pattern.endswith('*') and pattern.count('*') == 2 and '?' not in pattern:
        # Извлекаем текст между звездочками и ищем его в тексте
        middle_pattern = pattern[1:-1]
        return middle_pattern in text
    
    # Если шаблон начинается с звездочки (*pattern), проверяем окончание текста
    if pattern.startswith('*') and not pattern.endswith('*') and pattern.count('*') == 1 and '?' not in pattern:
        suffix = pattern[1:]
        return text.endswith(suffix)
    
    # Если шаблон заканчивается на звездочку (pattern*), проверяем начало текста
    if pattern.endswith('*') and not pattern.startswith('*') and pattern.count('*') == 1 and '?' not in pattern:
        prefix = pattern[:-1]
        return text.startswith(prefix)
    
    # Для более сложных шаблонов применяем подход со скользящим окном
    # Преобразуем шаблон в регулярное выражение
    import re
    # Преобразование шаблона fnmatch в regex
    regex_pattern = fnmatch.translate(pattern)
    
    # Используем скользящее окно для длинных текстов
    regex = re.compile(regex_pattern)
    
    # Размер окна - больше для обработки длинных ключевых слов
    window_size = min(len(text), MAX_WINDOW_SIZE * 2)
    overlap = min(200, window_size // 2)  # Перекрытие окон для обеспечения непрерывности
    
    # Проверяем каждое окно текста
    for i in range(0, len(text), window_size - overlap):
        chunk = text[i:i + window_size]
        if regex.fullmatch(chunk):
            return True
        
        # Проверяем также на совпадение частично или внутри чанка
        if regex.search(chunk):
            return True
    
    # Если прошли весь текст и не нашли совпадений
    return False

def fuzzy_match(text: str, phrase: str, threshold: float = 0.9) -> bool:
    """Нечеткое сравнение для поиска похожей подстроки в тексте, с учетом вариаций длины"""
    if not phrase:
        return False
    
    # Для длинных текстов применяем оптимизированный подход
    if len(text) > 200:  # Порог для оптимизации
        return optimized_fuzzy_match(text, phrase, threshold)
    
    # Для коротких текстов используем прежний алгоритм
    p_len = len(phrase)
    t_len = len(text)
    if t_len < int(p_len * 0.8):
        return False
    min_len = max(1, int(p_len * 0.8))
    max_len = int(p_len * 1.2) + 1
    for l in range(min_len, max_len + 1):
        for i in range(t_len - l + 1):
            substring = text[i:i + l]
            matcher = SequenceMatcher(None, substring, phrase)
            if matcher.ratio() >= threshold:
                return True
    return False

def optimized_fuzzy_match(text: str, phrase: str, threshold: float = 0.9) -> bool:
    """Оптимизированная версия fuzzy_match для длинных текстов"""
    p_len = len(phrase)
    
    # Разбиваем текст на слова
    words = text.split()
    
    # Предварительная фильтрация: сначала проверяем отдельные слова и их окружение
    # Это позволяет избежать проверки всех возможных подстрок
    for i, word in enumerate(words):
        # Если слово близко к фразе по длине, проверим его
        if 0.5 <= len(word) / p_len <= 2.0:
            matcher = SequenceMatcher(None, word, phrase)
            if matcher.ratio() >= threshold * 0.8:  # Немного снижаем порог для первичной проверки
                return True
        
        # Проверяем группы из 2-3 слов (если возможно)
        if i < len(words) - 1:
            two_words = ' '.join(words[i:i+2])
            if 0.7 <= len(two_words) / p_len <= 1.5:
                matcher = SequenceMatcher(None, two_words, phrase)
                if matcher.ratio() >= threshold:
                    return True
        
        if i < len(words) - 2:
            three_words = ' '.join(words[i:i+3])
            if 0.8 <= len(three_words) / p_len <= 1.3:
                matcher = SequenceMatcher(None, three_words, phrase)
                if matcher.ratio() >= threshold:
                    return True
    
    # Применяем скользящее окно
    return sliding_window_match(text, phrase, threshold)

def sliding_window_match(text: str, phrase: str, threshold: float = 0.9) -> bool:
    """Use sliding window approach to find fuzzy matches in long text"""
    # Быстрый путь: проверим сначала прямое вхождение подстроки
    if phrase.lower() in text.lower():
        return True
    
    # Специальная обработка для очень коротких слов (без пробелов)
    if ' ' not in phrase and len(phrase) <= 10:
        # Ищем среди слов текста похожие слова
        text_words = text.lower().split()
        phrase_lower = phrase.lower()
        
        for word in text_words:
            # Выбираем порог в зависимости от длины слова
            word_threshold = 0.7 if len(phrase) <= 4 else 0.75 if len(phrase) <= 6 else 0.8
            similarity = SequenceMatcher(None, word, phrase_lower).ratio()
            
            # Для длинных слов (как компьютер) достаточно более высокого порога
            if len(word) >= 8 and len(phrase) >= 8 and abs(len(word) - len(phrase)) <= 2:
                word_threshold = 0.85
                
            if similarity >= word_threshold:
                # Дополнительная проверка для отсечения ложных совпадений
                if len(word) == len(phrase) or abs(len(word) - len(phrase)) <= 2:
                    # Если длина слов похожа и они очень похожи, то это скорее всего опечатка
                    return True
    
    # Проверяем последовательность слов, если это возможно
    # Это хорошо работает для ключевых фраз, состоящих из нескольких слов
    text_words = text.lower().split()
    phrase_words = phrase.lower().split()
    
    if len(phrase_words) > 1:
        # Для точных совпадений слов
        for i in range(len(text_words) - len(phrase_words) + 1):
            exact_match = True
            for j in range(len(phrase_words)):
                if text_words[i+j] != phrase_words[j]:
                    exact_match = False
                    break
            if exact_match:
                return True
        
        # Проверка небольших опечаток в словах: сначала проверяем, насколько каждое слово совпадает
        for i in range(len(text_words) - len(phrase_words) + 1):
            # Для каждого слова рассчитываем коэффициент сходства
            word_similarities = []
            for j in range(len(phrase_words)):
                word_similarity = SequenceMatcher(None, text_words[i+j], phrase_words[j]).ratio()
                word_similarities.append(word_similarity)
            
            # Рассчитываем средний коэффициент сходства для всех слов в последовательности
            avg_word_similarity = sum(word_similarities) / len(word_similarities)
            # И количество слов с высоким совпадением
            high_similarity_count = sum(1 for sim in word_similarities if sim >= 0.9)
            
            # Проверяем разные условия для определения совпадения с небольшими опечатками:
            # 1. Средний коэффициент сходства по всем словам высокий И большинство слов точно совпадают
            # 2. Только одно слово имеет опечатку, а остальные точно совпадают
            if (avg_word_similarity >= 0.9 and high_similarity_count >= len(phrase_words) * 0.7) or \
               (len(phrase_words) >= 3 and high_similarity_count >= len(phrase_words) - 1):
                # Проверяем все выражение целиком для уверенности
                window = text_words[i:i+len(phrase_words)]
                window_text = ' '.join(window)
                phrase_text = phrase.lower()
                total_ratio = SequenceMatcher(None, window_text, phrase_text).ratio()
                
                # Для длинных фраз требования могут быть ниже, для коротких - выше
                min_threshold = 0.9 if len(phrase_words) >= 4 else 0.92
                if total_ratio >= min_threshold:
                    return True
    
    # Для коротких фраз и одиночных слов с опечатками
    if len(phrase) <= 15 or ' ' not in phrase:
        # Ищем похожие фрагменты в тексте
        best_ratio = 0
        phrase_lower = phrase.lower()
        
        # Для коротких фраз проверяем скользящим окном близким по размеру к самой фразе
        window_size = min(len(phrase) * 2, 30)
        for i in range(len(text) - window_size + 1):
            chunk = text[i:i+window_size].lower()
            ratio = SequenceMatcher(None, chunk, phrase_lower).ratio()
            best_ratio = max(best_ratio, ratio)
            
            # Нашли очень близкое совпадение
            if ratio >= 0.9:
                return True
    
    # Для длинных текстов используем скользящее окно большего размера
    window_size = min(200, max(len(text), len(phrase) * 3))
    overlap = len(phrase)
    
    for i in range(0, len(text), window_size - overlap):
        chunk = text[i:i + window_size]
        if len(chunk) < len(phrase) // 2:  # Пропускаем короткие куски
            continue
        
        # Применяем нечеткое сравнение с корректированным порогом
        # Для фраз разной длины используем разные пороги
        adjusted_threshold = threshold
        if ' ' not in phrase:
            if len(phrase) < 7:  # Для очень коротких слов
                # Для очень коротких слов снижаем порог сильнее
                adjusted_threshold = 0.8
            elif len(phrase) < 10:
                # Для коротких одиночных слов допускаем опечатки, снижаем порог
                adjusted_threshold = 0.85
        elif len(phrase.split()) >= 4:
            # Для длинных фраз повышаем требования
            adjusted_threshold = max(threshold, 0.95)
        else:
            # Для средних фраз средний порог
            adjusted_threshold = max(threshold, 0.9)
            
        if SequenceMatcher(None, chunk.lower(), phrase.lower()).ratio() >= adjusted_threshold:
            return True
    
    return False

def sliding_window_match_direct(text: str, phrase: str, keyword: Keyword) -> bool:
    """Проверяет, содержит ли текст ключевое слово с помощью скользящего окна"""
    p_len = len(phrase)
    t_len = len(text)
    window_size = min(t_len, MAX_WINDOW_SIZE)
    
    for i in range(t_len - window_size + 1):
        window = text[i:i + window_size]
        if keyword.transliterate_enabled:
            window_en = transliterate_ru_to_en(window)
            if phrase in window_en:
                return True
        else:
            if phrase in window:
                return True
    
    return False

def match_keyword(text: str, keyword: Keyword) -> Tuple[bool, float]:
    """Проверяет, соответствует ли текст ключевому слову с учетом всех опций"""
    phrase = keyword.phrase
    
    # Применяем чувствительность к регистру
    if not keyword.case_sensitive:
        text = text.lower()
        phrase = phrase.lower()
    
    # Для очень длинных сообщений и коротких ключевых слов
    # применяем окно скольжения сразу
    if len(text) > 1000 and len(phrase) < 10 and not keyword.is_pattern:
        return sliding_window_match_direct(text, phrase, keyword), 0.95
    
    # Стандартная проверка на включение
    if phrase in text:
        return True, 1.0
        
    # Проверка по шаблону, если включено
    if keyword.is_pattern:
        if match_with_pattern(text, phrase):
            return True, 1.0
    
    # Проверяем транслитерацию, если включена
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
    text = update.message.text or update.message.caption or ""
    if not text:
        return
    
    # Для длинных сообщений логируем длину для отладки
    if len(text) > 500:
        logger.debug(f"Processing long message: {len(text)} characters")

    try:
        async with asyncio.timeout(2):  # Таймаут 2 секунды на поиск ключевых слов
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
    except asyncio.TimeoutError:
        logger.warning(f"Keyword detection timed out for message of length {len(text)}")
        # Для таймаутов при длинных сообщениях применяем упрощенный алгоритм поиска
        # только для прямых совпадений (без нечёткого поиска)
        await process_long_message_with_timeout(update, context, text)


async def process_long_message_with_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """
    Обрабатывает длинные сообщения, которые вызвали таймаут при обычной обработке.
    Использует более простой и быстрый алгоритм поиска ключевых слов (только прямое совпадение).
    """
    try:
        # Получаем все ключевые слова для группы
        async with async_session() as session:
            kw_stmt = (
                select(Keyword)
                .join(GroupSetting)
                .where(GroupSetting.chat_id == update.effective_chat.id)
            )
            kw_rows: Iterable[Keyword] = (await session.scalars(kw_stmt)).all()
            
            # Используем более быстрый алгоритм: проверяем только прямое включение фразы
            # без нечеткого поиска и других ресурсоемких операций
            for kw in kw_rows:
                phrase = kw.phrase
                
                # Применяем чувствительность к регистру
                if not kw.case_sensitive:
                    check_text = text.lower()
                    check_phrase = phrase.lower()
                else:
                    check_text = text
                    check_phrase = phrase
                
                # Проверяем прямое включение фразы в тексте
                if check_phrase in check_text:
                    await _respond(update, context, kw)
                    return
                
                # Для коротких фраз проверяем скользящим окном
                if len(phrase) < 15:
                    # Разбиваем длинное сообщение на части по MAX_WINDOW_SIZE символов с перекрытием
                    chunk_size = MAX_WINDOW_SIZE
                    overlap = len(phrase)
                    for i in range(0, len(check_text), chunk_size - overlap):
                        chunk = check_text[i:i + chunk_size]
                        if check_phrase in chunk:
                            await _respond(update, context, kw)
                            return
    except Exception as e:
        logger.error(f"Error in processing long message: {e}")


async def _respond(update: Update, context: ContextTypes.DEFAULT_TYPE, kw: Keyword) -> None:
    response_text = kw.response_text
    
    # Обработка подстановки упоминания пользователя, если указан mention_tag
    if kw.response_text and kw.mention_tag and kw.mention_tag in kw.response_text:
        # Получаем информацию о пользователе
        user = update.message.from_user
        user_mention = f"@{user.username}" if user.username else user.first_name
        
        # Заменяем тег на упоминание пользователя
        response_text = kw.response_text.replace(kw.mention_tag, user_mention)
        await update.message.reply_text(response_text)
        return
    elif kw.response_text:
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

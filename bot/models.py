"""SQLAlchemy models for group settings and keywords."""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

class GroupSetting(Base):
    __tablename__ = "group_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)

    # Anti-spam per-group config (overrides defaults)
    spam_limit: Mapped[int] = mapped_column(Integer, default=5)
    spam_interval: Mapped[int] = mapped_column(Integer, default=10)
    repeat_limit: Mapped[int] = mapped_column(Integer, default=3)
    repeat_interval: Mapped[int] = mapped_column(Integer, default=10)
    link_spam_limit: Mapped[int] = mapped_column(Integer, default=3)  # Количество ссылок до блокировки
    link_spam_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # Включена ли блокировка за ссылки

    keywords: Mapped[list["Keyword"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )

class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("group_settings.id", ondelete="CASCADE"))

    phrase: Mapped[str] = mapped_column(String(255), index=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response_file_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # photo, video, document
    lang: Mapped[str] = mapped_column(String(5), default="ru")  # ru or en
    
    # Новые поля для расширенных опций ключевых слов
    is_pattern: Mapped[bool] = mapped_column(Boolean, default=False)  # Использовать ли паттерн-матчинг
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)  # Учитывать ли регистр
    transliterate_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # Включить транслитерацию
    fuzzy_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # Включить нечеткое сравнение

    group: Mapped["GroupSetting"] = relationship(back_populates="keywords")

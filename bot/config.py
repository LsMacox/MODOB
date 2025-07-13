"""Configuration loader using environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

@dataclass
class Settings:
    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # Database
    DB_HOST: str = os.getenv("DB_HOST", "db")
    DB_PORT: str = os.getenv("DB_PORT", "5432")
    DB_NAME: str = os.getenv("DB_NAME", "botdb")
    DB_USER: str = os.getenv("DB_USER", "bot")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "botpass")

    # Anti-spam defaults (can be overridden per-group by admin)
    SPAM_LIMIT: int = int(os.getenv("SPAM_LIMIT", 5))          # msgs
    SPAM_INTERVAL: int = int(os.getenv("SPAM_INTERVAL", 10))   # seconds
    REPEAT_LIMIT: int = int(os.getenv("REPEAT_LIMIT", 3))      # identical msgs
    REPEAT_INTERVAL: int = int(os.getenv("REPEAT_INTERVAL", 10))
    LINK_SPAM_LIMIT: int = int(os.getenv("LINK_SPAM_LIMIT", 3))  # ссылок до блокировки
    LINK_SPAM_ENABLED: bool = os.getenv("LINK_SPAM_ENABLED", "1").lower() in ("1", "true", "yes")

    # Cache
    FILE_ID_TTL: int = 60 * 60 * 12  # 12h in seconds
    
    # Access control
    ALLOWED_USERS: List[int] = field(
        default_factory=lambda: [int(id) for id in os.getenv("ALLOWED_USERS", "").split(",") if id.strip()]
    )
    NOTIFY_ON_UNAUTHORIZED: bool = os.getenv("NOTIFY_ON_UNAUTHORIZED", "1").lower() in ("1", "true", "yes")
    UNAUTHORIZED_MESSAGE: str = os.getenv(
        "UNAUTHORIZED_MESSAGE", 
        "У вас нет доступа к этому боту. Обратитесь к администратору."
    )

settings = Settings()

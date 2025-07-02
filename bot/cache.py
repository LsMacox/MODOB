"""In-memory TTL cache for Telegram file_id re-use."""
from cachetools import TTLCache

from .config import settings

file_cache: TTLCache[str, str] = TTLCache(maxsize=2048, ttl=settings.FILE_ID_TTL)

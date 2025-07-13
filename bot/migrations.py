"""Integration with Alembic migrations."""
from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic import command

logger = logging.getLogger(__name__)

def get_alembic_config():
    """Get Alembic configuration."""
    # Определяем путь к корневой директории проекта
    project_root = Path(__file__).parent.parent.absolute()
    alembic_ini = project_root / "alembic.ini"
    
    if not alembic_ini.exists():
        logger.error(f"Alembic config not found at {alembic_ini}")
        return None
    
    alembic_cfg = Config(str(alembic_ini))
    return alembic_cfg

def run_sync_migrations():
    """Run database migrations synchronously."""
    logger.info("Starting database migrations...")
    
    alembic_cfg = get_alembic_config()
    if not alembic_cfg:
        logger.error("Failed to get Alembic configuration")
        return False
    
    try:
        # Это обычный блокирующий вызов, который подходит для скриптов
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations completed successfully")
        return True
    except Exception as e:
        logger.error(f"Error running migrations: {e}", exc_info=True)
        return False

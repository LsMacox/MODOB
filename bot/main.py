"""Entry point for the Telegram bot."""
from __future__ import annotations

import asyncio
import logging
import sys
from telegram import Update, BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .config import settings

from .handlers.core import handle_message
from .handlers.keyword_management import get_keyword_management_handlers
from .handlers.help import get_help_handlers
from .handlers.spam_settings import get_spam_settings_handlers
from .handlers.private_chat import get_private_chat_handlers
from .handlers.group_events import get_group_event_handlers
from .handlers.group_info import get_group_info_handlers
from .migrations import run_sync_migrations

# --- Logging Setup ---
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("groups", "Показать группы, где вы администратор"),
        BotCommand("help", "Подробная справка по боту и его настройкам")
    ], scope=BotCommandScopeAllPrivateChats())
    
    await application.bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())

def main() -> None:
    """Set up and run the bot."""
    token = settings.BOT_TOKEN
    if not token:
        logger.error("BOT_TOKEN is not set in environment variables.")
        sys.exit(1)

    application = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)
        .pool_timeout(30)
        .connection_pool_size(100)
        .post_init(post_init)
        .build()
    )

    # --- Register Handlers ---
    for h in get_private_chat_handlers():
        application.add_handler(h, group=0)

    for h in get_keyword_management_handlers():
        application.add_handler(h, group=1)
    for h in get_help_handlers():
        application.add_handler(h, group=1)
    for h in get_spam_settings_handlers():
        application.add_handler(h, group=1)
    for h in get_group_event_handlers():
        application.add_handler(h, group=1)
    for h in get_group_info_handlers():
        application.add_handler(h, group=1)
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        ),
        group=10,
    )
    
    logger.info("Bot configured and ready to start...")

    # --- Run the Bot ---
    logger.info("Starting bot polling...")
    application.run_polling(
        allowed_updates=[
            "message", 
            "edited_message", 
            "chat_member", 
            "callback_query",
            "my_chat_member",
        ]
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'migrate':
        logger.info("Running migrations...")
        run_sync_migrations()
        logger.info("Migrations finished.")
    else:
        logger.info("Starting bot...")
        main()
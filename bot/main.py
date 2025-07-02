"""Entry point for the Telegram bot."""
from __future__ import annotations

import asyncio
import logging
import sys

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .config import settings
from .database import init_db
from .handlers.admin import admin_handlers
from .handlers.core import handle_message

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)


def run_bot() -> None:
    if not settings.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set in environment variables.")
        return

    # Create event loop and run DB initialization
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())

    application = (
        ApplicationBuilder().token(settings.BOT_TOKEN).concurrent_updates(True).build()
    )

    # Register admin command handlers
    for h in admin_handlers():
        application.add_handler(h)

    # Core message handler (low priority)
    application.add_handler(
        MessageHandler(filters.ALL & (~filters.COMMAND), handle_message),
        1,
    )

    logger.info("Bot started. Waiting for updates…")
    application.run_polling(
        close_loop=False,
        allowed_updates=["message", "edited_message", "chat_member"],
    )


def main() -> None:
    try:
        run_bot()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")


if __name__ == "__main__":
    main()

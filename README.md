# Keyword Auto-Responder Telegram Bot

Async Telegram bot for groups: detects keywords, replies with text/photo/video/document, and provides in-bot admin panel for managing keywords and anti-spam limits.

## Features
* python-telegram-bot v20 (async, `Application.run_polling`)
* Admin panel (`/panel`) with inline buttons (RU)
* Keyword add/list, customizable response (supports file_id media)
* Per-group anti-spam (rate-limit) with auto-ban and `/unban`
* PostgreSQL persistence (keywords & settings)
* In-memory 12-hour cache for re-used media file_id
* Dockerfile + `docker-compose.yml` (prod & dev hot-reload)

## Quick start (dev)
```bash
# 1. Clone repo & cd into it
cp .env.example .env  # fill BOT_TOKEN
# 2. Build & run containers with hot-reload
docker compose up --build bot-dev
```

For production run `docker compose up --build bot`.

## Bot Commands
* `/addkeyword <phrase>` — reply with text/photo/video/document to define response.
* `/listkeywords` — list all phrases in this group.
* `/panel` — inline control panel (spam limit ↑ ↓, show list).
* `/unban <user_id>` — manually unblock user.

## Project structure
```
modob/
├── bot/
│   ├── __init__.py
│   ├── main.py          # entry
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── cache.py
│   ├── anti_spam.py
│   └── handlers/
│       ├── __init__.py
│       ├── admin.py
│       └── core.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## Notes / next steps
* Expand inline UI: delete keyword, set repeat_limit, etc. (TODO)
* Add Alembic migrations & unit-tests (pytest-asyncio).
* Add CI (GitHub Actions) and linting.

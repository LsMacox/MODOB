# Keyword Auto-Responder Telegram Bot

Асинхронный Telegram бот для групп: обнаруживает ключевые слова, отвечает текстом/фото/видео/документами и предоставляет встроенную панель администратора для управления ключевыми словами и настройками анти-спама.

## Возможности
* python-telegram-bot v22 (асинхронный, `Application.run_polling`)
* Добавление/просмотр ключевых слов, настраиваемые ответы (поддержка file_id для медиа)
* Анти-спам с настройками для каждой группы, авто-бан
* PostgreSQL для хранения ключевых слов и настроек
* Кэш в памяти на 12 часов для повторно используемых медиа file_id
* Поддержка нечеткого поиска, шаблонов, транслитерации и регистрозависимости для ключевых слов
* Dockerfile + `docker-compose.yml` (продакшн и разработка с горячей перезагрузкой)

## Установка и запуск

### Предварительные требования
* Docker и Docker Compose
* Telegram Bot Token (получите от @BotFather)

### Локальная разработка

1. Клонируйте репозиторий и перейдите в его директорию:
   ```bash
   git clone https://github.com/yourusername/modob.git
   cd modob
   ```

2. Создайте файл .env на основе примера:
   ```bash
   cp .env.example .env
   ```

3. Отредактируйте файл .env и добавьте необходимые переменные окружения:
   ```
   BOT_TOKEN=your_bot_token_here
   DB_NAME=postgres
   DB_USER=postgres
   DB_PASSWORD=postgres
   DB_HOST=postgres
   DB_PORT=5432
   ```

4. Запустите контейнеры с режимом горячей перезагрузки для разработки:
   ```bash
   docker compose up --build bot-dev
   ```

5. Для отслеживания логов в отдельном терминале:
   ```bash
   docker compose logs -f bot-dev
   ```

### Изменение кода и отладка

При запуске в режиме разработки (`bot-dev`):
* Код автоматически перезагружается при внесении изменений
* Логи выводятся в консоль в подробном формате
* База данных PostgreSQL сохраняет данные в Docker volume

### Развертывание в продакшн

1. На сервере клонируйте репозиторий и перейдите в его директорию:
   ```bash
   git clone https://github.com/yourusername/modob.git
   cd modob
   ```

2. Создайте файл .env с настройками для продакшн:
   ```bash
   cp .env.example .env
   ```

3. Отредактируйте файл .env, добавив следующие параметры:
   ```
   BOT_TOKEN=your_production_bot_token
   DB_NAME=postgres
   DB_USER=postgres
   DB_PASSWORD=strong_production_password
   DB_HOST=postgres
   DB_PORT=5432
   ```

4. Запустите контейнеры в режиме продакшн:
   ```bash
   docker compose up -d --build bot
   ```

5. Проверьте статус запущенных контейнеров:
   ```bash
   docker compose ps
   ```

6. Просмотр логов в продакшн:
   ```bash
   docker compose logs -f bot
   ```

### Обновление бота в продакшн

```bash
git pull
docker compose down
docker compose up -d --build bot
```

## Команды бота
* `/help` — показать подробную справку о боте и его функциях
* `/start` — приветствие и основная справка
* `/groups` — список групп, где пользователь является администратором и бот присутствует

Все остальное управление ботом выполняется через встроенные кнопки в интерфейсе.

## Дополнительная информация

### Управление ключевыми словами
Ключевые слова поддерживают следующие настройки:
* Шаблоны с использованием * и ? (например, "привет*")
* Чувствительность к регистру
* Транслитерация (автоматический перевод между русским и английским)
* Нечёткий поиск (сработает даже при небольших опечатках)

### Анти-спам система
Система анти-спама контролирует:
* Количество сообщений в заданный интервал времени
* Количество ссылок в заданный интервал времени
* Автоматические блокировки с увеличением времени при повторных нарушениях

## Работа с миграциями Alembic

### Настройка Alembic для управления миграциями БД

1. Перейдите в контейнер с ботом:
   ```bash
   docker compose exec bot bash
   ```

2. Инициализация Alembic (если еще не инициализирован):
   ```bash
   cd /app
   alembic init alembic
   ```

3. Настройте файл `alembic.ini` с текущими параметрами подключения к БД:
   ```python
   # sqlalchemy.url = driver://user:pass@localhost/dbname
   sqlalchemy.url = postgresql://%(DB_USER)s:%(DB_PASSWORD)s@%(DB_HOST)s:%(DB_PORT)s/%(DB_NAME)s
   ```

4. Настройте `env.py` в папке `alembic` для загрузки переменных из .env:
   ```python
   # Добавьте в начале файла
   import os
   from dotenv import load_dotenv
   load_dotenv()
   
   # Добавьте перед функцией run_migrations_online
   config.set_main_option('sqlalchemy.url', config.get_main_option('sqlalchemy.url').format(
       DB_USER=os.getenv("DB_USER"),
       DB_PASSWORD=os.getenv("DB_PASSWORD"),
       DB_HOST=os.getenv("DB_HOST"),
       DB_PORT=os.getenv("DB_PORT"),
       DB_NAME=os.getenv("DB_NAME")
   ))
   
   # Импортируйте модели для 'autogenerate'
   from bot.models import Base
   target_metadata = Base.metadata
   ```

### Основные команды Alembic

#### Создание миграции

1. Автоматическая генерация миграции на основе изменений в моделях:
   ```bash
   alembic revision --autogenerate -m "Description of changes"
   ```

2. Создание пустой миграции вручную:
   ```bash
   alembic revision -m "Create empty migration"
   ```

#### Применение миграций

1. Обновить БД до последней версии (применить все миграции):
   ```bash
   alembic upgrade head
   ```

2. Обновить на одну миграцию вперед:
   ```bash
   alembic upgrade +1
   ```

3. Откатить на одну миграцию назад:
   ```bash
   alembic downgrade -1
   ```

4. Откатить до начального состояния:
   ```bash
   alembic downgrade base
   ```

#### Просмотр информации о миграциях

1. Показать текущую версию БД:
   ```bash
   alembic current
   ```

2. Показать историю миграций:
   ```bash
   alembic history
   ```

### Пример рабочего процесса

1. Измените модели в `bot/models.py` (например, добавьте новое поле в модель)
2. Создайте миграцию:
   ```bash
   docker compose exec bot bash -c "cd /app && alembic revision --autogenerate -m 'Add new field'"
   ```
3. Проверьте созданную миграцию в директории `alembic/versions/`
4. Примените миграцию:
   ```bash
   docker compose exec bot bash -c "cd /app && alembic upgrade head"
   ```

## Планы на будущее
* Добавление юнит-тестов (pytest-asyncio)
* Добавление CI (GitHub Actions) и линтинг кода

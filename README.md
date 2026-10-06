# 🧠 RAG-бот по психологии

Telegram-бот, который отвечает на вопросы по книгам по психологии, используя RAG (Retrieval-Augmented Generation).

## 🎯 Что умеет

- 📚 Загружать PDF-книги и индексировать их в Qdrant
- 🔍 Искать ответы по смыслу (векторный поиск)
- 🤖 Генерировать ответы через YandexGPT
- 💬 Вести диалог с учётом истории
- 📊 Показывать статистику
- 📎 Загружать новые книги прямо через Telegram
- 🌐 Искать по всем книгам или по конкретной

## 🛠️ Технологии

- **Python 3.11** — основной язык
- **Docker** — контейнеризация
- **Qdrant** — векторная база данных
- **FastEmbed** — модель эмбеддингов (`paraphrase-multilingual-MiniLM-L12-v2`)
- **YandexGPT** — генерация ответов
- **python-telegram-bot** — Telegram API
- **LangChain** — нарезка текста на куски

## 📁 Структура проекта
rag-bot/
├── bot/
│ ├── telegram_bot_psychology.py # Основной код бота
│ ├── Dockerfile # Инструкция сборки образа
│ └── .dockerignore # Исключения для Docker
├── books/ # PDF-книги (не в Git)
├── .env.example # Шаблон переменных окружения
├── .gitignore # Исключения для Git
├── docker-compose.yml # Конфигурация контейнеров
├── restart.sh # Скрипт перезапуска
└── README.md # Этот файл


## 🚀 Установка и запуск

### 1. Клонируй репозиторий

```bash
git clone https://github.com/твой-username/rag-bot.git
cd rag-bot

2. Создай .env
cp .env.example .env

Открой .env и вставь свои токены:

TELEGRAM_TOKEN — получить у @BotFather

YA_API_KEY — получить в Yandex Cloud

YA_FOLDER_ID — ID каталога в Yandex Cloud

3. Положи книги в папку books/
mkdir -p books
cp /путь/к/книгам/*.pdf books/

5. Проверь логи
docker logs -f rag-bot

6. Открой бота в Telegram
Найди своего бота и напиши /start.

📝 Команды бота
Команда	Что делает
/start	Приветствие и клавиатура
/books	Показать список книг
/help	Справка
📚 Выбрать книгу	Выбрать книгу для поиска
📎 Загрузить PDF	Загрузить новую книгу
🧹 Очистить историю	Сбросить историю диалога
📋 Статистика	Показать статистику
⚙️ Переменные окружения
Переменная	Описание
TELEGRAM_TOKEN	Токен Telegram-бота
YA_API_KEY	API-ключ YandexGPT
YA_FOLDER_ID	ID каталога Yandex Cloud
HTTP_PROXY	Прокси для Telegram (если нужен)
HTTPS_PROXY	Прокси для Telegram (если нужен)
🔧 Как это работает
Загрузка книги: PDF → текст → нарезка на куски → эмбеддинги → Qdrant

Вопрос: текст → эмбеддинг → поиск в Qdrant → топ-5 кусков

Ответ: куски + вопрос → YandexGPT → готовый ответ

⚠️ Требования
Docker и Docker Compose

VPN или прокси для доступа к Telegram (если в России)

Минимум 4 ГБ RAM


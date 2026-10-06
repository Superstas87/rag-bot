import os
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# Читаем секреты из переменных окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
YA_API_KEY = os.environ.get("YA_API_KEY")
YA_FOLDER_ID = os.environ.get("YA_FOLDER_ID")

import logging
import tempfile
import shutil
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct, Filter, FieldCondition, MatchValue
from fastembed import TextEmbedding
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter
import requests

# =======================================================
# 0. НАСТРОЙКИ
# =======================================================

# Qdrant
QDRANT_HOST = "qdrant"
QDRANT_PORT = 6333
COLLECTION_NAME = "psychology_books"

# Путь к папке с книгами
BOOKS_FOLDER = "books"

conversation_history = {}
MAX_HISTORY_LENGTH = 20

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Функция для Reply-клавиатуры
def get_main_keyboard():
    """Создаёт постоянную клавиатуру с основными кнопками"""
    keyboard = [
        [KeyboardButton("📚 Выбрать книгу"), KeyboardButton("❓ Помощь")],
        [KeyboardButton("📎 Загрузить PDF"), KeyboardButton("🧹 Очистить историю")],
        [KeyboardButton("📋 Статистика")]
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,   # Кнопки подстраиваются под размер экрана
        is_persistent=True,     # Клавиатура всегда видна (не скрывается)
        input_field_placeholder="Напишите вопрос по книге...",
    )

# =======================================================
# 1. ФУНКЦИЯ YANDEXGPT
# =======================================================

def generate_with_yandexgpt(prompt: str) -> str:
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    
    headers = {
        "Authorization": f"Api-Key {YA_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "modelUri": f"gpt://{YA_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.3,
            "maxTokens": 700
        },
        "messages": [
            {
                "role": "system",
                "text": (
                    "Ты — дружелюбный психолог-консультант. Отвечай на вопросы строго на основе "
                    "предоставленного контекста из книги. Не используй свои знания. "
                    "Если ответа нет в контексте, скажи: 'В этой книге я не нашёл ответа на ваш вопрос. "
                    "Попробуйте переформулировать или выберите другую книгу.'"
                )
            },
            {
                "role": "user",
                "text": prompt
            }
        ]
    }
    
    response = requests.post(url, headers=headers, json=data)
    
    if response.status_code == 200:
        result = response.json()
        return result["result"]["alternatives"][0]["message"]["text"]
    else:
        raise Exception(f"Ошибка API: {response.status_code} - {response.text}")

# =======================================================
# 2. ФУНКЦИИ РАБОТЫ С КНИГАМИ
# =======================================================

def get_available_books():
    """Возвращает список PDF-файлов в папке books"""
    if not os.path.exists(BOOKS_FOLDER):
        os.makedirs(BOOKS_FOLDER)
        return []
    
    books = []
    for file in os.listdir(BOOKS_FOLDER):
        if file.lower().endswith((".pdf", ".PDF")):
            books.append(file)
    return books

def process_book(file_path: str) -> int:
    """Читает книгу, режет на куски, заливает в Qdrant"""
    print(f"📖 Обрабатываю книгу: {file_path}")
    
    client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)
    book_name = os.path.basename(file_path)
    
    # Проверяем, есть ли коллекция
    collections = client.get_collections().collections
    collection_names = [c.name for c in collections]
    
    if COLLECTION_NAME not in collection_names:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE)
        )
        print("   ✅ Коллекция создана")
    
    # Загружаем модель
    embedding_model = TextEmbedding(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        cache_dir="/root/.cache/fastembed"
    )
    
    # Читаем PDF
    reader = PdfReader(file_path)
    full_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + "\n"
    
    print(f"   📝 Извлечено {len(full_text)} символов")
    
    # Режем на куски
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=70,
        length_function=len,
        separators=["\n\n", "\n", ".", " ", ""]
    )
    chunks = text_splitter.split_text(full_text)
    print(f"   ✂️ Получено {len(chunks)} кусков")
    
    # Заливаем в Qdrant
    points = []
    for idx, chunk in enumerate(chunks):
        vector = list(embedding_model.embed([chunk]))[0]
        points.append(
            PointStruct(
                id=idx,
                vector=vector.tolist(),
                payload={
                    "text": chunk,
                    "source": book_name,
                    "chunk_id": idx
                }
            )
        )
        
        if len(points) >= 100:
            client.upsert(collection_name=COLLECTION_NAME, points=points)
            points = []
    
    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)
    
    print(f"   ✅ Залито {len(chunks)} кусков в Qdrant")
    return len(chunks)

"""Поиск ответа"""
def ask_question(question: str, book_name: str, user_id: int = None) -> str:
    """Ищет ответ только в выбранной книге, с учетом истории"""
    if not book_name:
        return "📚 Сначала выберите книгу с помощью /books"

    client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)

    collections = client.get_collections().collections
    collection_names = [c.name for c in collections]

    if COLLECTION_NAME not in collection_names:
        return "📚 Нет загруженных книг. Загрузите книги в папку /books"

    embedding_model = TextEmbedding(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        cache_dir="/root/.cache/fastembed"
    )
    
        # --- ФОРМИРУЕМ ПОИСКОВЫЙ ЗАПРОС С УЧЁТОМ ИСТОРИИ ---
    search_query = question
    if user_id and user_id in conversation_history:
        # Берём последние 2 сообщения (предыдущий вопрос + ответ)
        recent = conversation_history[user_id][-3:]
        # Ищем последний вопрос пользователя (не считая текущего)
        previous_questions = [m["text"] for m in recent if m["role"] == "user" and m["text"] != question]
        if previous_questions:
            # Объединяем предыдущий вопрос с текущим для лучшего поиска
            search_query = previous_questions[-1] + " " + question
    
    question_vector = list(embedding_model.embed([search_query]))[0]
    if book_name == "🌐 Все книги":
        # Без фильтра — поиск по всем книгам
        search_result = client.query_points(
            collection_name=COLLECTION_NAME,
            query=question_vector.tolist(),
            limit=5
        )
    else:

        search_result = client.query_points(
            collection_name=COLLECTION_NAME,
            query=question_vector.tolist(),
            query_filter=Filter(
                must=[FieldCondition(
                    key="source",
                    match=MatchValue(value=book_name)
                )]
            ),
            limit=5
        )

    results = search_result.points

    if not results:
        return f"🤔 В книге '{book_name}' я не нашёл ответа. Попробуйте переформулировать вопрос."

    # --- СОБИРАЕМ КОНТЕКСТ С ИСТОЧНИКАМИ ---
    context_parts = []
    sources = set()
    for result in results:
        source = result.payload.get("source", "неизвестно")
        text = result.payload["text"]
        context_parts.append(f"[Из книги: {source}]\n{text}")
        sources.add(source)

    context = "\n\n".join(context_parts)

    # --- ФОРМИРУЕМ ПРОМПТ С ИСТОРИЕЙ ---
    history_text = ""
    if user_id and user_id in conversation_history:
        for msg in conversation_history[user_id][-5:]:  # последние 5 сообщений
            role = "Пользователь" if msg["role"] == "user" else "Ассистент"
            history_text += f"{role}: {msg['text']}\n"

    # --- УКАЗЫВАЕМ ИСТОЧНИКИ В ПРОМПТЕ ---
    if book_name == "🌐 Все книги":
        source_hint = (
            "Отвечай, используя контекст из разных книг. "
            "В конце ответа укажи, из какой книги (или книг) взята информация."
        )
    else:
        source_hint = f"Отвечай строго на основе книги '{book_name}'."

    prompt = f"""
История диалога:
{history_text}

Вопрос: {question}

Контекст из книг:
{context}

{source_hint}
Ответь на вопрос, используя контекст и учитывая историю диалога.
"""

    try:
        answer = generate_with_yandexgpt(prompt)
        return answer
    except Exception as e:
        return f"⚠️ Ошибка при генерации ответа: {str(e)}"

# =======================================================
# 3. ОБРАБОТЧИКИ КОМАНД ТЕЛЕГРАМ
# =======================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие"""
    await update.message.reply_text(
        "🧠 Добро пожаловать в бот по психологии!\n\n"
        "Я помогу вам найти ответы в книгах по психологии.\n\n"
        "📚 /books — посмотреть список доступных книг\n"
        "❓ После выбора книги просто задавайте вопросы в чате\n\n"
        "💡 Совет: сначала выберите книгу, затем задавайте вопросы по ней.",
        reply_markup=get_main_keyboard()
    )

async def show_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список книг кнопками"""
    books = get_available_books()
    
    if not books:
        await update.message.reply_text(
            "📭 В папке /books нет ни одной книги.\n\n"
            "Добавьте PDF-файлы в папку books в проекте и перезапустите бота.\n"
            f"Путь: {os.path.abspath(BOOKS_FOLDER)}"
        )
        return
    
    keyboard = []
    for idx, book in enumerate(books):
        display_name = book.replace(".pdf", "").replace(".PDF", "")
        keyboard.append([InlineKeyboardButton(display_name, callback_data=str(idx))])
    
    keyboard.append([InlineKeyboardButton("🌐 Все книги", callback_data="all")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📚 Выберите книгу, по которой хотите задавать вопросы,\n"
        "или нажмите «🌐 Все книги» для поиска по всем книгам:",
        reply_markup=reply_markup
    )

async def book_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора книги"""
    query = update.callback_query
    await query.answer()

    if query.data == "all":
        # Пользователь выбрал "Все книги"
        context.user_data['current_book'] = "🌐 Все книги"
        await query.edit_message_text(
            "✅ Выбран режим: **🌐 Все книги**\n\n"
            "Теперь я буду искать ответы по всем книгам сразу.\n"
            "В ответе я укажу, из какой книги взята информация.\n\n"
            "📚 /books — выбрать конкретную книгу"
        )
        return

    try:
        book_index = int(query.data)
        books = get_available_books()
        book_name = books[book_index]
    except (IndexError, ValueError):
        await query.edit_message_text("⚠️ Ошибка: книга не найдена.")
        return

    context.user_data['current_book'] = book_name
    
    display_name = book_name.replace(".pdf", "").replace(".PDF", "")
    
    await query.edit_message_text(
        f"✅ Выбрана книга: **{display_name}**\n\n"
        f"Теперь вы можете задавать вопросы по этой книге.\n"
        f"Просто напишите ваш вопрос в чат.\n\n"
        f"📚 /books — выбрать другую книгу"
    )
    await query.message.reply_text(
        "Клавиатура всегда доступна внизу 👇",
        reply_markup=get_main_keyboard()
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик загруженных PDF-файлов"""
    document = update.message.document
    
    # Проверяем, что это PDF
    if document.mime_type != "application/pdf":
        await update.message.reply_text(
            "⚠️ Пожалуйста, отправьте файл в формате PDF."
        )
        return
    
    # Проверяем размер (не больше 50 МБ — лимит Telegram)
    if document.file_size > 50 * 1024 * 1024:
        await update.message.reply_text(
            "⚠️ Файл слишком большой. Максимум — 50 МБ."
        )
        return
    
    await update.message.reply_text(
        f"⏳ Получил файл: {document.file_name}\n"
        f"Загружаю и обрабатываю. Это может занять несколько минут..."
    )
    
    # Формируем путь для сохранения
    file_name = document.file_name
    # Убираем возможные опасные символы из имени
    safe_name = "".join(c for c in file_name if c.isalnum() or c in " .-_()").strip()
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    
    file_path = os.path.join(BOOKS_FOLDER, safe_name)
    
    try:
        # Скачиваем файл из Telegram
        file = await context.bot.get_file(document.file_id)
        await file.download_to_drive(file_path)
        
        await update.message.reply_text(
            f"✅ Файл сохранён: {safe_name}\n"
            f"📖 Обрабатываю книгу..."
        )
        
        # Загружаем книгу в Qdrant
        chunks = process_book(file_path)
        
        await update.message.reply_text(
            f"🎉 Книга успешно добавлена!\n\n"
            f"📄 Имя: {safe_name}\n"
            f"📝 Кусков: {chunks}\n\n"
            f"Теперь вы можете выбрать её через /books"
        )
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Ошибка при обработке файла: {str(e)}"
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик вопросов"""
    user_id = update.effective_user.id
    question = update.message.text
    
    if not question.strip() or question.startswith("/"):
        return
    
    # --- ОБРАБОТКА КНОПОК REPLY-КЛАВИАТУРЫ ---
    if question == "📚 Выбрать книгу":
        await show_books(update, context)
        return

    if question == "❓ Помощь":
        await help_command(update, context)
        return

    if question == "📎 Загрузить PDF":
        await update.message.reply_text(
            "📎 Отправьте PDF-файл в чат, и я добавлю его в библиотеку.\n"
            "Максимальный размер — 50 МБ.",
            reply_markup=get_main_keyboard()
        )
        return

    if question == "🧹 Очистить историю":
        if user_id in conversation_history:
            conversation_history[user_id] = []
        await update.message.reply_text(
            "🧹 История диалога очищена. Можем начать заново!",
            reply_markup=get_main_keyboard()
        )
        return

    if question == "📋 Статистика":
        await show_statistics(update, context)
        return
        
    # --- КОНЕЦ ОБРАБОТКИ КНОПОК ---

    # Проверяем, выбрана ли книга
    current_book = context.user_data.get('current_book')
    
    if not current_book:
        await update.message.reply_text(
            "📚 Сначала выберите книгу с помощью команды /books"
        )
        return
    
    # --- СОХРАНЯЕМ ВОПРОС В ИСТОРИЮ ---
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    
    conversation_history[user_id].append({
        "role": "user",
        "text": question
    })
    
    # Ограничиваем длину истории
    if len(conversation_history[user_id]) > MAX_HISTORY_LENGTH:
        conversation_history[user_id] = conversation_history[user_id][-MAX_HISTORY_LENGTH:]    
    
    await update.message.reply_text("🤔 Ищу ответ в книге...")
    
    try:
        answer = ask_question(question, current_book, user_id)
        await update.message.reply_text(answer)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {str(e)}")

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику использования бота"""
    user_id = update.effective_user.id
    
    # --- 1. Книги в библиотеке ---
    books = get_available_books()
    total_books = len(books)
    
    # --- 2. Куски в Qdrant ---
    total_chunks = 0
    try:
        client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)
        collections = client.get_collections().collections
        collection_names = [c.name for c in collections]
        
        if COLLECTION_NAME in collection_names:
            info = client.get_collection(COLLECTION_NAME)
            total_chunks = info.points_count
    except Exception as e:
        print(f"⚠️ Ошибка при получении статистики Qdrant: {e}")
    
    # --- 3. Вопросы пользователя ---
    user_history = conversation_history.get(user_id, [])
    total_questions = len([m for m in user_history if m["role"] == "user"])
    total_answers = len([m for m in user_history if m["role"] == "assistant"])
    
    # --- 4. Текущая книга ---
    current_book = context.user_data.get('current_book', 'не выбрана')
    if current_book and current_book != "🌐 Все книги":
        display_book = current_book.replace(".pdf", "").replace(".PDF", "")
    else:
        display_book = current_book
    
    # --- 5. Формируем текст статистики ---
    stats_text = (
        f"📊 Статистика\n\n"
        f"📚 Книг в библиотеке: {total_books}\n"
        f"📝 Кусков в Qdrant: {total_chunks}\n\n"
        f"❓ Ваших вопросов: {total_questions}\n"
        f"💬 Ответов получено: {total_answers}\n"
        f"🧠 Сообщений в истории: {len(user_history)}\n\n"
        f"📖 Текущая книга: {display_book}\n"
    )
    
    # --- 6. Отправляем статистику ---
    await update.message.reply_text(
        stats_text,
        reply_markup=get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Справка"""
    await update.message.reply_text(
        "🧠 Бот-психолог\n\n"
        "📚 «Выбрать книгу» — показать список книг\n"
        "❓ «Помощь» — эта справка\n"
        "📎 «Загрузить PDF» — добавить книгу\n"
        "🧹 «Очистить историю» — сбросить диалог\n\n"
        "Задавайте вопросы по выбранной книге в чате.",
        reply_markup=get_main_keyboard()
    )

# =======================================================
# 4. ЗАГРУЗКА КНИГ ПРИ СТАРТЕ
# =======================================================

def load_all_books():
    """Загружает книги из папки при старте, если их ещё нет в Qdrant"""
    books = get_available_books()
    
    if not books:
        print("📭 Нет книг для загрузки. Добавьте PDF в папку books")
        return
    
    client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)
    
    # Проверяем, какие книги уже загружены
    collections = client.get_collections().collections
    collection_names = [c.name for c in collections]
    
    loaded_books = set()
    if COLLECTION_NAME in collection_names:
        try:
            # Получаем все уникальные source из коллекции
            scroll_result = client.scroll(
                collection_name=COLLECTION_NAME,
                limit=10000,
                with_payload=["source"]
            )
            for point in scroll_result[0]:
                loaded_books.add(point.payload.get("source"))
            print(f"   📚 Уже загружено книг: {len(loaded_books)}")
        except Exception as e:
            print(f"   ⚠️ Не удалось получить список загруженных книг: {e}")
    
    print(f"📚 Найдено книг в папке: {len(books)}")
    
    # Загружаем только те книги, которых нет в Qdrant
    for book in books:
        print(f"   🔍 Проверяю книгу: {book}")
        if book in loaded_books:
            print(f"   ⏭️ {book} — уже загружена, пропускаем")
            continue
        
        book_path = os.path.join(BOOKS_FOLDER, book)
        print(f"   📂 Путь: {book_path}")
        try:
            chunks = process_book(book_path)
            print(f"   ✅ {book} — {chunks} кусков")
        except Exception as e:
            print(f"   ❌ Ошибка при загрузке {book}: {str(e)}")
            import traceback
            traceback.print_exc()

# =======================================================
# 5. ЗАПУСК БОТА
# =======================================================

def main():
    # Загружаем книги при старте
    print("📚 Загружаю книги...")
    load_all_books()
    
    # Создаём приложение
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("books", show_books))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("statistics", show_statistics))
    
    # Выбор книги
    application.add_handler(CallbackQueryHandler(book_selected))

    # --- НОВЫЙ ОБРАБОТЧИК: загрузка PDF ---
    application.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    
    # Текстовые сообщения (вопросы)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Запускаем бота
    print("🧠 Бот-психолог запущен!")
    application.run_polling(allowed_updates=[])

if __name__ == "__main__":
    main()

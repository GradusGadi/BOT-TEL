
import logging
import os
import sqlite3
import time
import threading
import signal
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image
import imagehash

# Простой веб-сервер для Render
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "✅ Bot is running!"

def run_flask():
    app_flask.run(host='0.0.0.0', port=5000)

# Запускаем Flask в отдельном потоке
flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

# Получаем настройки из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DB_FILE = "photos.db"

# Временное хранилище
user_last_photos = {}
last_warning_time = {}

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS hashes (hash TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

def hash_exists(img_hash):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM hashes WHERE hash = ?", (img_hash,))
    return cursor.fetchone() is not None

def save_hash(img_hash):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO hashes (hash) VALUES (?)", (img_hash,))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

async def check_photos_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка лимита фото"""
    user = update.effective_user
    message = update.message
    
    if user.id == ADMIN_USER_ID or not message or not message.photo:
        return
    
    user_id = user.id
    current_time = time.time()
    
    # Инициализируем или обновляем данные пользователя
    if user_id not in user_last_photos:
        user_last_photos[user_id] = []
    
    # Добавляем время отправки фото
    user_last_photos[user_id].append(current_time)
    
    # Оставляем только фото за последние 10 секунд
    user_last_photos[user_id] = [t for t in user_last_photos[user_id] if current_time - t <= 10]
    
    # Проверяем количество фото за последние 10 секунд
    photo_count = len(user_last_photos[user_id])
    
    # Предупреждаем только если больше 2 фото И это 3-е или последующее фото
    if photo_count >= 3 and user_last_photos[user_id][-1] == current_time:
        # Проверяем, не отправляли ли уже предупреждение в последние 30 секунд
        last_warn = last_warning_time.get(user_id, 0)
        if current_time - last_warn > 30:
            warning = "📸 Пожалуйста, не отправляйте больше 2 фото подряд! Ознакомьтесь с правилами в закреплённом сообщении."
            await message.reply_text(warning, reply_to_message_id=message.message_id)
            last_warning_time[user_id] = current_time

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основная функция обработки фото"""
    user = update.effective_user
    message = update.message

    # Сначала проверяем лимит фото
    await check_photos_limit(update, context)
    
    # Затем проверяем на дубликаты
    if user.id == ADMIN_USER_ID:
        return

    if not message or not message.photo:
        return

    photo = message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_path = f"temp_{photo.file_id}.jpg"
    await file.download_to_drive(file_path)

    try:
        image = Image.open(file_path)
        img_hash = str(imagehash.average_hash(image))

        if hash_exists(img_hash):
            mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user.id})"
            await update.effective_chat.send_message(
                text=f"⚠️ {mention}, это фото уже было отправлено ранее!",
                reply_to_message_id=message.message_id,
                parse_mode="Markdown"
            )
            await message.delete()
        else:
            save_hash(img_hash)

    except Exception as e:
        logging.error(f"Ошибка обработки фото: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logging.error(f"Ошибка: {context.error}")

def main():
    if not TOKEN:
        raise RuntimeError("❌ Переменная BOT_TOKEN не задана!")

    logging.basicConfig(level=logging.INFO)
    init_db()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(error_handler)

    # Graceful shutdown для Render
    def signal_handler(signum, frame):
        logging.info("🚪 Получен сигнал завершения...")
        app.stop()
        logging.info("✅ Бот корректно завершил работу")
        os._exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logging.info("✅ Бот запущен с обработчиком ошибок")
    
    try:
        app.run_polling()
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
        os._exit(1)

if __name__ == "__main__":
    main()

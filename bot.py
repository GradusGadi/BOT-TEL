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

# Для отслеживания альбомов
album_photo_count = {}

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS hashes (hash TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    logging.info("✅ База данных инициализирована")

def hash_exists(img_hash):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM hashes WHERE hash = ?", (img_hash,))
    result = cursor.fetchone() is not None
    conn.close()
    return result

def save_hash(img_hash):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO hashes (hash) VALUES (?)", (img_hash,))
        conn.commit()
        logging.info(f"💾 Хеш сохранен в БД: {img_hash}")
    except sqlite3.IntegrityError:
        logging.info(f"⚠️ Хеш уже существует: {img_hash}")
        pass
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения хеша: {e}")
    finally:
        conn.close()

async def check_photos_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка лимита фото в АЛЬБОМАХ"""
    user = update.effective_user
    message = update.message
    
    if user.id == ADMIN_USER_ID or not message or not message.photo:
        return
    
    # Проверяем только АЛЬБОМЫ (группы фото)
    if message.media_group_id:
        album_id = message.media_group_id
        
        # Увеличиваем счетчик фото в альбоме
        if album_id not in album_photo_count:
            album_photo_count[album_id] = 1
        else:
            album_photo_count[album_id] += 1
        
        # Если в альбоме больше 2 фото - предупреждаем
        if album_photo_count[album_id] > 2:
            warning = "📸 Пожалуйста, не отправляйте больше 2 фото в одном сообщении! Ознакомьтесь с правилами в закреплённом сообщении."
            await message.reply_text(warning, reply_to_message_id=message.message_id)
            
            # Очищаем счетчик для этого альбома
            album_photo_count[album_id] = -100  # Чтобы не спамить

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основная функция обработки фото"""
    user = update.effective_user
    message = update.message

    # Сначала проверяем лимит фото
    await check_photos_limit(update, context)
    
    # Затем проверяем на дубликаты
    if user.id == ADMIN_USER_ID:
        logging.info("👑 Сообщение от админа - пропускаем")
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
        
        logging.info(f"🔍 Обработка фото, хеш: {img_hash}")

        # ДИАГНОСТИКА: Проверяем что возвращает hash_exists
        exists = hash_exists(img_hash)
        logging.info(f"📊 Хеш {img_hash} существует в БД: {exists}")

        if exists:
            mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user.id})"
            logging.info(f"🚨 Найден дубликат! Удаляю сообщение от {mention}")
            
            await update.effective_chat.send_message(
                text=f"⚠️ {mention}, это фото уже было отправлено ранее!",
                reply_to_message_id=message.message_id,
                parse_mode="Markdown"
            )
            await message.delete()
            logging.info("✅ Дубликат удален")
        else:
            save_hash(img_hash)
            logging.info(f"💾 Сохранен новый хеш: {img_hash}")

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

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
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

    logging.info("✅ Бот запущен - проверяет только альбомы и дубликаты")
    
    try:
        app.run_polling()
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
        os._exit(1)

if __name__ == "__main__":
    main()

import logging
import sqlite3
import os
import time
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image
import imagehash

# === Настройки ===
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DB_FILE = "photos.db"

# Лог отправленных фото: user_id → список времён (в секундах)
user_photo_times = defaultdict(list)

# In-memory кэш для дублей (защита от флуда)
recent_hashes = set()

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS photo_hashes (
            hash TEXT PRIMARY KEY,
            message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_photo_hash(img_hash: str, message_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO photo_hashes (hash, message_id) VALUES (?, ?)",
            (img_hash, message_id)
        )
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка сохранения хеша: {e}")
    finally:
        conn.close()

def get_photo_message_id(img_hash: str) -> int:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT message_id FROM photo_hashes WHERE hash = ?", (img_hash,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ДОБАВЛЕНО: Проверка на None
    if update.message is None:
        return
        
    user = update.effective_user
    message = update.message

    # Админ игнорируется
    if user.id == ADMIN_USER_ID:
        return

    current_time = time.time()

    # Очистка старых записей (>1 секунды назад)
    user_photo_times[user.id] = [
        t for t in user_photo_times[user.id] if current_time - t <= 1.0
    ]

    # Добавляем текущее фото
    user_photo_times[user.id].append(current_time)

    # Проверка: больше 2 фото за 1 секунду?
    if len(user_photo_times[user.id]) > 2:
        mention = f"@{user.username}" if user.username else user.first_name
        try:
            await message.reply_text(
                f"⚠️ {mention}, запрещено отправлять больше 2 фото за раз!\n"
                "Это нарушение правил чата и может повлечь бан.\n"
                "Пожалуйста, ознакомьтесь с правилами в закреплённом сообщении.",
                reply_to_message_id=message.message_id
            )
        except:
            pass
        # Фото НЕ удаляются — только предупреждение
    else:
        # ДОБАВЛЕНО: Проверка наличия фото
        if not message.photo:
            return
            
        # Проверка на дубликат
        photo = message.photo[-1]
        file_path = f"temp_{photo.file_id}.jpg"
        try:
            file = await context.bot.get_file(photo.file_id)
            await file.download_to_drive(file_path)

            image = Image.open(file_path)
            img_hash = str(imagehash.phash(image, hash_size=8))

            if img_hash in recent_hashes or get_photo_message_id(img_hash) is not None:
                mention = f"@{user.username}" if user.username else user.first_name
                await message.reply_text(
                    f"⚠️ {mention}, это фото уже было отправлено ранее!",
                    reply_to_message_id=message.message_id
                )
                await message.delete()  # дубликаты УДАЛЯЮТСЯ
            else:
                recent_hashes.add(img_hash)
                save_photo_hash(img_hash, message.message_id)

        except Exception as e:
            logging.error(f"Ошибка обработки фото: {e}")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

def main():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )

    init_db()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    PORT = int(os.environ.get("PORT", 10000))
    RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
    if not RENDER_EXTERNAL_URL:
        raise RuntimeError("RENDER_EXTERNAL_URL не задан")

    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/{TOKEN}"
    logging.info(f"Webhook: {WEBHOOK_URL}")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()

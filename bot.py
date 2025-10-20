import logging
import sqlite3
import os
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image
import imagehash

# Настройки
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DB_FILE = "photos.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS photo_hashes (
            hash TEXT PRIMARY KEY,
            message_id INTEGER
        )
    """)
    conn.commit()
    conn.close()

def save_photo_hash(img_hash: str, message_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO photo_hashes (hash, message_id) VALUES (?, ?)",
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
    user = update.effective_user
    message = update.message

    # Пропускаем админа
    if user.id == ADMIN_USER_ID:
        return

    # Берём самое большое фото из списка версий
    photo = message.photo[-1]

    # Скачиваем фото
    file = await context.bot.get_file(photo.file_id)
    file_path = f"temp_{photo.file_id}.jpg"
    await file.download_to_drive(file_path)

    try:
        # Считаем хеш
        image = Image.open(file_path)
        img_hash = str(imagehash.average_hash(image))

        # Проверяем, был ли такой хеш
        if get_photo_message_id(img_hash) is not None:
            # Это дубликат — удаляем и уведомляем
            mention = f"@{user.username}" if user.username else user.first_name
            await message.reply_text(
                f"⚠️ {mention}, это фото уже было отправлено ранее!",
                reply_to_message_id=message.message_id
            )
            await message.delete()
        else:
            # Новое фото — сохраняем хеш
            save_photo_hash(img_hash, message.message_id)

    except Exception as e:
        logging.error(f"Ошибка обработки фото: {e}")
    finally:
        # Удаляем временный файл
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

    # Render настройки
    PORT = int(os.environ.get("PORT", 10000))
    RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
    if not RENDER_EXTERNAL_URL:
        raise RuntimeError("Переменная RENDER_EXTERNAL_URL не задана в Render!")

    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/{TOKEN}"

    logging.info(f"Запуск webhook на: {WEBHOOK_URL}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()

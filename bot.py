import logging
import os
import sqlite3
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image
import imagehash

# Получаем настройки из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DB_FILE = "photos.db"

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

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message

    if user.id == ADMIN_USER_ID:
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

def main():
    if not TOKEN:
        raise RuntimeError("❌ Переменная BOT_TOKEN не задана! Добавь её в Environment Variables на Render.")

    logging.basicConfig(level=logging.INFO)
    init_db()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Настройка webhook для Render
    port = int(os.environ.get("PORT", 8443))
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")

    if webhook_url:
        logging.info(f"🚀 Запуск в режиме webhook: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=webhook_url,
            url_path="",  # Без секретного пути — безопасно на Render
        )
    else:
        logging.info("✅ Локальный режим (polling)")
        app.run_polling()

if __name__ == "__main__":
    main()
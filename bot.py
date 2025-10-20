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
    """Инициализация базы данных для хешей"""
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
    """Сохраняет хеш фото и ID сообщения"""
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
    """Возвращает ID сообщения с оригиналом фото"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT message_id FROM photo_hashes WHERE hash = ?", (img_hash,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

async def check_duplicate_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, img_hash: str) -> bool:
    """Проверяет дубликат фото и удаляет если оригинал существует"""
    original_message_id = get_photo_message_id(img_hash)
    
    if original_message_id is None:
        return False
    
    try:
        original_message = await context.bot.get_message(
            chat_id=update.effective_chat.id,
            message_id=original_message_id
        )
        if original_message:
            mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name
            await update.message.reply_text(
                f"⚠️ {mention}, это фото уже было отправено ранее!",
                reply_to_message_id=update.message.message_id
            )
            await update.message.delete()
            return True
    except Exception:
        pass
    
    return False

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик фото"""
    user = update.effective_user
    message = update.message

    if user.id == ADMIN_USER_ID:
        return

    if len(message.photo) > 2:
        await message.reply_text(
            "📸 Пожалуйста, не отправляйте больше 2 фото в одном сообщении!",
            reply_to_message_id=message.message_id
        )
        return

    for photo in message.photo:
        file = await context.bot.get_file(photo.file_id)
        file_path = f"temp_{photo.file_id}.jpg"
        await file.download_to_drive(file_path)

        try:
            image = Image.open(file_path)
            img_hash = str(imagehash.average_hash(image))

            is_duplicate = await check_duplicate_photo(update, context, img_hash)
            if not is_duplicate:
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

    # Получаем порт и внешний URL от Render
    PORT = int(os.environ.get("PORT", 8443))
    RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")  # Render автоматически задаёт эту переменную

    if not RENDER_EXTERNAL_URL:
        raise ValueError("Переменная окружения RENDER_EXTERNAL_URL не установлена!")

    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/{TOKEN}"

    logging.info(f"Устанавливаю webhook на: {WEBHOOK_URL}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,  # путь, по которому Telegram будет отправлять обновления
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()

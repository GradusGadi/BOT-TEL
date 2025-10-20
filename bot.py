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
        # Пробуем получить оригинальное сообщение
        original_message = await context.bot.get_message(
            chat_id=update.effective_chat.id,
            message_id=original_message_id
        )
        # Если сообщение существует - это дубликат
        if original_message:
            mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name
            await update.message.reply_text(
                f"⚠️ {mention}, это фото уже было отправлено ранее!",
                reply_to_message_id=update.message.message_id
            )
            await update.message.delete()
            return True
    except Exception:
        # Оригинал удален - пропускаем фото
        pass
    
    return False

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик фото"""
    user = update.effective_user
    message = update.message

    # Игнорируем админа
    if user.id == ADMIN_USER_ID:
        return

    # Проверяем лимит фото в сообщении
    if len(message.photo) > 2:
        await message.reply_text(
            "📸 Пожалуйста, не отправляйте больше 2 фото в одном сообщении!",
            reply_to_message_id=message.message_id
        )
        return

    # Обрабатываем каждое фото в сообщении
    for photo in message.photo:
        # Скачиваем фото
        file = await context.bot.get_file(photo.file_id)
        file_path = f"temp_{photo.file_id}.jpg"
        await file.download_to_drive(file_path)

        try:
            # Создаем хеш фото
            image = Image.open(file_path)
            img_hash = str(imagehash.average_hash(image))

            # Проверяем дубликат
            is_duplicate = await check_duplicate_photo(update, context, img_hash)
            if not is_duplicate:
                # Сохраняем хеш нового фото
                save_photo_hash(img_hash, message.message_id)

        except Exception as e:
            logging.error(f"Ошибка обработки фото: {e}")
        finally:
            # Удаляем временный файл
            if os.path.exists(file_path):
                os.remove(file_path)

def main():
    # Настройка логирования
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )

    # Инициализация БД
    init_db()

    # Создание и запуск бота
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    logging.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()

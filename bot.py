
import logging
import os
import sqlite3
import time
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image
import imagehash

# Получаем настройки из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DB_FILE = "photos.db"

# Временное хранилище для альбомов и пользователей
temp_albums = {}
user_photo_count = {}
# Добавляем блокировку для одновременных запросов
processing_lock = False

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
    """Проверяет лимит фотографий в альбомах и по одному"""
    global processing_lock
    
    # Ждем если другой фото обрабатывается
    while processing_lock:
        await asyncio.sleep(0.1)
    
    processing_lock = True
    try:
        user = update.effective_user
        message = update.message
        
        # Админам можно всё
        if user.id == ADMIN_USER_ID:
            return
            
        if not message or not message.photo:
            return
        
        current_time = time.time()
        user_id = user.id
        
        # Очищаем старые данные пользователей (старше 10 секунд)
        users_to_remove = []
        for uid, data in list(user_photo_count.items()):
            if current_time - data.get('timestamp', 0) > 10:  # 10 секунд
                users_to_remove.append(uid)
        for uid in users_to_remove:
            del user_photo_count[uid]
        
        # Очищаем старые альбомы (старше 1 часа)
        albums_to_remove = []
        for aid, data in list(temp_albums.items()):
            if current_time - data.get('timestamp', 0) > 3600:
                albums_to_remove.append(aid)
        for aid in albums_to_remove:
            del temp_albums[aid]
        
        # Проверка отдельных фото (не альбом)
        if not message.media_group_id:
            if user_id not in user_photo_count:
                user_photo_count[user_id] = {
                    'count': 1,
                    'timestamp': current_time,
                    'warning_sent': False,
                    'username': user.username or user.first_name,
                    'last_message_id': message.message_id
                }
            else:
                user_photo_count[user_id]['count'] += 1
                user_photo_count[user_id]['timestamp'] = current_time
                user_photo_count[user_id]['last_message_id'] = message.message_id
            
            # Если пользователь отправил больше 2 фото за 10 секунд
            if (user_photo_count[user_id]['count'] >= 3 and 
                not user_photo_count[user_id]['warning_sent']):
                
                warning = "📸 Пожалуйста, не отправляйте больше 2 фото подряд! Ознакомьтесь с правилами в закреплённом сообщении."
                await message.reply_text(warning, reply_to_message_id=user_photo_count[user_id]['last_message_id'])
                user_photo_count[user_id]['warning_sent'] = True
        
        # Проверка альбомов (групп фото)
        else:
            album_id = message.media_group_id
            
            if album_id not in temp_albums:
                temp_albums[album_id] = {
                    'count': 1,
                    'first_message_id': message.message_id,
                    'warning_sent': False,
                    'timestamp': current_time
                }
            else:
                temp_albums[album_id]['count'] += 1
                
            # Если в альбоме больше 2 фото и предупреждение ещё не отправлялось
            if (temp_albums[album_id]['count'] > 2 and 
                not temp_albums[album_id]['warning_sent']):
                
                warning = "📸 В альбоме больше 2 фотографий! Пожалуйста, ознакомьтесь с правилами в закреплённом сообщении."
                await message.reply_text(warning, 
                                       reply_to_message_id=temp_albums[album_id]['first_message_id'])
                temp_albums[album_id]['warning_sent'] = True
    finally:
        processing_lock = False

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основная функция обработки фото (проверка дубликатов + лимитов)"""
    user = update.effective_user
    message = update.message

    # Сначала проверяем лимит фото
    await check_photos_limit(update, context)
    
    # Затем проверяем на дубликаты
    if user.id == ADMIN_USER_ID:
        return

    # Защита от None
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

def main():
    if not TOKEN:
        raise RuntimeError("❌ Переменная BOT_TOKEN не задана! Добавь её в Environment Variables на Render.")

    logging.basicConfig(level=logging.INFO)
    init_db()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # ВСЕГДА ИСПОЛЬЗУЕМ POLLING ДЛЯ RENDER
    logging.info("✅ Запуск в режиме polling для Render")
    app.run_polling()

if __name__ == "__main__":
    main()

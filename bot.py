import os
import json
import logging
import time
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, TARGET_CHAT_ID, QUEUE_FILE, STATE_FILE

# === Настройка ===
logging.basicConfig(level=logging.INFO)
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler()

# Буфер для альбомов
album_buffer = defaultdict(list)
ALBUM_TIMEOUT = 3.0
album_processing = set()

# Глобальные переменные
SEND_INTERVAL = 30 * 60  # 30 минут
is_sending = False

# === Вспомогательные функции ===
def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def save_queue(queue):
    with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
        for item in queue:
            f.write(item + '\n')

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"is_paused": False, "next_send_time": 0}
    with open(STATE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f)

def is_paused():
    return load_state().get("is_paused", False)

def set_paused(paused: bool):
    state = load_state()
    state["is_paused"] = paused
    save_state(state)

def get_next_send_time():
    return load_state().get("next_send_time", 0)

def set_next_send_time():
    state = load_state()
    state["next_send_time"] = time.time() + SEND_INTERVAL
    save_state(state)

def should_send_now():
    if is_paused():
        return False
    next_time = get_next_send_time()
    return next_time <= time.time()

def get_time_until_next_send():
    if is_paused():
        return "⏸ На паузе"
    
    if should_send_now():
        return "⚡ Готово к отправке"
    
    next_time = get_next_send_time()
    now = time.time()
    diff = int(next_time - now)
    
    if diff < 60:
        return f"⏰ Через {diff} сек"
    mins = diff // 60
    secs = diff % 60
    return f"⏰ Через {mins} мин {secs} сек"

# === Клавиатура ===
def get_control_keyboard():
    status = "⏸ Остановить" if not is_paused() else "▶️ Возобновить"
    callback = "pause" if not is_paused() else "resume"
    
    keyboard = [
        [InlineKeyboardButton(status, callback_data=callback)],
        [InlineKeyboardButton("🚀 Опубликовать сейчас", callback_data="publish_now")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh")],
        [InlineKeyboardButton("🔍 Проверить альбомы", callback_data="check_albums")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_status_text():
    count = len(load_queue())
    status = "⏸ на паузе" if is_paused() else "▶️ работает"
    albums_count = len(album_buffer) + len(album_processing)
    album_info = f"\n📦 Альбомы в обработке: {albums_count}" if albums_count > 0 else ""
    return f"📸 В очереди: {count} фото\nСтатус: {status}{album_info}\n{get_time_until_next_send()}"

# === Команда /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id == TARGET_CHAT_ID:
        return
    
    await update.message.reply_text(
        get_status_text(),
        reply_markup=get_control_keyboard()
    )

# === Обработка фото ===
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id == TARGET_CHAT_ID:
        return
    
    photo = update.message.photo[-1]
    file_id = photo.file_id
    media_group_id = update.message.media_group_id

    if media_group_id is None:
        # Одиночное фото
        queue = load_queue()
        queue.append(file_id)
        save_queue(queue)
        count = len(queue)
        logging.info(f"✅ Фото добавлено. Всего: {count}")
        await update.message.reply_text(
            f"📸 Фото добавлено в очередь!\nПозиция: #{count}",
            reply_markup=get_control_keyboard()
        )
    else:
        # Альбом
        logging.info(f"📦 Получено фото из альбома {media_group_id}")
        album_buffer[media_group_id].append((update.message.id, file_id))
        
        if len(album_buffer[media_group_id]) == 1:
            album_processing.add(media_group_id)
            
            async def process_album():
                try:
                    await asyncio.sleep(ALBUM_TIMEOUT)
                    
                    if media_group_id in album_buffer:
                        photos = sorted(album_buffer[media_group_id], key=lambda x: x[0])
                        new_ids = [fid for _, fid in photos]
                        
                        queue = load_queue()
                        start_pos = len(queue) + 1
                        queue.extend(new_ids)
                        save_queue(queue)
                        count = len(queue)
                        
                        logging.info(f"✅ Альбом {media_group_id} обработан: {len(new_ids)} фото. Позиции: {start_pos}-{count}")
                        
                        del album_buffer[media_group_id]
                        album_processing.discard(media_group_id)
                        
                        await update.message.reply_text(
                            f"📦 Альбом из {len(new_ids)} фото добавлен!\n"
                            f"📍 Позиции: #{start_pos}-#{count}",
                            reply_markup=get_control_keyboard()
                        )
                            
                except Exception as e:
                    logging.error(f"❌ Ошибка обработки альбома {media_group_id}: {e}")
                    album_processing.discard(media_group_id)
            
            asyncio.create_task(process_album())

# === Обработка кнопок ===
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.message.chat.id == TARGET_CHAT_ID:
        return
    
    action = query.data

    if action == "pause":
        set_paused(True)
        scheduler.remove_all_jobs()
        await query.edit_message_text(get_status_text(), reply_markup=get_control_keyboard())
        
    elif action == "resume":
        set_paused(False)
        set_next_send_time()
        scheduler.remove_all_jobs()
        
        # Сразу отправляем первое фото
        asyncio.create_task(send_next_photo())
        
        # Потом — каждые 30 минут
        scheduler.add_job(
            send_next_photo,
            'interval',
            minutes=30,
            id='half_hour_send'
        )
        await query.edit_message_text(get_status_text(), reply_markup=get_control_keyboard())
        
    elif action == "publish_now":
        if is_paused():
            await query.answer("❌ На паузе", show_alert=True)
            return
            
        queue = load_queue()
        if not queue:
            await query.answer("❌ Очередь пуста", show_alert=True)
            return
            
        success = await send_next_photo_immediately()
        await query.answer("✅ Отправлено!" if success else "❌ Ошибка", show_alert=True)
        await query.edit_message_text(get_status_text(), reply_markup=get_control_keyboard())
        
    elif action == "check_albums":
        active_albums = len(album_buffer) + len(album_processing)
        if active_albums > 0:
            await query.answer(f"📦 Обрабатывается альбомов: {active_albums}")
        else:
            await query.answer("✅ Нет альбомов в обработке")
            
    elif action == "refresh":
        await query.edit_message_text(get_status_text(), reply_markup=get_control_keyboard())

# === Отправка фото ===
async def send_next_photo_immediately():
    global is_sending
    if is_sending or is_paused():
        return False
        
    queue = load_queue()
    if not queue:
        return False
        
    is_sending = True
    try:
        file_id = queue.pop(0)
        await application.bot.send_photo(chat_id=TARGET_CHAT_ID, photo=file_id)
        logging.info(f"🚀 Отправлено немедленно. Осталось: {len(queue)}")
        set_next_send_time()
        save_queue(queue)
        return True
        
    except Exception as e:
        logging.error(f"❌ Ошибка: {e}")
        queue.insert(0, file_id)
        save_queue(queue)
        return False
    finally:
        is_sending = False

async def send_next_photo():
    global is_sending
    if is_sending or is_paused():
        return
    
    if not should_send_now():
        logging.info(f"⏳ Ещё не время для отправки. Ждём...")
        return
    
    queue = load_queue()
    if not queue:
        state = load_state()
        state["next_send_time"] = 0
        save_state(state)
        logging.info("📭 Очередь пуста")
        return
    
    is_sending = True
    try:
        file_id = queue.pop(0)
        await application.bot.send_photo(chat_id=TARGET_CHAT_ID, photo=file_id)
        logging.info(f"✅ Отправлено по расписанию. Осталось: {len(queue)}")
        set_next_send_time()
        save_queue(queue)
        
    except Exception as e:
        logging.error(f"❌ Ошибка: {e}")
        queue.insert(0, file_id)
        save_queue(queue)
    finally:
        is_sending = False

# === Запуск ===
async def main():
    logging.info("🤖 Запуск бота...")
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Настраиваем планировщик
    scheduler.remove_all_jobs()
    
    if not is_paused():
        if get_next_send_time() == 0:
            set_next_send_time()
        
        # Сразу отправляем первое фото при запуске
        asyncio.create_task(send_next_photo())
        
        # Потом — каждые 30 минут
        scheduler.add_job(
            send_next_photo,
            'interval',
            minutes=30,
            id='half_hour_send'
        )
        logging.info("✅ Планировщик запущен: первое фото сразу, потом каждые 30 мин")

    scheduler.start()
    
    # Запускаем бота
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())

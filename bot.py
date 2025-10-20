import os
import json
import logging
import time
import asyncio
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

from config import BOT_TOKEN, TARGET_CHAT_ID, QUEUE_FILE, STATE_FILE

# === Настройка ===
logging.basicConfig(level=logging.INFO)
application = Application.builder().token(BOT_TOKEN).build()

# Буфер для альбомов
album_buffer = defaultdict(list)
ALBUM_TIMEOUT = 3.0

# Глобальные переменные
SEND_INTERVAL = 30 * 60  # 30 минут
is_sending = False
send_task = None

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
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_status_text():
    count = len(load_queue())
    status = "⏸ на паузе" if is_paused() else "▶️ работает"
    return f"📸 В очереди: {count} фото\nСтатус: {status}\n{get_time_until_next_send()}"

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
        album_buffer[media_group_id].append((update.message.message_id, file_id))
        
        if len(album_buffer[media_group_id]) == 1:
            async def process_album():
                await asyncio.sleep(ALBUM_TIMEOUT)
                
                if media_group_id in album_buffer:
                    photos = sorted(album_buffer[media_group_id], key=lambda x: x[0])
                    new_ids = [fid for _, fid in photos]
                    
                    queue = load_queue()
                    start_pos = len(queue) + 1
                    queue.extend(new_ids)
                    save_queue(queue)
                    count = len(queue)
                    
                    logging.info(f"✅ Альбом обработан: {len(new_ids)} фото. Позиции: {start_pos}-{count}")
                    del album_buffer[media_group_id]
                    
                    await update.message.reply_text(
                        f"📦 Альбом из {len(new_ids)} фото добавлен!\n📍 Позиции: #{start_pos}-#{count}",
                        reply_markup=get_control_keyboard()
                    )
            
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
        global send_task
        if send_task:
            send_task.cancel()
        await query.edit_message_text(get_status_text(), reply_markup=get_control_keyboard())
        
    elif action == "resume":
        set_paused(False)
        set_next_send_time()
        # Запускаем отправку
        send_task = asyncio.create_task(send_scheduler())
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

# === Планировщик отправки ===
async def send_scheduler():
    """Отправляет фото каждые 30 минут"""
    while not is_paused():
        await send_next_photo()
        await asyncio.sleep(SEND_INTERVAL)

# === Запуск ===
async def main():
    logging.info("🤖 Запуск бота...")
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Запускаем планировщик если не на паузе
    if not is_paused():
        global send_task
        send_task = asyncio.create_task(send_scheduler())
        logging.info("✅ Планировщик запущен")

    # Запускаем бота
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())

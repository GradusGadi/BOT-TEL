import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from config import BOT_TOKEN, TARGET_CHAT_ID, QUEUE_FILE, STATE_FILE

# === Настройка ===
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# Буфер для альбомов - улучшенный
album_buffer = defaultdict(list)
ALBUM_TIMEOUT = 3.0  # Увеличил до 3 секунд
album_processing = set()  # Отслеживаем обрабатываемые альбомы

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
    """Проверяет, пора ли отправлять фото прямо сейчас"""
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=status, callback_data=callback)],
        [InlineKeyboardButton(text="🚀 Опубликовать сейчас", callback_data="publish_now")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh")],
        [InlineKeyboardButton(text="🔍 Проверить альбомы", callback_data="check_albums")]
    ])

def get_status_text():
    count = len(load_queue())
    status = "⏸ на паузе" if is_paused() else "▶️ работает"
    albums_count = len(album_buffer) + len(album_processing)
    album_info = f"\n📦 Альбомы в обработке: {albums_count}" if albums_count > 0 else ""
    return f"📸 В очереди: {count} фото\nСтатус: {status}{album_info}\n{get_time_until_next_send()}"

# === Обработка всех сообщений ===
@dp.message()
async def handle_all_messages(message: types.Message):
    if message.chat.id == TARGET_CHAT_ID:
        return
    if message.text == "/start":
        try:
            await message.answer(get_status_text(), reply_markup=get_control_keyboard())
        except TelegramForbiddenError:
            logging.warning("Бот заблокирован")
    elif message.photo:
        file_id = message.photo[-1].file_id
        media_group_id = message.media_group_id

        if media_group_id is None:
            # Одиночное фото
            queue = load_queue()
            queue.append(file_id)
            save_queue(queue)
            count = len(queue)
            logging.info(f"✅ Фото добавлено. Всего: {count}")
            try:
                await message.answer(f"📸 Фото добавлено в очередь!\nПозиция: #{count}", reply_markup=get_control_keyboard())
            except TelegramForbiddenError:
                pass
        else:
            # Альбом - улучшенная обработка
            logging.info(f"📦 Получено фото из альбома {media_group_id}")
            album_buffer[media_group_id].append((message.message_id, file_id))
            
            # Запускаем обработку альбома если это первое фото
            if len(album_buffer[media_group_id]) == 1:
                album_processing.add(media_group_id)
                
                async def process_album():
                    try:
                        # Ждем дольше для надежности
                        await asyncio.sleep(ALBUM_TIMEOUT)
                        
                        if media_group_id in album_buffer:
                            photos = sorted(album_buffer[media_group_id], key=lambda x: x[0])
                            new_ids = [fid for _, fid in photos]
                            
                            # Добавляем все фото в очередь
                            queue = load_queue()
                            start_pos = len(queue) + 1
                            queue.extend(new_ids)
                            save_queue(queue)
                            count = len(queue)
                            
                            # Логируем детали
                            logging.info(f"✅ Альбом {media_group_id} обработан: {len(new_ids)} фото. Позиции: {start_pos}-{count}")
                            
                            # Очищаем буфер
                            del album_buffer[media_group_id]
                            album_processing.discard(media_group_id)
                            
                            # Уведомляем пользователя
                            try:
                                await message.answer(
                                    f"📦 Альбом из {len(new_ids)} фото добавлен!\n"
                                    f"📍 Позиции: #{start_pos}-#{count}",
                                    reply_markup=get_control_keyboard()
                                )
                            except TelegramForbiddenError:
                                pass
                                
                    except Exception as e:
                        logging.error(f"❌ Ошибка обработки альбома {media_group_id}: {e}")
                        album_processing.discard(media_group_id)
                
                asyncio.create_task(process_album())

# === Кнопки ===
@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    if callback.message.chat.id == TARGET_CHAT_ID:
        return
    action = callback.data

    if action == "pause":
        set_paused(True)
        scheduler.remove_all_jobs()
        await callback.answer("⏸ Остановлено")
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
        await callback.answer("▶️ Возобновлено! Фото отправляется...")
    elif action == "publish_now":
        if is_paused():
            await callback.answer("❌ На паузе")
            return
        queue = load_queue()
        if not queue:
            await callback.answer("❌ Очередь пуста")
            return
        success = await send_next_photo_immediately()
        await callback.answer("✅ Отправлено!" if success else "❌ Ошибка")
    elif action == "check_albums":
        # Показываем статус альбомов
        active_albums = len(album_buffer) + len(album_processing)
        if active_albums > 0:
            await callback.answer(f"📦 Обрабатывается альбомов: {active_albums}")
        else:
            await callback.answer("✅ Нет альбомов в обработке")
    elif action == "refresh":
        try:
            await callback.message.edit_text(get_status_text(), reply_markup=get_control_keyboard())
            await callback.answer("✅ Обновлено")
        except TelegramBadRequest:
            await callback.answer("✅ Актуально")

    if action != "refresh":
        try:
            await callback.message.edit_text(get_status_text(), reply_markup=get_control_keyboard())
        except TelegramBadRequest:
            pass

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
        await bot.send_photo(chat_id=TARGET_CHAT_ID, photo=file_id)
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
        await bot.send_photo(chat_id=TARGET_CHAT_ID, photo=file_id)
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
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

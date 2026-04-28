import logging
import os
import re
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, filters,
    ContextTypes, CommandHandler, CallbackQueryHandler
)

# === НАСТРОЙКИ ===
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DB_FILE = "bot.db"

# === РЕГЕКСЫ ===
URL_REGEX = re.compile(r"(https?://|www\.|t\.me/)", re.IGNORECASE)
OBFUSCATED_REGEX = re.compile(r"(h\s*t\s*t\s*p|hxxp|w\s*w\s*w|t\s*\.\s*me|dot)", re.IGNORECASE)

# === БД ===
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

def log_ban(user_id, username, reason):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO bans (user_id, username, reason) VALUES (?, ?, ?)",
        (user_id, username, reason)
    )

    conn.commit()
    conn.close()

# === НОРМАЛИЗАЦИЯ ===
def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    text = text.replace("(dot)", ".").replace("[dot]", ".").replace("{dot}", ".")
    return text

# === МОДЕРАЦИЯ ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return

    user = update.effective_user
    message = update.message

    # игнор админа
    if user.id == ADMIN_USER_ID:
        return

    # игнор админов чата
    try:
        member = await context.bot.get_chat_member(message.chat_id, user.id)
        if member.status in ["administrator", "creator"]:
            return
    except:
        pass

    text = message.text or message.caption or ""
    has_link = False

    # 1. entities
    if message.entities:
        for entity in message.entities:
            if entity.type in ["url", "text_link"]:
                has_link = True
                break

    # 2. обычные ссылки
    if not has_link and URL_REGEX.search(text):
        has_link = True

    # 3. обходы
    if not has_link:
        normalized = normalize_text(text)
        if URL_REGEX.search(normalized) or OBFUSCATED_REGEX.search(text):
            has_link = True

    if has_link:
        username = user.username or user.first_name

        try:
            await message.delete()
        except Exception as e:
            logging.error(f"Ошибка удаления: {e}")

        try:
            await context.bot.ban_chat_member(
                chat_id=message.chat_id,
                user_id=user.id
            )
        except Exception as e:
            logging.error(f"Ошибка бана: {e}")

        log_ban(user.id, username, "link")

# === ПАНЕЛЬ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id != ADMIN_USER_ID:
        return

    keyboard = [
        [InlineKeyboardButton("📊 Последние баны", callback_data="bans")],
        [InlineKeyboardButton("🧹 Очистить лог", callback_data="clear")]
    ]

    await update.message.reply_text(
        "Панель управления:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# === ПОКАЗ БАНОВ ===
async def show_bans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT username, reason, created_at FROM bans ORDER BY id DESC LIMIT 10"
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        text = "Нет записей"
    else:
        text = "📊 Последние баны:\n\n"
        for r in rows:
            text += f"{r[0]} | {r[1]} | {r[2]}\n"

    await query.edit_message_text(text)

# === ОЧИСТКА ===
async def clear_bans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bans")
    conn.commit()
    conn.close()

    await query.edit_message_text("🧹 Лог очищен")

# === КНОПКИ ===
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "bans":
        await show_bans(update, context)
    elif query.data == "clear":
        await clear_bans(update, context)

# === MAIN ===
def main():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.ALL, handle_message))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    PORT = int(os.environ.get("PORT", 10000))
    RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/{TOKEN}"

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()

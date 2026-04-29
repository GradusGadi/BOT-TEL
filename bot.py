import logging
import os
import re
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, filters,
    ContextTypes, CommandHandler, CallbackQueryHandler
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DB_FILE = "bot.db"

# === DB ===
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

# === ПРОВЕРКА ССЫЛОК ===
def has_link(message):
    text = message.text or message.caption or ""

    entities = []
    if message.entities:
        entities.extend(message.entities)
    if message.caption_entities:
        entities.extend(message.caption_entities)

    for entity in entities:

        # === скрытые ссылки ===
        if entity.type == "text_link":
            url = entity.url.lower()

            if "t.me/+" in url or "joinchat" in url:
                return True

            if not ("t.me/" in url):
                return True

        # === обычные ссылки ===
        if entity.type == "url":
            url = text[entity.offset: entity.offset + entity.length].lower()

            if "t.me/+" in url or "joinchat" in url:
                return True

            if not ("t.me/" in url):
                return True

        # === /start@bot ===
        if entity.type == "bot_command" and "@" in text:
            return True

    # === regex fallback ===

    # инвайты
    if re.search(r"t\.me/\+", text, re.IGNORECASE):
        return True

    if re.search(r"joinchat", text, re.IGNORECASE):
        return True

    # внешние ссылки
    if re.search(r"https?://", text, re.IGNORECASE):
        if not re.search(r"https?://t\.me/[a-zA-Z0-9_]+/?$", text):
            return True

    if re.search(r"www\.", text, re.IGNORECASE):
        return True

    # обходы
    normalized = re.sub(r"\s+", "", text.lower())
    normalized = normalized.replace("(dot)", ".")

    if "http" in normalized and "t.me/" not in normalized:
        return True

    return False

# === МОДЕРАЦИЯ ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return

    message = update.message
    user = update.effective_user

    # только группы
    if message.chat.type == "private":
        return

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

    if has_link(message):
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
    if update.message.chat.type != "private":
        return

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

# === КНОПКИ ===
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    if query.data == "bans":
        cursor.execute("SELECT username, reason, created_at FROM bans ORDER BY id DESC LIMIT 10")
        rows = cursor.fetchall()

        if not rows:
            text = "Нет записей"
        else:
            text = "📊 Последние баны:\n\n"
            for r in rows:
                text += f"{r[0]} | {r[1]} | {r[2]}\n"

        await query.edit_message_text(text)

    elif query.data == "clear":
        cursor.execute("DELETE FROM bans")
        conn.commit()
        await query.edit_message_text("🧹 Лог очищен")

    conn.close()

# === MAIN ===
def main():
    logging.basicConfig(level=logging.INFO)

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    PORT = int(os.environ.get("PORT", 10000))
    URL = os.getenv("RENDER_EXTERNAL_URL")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()

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

URL_REGEX = re.compile(r"(https?://|www\.|t\.me/)", re.IGNORECASE)
OBFUSCATED_REGEX = re.compile(r"(h\s*t\s*t\s*p|hxxp|w\s*w\s*w|t\s*\.\s*me|dot)", re.IGNORECASE)

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

# === normalize ===
def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    text = text.replace("(dot)", ".").replace("[dot]", ".").replace("{dot}", ".")
    return text

# === moderation ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return

    message = update.message
    user = update.effective_user

    # ⚠️ ТОЛЬКО В ГРУППАХ
    if message.chat.type == "private":
        return

    if user.id == ADMIN_USER_ID:
        return

    try:
        member = await context.bot.get_chat_member(message.chat_id, user.id)
        if member.status in ["administrator", "creator"]:
            return
    except:
        pass

    text = message.text or message.caption or ""
    has_link = False

    if message.entities:
        for entity in message.entities:
            if entity.type in ["url", "text_link"]:
                has_link = True
                break

    if not has_link and URL_REGEX.search(text):
        has_link = True

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

# === ADMIN PANEL ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return

    user = update.effective_user

    if user.id != ADMIN_USER_ID:
        await update.message.reply_text("Нет доступа")
        return

    keyboard = [
        [InlineKeyboardButton("📊 Последние баны", callback_data="bans")],
        [InlineKeyboardButton("🧹 Очистить лог", callback_data="clear")]
    ]

    await update.message.reply_text(
        "Панель управления:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# === buttons ===
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

    # 🔥 ВАЖНО: порядок
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

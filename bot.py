import logging
import os
import re
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# === Настройки ===
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

# Основной regex для ссылок
URL_REGEX = re.compile(
    r"(https?://|www\.|t\.me/)",
    re.IGNORECASE
)

# Обходы типа hxxp, h t t p, dot и т.п.
OBFUSCATED_REGEX = re.compile(
    r"(h\s*t\s*t\s*p|hxxp|w\s*w\s*w|t\s*\.\s*me|dot)",
    re.IGNORECASE
)

def normalize_text(text: str) -> str:
    """
    Убираем лишние пробелы и приводим к удобному виду
    для поиска обходов
    """
    text = text.lower()

    # убираем пробелы между символами: h t t p → http
    text = re.sub(r"\s+", "", text)

    # заменяем (dot) → .
    text = text.replace("(dot)", ".").replace("[dot]", ".").replace("{dot}", ".")

    return text


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return

    user = update.effective_user
    message = update.message

    # Игнор конкретного админа
    if user.id == ADMIN_USER_ID:
        return

    # Проверка на админов чата
    try:
        member = await context.bot.get_chat_member(message.chat_id, user.id)
        if member.status in ["administrator", "creator"]:
            return
    except:
        pass

    text = message.text or message.caption or ""

    has_link = False

    # === 1. Telegram entities (самое точное) ===
    if message.entities:
        for entity in message.entities:
            if entity.type in ["url", "text_link"]:
                has_link = True
                break

    # === 2. Обычные ссылки ===
    if not has_link and URL_REGEX.search(text):
        has_link = True

    # === 3. Защита от обхода ===
    if not has_link:
        normalized = normalize_text(text)

        if URL_REGEX.search(normalized) or OBFUSCATED_REGEX.search(text):
            has_link = True

    if has_link:
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


def main():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )

    app = Application.builder().token(TOKEN).build()

    # Обрабатываем все сообщения
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    PORT = int(os.environ.get("PORT", 10000))
    RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
    if not RENDER_EXTERNAL_URL:
        raise RuntimeError("RENDER_EXTERNAL_URL не задан")

    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/{TOKEN}"
    logging.info(f"Webhook: {WEBHOOK_URL}")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=WEBHOOK_URL
    )


if __name__ == "__main__":
    main()

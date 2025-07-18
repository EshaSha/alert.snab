import os
import sqlite3
import logging
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Включаем логирование для отладки
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения из .env
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Подключаемся к базе данных SQLite
conn = sqlite3.connect('subscriptions.db', check_same_thread=False)
cursor = conn.cursor()

# Создаем таблицу подписок, если её ещё нет
cursor.execute('''
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY,
    street TEXT NOT NULL
)
''')
conn.commit()

# Состояние для ConversationHandler
ASK_STREET = 0

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для отслеживания отключений в Красноярске.\n"
        "Используй команду /subscribe чтобы подписаться на уведомления.\n"
        "Команда /unsubscribe удалит твою подписку.\n"
        "Команда /get покажет информацию по твоей подписке."
    )

# Начало диалога подписки — проверяем есть ли подписка, иначе спрашиваем улицу
async def subscribe_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor.execute("SELECT street FROM subscriptions WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    if row:
        await update.message.reply_text(
            f"У тебя уже есть подписка на улицу: {row[0]}.\n"
            "Если хочешь изменить, используй команду /unsubscribe, а потом создай новую подписку."
        )
        return ConversationHandler.END

    await update.message.reply_text("Пожалуйста, напиши название улицы для подписки.")
    return ASK_STREET

# Получение названия улицы и сохранение подписки
async def subscribe_street(update: Update, context: ContextTypes.DEFAULT_TYPE):
    street = update.message.text.strip()
    user_id = update.effective_user.id

    try:
        cursor.execute("SELECT street FROM subscriptions WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        if row:
            # На всякий случай при повторном вводе, хотя сюда мы не должны попасть, т.к. проверка в subscribe_start
            await update.message.reply_text(
                f"У тебя уже есть подписка на улицу: {row[0]}.\n"
                "Если хочешь изменить, используй команду /unsubscribe, а потом создай новую подписку."
            )
            return ConversationHandler.END

        cursor.execute("INSERT INTO subscriptions(user_id, street) VALUES (?, ?)", (user_id, street))
        conn.commit()

        await update.message.reply_text(f"Подписка на улицу '{street}' успешно создана!")
    except Exception as e:
        logger.error(f"Ошибка при создании подписки: {e}")
        await update.message.reply_text("Произошла ошибка при создании подписки. Попробуй позже.")

    return ConversationHandler.END

# Удаление подписки
async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        cursor.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        conn.commit()
        await update.message.reply_text("Твоя подписка удалена.")
    except Exception as e:
        logger.error(f"Ошибка при удалении подписки: {e}")
        await update.message.reply_text("Произошла ошибка при удалении подписки. Попробуй позже.")

# Получение информации по подписке (пока заглушка)
async def get_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor.execute("SELECT street FROM subscriptions WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    if not row:
        await update.message.reply_text("У тебя нет подписки. Создай её командой /subscribe.")
        return

    street = row[0].lower()
    entries = fetch_and_parse()
    if entries is None:
        await update.message.reply_text("Не удалось получить данные с сайта. Попробуй позже.")
        return

    matched = []
    for entry in entries:
        if street in entry["addresses"].lower():
            matched.append(entry)

    if not matched:
        await update.message.reply_text("Текущих и запланированных на завтра отключений нет.")
        return

    messages = ["Отключения запланированы:\n"]
    for entry in matched:
        msg = (
            f"Ресурс: {entry['resource']}\n"
            f"Адреса и причина: {entry['addresses']}\n"
            f"Время: {entry['times']}\n"
            f"Ссылка: http://005красноярск.рф/#otkl"
        )
        messages.append(msg)

    # Отправляем сообщения, каждое отдельным сообщением (если много)
    for msg in messages:
        await update.message.reply_text(msg)

#Функция для парсинга сайта и поиска информации по улице
def fetch_and_parse():
    url = "http://93.92.65.26/aspx/Gorod.htm"

    try:
        response = requests.get(url)
        response.encoding = 'windows-1251'  # учитываем кодировку сайта
        html = response.text
    except Exception as e:
        logger.error(f"Ошибка при загрузке страницы: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        logger.error("Таблица не найдена на странице")
        return None

    entries = []
    rows = table.find_all("tr")

    # Пропускаем заголовок таблицы (первую строку)
    for row in rows[1:]:
        cols = row.find_all("td")
        if len(cols) != 3:
            continue

        resource = cols[0].get_text(separator="\n").strip()
        addresses = cols[1].get_text(separator=";").strip()
        times = cols[2].get_text(separator="\n").strip()

        # Пропускаем пустые или служебные строки
        if not addresses or "Запланированные отключения на завтра" in addresses:
            continue

        entries.append({
            "resource": resource,
            "addresses": addresses,
            "times": times,
        })

    return entries

def main():
    # Создаем приложение бота
    app = ApplicationBuilder().token(TOKEN).build()

    # Создаем обработчик диалога для подписки
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('subscribe', subscribe_start)],
        states={
            ASK_STREET: [MessageHandler(filters.TEXT & ~filters.COMMAND, subscribe_street)],
        },
        fallbacks=[],
    )

    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("get", get_info))

    print("Бот запущен...")
    app.run_polling()

if __name__ == '__main__':
    main()
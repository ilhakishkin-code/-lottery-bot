import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "/app/data/lottery_bot.db")

if not BOT_TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. Скопируйте .env.example в .env и впишите токен, "
        "полученный у @BotFather."
    )


def _parse_admin_ids(raw: str) -> set[int]:
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


# Telegram id владельца бота (или нескольких доверенных людей через запятую).
# ТОЛЬКО эти id видят список участников, статистику по каналам и могут выбирать
# победителей. Владельцы каналов, которые создают розыгрыш через /new_lot,
# в этот список НЕ попадают и участников не видят.
ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))

if not ADMIN_IDS:
    raise RuntimeError(
        "Не задан ADMIN_IDS. Впишите в .env свой Telegram id (узнать его можно "
        "у бота @userinfobot), например: ADMIN_IDS=123456789"
    )

# Часовой пояс организаторов розыгрышей — везде считаем московское время (UTC+3)
MSK_OFFSET_HOURS = 3

# Варианты текста кнопки, предлагаемые в мастере создания розыгрыша
BUTTON_TEXT_PRESETS = ["Участвовать", "Принять участие", "Я участвую"]

# Контакт, который бот показывает победителю ("напишите за призом сюда").
# Указывайте username без @, например: PRIZE_CONTACT_USERNAME=my_manager
# Если оставить пустым — контакт для каждого сообщения будет браться
# автоматически из аккаунта того админа, который выбрал победителя.
PRIZE_CONTACT_USERNAME = os.getenv("PRIZE_CONTACT_USERNAME", "").lstrip("@").strip()

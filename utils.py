import html as _html
from datetime import datetime, timezone, timedelta
from config import MSK_OFFSET_HOURS

MSK = timezone(timedelta(hours=MSK_OFFSET_HOURS))


def esc(value) -> str:
    """
    Экранирует спецсимволы (<, >, &), чтобы они не ломали HTML-разметку Telegram
    при подстановке в текст (например, название канала может содержать что угодно).
    Используйте для ЛЮБЫХ значений, вставляемых в сообщения через f-строки,
    когда сообщение отправляется с parse_mode HTML (это режим по умолчанию для бота).
    """
    return _html.escape(str(value)) if value is not None else ""


def parse_msk_datetime(raw: str) -> datetime:
    """
    Парсит строку "ДД.ММ.ГГГГ ЧЧ:ММ" как московское время.
    Бросает ValueError, если формат неверный или дата не в будущем.
    """
    raw = raw.strip()
    dt_naive = datetime.strptime(raw, "%d.%m.%Y %H:%M")
    dt_msk = dt_naive.replace(tzinfo=MSK)
    if dt_msk <= datetime.now(MSK):
        raise ValueError("Дата и время должны быть в будущем")
    return dt_msk

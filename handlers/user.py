from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message

import database as db
from utils import esc

router = Router(name="user")


@router.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message, command: CommandObject):
    """
    Пользователь нажал кнопку "Участвовать" под постом в канале — это open-ссылка
    вида t.me/<bot>?start=g<giveaway_id>, поэтому мы сразу знаем, из какого именно
    розыгрыша (и, следовательно, канала) пришёл человек.
    """
    await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

    payload = command.args or ""
    if not payload.startswith("g"):
        await start_plain(message)
        return

    try:
        giveaway_id = int(payload[1:])
    except ValueError:
        await start_plain(message)
        return

    giveaway = await db.get_giveaway(giveaway_id)
    if not giveaway or giveaway["status"] != "published":
        await message.answer("<b>Этот розыгрыш недоступен или уже завершён.</b>")
        return

    is_new = await db.add_participant(
        giveaway_id,
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    if is_new:
        await message.answer(
            f"<b><tg-emoji emoji-id=\"5197335724411624959\">🤳</tg-emoji> Отилчно! Вы участвуете в розыгрыше в канале «{esc(giveaway['channel_title'])}»!</b>\n"
            "<blockquote>"
            f"<i>Результаты придут в бота, после подведения итогов розыгрыша.</i>\n\n"
            "</blockquote>"
            "Удачи!<tg-emoji emoji-id=\"5195058485506648605\">🐱</tg-emoji>"
        )
    else:
        await message.answer(
            "<b>Вы уже участвуете в этом розыгрыше — ваша заявка успешно зарегистрирована.</b>.\n"
            "Просто дождитесь итогов, результаты придут сюда автоматически."
        )


@router.message(CommandStart())
async def start_plain(message: Message):
    await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer(
        "<b>Привет!</b><tg-emoji emoji-id=\"5195058485506648605\">👋</tg-emoji>\n"
        "<b>Я Pet — бот для проведения розыгрышей в Telegram-каналах от GGSel.</b>\n\n"
        "Если вы владелец канала и хотите организовать розыгрыш — просто напишите команду <code>/new_lot</code> и я помогу вам его устроить.<tg-emoji emoji-id=\"5323701775453165784\">🐱</tg-emoji>\n"
        "Если вы нажали кнопку <b>«Участвовать»</b> в канале — не переживайте, вы участвуете в розыгрыше, осталось только дождаться результатов. Удачи!"
        "<tg-emoji emoji-id=\"5195216901080390378\">✋</tg-emoji>"
    )

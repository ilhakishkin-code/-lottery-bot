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
        await message.answer("Этот розыгрыш недоступен или уже завершён.")
        return

    is_new = await db.add_participant(
        giveaway_id,
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    if is_new:
        await message.answer(
            f"🎉 Вы участвуете в розыгрыше в канале «{esc(giveaway['channel_title'])}»!\n\n"
            f"Результаты придут сюда, в этот чат с ботом, когда организатор подведёт итоги."
        )
    else:
        await message.answer("Вы уже участвуете в этом розыгрыше — заявка уже зарегистрирована ✅")


@router.message(CommandStart())
async def start_plain(message: Message):
    await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer(
        "Привет! Я бот для розыгрышей в Telegram-каналах.\n\n"
        "Если вы владелец канала и хотите провести розыгрыш — используйте команду /new_lot.\n"
        "Если вы попали сюда по кнопке «Участвовать» из канала — значит, всё сработало, "
        "просто дождитесь результатов."
    )

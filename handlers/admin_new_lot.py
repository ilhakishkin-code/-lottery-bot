from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, MessageOriginChannel
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import database as db
from states import NewLotStates
from config import BUTTON_TEXT_PRESETS
from keyboards import (
    button_text_choice_kb,
    confirm_publish_kb,
    giveaway_post_kb,
)
from utils import parse_msk_datetime, esc

router = Router(name="new_lot")


def _get_forwarded_channel(message: Message):
    """
    Достаём канал, из которого переслано сообщение.
    Bot API 7.0+ не всегда заполняет message.forward_from_chat — вместо него
    может использоваться message.forward_origin. Поддерживаем оба варианта.
    """
    if message.forward_from_chat is not None:
        return message.forward_from_chat
    if isinstance(message.forward_origin, MessageOriginChannel):
        return message.forward_origin.chat
    return None


@router.message(Command("new_lot"))
async def new_lot_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(NewLotStates.waiting_forward)
    await message.answer(
        "⚙️ <b>Создание розыгрыша</b>\n\n"
        "1. Добавьте бота в канал.\n"
        "2. Выдайте боту права на отправку сообщений.\n"
        "3. Перешлите сюда любое сообщение из канала — так я узнаю, куда публиковать пост.",
        parse_mode="HTML",
    )


@router.message(NewLotStates.waiting_forward, F.forward_origin | F.forward_from_chat)
async def new_lot_got_forward(message: Message, state: FSMContext, bot: Bot):
    chat = _get_forwarded_channel(message)
    if chat is None or chat.type != "channel":
        await message.answer("Это сообщение не из канала. Перешлите сообщение именно из канала.")
        return

    # Проверяем, что бот действительно состоит в канале и может туда постить
    try:
        member = await bot.get_chat_member(chat.id, (await bot.get_me()).id)
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(
            "Не вижу бота в этом канале. Добавьте бота в канал администратором "
            "с правом отправки сообщений и перешлите сообщение ещё раз."
        )
        return

    can_post = getattr(member, "can_post_messages", True)
    if member.status not in ("administrator", "creator") or can_post is False:
        await message.answer(
            "Боту нужны права администратора канала с возможностью публикации сообщений. "
            "Выдайте права и перешлите сообщение снова."
        )
        return

    await db.upsert_channel(chat.id, chat.title or str(chat.id), message.from_user.id)
    giveaway_id = await db.create_giveaway_draft(message.from_user.id, chat.id, chat.title or str(chat.id))

    await state.update_data(giveaway_id=giveaway_id)
    await state.set_state(NewLotStates.waiting_post)
    await message.answer(
        f"Канал «{esc(chat.title)}» подключён ✅\n\n"
        "Теперь отправьте пост, который будет опубликован в канале — любой контент "
        "(текст, фото, видео, гифка), с любым форматированием и эмодзи как есть."
    )


@router.message(NewLotStates.waiting_forward)
async def new_lot_waiting_forward_fallback(message: Message):
    await message.answer("Жду пересланное сообщение из канала, а не обычный текст.")


@router.message(NewLotStates.waiting_post)
async def new_lot_got_post(message: Message, state: FSMContext):
    """
    Принимаем ЛЮБОЙ тип сообщения как пост (текст, фото, видео, гифка, что угодно) —
    и не пересобираем его текст заново, а запоминаем, ГДЕ лежит оригинал (chat_id +
    message_id). При публикации мы скопируем это сообщение через copyMessage —
    это сохраняет форматирование и кастомные (анимированные) эмодзи как есть,
    в отличие от пересборки текста через HTML-теги.
    """
    data = await state.get_data()
    await db.update_giveaway(
        data["giveaway_id"],
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )
    await state.set_state(None)
    await message.answer(
        "Выберите готовый вариант текста кнопки или напишите свой:",
        reply_markup=button_text_choice_kb(),
    )


@router.callback_query(F.data.startswith("btntext:"))
async def new_lot_button_text_choice(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.split(":", 1)[1]
    if choice == "custom":
        await state.set_state(NewLotStates.waiting_button_text_custom)
        await callback.message.edit_text("Напишите текст, который будет на кнопке:")
        await callback.answer()
        return

    text = BUTTON_TEXT_PRESETS[int(choice)]
    data = await state.get_data()
    await db.update_giveaway(data["giveaway_id"], button_text=text)
    await state.set_state(NewLotStates.waiting_winners_count)
    await callback.message.edit_text(
        f"Текст кнопки: «{esc(text)}»\n\nВведите количество победителей (число от 1 до 100):"
    )
    await callback.answer()


@router.message(NewLotStates.waiting_button_text_custom, F.text)
async def new_lot_button_text_custom(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    await db.update_giveaway(data["giveaway_id"], button_text=text)
    await state.set_state(NewLotStates.waiting_winners_count)
    await message.answer(f"Текст кнопки: «{esc(text)}»\n\nВведите количество победителей (число от 1 до 100):")


@router.message(NewLotStates.waiting_winners_count, F.text)
async def new_lot_winners_count(message: Message, state: FSMContext):
    raw = message.text.strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 100):
        await message.answer("Введите целое число от 1 до 100.")
        return

    data = await state.get_data()
    await db.update_giveaway(data["giveaway_id"], winners_count=int(raw))
    await state.set_state(NewLotStates.waiting_datetime)
    await message.answer(
        "Дата и время итогов.\n"
        "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
        "Время — московское (МСК, UTC+3). Дата и время должны быть в будущем.\n\n"
        "Например: 27.08.2026 15:30",
        parse_mode="HTML",
    )


@router.message(NewLotStates.waiting_datetime, F.text)
async def new_lot_datetime(message: Message, state: FSMContext):
    try:
        dt_msk = parse_msk_datetime(message.text)
    except ValueError:
        await message.answer(
            "Не получилось разобрать дату. Проверьте формат "
            "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code> и что дата в будущем.",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    giveaway_id = data["giveaway_id"]
    await db.update_giveaway(giveaway_id, draw_datetime=dt_msk.strftime("%d.%m.%Y %H:%M"))

    # Обязательная подписка на каналы — пока не реализуем (по договорённости), просто пропускаем.

    giveaway = await db.get_giveaway(giveaway_id)
    await state.set_state(NewLotStates.confirm)
    await message.answer(
        "Проверьте розыгрыш перед публикацией:\n\n"
        f"📢 Канал: {esc(giveaway['channel_title'])}\n"
        f"🔘 Кнопка: {esc(giveaway['button_text'])}\n"
        f"🏆 Победителей: {giveaway['winners_count']}\n"
        f"🕒 Итоги: {giveaway['draw_datetime']} (МСК)\n\n"
        "Публикуем?",
        reply_markup=confirm_publish_kb(giveaway_id),
    )


@router.callback_query(F.data.startswith("cancel_draft:"))
async def new_lot_cancel(callback: CallbackQuery, state: FSMContext):
    giveaway_id = int(callback.data.split(":")[1])
    await db.update_giveaway(giveaway_id, status="finished")
    await state.clear()
    await callback.message.edit_text("Черновик отменён.")
    await callback.answer()


@router.callback_query(F.data.startswith("publish:"))
async def new_lot_publish(callback: CallbackQuery, state: FSMContext, bot: Bot):
    giveaway_id = int(callback.data.split(":")[1])
    giveaway = await db.get_giveaway(giveaway_id)
    if not giveaway:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return

    me = await bot.get_me()
    kb = giveaway_post_kb(me.username, giveaway_id, giveaway["button_text"])

    # copyMessage клонирует исходное сообщение (то, что вы прислали боту в личку)
    # как есть — с любым форматированием и кастомными эмодзи, без пересборки текста.
    sent = await bot.copy_message(
        chat_id=giveaway["channel_id"],
        from_chat_id=giveaway["source_chat_id"],
        message_id=giveaway["source_message_id"],
        reply_markup=kb,
    )

    await db.update_giveaway(giveaway_id, status="published", message_id=sent.message_id)
    await state.clear()
    await callback.message.edit_text(
        f"✅ Розыгрыш опубликован в канале «{esc(giveaway['channel_title'])}»!"
    )
    await callback.answer()

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


def _get_forward_info(message: Message):
    """
    Достаём (канал, id оригинального сообщения) из пересланного поста.
    Bot API 7.0+ отдаёт это через message.forward_origin (MessageOriginChannel),
    старые поля forward_from_chat/forward_from_message_id оставлены как запасной
    вариант на случай нестандартного клиента.
    """
    if isinstance(message.forward_origin, MessageOriginChannel):
        return message.forward_origin.chat, message.forward_origin.message_id
    if message.forward_from_chat is not None:
        return message.forward_from_chat, message.forward_from_message_id
    return None, None


@router.message(Command("new_lot"))
async def new_lot_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(NewLotStates.waiting_forward)
    await message.answer(
        "⚙️ <b>Создание розыгрыша:</b>\n\n"
        "<blockquote>"
        "1. Добавьте бота в канал администратором с правом "
        "<b>«Редактировать сообщения других участников»</b>.\n"
        "2. Опубликуйте пост розыгрыша в канале <b>сами, как обычно</b> — со всем "
        "форматированием, картинками и (если есть) анимированными эмодзи. "
        "Бот не будет ничего пересобирать, поэтому всё сохранится как есть.\n"
        "3. Перешлите сюда этот уже опубликованный пост — бот прикрепит к нему "
        "кнопку «Участвовать», не трогая сам текст.",
        "</blockquote>"
        parse_mode="HTML",
    )


@router.message(NewLotStates.waiting_forward, F.forward_origin | F.forward_from_chat)
async def new_lot_got_forward(message: Message, state: FSMContext, bot: Bot):
    chat, source_message_id = _get_forward_info(message)
    if chat is None or chat.type != "channel" or source_message_id is None:
        await message.answer(
            "<b>Это должен быть пересланный ОПУБЛИКОВАННЫЙ пост из канала.</b>"
            "<b>Сначала опубликуйте пост в канале, затем перешлите его сюда.</b>"
        )
        return

    # Боту нужно право РЕДАКТИРОВАТЬ сообщения других — именно этим правом
    # мы потом прикрепим кнопку к вашему посту, не публикуя ничего от своего имени.
    try:
        member = await bot.get_chat_member(chat.id, (await bot.get_me()).id)
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(
            "Не вижу бота в этом канале. Добавьте бота администратором с правом "
            "«Редактировать сообщения других участников» и перешлите пост ещё раз."
        )
        return

    can_edit = getattr(member, "can_edit_messages", False)
    if member.status not in ("administrator", "creator") or not can_edit:
        await message.answer(
            "<b>Боту нужны права администратора канала с возможностью</b> "
            "<b>«Редактировать сообщения других участников»</b>. "
            "Выдайте это право и перешлите пост снова.",
            parse_mode="HTML",
        )
        return

    await db.upsert_channel(chat.id, chat.title or str(chat.id), message.from_user.id)
    giveaway_id = await db.create_giveaway_draft(message.from_user.id, chat.id, chat.title or str(chat.id))
    await db.update_giveaway(giveaway_id, source_chat_id=chat.id, source_message_id=source_message_id)

    await state.update_data(giveaway_id=giveaway_id)
    await state.set_state(None)
    await message.answer(
        "<b>Пост получен!\n\n</b>"
        "<blockquote>"
        "Выберите готовый вариант текста кнопки или напишите свой:"
        "</blockquote>",
        reply_markup=button_text_choice_kb(),
    )


@router.message(NewLotStates.waiting_forward)
async def new_lot_waiting_forward_fallback(message: Message):
    await message.answer(
        "Жду пересланный ОПУБЛИКОВАННЫЙ пост из канала, а не обычное сообщение."
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
        f"<b>Текст кнопки: «{esc(text)}»</b>\n\n"
        "<blockquote>"
        "Введите количество победителей (число от 1 до 100):"
        "</blockquote>"
    )
    await callback.answer()


@router.message(NewLotStates.waiting_button_text_custom, F.text)
async def new_lot_button_text_custom(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    await db.update_giveaway(data["giveaway_id"], button_text=text)
    await state.set_state(NewLotStates.waiting_winners_count)
    await message.answer(f"<b>Текст кнопки: «{esc(text)}»</b>\n\n"
                         "Введите количество победителей (число от 1 до 100):")


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
        "<blockquote>"
        "<b>Дата и время итогов.</b>\n"
        "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
        "Время — московское (МСК). Дата и время должны быть в будущем.\n\n"
        "Например: 27.08.2026 15:30"
        "</blockquote>",
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

    # Прикрепляем кнопку к УЖЕ ОПУБЛИКОВАННОМУ вами посту, не пересылая и не
    # переотправляя его содержимое — поэтому анимированные эмодзи и любое
    # форматирование остаются ровно такими, какими вы их опубликовали сами.
    try:
        await bot.edit_message_reply_markup(
            chat_id=giveaway["source_chat_id"],
            message_id=giveaway["source_message_id"],
            reply_markup=kb,
        )
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        await callback.answer(
            "Не удалось прикрепить кнопку. Проверьте, что у бота есть право "
            "«Редактировать сообщения других участников» в этом канале.",
            show_alert=True,
        )
        return

    await db.update_giveaway(giveaway_id, status="published", message_id=giveaway["source_message_id"])
    await state.clear()
    await callback.message.edit_text(
        f"✅ Кнопка прикреплена к посту в канале «{esc(giveaway['channel_title'])}»!"
    )
    await callback.answer()

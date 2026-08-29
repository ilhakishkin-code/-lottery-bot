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
        "<tg-emoji emoji-id=\"5341715473882955310\">⚙️</tg-emoji>"
        "<b>Создание розыгрыша:</b>\n\n"
        "<blockquote>"
        "1. Добавьте бота в канал администратором с правом "
        "<b>«Редактировать сообщения других участников»</b>.\n"
        "2. Опубликуйте пост розыгрыша в канале <b>сами, как обычно</b> — со всем "
        "форматированием, картинками и (если есть) анимированными эмодзи. "
        "Бот не будет ничего пересобирать, поэтому всё сохранится как есть.\n"
        "3. Перешлите сюда этот уже опубликованный пост — бот прикрепит к нему "
        "кнопку «Участвовать», не трогая сам текст."
        "</blockquote>",
        parse_mode="HTML",
    )
 
 
@router.message(NewLotStates.waiting_forward, F.forward_origin | F.forward_from_chat)
async def new_lot_got_forward(message: Message, state: FSMContext, bot: Bot):
    chat, source_message_id = _get_forward_info(message)
    if chat is None or chat.type != "channel" or source_message_id is None:
        await message.answer(
            "<b>Это должен быть пересланный ОПУБЛИКОВАННЫЙ пост из канала.</b>\n\n"
            "<blockquote>"
            "Сначала опубликуйте пост в канале, затем перешлите его сюда."
            "</blockquote>",
            parse_mode="HTML",
        )
        return
 
    # Боту нужно право РЕДАКТИРОВАТЬ сообщения других — именно этим правом
    # мы потом прикрепим кнопку к вашему посту, не публикуя ничего от своего имени.
    try:
        member = await bot.get_chat_member(chat.id, (await bot.get_me()).id)
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(
            "<b>Не вижу бота в этом канале.</b>\n\n"
            "<blockquote>"
            "<b>Не вижу бота в этом канале. Добавьте бота в канал администратором</b>"
            "<b>с правом редактировать сообщения и перешлите сообщение ещё раз.</b>"
            "</blockquote>",
            parse_mode="HTML",
        )
        return
 
    can_edit = getattr(member, "can_edit_messages", False)
    if member.status not in ("administrator", "creator") or not can_edit:
        await message.answer(
            "<b>Боту нужны права администратора канала.</b>\n\n"
            "<blockquote>"
            "Выдайте право «Редактировать сообщения других участников» "
            "и перешлите пост снова."
            "</blockquote>",
            parse_mode="HTML",
        )
        return
 
    await db.upsert_channel(chat.id, chat.title or str(chat.id), message.from_user.id)
    giveaway_id = await db.create_giveaway_draft(message.from_user.id, chat.id, chat.title or str(chat.id))
    await db.update_giveaway(giveaway_id, source_chat_id=chat.id, source_message_id=source_message_id)
 
    await state.update_data(giveaway_id=giveaway_id)
    await state.set_state(None)
    await message.answer(
        "<tg-emoji emoji-id=\"5206607081334906820\">✔️</tg-emoji> "
        "<b>Пост получен!</b>\n\n"
        "<blockquote>"
        "Выберите готовый вариант текста кнопки или напишите свой:"
        "</blockquote>",
        reply_markup=button_text_choice_kb(),
        parse_mode="HTML",
    )
 
 
@router.message(NewLotStates.waiting_forward)
async def new_lot_waiting_forward_fallback(message: Message):
    await message.answer(
        "<tg-emoji emoji-id=\"5210956306952758910\">👀</tg-emoji><b>Жду пересланное сообщение из канала, а не обычный текст.</b>"),
        parse_mode="HTML",
    )
 
 
@router.callback_query(F.data.startswith("btntext:"))
async def new_lot_button_text_choice(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.split(":", 1)[1]
    if choice == "custom":
        await state.set_state(NewLotStates.waiting_button_text_custom)
        await callback.message.edit_text(
            "<tg-emoji emoji-id=\"5210956306952758910\">👀</tg-emoji><b>Напишите текст, который будет на кнопке:</b>",
            parse_mode="HTML",
        )
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
        "</blockquote>",
        parse_mode="HTML",
    )
    await callback.answer()
 
 
@router.message(NewLotStates.waiting_button_text_custom, F.text)
async def new_lot_button_text_custom(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    await db.update_giveaway(data["giveaway_id"], button_text=text)
    await state.set_state(NewLotStates.waiting_winners_count)
    await message.answer(
        f"<tg-emoji emoji-id=\"5210956306952758910\">👀</tg-emoji><b>Текст кнопки: «{esc(text)}»</b>\n\n"
        "<blockquote>"
        "Введите количество победителей (число от 1 до 100):"
        "</blockquote>",
        parse_mode="HTML",
    )
 
 
@router.message(NewLotStates.waiting_winners_count, F.text)
async def new_lot_winners_count(message: Message, state: FSMContext):
    raw = message.text.strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 100):
        await message.answer(
            "<tg-emoji emoji-id=\"5210956306952758910\">👀</tg-emoji><b>Введите целое число от 1 до 100.</b>",
            parse_mode="HTML",
        )
        return
 
    data = await state.get_data()
    await db.update_giveaway(data["giveaway_id"], winners_count=int(raw))
    await state.set_state(NewLotStates.waiting_datetime)
    await message.answer(
        "<tg-emoji emoji-id=\"5458603043203327669\">🔔</tg-emoji> <b>Дата и время итогов:</b>\n"
        "<blockquote>"
        "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
        "Время — московское (МСК, UTC+3). Дата и время должны быть в будущем.\n\n"
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
            "<b>Не получилось разобрать дату.</b>\n\n"
            "<blockquote>"
            "Проверьте формат <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> "
            "и что дата в будущем."
            "</blockquote>",
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
        "<b>Проверьте розыгрыш перед публикацией:</b>\n\n"
        f"<tg-emoji emoji-id=\"5461151367559141950\">🎉</tg-emoji><b> Канал:</b> {html.escape(giveaway['channel_title'])}\n"
        f"<tg-emoji emoji-id=\"5438496463044752972\">⭐️</tg-emoji><b> Кнопка:</b> {html.escape(giveaway['button_text'])}\n"
        f"<tg-emoji emoji-id=\"5440539497383087970\">🥇</tg-emoji><b> Победителей:</b> {giveaway['winners_count']}\n"
        f"<tg-emoji emoji-id=\"5447410659077661506\">🌐</tg-emoji><b> Итоги:</b> {giveaway['draw_datetime']} (МСК)\n\n"
        "<b>Публикуем?</b>",
        reply_markup=confirm_publish_kb(giveaway_id),
        parse_mode="HTML",
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
    except (TelegramBadRequest, TelegramForbiddenError):
        await callback.answer(
            "Не удалось прикрепить кнопку. Проверьте, что у бота есть право "
            "«Редактировать сообщения других участников» в этом канале.",
            show_alert=True,
        )
        return
 
    await db.update_giveaway(giveaway_id, status="published", message_id=giveaway["source_message_id"])
    await state.clear()
    await callback.message.edit_text(
     
        f"<tg-emoji emoji-id=\"5206607081334906820\">✔️</tg-emoji><b>Кнопка прикреплена к посту в канале «{esc(giveaway['channel_title'])}»!</b>",
        parse_mode="HTML",
    )
    await callback.answer()
 

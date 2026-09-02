from datetime import datetime, timezone
 
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
    end_lot_list_kb,
    end_lot_confirm_kb,
)
from utils import parse_msk_datetime, esc
 
router = Router(name="new_lot")
 
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
 
 
def _get_forward_info(message: Message):
    if isinstance(message.forward_origin, MessageOriginChannel):
        return message.forward_origin.chat, message.forward_origin.message_id
    if message.forward_from_chat is not None:
        return message.forward_from_chat, message.forward_from_message_id
    return None, None
 
 
async def _attach_button(bot: Bot, giveaway: dict, chat_id: int, message_id: int) -> bool:
    me = await bot.get_me()
    kb = giveaway_post_kb(me.username, giveaway["id"], giveaway["button_text"])
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=kb)
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    await db.update_giveaway(giveaway["id"], status="published", source_message_id=message_id)
    return True
 
 
@router.message(Command("new_lot"))
async def new_lot_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(NewLotStates.waiting_forward)
    await message.answer(
        "<tg-emoji emoji-id=\"5341715473882955310\">⚙️</tg-emoji>"
        "<b>Создание розыгрыша:</b>\n\n"
        "<blockquote>"
        "1. Добавьте меня в ваш канал администратором и дайте право "
        "<b>«Редактировать сообщения других участников»</b>.\n"
        "2. Перешлите сюда любое сообщение из этого канала — так бот поймет, куда "
        "потом публиковать розыгрыш.\n"
        "</blockquote>",
        parse_mode="HTML",
    )
 
 
@router.message(NewLotStates.waiting_forward, F.forward_origin | F.forward_from_chat)
async def new_lot_got_forward(message: Message, state: FSMContext, bot: Bot):
    chat, _ = _get_forward_info(message)
    if chat is None or chat.type != "channel":
        await message.answer("<b>Это сообщение не из канала. Перешлите сообщение именно из канала</b>.<tg-emoji emoji-id=\"5195216901080390378\">✋</tg-emoji>")
        return
 
    try:
        member = await bot.get_chat_member(chat.id, (await bot.get_me()).id)
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(
            "<b>Не вижу бота в этом канале. Добавьте бота администратором с правом.</b> "
            "<b>«Редактировать сообщения других участников» и перешлите сообщение ещё раз.</b><tg-emoji emoji-id=\"5195216901080390378\">✋</tg-emoji>"
        )
        return
 
    can_edit = getattr(member, "can_edit_messages", False)
    if member.status not in ("administrator", "creator") or not can_edit:
        await message.answer(
            "<b>Боту нужны права администратора канала с возможностью</b> "
            "<b>«Редактировать сообщения других участников»</b>. "
            "<b>Выдайте это право и перешлите сообщение снова.</b>",
            parse_mode="HTML",
        )
        return
 
    await db.upsert_channel(chat.id, chat.title or str(chat.id), message.from_user.id)
    giveaway_id = await db.create_giveaway_draft(message.from_user.id, chat.id, chat.title or str(chat.id))
 
    await state.update_data(giveaway_id=giveaway_id)
    await state.set_state(NewLotStates.waiting_post)
    await message.answer( 
        "<tg-emoji emoji-id=\"5195216901080390378\">✋</tg-emoji> "
        f"<b>Канал «{esc(chat.title)}» подключён!</b>\n\n"
        "<blockquote>"
        "Теперь пришлите сюда пост розыгрыша."
        "</blockquote>"
 
    )
 
 
@router.message(NewLotStates.waiting_forward)
async def new_lot_waiting_forward_fallback(message: Message):
    await message.answer("<b>Жду пересланное сообщение из канала, а не обычный текст.</b><tg-emoji emoji-id=\"5195216901080390378\">✋</tg-emoji>")
 
 
@router.message(NewLotStates.waiting_post)
async def new_lot_got_post(message: Message, state: FSMContext):
    data = await state.get_data()
    await db.update_giveaway(
        data["giveaway_id"],
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )
    await state.set_state(None)
    await message.answer(
        "<b>Пост получен.</b> <tg-emoji emoji-id=\"5195216901080390378\">✋</tg-emoji>\n\n"
     "<blockquote>"
        "Выберите готовый вариант текста кнопки или напишите свой:"
     "</blockquote>",
        reply_markup=button_text_choice_kb(),
    )
 
 
@router.callback_query(F.data.startswith("btntext:"))
async def new_lot_button_text_choice(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.split(":", 1)[1]
    if choice == "custom":
        await state.set_state(NewLotStates.waiting_button_text_custom)
        await callback.message.edit_text("<tg-emoji emoji-id=\"5195058485506648605\">👋</tg-emoji><b>Напишите текст, который будет на кнопке:</b>")
        await callback.answer()
        return
 
    text = BUTTON_TEXT_PRESETS[int(choice)]
    data = await state.get_data()
    await db.update_giveaway(data["giveaway_id"], button_text=text)
    await state.set_state(NewLotStates.waiting_winners_count)
    await callback.message.edit_text(
        f"<tg-emoji emoji-id=\"5195058485506648605\">👋</tg-emoji> <b>Текст кнопки: «{esc(text)}»</b>\n\n"
        "<tg-emoji emoji-id=\"5195202345436224393\">🫰</tg-emoji> <b>Введите количество победителей (число от 1 до 100):</b>"
    )
    await callback.answer()
 
 
@router.message(NewLotStates.waiting_button_text_custom, F.text)
async def new_lot_button_text_custom(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    await db.update_giveaway(data["giveaway_id"], button_text=text)
    await state.set_state(NewLotStates.waiting_winners_count)
    await message.answer(f"<tg-emoji emoji-id=\"5195058485506648605\">👋</tg-emoji> <b>Текст кнопки: «{esc(text)}»</b>\n\n"
                         "<tg-emoji emoji-id=\"5195202345436224393\">🫰</tg-emoji> <b>Введите количество победителей (число от 1 до 100):</b>"
                        )
 
 
@router.message(NewLotStates.waiting_winners_count, F.text)
async def new_lot_winners_count(message: Message, state: FSMContext):
    raw = message.text.strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 100):
        await message.answer("<b>Введите целое число от 1 до 100:</b>")
        return
 
    data = await state.get_data()
    await db.update_giveaway(data["giveaway_id"], winners_count=int(raw))
    await state.set_state(NewLotStates.waiting_datetime)
    await message.answer(
        "<b>Введите дату и время итогов.</b><tg-emoji emoji-id=\"5195058485506648605\">👋</tg-emoji>\n\n"
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
            "<b>Не получилось разобрать дату. Проверьте формат:</b>"
            "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code> <b>и что дата в будущем.</b>",
            parse_mode="HTML",
        )
        return
 
    data = await state.get_data()
    giveaway_id = data["giveaway_id"]
    await db.update_giveaway(giveaway_id, draw_datetime=dt_msk.strftime("%d.%m.%Y %H:%M"))
 
    giveaway = await db.get_giveaway(giveaway_id)
    await state.set_state(NewLotStates.confirm)
    await message.answer(
        "<b>Проверьте розыгрыш перед публикацией:</b>\n\n"
        f"<tg-emoji emoji-id=\"5461151367559141950\">🎉</tg-emoji><b> Канал:</b> {esc(giveaway['channel_title'])}\n"
        f"<tg-emoji emoji-id=\"5438496463044752972\">⭐️</tg-emoji><b> Кнопка:</b> {esc(giveaway['button_text'])}\n"
        f"<tg-emoji emoji-id=\"5440539497383087970\">🥇</tg-emoji><b> Победителей:</b> {giveaway['winners_count']}\n"
        f"<tg-emoji emoji-id=\"5447410659077661506\">🌐</tg-emoji><b> Итоги:</b> {giveaway['draw_datetime']} (МСК)\n\n"
        "<b>Готовы получить финальное сообщение с кнопкой?</b>",
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
async def new_lot_send_preview(callback: CallbackQuery, state: FSMContext, bot: Bot):
    giveaway_id = int(callback.data.split(":")[1])
    giveaway = await db.get_giveaway(giveaway_id)
    if not giveaway:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
 
    me = await bot.get_me()
    kb = giveaway_post_kb(me.username, giveaway_id, giveaway["button_text"])
 
    await bot.copy_message(
        chat_id=giveaway["source_chat_id"],
        from_chat_id=giveaway["source_chat_id"],
        message_id=giveaway["source_message_id"],
        reply_markup=kb,
    )
 
    await db.update_giveaway(
        giveaway_id,
        status="awaiting_channel_post",
        awaiting_since=datetime.now(timezone.utc).isoformat(),
    )
    await state.clear()
    await callback.message.answer(
        f"<tg-emoji emoji-id=\"5195058485506648605\">👋</tg-emoji> <b>Теперь перешлите сообщение выше в канал.</b> «{esc(giveaway['channel_title'])}».\n\n"
    )
    await callback.answer()
 
 
@router.channel_post(F.chat.type == "channel")
async def on_channel_post(message: Message, bot: Bot):
    giveaway = await db.get_awaiting_giveaway_for_channel(message.chat.id)
    if not giveaway:
        return
 
    ok = await _attach_button(bot, giveaway, message.chat.id, message.message_id)
    if ok:
        try:
            await bot.send_message(
                giveaway["owner_id"],
                f"<b>Кнопка автоматически прикреплена к посту в канале «{esc(giveaway['channel_title'])}»!</b>",
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
 
 
@router.message(F.forward_origin | F.forward_from_chat)
async def manual_attach_fallback(message: Message, bot: Bot, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return
 
    chat, source_message_id = _get_forward_info(message)
    if chat is None or chat.type != "channel" or source_message_id is None:
        return
 
    giveaway = await db.get_awaiting_giveaway_for_channel(chat.id)
    if not giveaway or giveaway["owner_id"] != message.from_user.id:
        return
 
    ok = await _attach_button(bot, giveaway, chat.id, source_message_id)
    if ok:
        await message.answer(f"✅ Кнопка прикреплена к посту в канале «{esc(giveaway['channel_title'])}» вручную.")
    else:
        await message.answer(
            "Не удалось прикрепить кнопку. Проверьте, что у бота есть право "
            "«Редактировать сообщения других участников» в этом канале."
        )
 
 
@router.message(Command("end_lot"))
async def end_lot_start(message: Message):
    giveaways = await db.list_giveaways_by_owner(message.from_user.id)
    active = [g for g in giveaways if g["status"] == "published"]
    if not active:
        await message.answer("У вас нет активных розыгрышей, которые можно завершить досрочно.")
        return
    await message.answer(
        "Выберите розыгрыш, который нужно завершить досрочно (кнопка «Участвовать» будет убрана из поста):",
        reply_markup=end_lot_list_kb(active),
    )
 
 
@router.callback_query(F.data.startswith("end_lot:"))
async def end_lot_ask_confirm(callback: CallbackQuery):
    giveaway_id = int(callback.data.split(":")[1])
    giveaway = await db.get_giveaway(giveaway_id)
    if not giveaway or giveaway["owner_id"] != callback.from_user.id:
        await callback.answer("Это не ваш розыгрыш", show_alert=True)
        return
    await callback.message.edit_text(
        f"Точно завершить розыгрыш в канале «{esc(giveaway['channel_title'])}» досрочно? "
        f"Кнопка «Участвовать» будет убрана из поста, действие необратимо.",
        reply_markup=end_lot_confirm_kb(giveaway_id),
    )
    await callback.answer()
 
 
@router.callback_query(F.data.startswith("end_lot_yes:"))
async def end_lot_finish(callback: CallbackQuery, bot: Bot):
    giveaway_id = int(callback.data.split(":")[1])
    giveaway = await db.get_giveaway(giveaway_id)
    if not giveaway or giveaway["owner_id"] != callback.from_user.id:
        await callback.answer("Это не ваш розыгрыш", show_alert=True)
        return
 
    try:
        await bot.edit_message_reply_markup(
            chat_id=giveaway["source_chat_id"] if giveaway["status"] != "published" else giveaway["channel_id"],
            message_id=giveaway["source_message_id"],
            reply_markup=None,
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        pass
 
    await db.update_giveaway(giveaway_id, status="finished")
    await callback.message.edit_text("✅ Розыгрыш завершён, кнопка «Участвовать» убрана из поста.")
    await callback.answer()
 
 
@router.callback_query(F.data.startswith("end_lot_no:"))
async def end_lot_cancel(callback: CallbackQuery):
    await callback.message.edit_text("Отменено, розыгрыш продолжается.")
    await callback.answer()
 

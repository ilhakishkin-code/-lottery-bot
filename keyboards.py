from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BUTTON_TEXT_PRESETS


def button_text_choice_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, text in enumerate(BUTTON_TEXT_PRESETS):
        b.button(text=text, callback_data=f"btntext:{i}")
    b.button(text="✏️ Написать свой вариант", callback_data="btntext:custom")
    b.adjust(1)
    return b.as_markup()


def confirm_publish_kb(giveaway_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Прислать превью с кнопкой", callback_data=f"publish:{giveaway_id}")
    b.button(text="❌ Отменить", callback_data=f"cancel_draft:{giveaway_id}")
    b.adjust(1)
    return b.as_markup()


def giveaway_post_kb(bot_username: str, giveaway_id: int, button_text: str) -> InlineKeyboardMarkup:
    """Кнопка под постом в канале — уводит пользователя в ЛС бота с /start=g<id>."""
    b = InlineKeyboardBuilder()
    b.button(text=button_text, url=f"https://t.me/{bot_username}?start=g{giveaway_id}")
    return b.as_markup()


def giveaways_list_kb(giveaways: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for g in giveaways:
        status_emoji = {"draft": "📝", "published": "🟢", "finished": "🏁"}.get(g["status"], "")
        label = f"{status_emoji} #{g['id']} — {g['channel_title']}"
        b.button(text=label, callback_data=f"gv:{g['id']}")
    b.adjust(1)
    return b.as_markup()


def giveaway_detail_kb(giveaway_id: int, status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status == "draft":
        b.button(text="✅ Опубликовать", callback_data=f"publish:{giveaway_id}")
    b.button(text="👥 Участники", callback_data=f"gv_participants:{giveaway_id}")
    b.button(text="📣 Напомнить всем об итогах", callback_data=f"gv_notify_all:{giveaway_id}")
    b.button(text="🏆 Выбрать победителя", callback_data=f"gv_pick:{giveaway_id}")
    b.button(text="🚫 Удалить участника", callback_data=f"gv_remove_participant:{giveaway_id}")
    b.button(text="🔔 Победители и напоминания", callback_data=f"gv_winners:{giveaway_id}")
    b.button(text="🗑 Удалить розыгрыш", callback_data=f"gv_delete:{giveaway_id}")
    b.button(text="⬅️ К списку розыгрышей", callback_data="gv_back")
    b.adjust(1)
    return b.as_markup()


def gv_delete_confirm_kb(giveaway_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚠️ Да, удалить этот розыгрыш", callback_data=f"gv_delete_yes:{giveaway_id}")
    b.button(text="Отмена", callback_data=f"gv_delete_no:{giveaway_id}")
    b.adjust(1)
    return b.as_markup()


def winners_list_kb(giveaway_id: int, winners: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for w in winners:
        label = f"🔔 Напомнить: {('@' + w['username']) if w['username'] else w['user_id']}"
        b.button(text=label, callback_data=f"gv_remind:{giveaway_id}:{w['user_id']}")
    b.button(text="⬅️ Назад", callback_data=f"gv:{giveaway_id}")
    b.adjust(1)
    return b.as_markup()


def reset_confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚠️ Да, удалить всё", callback_data="reset_confirm")
    b.button(text="Отмена", callback_data="reset_cancel")
    b.adjust(1)
    return b.as_markup()


def end_lot_list_kb(giveaways: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for g in giveaways:
        b.button(text=f"#{g['id']} — {g['channel_title']}", callback_data=f"end_lot:{g['id']}")
    b.adjust(1)
    return b.as_markup()


def end_lot_confirm_kb(giveaway_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚠️ Да, завершить досрочно", callback_data=f"end_lot_yes:{giveaway_id}")
    b.button(text="Отмена", callback_data=f"end_lot_no:{giveaway_id}")
    b.adjust(1)
    return b.as_markup()

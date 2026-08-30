import asyncio

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

import database as db
from states import WinnerFlowStates
from config import ADMIN_IDS, PRIZE_CONTACT_USERNAME
from keyboards import giveaways_list_kb, giveaway_detail_kb, winners_list_kb, reset_confirm_kb
from utils import esc

router = Router(name="admin_panel")

# Весь этот роутер — приватная админ-панель. Помимо проверки is_admin внутри
# каждого хендлера, дополнительно блокируем срабатывание где-либо, кроме личных
# сообщений с ботом: если админ случайно наберёт /giveaways или /stats в группе
# или в обсуждении при канале, бот там вообще не ответит — ответ со списком
# участников не должен даже теоретически попасть в чат, где его видят посторонние.
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def prize_contact(fallback_username: str | None) -> str:
    """
    Кого показать победителю как контакт за призом.
    Если в конфиге задан PRIZE_CONTACT_USERNAME — используем его всегда,
    независимо от того, кто из админов выбрал победителя.
    Иначе — берём username того админа, который сейчас нажал кнопку.
    """
    if PRIZE_CONTACT_USERNAME:
        return f"@{PRIZE_CONTACT_USERNAME}"
    if fallback_username:
        return f"@{fallback_username}"
    return "организатору розыгрыша"


# Все хендлеры этого роутера — приватная админ-панель. Владельцы каналов,
# создавшие розыгрыш через /new_lot, доступа сюда не имеют: проверка ниже
# стоит первой строкой в каждом хендлере. На команды посторонних отвечаем
# молча (ничего не отправляем) — это специально, чтобы не выдавать даже
# сам факт существования админ-функций.

@router.message(Command("giveaways"))
async def all_giveaways(message: Message):
    if not is_admin(message.from_user.id):
        return
    giveaways = await db.list_all_giveaways()
    if not giveaways:
        await message.answer("Розыгрышей пока нет.")
        return
    await message.answer("Все розыгрыши (по всем каналам):", reply_markup=giveaways_list_kb(giveaways))


@router.message(Command("stats"))
async def channel_stats(message: Message):
    """Сводка по всем каналам сразу: сколько людей нажали 'Участвовать' в каждом."""
    if not is_admin(message.from_user.id):
        return
    rows = await db.channel_stats_all()
    if not rows:
        await message.answer("Пока нет данных.")
        return

    lines = ["📊 <b>Статистика по каналам</b>\n"]
    for r in rows:
        lines.append(f"• {esc(r['channel_title'])} — {r['participants']} участник(ов)")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data == "gv_back")
async def gv_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    giveaways = await db.list_all_giveaways()
    await callback.message.edit_text("Все розыгрыши (по всем каналам):", reply_markup=giveaways_list_kb(giveaways))
    await callback.answer()


@router.callback_query(F.data.startswith("gv:"))
async def gv_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    giveaway_id = int(callback.data.split(":")[1])
    giveaway = await db.get_giveaway(giveaway_id)
    if not giveaway:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    count = await db.count_participants(giveaway_id)
    owner_username = await db.get_username_by_owner(giveaway["owner_id"])
    owner_label = f"@{owner_username}" if owner_username else f"id {giveaway['owner_id']}"
    status_ru = {"draft": "черновик", "published": "идёт", "finished": "завершён"}.get(giveaway["status"], giveaway["status"])
    text = (
        f"🎁 Розыгрыш #{giveaway_id}\n"
        f"Канал: {esc(giveaway['channel_title'])}\n"
        f"Создал (админ канала): {owner_label}\n"
        f"Статус: {status_ru}\n"
        f"Участников: {count}\n"
        f"Победителей должно быть: {giveaway['winners_count']}\n"
        f"Итоги: {giveaway['draw_datetime']} (МСК)"
    )
    await callback.message.edit_text(text, reply_markup=giveaway_detail_kb(giveaway_id, giveaway["status"]))
    await callback.answer()


@router.callback_query(F.data.startswith("gv_participants:"))
async def gv_participants(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    giveaway_id = int(callback.data.split(":")[1])
    giveaway = await db.get_giveaway(giveaway_id)
    participants = await db.list_participants(giveaway_id)

    if not participants:
        await callback.answer("Пока никто не участвует", show_alert=True)
        return

    # Список отправляем ФАЙЛОМ, а не сообщением — у Telegram жёсткий лимит
    # на длину сообщения (~4096 символов), и список участников легко его
    # превышает уже на сотне человек. Файл снимает это ограничение полностью.
    lines = [f"Участники розыгрыша #{giveaway_id} — {giveaway['channel_title']} ({len(participants)}):", ""]
    for p in participants:
        uname = f"@{p['username']}" if p["username"] else "(без username)"
        lines.append(f"{uname} — id {p['user_id']}")

    file_content = "\n".join(lines).encode("utf-8")
    document = BufferedInputFile(file_content, filename=f"participants_giveaway_{giveaway_id}.txt")

    await callback.message.answer_document(
        document=document,
        caption=f"👥 Все участники розыгрыша #{giveaway_id} — {esc(giveaway['channel_title'])} ({len(participants)})",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gv_notify_all:"))
async def gv_notify_all(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    giveaway_id = int(callback.data.split(":")[1])
    giveaway = await db.get_giveaway(giveaway_id)
    if not giveaway:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return

    participants = await db.list_participants(giveaway_id)
    if not participants:
        await callback.answer("Участников пока нет", show_alert=True)
        return

    await callback.answer(
        f"Начинаю рассылку {len(participants)} участникам — это может занять время…",
        show_alert=True,
    )

    text = (
        f"<tg-emoji emoji-id=\"5461151367559141950\">🎉</tg-emoji><b>Поздравляем с победой, вы выиграли PlayStation 5!</b>\n"
        f"<tg-emoji emoji-id=\"5440539497383087970\">🥇</tg-emoji><b>Вы заняли 1-е место в розыгрыше в канале «{giveaway['channel_title']}» от GGSel — ваш приз уже ждёт вас!</b>\n\n"
        f"<tg-emoji emoji-id=\"5253742260054409879\">✉️</tg-emoji><b>Чтобы получить награду - напишите менеджеру GGSel: @ggsellevents</b>\n"
        f"<tg-emoji emoji-id=\"5416117059207572332\">➡️</tg-emoji><b>Менеджер быстро всё проверит и поможет с получением приза.</b>"
    )

    success = 0
    failed = []
    for p in participants:
        try:
            await bot.send_message(p["user_id"], text)
            success += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed.append(p)
        await asyncio.sleep(0.05)  # не упереться в лимиты Telegram при массовой рассылке

    summary_lines = [
        f"📣 Рассылка по розыгрышу #{giveaway_id} — {esc(giveaway['channel_title'])} завершена.",
        f"Всего участников: {len(participants)}",
        f"✅ Доставлено: {success}",
        f"❌ Не доставлено: {len(failed)}",
    ]
    await callback.message.answer("\n".join(summary_lines))

    if failed:
        fail_lines = [f"@{p['username']}" if p["username"] else str(p["user_id"]) for p in failed]
        file_content = "\n".join(fail_lines).encode("utf-8")
        document = BufferedInputFile(file_content, filename=f"failed_notify_giveaway_{giveaway_id}.txt")
        await callback.message.answer_document(
            document=document,
            caption="Кому не удалось доставить (заблокировали бота или ещё не запускали его)",
        )


@router.callback_query(F.data.startswith("gv_pick:"))
async def gv_pick_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    giveaway_id = int(callback.data.split(":")[1])
    await state.set_state(WinnerFlowStates.waiting_user_input)
    await state.update_data(giveaway_id=giveaway_id)
    await callback.message.answer(
        "Введите @username или id пользователя-победителя (должен быть среди участников этого розыгрыша):"
    )
    await callback.answer()


@router.message(WinnerFlowStates.waiting_user_input, F.text)
async def gv_pick_finish(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    giveaway_id = data["giveaway_id"]
    giveaway = await db.get_giveaway(giveaway_id)

    participant = await db.find_participant(giveaway_id, message.text)
    if not participant:
        await message.answer(
            "Такой участник не найден среди зарегистрированных в этом розыгрыше. Проверьте id/username и попробуйте снова."
        )
        return

    # За призом победитель пишет менеджеру, указанному в PRIZE_CONTACT_USERNAME
    # (или, если контакт не задан в конфиге, тому, кто сейчас выбрал победителя).
    contact = prize_contact(message.from_user.username)

    win_text = (
        f"🎉 Поздравляем! Вы выиграли в розыгрыше в канале «{esc(giveaway['channel_title'])}»!\n\n"
        f"Чтобы получить приз, напишите: {contact}"
    )

    try:
        await bot.send_message(participant["user_id"], win_text)
    except (TelegramForbiddenError, TelegramBadRequest):
        await message.answer(
            "⚠️ Не удалось отправить сообщение победителю — пользователь заблокировал бота "
            "или ещё не запускал его. Победитель зафиксирован, но уведомление не доставлено."
        )
        await db.add_winner(giveaway_id, participant["user_id"], participant["username"])
        await state.clear()
        return

    await db.add_winner(giveaway_id, participant["user_id"], participant["username"])
    await state.clear()
    uname = f"@{participant['username']}" if participant["username"] else participant["user_id"]
    await message.answer(f"Готово! Победитель {uname} уведомлён ✅")


@router.callback_query(F.data.startswith("gv_winners:"))
async def gv_winners(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    giveaway_id = int(callback.data.split(":")[1])
    winners = await db.list_winners(giveaway_id)
    if not winners:
        await callback.answer("Победители ещё не выбраны", show_alert=True)
        return

    lines = ["🏆 Победители:\n"]
    for w in winners:
        uname = f"@{w['username']}" if w["username"] else w["user_id"]
        reminded = " · напоминание отправлено" if w["reminder_sent_at"] else ""
        lines.append(f"• {uname}{reminded}")

    await callback.message.answer("\n".join(lines), reply_markup=winners_list_kb(giveaway_id, winners))
    await callback.answer()


@router.callback_query(F.data.startswith("gv_remind:"))
async def gv_remind(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    _, giveaway_id, user_id = callback.data.split(":")
    giveaway_id, user_id = int(giveaway_id), int(user_id)
    giveaway = await db.get_giveaway(giveaway_id)

    admin_username = callback.from_user.username
    contact = prize_contact(admin_username)

    reminder_text = (
        f"⏰ Напоминаем: у вас остаётся мало времени, чтобы забрать приз "
        f"в розыгрыше «{esc(giveaway['channel_title'])}»!\n\nСвяжитесь с {contact} как можно скорее."
    )

    try:
        await bot.send_message(user_id, reminder_text)
        await db.mark_reminder_sent(giveaway_id, user_id)
        await callback.answer("Напоминание отправлено ✅", show_alert=True)
    except (TelegramForbiddenError, TelegramBadRequest):
        await callback.answer("Не удалось отправить — пользователь заблокировал бота", show_alert=True)


# ---------- полная очистка данных ----------

@router.message(Command("reset_all"))
async def reset_all_start(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "⚠️ Вы точно хотите удалить ВСЕ данные — все каналы, розыгрыши, участников, "
        "победителей и пользователей? Это действие необратимо.",
        reply_markup=reset_confirm_kb(),
    )


@router.callback_query(F.data == "reset_confirm")
async def reset_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await db.clear_all()
    await state.clear()
    await callback.message.edit_text("✅ Все данные удалены. База пустая, можно начинать заново.")
    await callback.answer()


@router.callback_query(F.data == "reset_cancel")
async def reset_cancel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.message.edit_text("Отменено, данные не тронуты.")
    await callback.answer()

from aiogram.fsm.state import State, StatesGroup


class NewLotStates(StatesGroup):
    waiting_forward = State()           # ждём пересланный опубликованный пост из канала
    waiting_button_text_custom = State()  # если выбрали "свой вариант" текста кнопки
    waiting_winners_count = State()     # ждём число победителей
    waiting_datetime = State()          # ждём дату и время подведения итогов
    confirm = State()                   # финальное подтверждение и публикация


class WinnerFlowStates(StatesGroup):
    waiting_user_input = State()   # ждём @username / id для выбора победителя

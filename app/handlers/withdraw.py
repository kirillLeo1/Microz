from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from ..services.tasks_service import get_user
from ..utils.i18n import i18n
from ..db import fetchrow
from ..config import settings
router = Router()

# --- СТАН ---
class WState:
    stage = {}  # user_id -> 'country' | 'method' | 'details' | 'amount'
    data = {}   # user_id -> {'country':..., 'method':..., 'details':..., 'amount_qc':...}

async def notify_admins_withdrawal(bot, user_row, wd_row, username: str | None):
    """
    Шле адмінам повідомлення про нову заявку.
    user_row — рядок з таблиці users (ти вже маєш user у хендлерах),
    wd_row — рядок з таблиці withdrawals, повернутий RETURNING.
    """
    tg_id = user_row["tg_id"]
    uname = (username or "").lstrip("@")
    usd = wd_row["amount_qc"] * 0.005  # 1 QC = $0.005

    title = "💸 Нова заявка на вивід"  # або "Новая заявка на вывод"
    # посилання працює навіть без username
    contact = f"<a href='tg://user?id={tg_id}'>#{tg_id}</a>"
    if uname:
        contact += f" (@{uname})"

    text = (
        f"{title}\n\n"
        f"ID заявки: <code>{wd_row['id']}</code>\n"
        f"Користувач: {contact}\n"
        f"Сума: <b>{wd_row['amount_qc']} QC</b> (~${usd:.2f})\n"
        f"Країна: {wd_row['country']}\n"
        f"Спосіб: {wd_row['method']}\n"
        f"Реквізити: {wd_row['details']}\n"
        f"Статус: pending"
    )

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            # ігноруємо одиничні фейли відправки, щоб не валити потік
            pass

def reset(uid: int):
    WState.stage.pop(uid, None)
    WState.data.pop(uid, None)

def kb_methods(lang: str):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=i18n.t(lang, "withdraw_crypto"))],
            [KeyboardButton(text=i18n.t(lang, "withdraw_card"))],
            [KeyboardButton(text=i18n.t(lang, "withdraw_other"))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

# --- СТАРТ МАЙСТРА ---
@router.message(F.text.in_({"💸 Вивід коштів", "💸 Вывод средств", "💸 Withdraw"}))
async def withdraw_entry(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = user["language"]

    if user["balance_qc"] < 1000:
        await msg.answer(i18n.t(lang, "withdraw_min"))
        return

    reset(msg.from_user.id)
    WState.stage[msg.from_user.id] = "country"
    WState.data[msg.from_user.id] = {}

    await msg.answer(i18n.t(lang, "withdraw_start"))

# --- КРАЇНА ---
@router.message(lambda m: WState.stage.get(m.from_user.id) == "country")
async def w_country(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = user["language"]

    WState.data[msg.from_user.id]["country"] = msg.text.strip()
    WState.stage[msg.from_user.id] = "method"

    await msg.answer(i18n.t(lang, "withdraw_method"), reply_markup=kb_methods(lang))

# --- МЕТОД ---
@router.message(lambda m: WState.stage.get(m.from_user.id) == "method")
async def w_method(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = user["language"]

    # приймаємо будь-що, але зазвичай це одна з кнопок:
    WState.data[msg.from_user.id]["method"] = msg.text.strip()
    WState.stage[msg.from_user.id] = "details"

    # прибираємо клаву, щоб не дублювалась
    await msg.answer(i18n.t(lang, "withdraw_details"), reply_markup=ReplyKeyboardRemove())

# --- РЕКВІЗИТИ ---
@router.message(lambda m: WState.stage.get(m.from_user.id) == "details")
async def w_details(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = user["language"]

    WState.data[msg.from_user.id]["details"] = msg.text.strip()
    WState.stage[msg.from_user.id] = "amount"

    await msg.answer(i18n.t(lang, "withdraw_amount"))

# --- СУМА ---
@router.message(lambda m: WState.stage.get(m.from_user.id) == "amount")
async def w_amount(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = user["language"]

    try:
        val = int(msg.text.strip())
    except Exception:
        await msg.answer("Enter integer.")
        return

    if val == 0:
        val = user["balance_qc"]
    if val > user["balance_qc"]:
        await msg.answer("Too much.")
        return

    WState.data[msg.from_user.id]["amount_qc"] = val
    d = WState.data[msg.from_user.id]

    confirm = i18n.t(
        lang,
        "withdraw_confirm",
        qc=val,
        country=d["country"],
        method=d["method"],
        details=d["details"],
    )
    await msg.answer(confirm)

    # зберігаємо заявку
    wd_row = await fetchrow(
        """
        INSERT INTO withdrawals (user_id, amount_qc, country, method, details)
        VALUES ((SELECT id FROM users WHERE tg_id=$1), $2, $3, $4, $5)
        RETURNING id, amount_qc, country, method, details, status, created_at
        """,
        msg.from_user.id,
        val,
        d["country"],
        d["method"],
        d["details"],
    )

    # повідомляємо адмінів (без кнопок, просто повідомлення)
    await notify_admins_withdrawal(
        bot=msg.bot,
        user_row=user,
        wd_row=wd_row,
        username=msg.from_user.username,
    )

    await msg.answer(i18n.t(lang, 'withdraw_saved'), reply_markup=ReplyKeyboardRemove())
    reset(msg.from_user.id)


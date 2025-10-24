from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from ..services.tasks_service import get_user
from ..utils.i18n import i18n
from ..db import fetchrow, execute

router = Router()

class WState:
    country = {}
    method = {}
    details = {}
    amount = {}

def kb_methods(lang):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=i18n.t(lang,"withdraw_crypto"))],
                  [KeyboardButton(text=i18n.t(lang,"withdraw_card"))],
                  [KeyboardButton(text=i18n.t(lang,"withdraw_other"))]],
        resize_keyboard=True, one_time_keyboard=True
    )

@router.message(F.text.in_({"💸 Вивід коштів","💸 Вывод средств","💸 Withdraw"}))
async def withdraw_entry(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = user["language"]
    if user["balance_qc"] < 1000:
        await msg.answer(i18n.t(lang,"withdraw_min"))
        return
    await msg.answer(i18n.t(lang,"withdraw_start"))
    WState.country[msg.from_user.id] = True

@router.message(lambda m: WState.country.get(m.from_user.id, False))
async def w_country(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = user["language"]
    WState.country[msg.from_user.id] = msg.text.strip()
    await msg.answer(i18n.t(lang,"withdraw_method"), reply_markup=kb_methods(lang))

@router.message(lambda m: WState.country.get(m.from_user.id) and not WState.method.get(m.from_user.id))
async def w_method(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = user["language"]
    WState.method[msg.from_user.id] = msg.text.strip()
    await msg.answer(i18n.t(lang,"withdraw_details"))

@router.message(lambda m: WState.method.get(m.from_user.id) and not WState.details.get(m.from_user.id))
async def w_details(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = user["language"]
    WState.details[msg.from_user.id] = msg.text.strip()
    await msg.answer(i18n.t(lang,"withdraw_amount"))

@router.message(lambda m: WState.details.get(m.from_user.id) and not WState.amount.get(m.from_user.id))
async def w_amount(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = user["language"]
    try:
        val = int(msg.text.strip())
    except:
        await msg.answer("Enter integer.")
        return
    if val == 0:
        val = user["balance_qc"]
    if val > user["balance_qc"]:
        await msg.answer("Too much.")
        return
    WState.amount[msg.from_user.id] = val
    text = i18n.t(lang,"withdraw_confirm", qc=val, country=WState.country[msg.from_user.id],
                  method=WState.method[msg.from_user.id], details=WState.details[msg.from_user.id])
    await msg.answer(text)
    # Save
    await fetchrow("""
        INSERT INTO withdrawals (user_id, amount_qc, country, method, details)
        VALUES ((SELECT id FROM users WHERE tg_id=$1), $2, $3, $4, $5)
    """, msg.from_user.id, val, WState.country[msg.from_user.id], WState.method[msg.from_user.id], WState.details[msg.from_user.id])
    await msg.answer(i18n.t(lang,"withdraw_saved"))
    # cleanup
    for d in (WState.country, WState.method, WState.details, WState.amount):
        d.pop(msg.from_user.id, None)

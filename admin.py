from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from ..config import settings
from ..utils.i18n import i18n
from ..utils.keyboards import admin_menu_kb
from ..db import fetch, fetchrow, execute
from ..services.tasks_service import get_or_create_chain

router = Router()

def is_admin(uid: int) -> bool:
    return uid in settings.ADMIN_IDS

@router.message(Command("admin"))
async def admin_entry(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer(i18n.t("en","admin_only"))
        return
    lang = (await fetchrow("SELECT language FROM users WHERE tg_id=$1", msg.from_user.id))["language"] or "en"
    await msg.answer(i18n.t(lang,"admin_menu"), reply_markup=admin_menu_kb(i18n._texts[lang]))

@router.callback_query(F.data.startswith("admin:"))
async def admin_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Nope")
        return
    lang = (await fetchrow("SELECT language FROM users WHERE tg_id=$1", cb.from_user.id))["language"] or "en"
    key = cb.data.split(":")[1]
    if key=="stats":
        users = await fetchrow("SELECT COUNT(*) c FROM users")
        active = await fetchrow("SELECT COUNT(*) c FROM users WHERE status='active'")
        sum_balance = await fetchrow("SELECT COALESCE(SUM(balance_qc),0) s FROM users")
        sum_earned = await fetchrow("SELECT COALESCE(SUM(earned_total_qc),0) s FROM users")
        payments = await fetchrow("SELECT COUNT(*) c FROM payments")
        refs = await fetchrow("SELECT COUNT(*) c FROM referral_rewards")
        await cb.message.edit_text(i18n.t(lang,"stats_text", users=users["c"], active=active["c"],
                                           sum_balance=sum_balance["s"], sum_earned=sum_earned["s"],
                                           payments=payments["c"], refs=refs["c"]))
    elif key=="tasks":
        # list chains
        chains = await fetch("SELECT * FROM chains ORDER BY id")
        text = i18n.t(lang,"chains_list") + "\n" + "\n".join([f"- {r['key']} (id={r['id']})" for r in chains])
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        for r in chains:
            kb.row(
                *( __import__('aiogram.types').types.InlineKeyboardButton(text=i18n.t(lang,"manage_chain", key=r["key"]), callback_data=f"chain:{r['id']}"), )
            )
        kb.row(__import__('aiogram.types').types.InlineKeyboardButton(text=i18n.t(lang,"new_chain"), callback_data="chain:new"))
        kb.row(__import__('aiogram.types').types.InlineKeyboardButton(text=i18n.t(lang,"back"), callback_data="admin:menu"))
        await cb.message.edit_text(text, reply_markup=kb.as_markup())
    elif key=="broadcast":
        await cb.message.edit_text(i18n.t(lang,"broadcast_enter"))
        router.broadcast_wait[cb.from_user.id] = True
    elif key=="withdraws":
        rows = await fetch("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id")
        if not rows:
            await cb.message.edit_text(i18n.t(lang,"withdraw_list") + " (0)")
            return
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        parts = []
        for r in rows:
            parts.append(i18n.t(lang,"withdraw_card_admin", id=r["id"], user_id=r["user_id"], qc=r["amount_qc"],
                                country=r["country"], method=r["method"], details=r["details"]))
            kb.row(__import__('aiogram.types').types.InlineKeyboardButton(text=f"#{r['id']}", callback_data=f"w:{r['id']}"))
        await cb.message.edit_text("\n\n".join(parts), reply_markup=kb.as_markup())
    elif key=="menu":
        await cb.message.edit_text(i18n.t(lang,"admin_menu"), reply_markup=admin_menu_kb(i18n._texts[lang]))

router.broadcast_wait = {}

@router.message(F.text.regexp(r".+"), lambda m: router.broadcast_wait.get(m.from_user.id))
async def broadcast_confirm(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    lang = (await fetchrow("SELECT language FROM users WHERE tg_id=$1", msg.from_user.id))["language"] or "en"
    text = msg.html_text or msg.text
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    total = await fetchrow("SELECT COUNT(*) c FROM users")
    kb.row(__import__('aiogram.types').types.InlineKeyboardButton(text=i18n.t(lang,"broadcast_confirm", count=total["c"]), callback_data="send_bc"))
    kb.row(__import__('aiogram.types').types.InlineKeyboardButton(text=i18n.t(lang,"back"), callback_data="admin:menu"))
    router.broadcast_text[msg.from_user.id] = text
    await msg.answer(text, reply_markup=kb.as_markup())

router.broadcast_text = {}

@router.callback_query(F.data=="send_bc")
async def do_broadcast(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Nope")
        return
    text = router.broadcast_text.pop(cb.from_user.id, None)
    router.broadcast_wait.pop(cb.from_user.id, None)
    if not text:
        await cb.answer("No text")
        return
    users = await fetch("SELECT tg_id FROM users ORDER BY id")
    ok=bad=0
    for r in users:
        try:
            await cb.bot.send_message(r["tg_id"], text)
            ok+=1
        except Exception:
            bad+=1
        await __import__('asyncio').sleep(0.05)
    lang = (await fetchrow("SELECT language FROM users WHERE tg_id=$1", cb.from_user.id))["language"] or "en"
    await cb.message.edit_text(i18n.t(lang,"broadcast_done", ok=ok, bad=bad))

@router.callback_query(F.data.startswith("chain:"))
async def chain_screen(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    lang = (await fetchrow("SELECT language FROM users WHERE tg_id=$1", cb.from_user.id))["language"] or "en"
    _, cid = cb.data.split(":")
    if cid=="new":
        # ask for key
        router.new_chain_wait[cb.from_user.id]="key"
        await cb.message.edit_text("Enter chain key (latin, unique):")
        return
    cid = int(cid)
    rows = await fetch("SELECT * FROM steps WHERE chain_id=$1 ORDER BY order_no", cid)
    text = i18n.t(lang,"chain_screen", key=cid, count=len(rows)) + "\n" + "\n".join([f"#{r['order_no']} (+{r['reward_qc']} QC) id={r['id']}\n{r['url']}" for r in rows])
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.row(__import__('aiogram.types').types.InlineKeyboardButton(text=i18n.t(lang,"add_step"), callback_data=f"step:add:{cid}"))
    kb.row(__import__('aiogram.types').types.InlineKeyboardButton(text=i18n.t(lang,"del_last"), callback_data=f"step:del_last:{cid}"))
    kb.row(__import__('aiogram.types').types.InlineKeyboardButton(text=i18n.t(lang,"toggle_all"), callback_data=f"step:toggle:{cid}"))
    kb.row(__import__('aiogram.types').types.InlineKeyboardButton(text=i18n.t(lang,"wipe_chain"), callback_data=f"step:wipe:{cid}"))
    kb.row(__import__('aiogram.types').types.InlineKeyboardButton(text=i18n.t(lang,"back"), callback_data="admin:tasks"))
    await cb.message.edit_text(text, reply_markup=kb.as_markup())

router.new_chain_wait = {}
router.step_create_state = {}

@router.message(lambda m: router.new_chain_wait.get(m.from_user.id)=="key")
async def new_chain_key(msg: Message):
    key = msg.text.strip()
    row = await get_or_create_chain(key)
    router.new_chain_wait.pop(msg.from_user.id, None)
    await msg.answer(f"Chain '{key}' created (id={row['id']}). Use the admin menu again.")

@router.callback_query(F.data.startswith("step:"))
async def step_ops(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    _, op, cid = cb.data.split(":")
    cid = int(cid)
    lang = (await fetchrow("SELECT language FROM users WHERE tg_id=$1", cb.from_user.id))["language"] or "en"
    if op=="add":
        router.step_create_state[cb.from_user.id] = {"cid": cid, "stage": "desc_uk"}
        await cb.message.edit_text(i18n.t(lang,"ask_desc_uk"))
    elif op=="del_last":
        last = await fetchrow("SELECT id FROM steps WHERE chain_id=$1 ORDER BY order_no DESC LIMIT 1", cid)
        if last:
            await execute("DELETE FROM steps WHERE id=$1", last["id"])
            await cb.message.edit_text(i18n.t(lang,"deleted"))
    elif op=="toggle":
        # flip all
        await execute("UPDATE steps SET is_active = NOT is_active WHERE chain_id=$1", cid)
        await cb.message.edit_text(i18n.t(lang,"toggled"))
    elif op=="wipe":
        await execute("DELETE FROM user_steps WHERE step_id IN (SELECT id FROM steps WHERE chain_id=$1)", cid)
        await execute("DELETE FROM steps WHERE chain_id=$1", cid)
        await cb.message.edit_text(i18n.t(lang,"wiped"))

@router.message(lambda m: isinstance(router.step_create_state.get(m.from_user.id), dict))
async def step_create_flow(msg: Message):
    s = router.step_create_state[msg.from_user.id]
    lang = (await fetchrow("SELECT language FROM users WHERE tg_id=$1", msg.from_user.id))["language"] or "en"
    if s["stage"]=="desc_uk":
        s["desc_uk"]=msg.text
        s["stage"]="desc_ru"
        await msg.answer(i18n.t(lang,"ask_desc_ru"))
        return
    if s["stage"]=="desc_ru":
        s["desc_ru"]=msg.text
        s["stage"]="desc_en"
        await msg.answer(i18n.t(lang,"ask_desc_en"))
        return
    if s["stage"]=="desc_en":
        s["desc_en"]=msg.text
        s["stage"]="title_uk"
        await msg.answer(i18n.t(lang,"ask_title_uk"))
        return
    if s["stage"]=="title_uk":
        s["title_uk"]=msg.text
        s["stage"]="title_ru"
        await msg.answer(i18n.t(lang,"ask_title_ru"))
        return
    if s["stage"]=="title_ru":
        s["title_ru"]=msg.text
        s["stage"]="title_en"
        await msg.answer(i18n.t(lang,"ask_title_en"))
        return
    if s["stage"]=="title_en":
        s["title_en"]=msg.text
        s["stage"]="url"
        await msg.answer(i18n.t(lang,"ask_url"))
        return
    if s["stage"]=="url":
        s["url"]=msg.text.strip()
        s["stage"]="reward"
        await msg.answer(i18n.t(lang,"ask_reward"))
        return
    if s["stage"]=="reward":
        try:
            s["reward_qc"]=int(msg.text.strip())
        except:
            await msg.answer("Enter integer")
            return
        # insert
        cid = s["cid"]
        last = await fetchrow("SELECT COALESCE(MAX(order_no),0) m FROM steps WHERE chain_id=$1", cid)
        order_no = last["m"] + 1
        await fetchrow("""
            INSERT INTO steps (chain_id, order_no, title_uk, title_ru, title_en, desc_uk, desc_ru, desc_en, url, reward_qc)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            RETURNING id
        """, cid, order_no, s["title_uk"], s["title_ru"], s["title_en"], s["desc_uk"], s["desc_ru"], s["desc_en"], s["url"], s["reward_qc"])
        router.step_create_state.pop(msg.from_user.id, None)
        await msg.answer(i18n.t(lang,"step_saved"))

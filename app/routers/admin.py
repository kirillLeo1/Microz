# app/routers/admin.py
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ADMINS_LIST
from ..models import Users, Tasks, Payments, QCWallets, Referrals, Withdrawals

admin_router = Router()

# ─ helpers ─
def _is_admin(uid: int) -> bool: return uid in ADMINS_LIST

async def _require_admin_msg(m: Message) -> bool:
    if not _is_admin(m.from_user.id):
        await m.answer("⛔️ Лише для адмінів.")
        return False
    return True

async def _require_admin_cq(cq: CallbackQuery) -> bool:
    if not _is_admin(cq.from_user.id):
        try: await cq.answer("⛔️ Лише для адмінів", show_alert=True)
        except: pass
        return False
    return True

async def _safe_cq_answer(cq: CallbackQuery, text: str | None = None, alert: bool = False):
    try: await cq.answer(text or "", show_alert=alert)
    except: pass

def _adm_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="adm:stats")
    kb.button(text="🧩 Завдання", callback_data="adm:tasks")
    kb.button(text="📣 Розсилка", callback_data="adm:bcast")
    kb.button(text="💸 Заявки на вивід", callback_data="adm:withdraws")
    kb.adjust(2, 2)
    return kb.as_markup()

# ─ entry ─
@admin_router.message(Command("admin"))
async def admin_entry(m: Message):
    if not await _require_admin_msg(m): return
    await m.answer("Адмін-меню:", reply_markup=_adm_menu_kb())

@admin_router.callback_query(F.data == "adm:back")
async def adm_back(cq: CallbackQuery):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq)
    try: await cq.message.edit_text("Адмін-меню:", reply_markup=_adm_menu_kb())
    except: await cq.message.answer("Адмін-меню:", reply_markup=_adm_menu_kb())

# ─ stats ─
@admin_router.callback_query(F.data == "adm:stats")
async def adm_stats(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq)
    total_users   = (await session.execute(select(func.count(Users.id)))).scalar_one()
    active_users  = (await session.execute(select(func.count(Users.id)).where(Users.status=="active"))).scalar_one()
    total_qc      = (await session.execute(select(func.coalesce(func.sum(QCWallets.balance_qc),0)))).scalar_one()
    total_earned  = (await session.execute(select(func.coalesce(func.sum(QCWallets.total_earned_qc),0)))).scalar_one()
    payments_cnt  = (await session.execute(select(func.count(Payments.id)))).scalar_one()
    referrals_cnt = (await session.execute(select(func.count(Referrals.id)))).scalar_one()
    text = (
        "📊 <b>Статистика</b>\n"
        f"Користувачів: <b>{total_users}</b>\n"
        f"Активних: <b>{active_users}</b>\n"
        f"Баланс QC сумарно: <b>{total_qc}</b>\n"
        f"Нараховано QC сумарно: <b>{total_earned}</b>\n"
        f"Платежів: <b>{payments_cnt}</b>\n"
        f"Рефералів: <b>{referrals_cnt}</b>\n"
    )
    kb = InlineKeyboardBuilder(); kb.button(text="⬅️ Назад", callback_data="adm:back")
    try: await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
    except: await cq.message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())

# ─ broadcast ─
class BcastSG(StatesGroup):
    text = State(); confirm = State()

@admin_router.callback_query(F.data == "adm:bcast")
async def adm_bcast_start(cq: CallbackQuery, state: FSMContext):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq); await state.clear(); await state.set_state(BcastSG.text)
    try: await cq.message.edit_text("Відправ текст для розсилки (HTML ок).")
    except: await cq.message.answer("Відправ текст для розсилки (HTML ок).")

@admin_router.message(BcastSG.text)
async def adm_bcast_text(m: Message, state: FSMContext):
    if not await _require_admin_msg(m): return
    await state.update_data(text=m.html_text or m.text or "")
    kb = InlineKeyboardBuilder()
    kb.button(text="🚀 Надіслати", callback_data="bcast:go")
    kb.button(text="❌ Скасувати", callback_data="bcast:cancel")
    kb.adjust(2)
    await state.set_state(BcastSG.confirm)
    await m.answer("Підтвердити розсилку?", reply_markup=kb.as_markup())

@admin_router.callback_query(BcastSG.confirm, F.data == "bcast:cancel")
async def adm_bcast_cancel(cq: CallbackQuery, state: FSMContext):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq, "Скасовано"); await state.clear()
    await cq.message.edit_text("Скасовано.", reply_markup=_adm_menu_kb())

@admin_router.callback_query(BcastSG.confirm, F.data == "bcast:go")
async def adm_bcast_go(cq: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq)
    text = (await state.get_data()).get("text","")
    ids = (await session.execute(select(Users.tg_id))).scalars().all()
    ok=fail=0
    await cq.message.edit_text("⚡️ Розсилка стартувала…")
    for i,uid in enumerate(ids,1):
        try: await cq.bot.send_message(uid, text, parse_mode="HTML")
        except: fail+=1
        else: ok+=1
        await asyncio.sleep(0.08)
        if i%200==0:
            try: await cq.message.edit_text(f"⚡️ Надіслано: {ok} | Помилок: {fail}")
            except: pass
    await state.clear()
    kb=InlineKeyboardBuilder(); kb.button(text="⬅️ Назад", callback_data="adm:back")
    await cq.message.edit_text(f"✅ Готово!\nНадіслано: {ok}\nПомилок: {fail}", reply_markup=kb.as_markup())

# ─ tasks (chains with reward_qc) ─
class ChainCreateSG(StatesGroup):
    desc_uk=State(); desc_ru=State(); desc_en=State(); url=State(); reward=State()

class ChainAddStepSG(StatesGroup):
    chain_key=State(); desc_uk=State(); desc_ru=State(); desc_en=State(); url=State(); reward=State()

def _title_placeholder()->str: return ""

async def _render_chain_view(session: AsyncSession, chain_key: Optional[str]) -> tuple[str, InlineKeyboardMarkup]:
    steps = (await session.execute(
        select(Tasks).where(Tasks.chain_key==chain_key).order_by(Tasks.created_at.asc())
    )).scalars().all()
    lines=[f"Ланцюг [{chain_key or 'SOLO'}]: {len(steps)} крок(ів)\n"]
    for i,t in enumerate(steps,1):
        lines.append(f"#{i} {'✅' if t.is_active else '⛔️'} +{t.reward_qc} QC  url={t.url}  (id={t.id})")
    kb=InlineKeyboardBuilder()
    raw = chain_key if chain_key is not None else "NULL"
    kb.button(text="➕ Додати крок",        callback_data=f"chain:add:{raw}")
    kb.button(text="🗑 Видалити останній",  callback_data=f"chain:del_last:{raw}")
    kb.button(text="⛔️/✅ Toggle всі",      callback_data=f"chain:tgl:{raw}")
    kb.button(text="⬅️ Назад",             callback_data="adm:tasks")
    kb.adjust(1)
    return "\n".join(lines), kb.as_markup()

@admin_router.callback_query(F.data == "adm:tasks")
async def adm_tasks_home(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq)
    rows=(await session.execute(
        select(Tasks.chain_key, func.count(Tasks.id), func.bool_or(Tasks.is_active), func.min(Tasks.created_at))
        .group_by(Tasks.chain_key).order_by(func.min(Tasks.created_at).asc())
    )).all()
    kb=InlineKeyboardBuilder(); lines=["Ланцюги завдань:\n"]
    if not rows:
        kb.button(text="➕ Створити ланцюг", callback_data="chain:create")
        kb.button(text="⬅️ Назад", callback_data="adm:back")
        try: await cq.message.edit_text("Немає завдань. Створити перший ланцюг?", reply_markup=kb.as_markup())
        except: await cq.message.answer("Немає завдань. Створити перший ланцюг?", reply_markup=kb.as_markup())
        return
    for chain_key,cnt,any_active,_ in rows:
        lines.append(f"• [{chain_key or 'SOLO'}] — кроків: {cnt} — {'✅ активні' if any_active else '⛔️ вимкн'}")
        kb.button(text=f"Керувати [{chain_key or 'SOLO'}]", callback_data=f"chain:view:{chain_key or 'NULL'}")
    kb.button(text="➕ Новий ланцюг", callback_data="chain:create")
    kb.button(text="⬅️ Назад", callback_data="adm:back"); kb.adjust(1)
    try: await cq.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    except: await cq.message.answer("\n".join(lines), reply_markup=kb.as_markup())

@admin_router.callback_query(F.data == "chain:create")
async def chain_create_start(cq: CallbackQuery, state: FSMContext):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq); await state.clear(); await state.set_state(ChainCreateSG.desc_uk)
    try: await cq.message.edit_text("Опис (укр) для першого кроку:")
    except: await cq.message.answer("Опис (укр) для першого кроку:")

@admin_router.message(ChainCreateSG.desc_uk)
async def chain_create_desc_uk(m: Message, state: FSMContext):
    if not await _require_admin_msg(m): return
    await state.update_data(desc_uk=m.text.strip()); await state.set_state(ChainCreateSG.desc_ru)
    await m.answer("Описание (рус) первого шага:")

@admin_router.message(ChainCreateSG.desc_ru)
async def chain_create_desc_ru(m: Message, state: FSMContext):
    if not await _require_admin_msg(m): return
    await state.update_data(desc_ru=m.text.strip()); await state.set_state(ChainCreateSG.desc_en)
    await m.answer("Description (en) of the first step:")

@admin_router.message(ChainCreateSG.desc_en)
async def chain_create_desc_en(m: Message, state: FSMContext):
    if not await _require_admin_msg(m): return
    await state.update_data(desc_en=m.text.strip()); await state.set_state(ChainCreateSG.url)
    await m.answer("URL (t.me/... або будь-який):")

@admin_router.message(ChainCreateSG.url)
async def chain_create_url(m: Message, state: FSMContext):
    if not await _require_admin_msg(m): return
    await state.update_data(url=m.text.strip()); await state.set_state(ChainCreateSG.reward)
    await m.answer("Скільки QC за цей крок? (число, за замовчуванням 1)")

@admin_router.message(ChainCreateSG.reward)
async def chain_create_reward(m: Message, state: FSMContext, session: AsyncSession):
    if not await _require_admin_msg(m): return
    data=await state.get_data()
    try: reward=int(m.text.strip())
    except: reward=1
    if reward<=0: reward=1
    chain_key=f"chain:{uuid.uuid4().hex[:12]}"
    session.add(Tasks(
        title_uk="", title_ru="", title_en="",
        desc_uk=data["desc_uk"], desc_ru=data["desc_ru"], desc_en=data["desc_en"],
        url=data["url"], reward_qc=reward, chain_key=chain_key,
        cooldown_sec=1800, is_active=True
    ))
    await state.clear()
    text,markup=await _render_chain_view(session, chain_key)
    await m.answer(f"✅ Створено ланцюг <code>{chain_key}</code>.", parse_mode="HTML")
    await m.answer(text, reply_markup=markup)

@admin_router.callback_query(F.data.startswith("chain:view:"))
async def chain_view(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq)
    raw=cq.data.split(":",2)[2]; chain_key=None if raw=="NULL" else raw
    text,markup=await _render_chain_view(session, chain_key)
    try: await cq.message.edit_text(text, reply_markup=markup)
    except: await cq.message.answer(text, reply_markup=markup)

@admin_router.callback_query(F.data.startswith("chain:add:"))
async def chain_add_step_start(cq: CallbackQuery, state: FSMContext):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq)
    raw=cq.data.split(":",2)[2]; chain_key=None if raw=="NULL" else raw
    await state.clear(); await state.set_state(ChainAddStepSG.desc_uk)
    await state.update_data(chain_key=chain_key)
    try: await cq.message.edit_text(f"Додаємо крок у [{chain_key or 'SOLO'}]\n\nОпис (укр):")
    except: await cq.message.answer(f"Додаємо крок у [{chain_key or 'SOLO'}]\n\nОпис (укр):")

@admin_router.message(ChainAddStepSG.desc_uk)
async def chain_add_step_desc_uk(m: Message, state: FSMContext):
    if not await _require_admin_msg(m): return
    await state.update_data(desc_uk=m.text.strip()); await state.set_state(ChainAddStepSG.desc_ru)
    await m.answer("Описание (рус):")

@admin_router.message(ChainAddStepSG.desc_ru)
async def chain_add_step_desc_ru(m: Message, state: FSMContext):
    if not await _require_admin_msg(m): return
    await state.update_data(desc_ru=m.text.strip()); await state.set_state(ChainAddStepSG.desc_en)
    await m.answer("Description (en):")

@admin_router.message(ChainAddStepSG.desc_en)
async def chain_add_step_desc_en(m: Message, state: FSMContext):
    if not await _require_admin_msg(m): return
    await state.update_data(desc_en=m.text.strip()); await state.set_state(ChainAddStepSG.url)
    await m.answer("URL:")

@admin_router.message(ChainAddStepSG.url)
async def chain_add_step_url(m: Message, state: FSMContext):
    if not await _require_admin_msg(m): return
    await state.update_data(url=m.text.strip()); await state.set_state(ChainAddStepSG.reward)
    await m.answer("Скільки QC за цей крок? (число, за замовчуванням 1)")

@admin_router.message(ChainAddStepSG.reward)
async def chain_add_step_reward(m: Message, state: FSMContext, session: AsyncSession):
    if not await _require_admin_msg(m): return
    data=await state.get_data()
    try: reward=int(m.text.strip())
    except: reward=1
    if reward<=0: reward=1
    session.add(Tasks(
        title_uk="", title_ru="", title_en="",
        desc_uk=data["desc_uk"], desc_ru=data["desc_ru"], desc_en=data["desc_en"],
        url=data["url"], reward_qc=reward, chain_key=data["chain_key"],
        cooldown_sec=1800, is_active=True
    ))
    await state.clear()
    text,markup=await _render_chain_view(session, data["chain_key"])
    await m.answer("✅ Додано крок.")
    await m.answer(text, reply_markup=markup)

@admin_router.callback_query(F.data.startswith("chain:del_last:"))
async def chain_del_last(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq,"Видаляю…")
    raw=cq.data.split(":",2)[2]; chain_key=None if raw=="NULL" else raw
    last=(await session.execute(
        select(Tasks).where(Tasks.chain_key==chain_key).order_by(Tasks.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not last:
        await _safe_cq_answer(cq,"Немає що видаляти",True); return
    await session.execute(delete(Tasks).where(Tasks.id==last.id))
    text,markup=await _render_chain_view(session, chain_key)
    try: await cq.message.edit_text("🗑 Видалено останній крок.\n\n"+text, reply_markup=markup)
    except: await cq.message.answer("🗑 Видалено останній крок.\n\n"+text, reply_markup=markup)

@admin_router.callback_query(F.data.startswith("chain:tgl:"))
async def chain_toggle_all(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq,"Оновлюю…")
    raw=cq.data.split(":",2)[2]; chain_key=None if raw=="NULL" else raw
    any_active=(await session.execute(
        select(func.bool_or(Tasks.is_active)).where(Tasks.chain_key==chain_key)
    )).scalar()
    new_state=not bool(any_active)
    await session.execute(update(Tasks).where(Tasks.chain_key==chain_key).values(is_active=new_state))
    text,markup=await _render_chain_view(session, chain_key)
    try: await cq.message.edit_text(text, reply_markup=markup)
    except:
        try: await cq.message.edit_reply_markup(reply_markup=markup)
        except: pass

# ─ withdraws ─ (без змін логіки списання)
class WdSG(StatesGroup): selected_id=State()

def _wd_list_kb(items: list[Withdrawals])->InlineKeyboardMarkup:
    kb=InlineKeyboardBuilder()
    for w in items:
        kb.button(text=f"#{w.id} • {w.amount_qc} QC • {w.status}", callback_data=f"wd:view:{w.id}")
    kb.button(text="⬅️ Назад", callback_data="adm:back"); kb.adjust(1)
    return kb.as_markup()

@admin_router.callback_query(F.data == "adm:withdraws")
async def adm_withdraws(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq)
    items=(await session.execute(
        select(Withdrawals).where(Withdrawals.status=="pending").order_by(Withdrawals.created_at.asc())
    )).scalars().all()
    if not items:
        kb=InlineKeyboardBuilder(); kb.button(text="⬅️ Назад", callback_data="adm:back")
        try: await cq.message.edit_text("Немає pending заявок.", reply_markup=kb.as_markup())
        except: await cq.message.answer("Немає pending заявок.", reply_markup=kb.as_markup())
        return
    try: await cq.message.edit_text("Pending заявки:", reply_markup=_wd_list_kb(items))
    except: await cq.message.answer("Pending заявки:", reply_markup=_wd_list_kb(items))

@admin_router.callback_query(F.data.startswith("wd:view:"))
async def wd_view(cq: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq)
    wid=int(cq.data.split(":",2)[2])
    w=(await session.execute(select(Withdrawals).where(Withdrawals.id==wid))).scalar_one_or_none()
    if not w: await _safe_cq_answer(cq,"Не знайдено",True); return
    user=(await session.execute(select(Users).where(Users.id==w.user_id))).scalar_one()
    kb=InlineKeyboardBuilder()
    kb.button(text="✉️ Написати", url=f"tg://user?id={user.tg_id}")
    kb.button(text="✅ Позначити як виплачено", callback_data=f"wd:paid:{w.id}")
    kb.button(text="⏳ Позначити як оброблено", callback_data=f"wd:processed:{w.id}")
    kb.button(text="⬅️ До списку", callback_data="adm:withdraws"); kb.adjust(1)
    text=(f"<b>Заявка #{w.id}</b>\n"
          f"User: <code>{user.tg_id}</code>\n"
          f"Сума: <b>{w.amount_qc} QC</b>\n"
          f"Країна: {w.country}\nМетод: {w.method}\nРеквізити: {w.details or '-'}\n"
          f"Статус: <b>{w.status}</b>\n")
    try: await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
    except: await cq.message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())

@admin_router.callback_query(F.data.startswith("wd:processed:"))
async def wd_mark_processed(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq,"Ок")
    wid=int(cq.data.split(":",2)[2])
    await session.execute(update(Withdrawals).where(Withdrawals.id==wid).values(status="processed"))
    cq.data=f"wd:view:{wid}"; await wd_view(cq, session, state=None)  # type: ignore

@admin_router.callback_query(F.data.startswith("wd:paid:"))
async def wd_mark_paid(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq): return
    await _safe_cq_answer(cq,"Підтверджено")
    wid=int(cq.data.split(":",2)[2])
    w=(await session.execute(select(Withdrawals).where(Withdrawals.id==wid))).scalar_one_or_none()
    if not w: await _safe_cq_answer(cq,"Не знайдено",True); return
    await session.execute(
        update(QCWallets).where(QCWallets.user_id==w.user_id).values(
            balance_qc=func.greatest(QCWallets.balance_qc - w.amount_qc, 0)
        )
    )
    await session.execute(update(Withdrawals).where(Withdrawals.id==wid).values(status="paid"))
    cq.data=f"wd:view:{wid}"; await wd_view(cq, session, state=None)  # type: ignore


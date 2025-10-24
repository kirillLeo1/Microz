# app/routers/admin.py
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from sqlalchemy import select, func, delete, update, exists
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ADMINS_LIST
from ..utils import now_utc
from ..models import (
    Users,
    Tasks,
    UserTasks,
    Payments,
    QCWallets,
    Withdrawals,
    TaskStatus,
)
from ..services.tasks import ensure_wallet

admin_router = Router()

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

PAGE_SIZE = 10


def _is_admin(user_id: int) -> bool:
    return user_id in ADMINS_LIST


async def _require_admin_msg(message: Message) -> bool:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔️ Доступ тільки для адмінів.")
        return False
    return True


async def _require_admin_cq(cq: CallbackQuery) -> bool:
    if not _is_admin(cq.from_user.id):
        await cq.answer("⛔️ Тільки для адмінів.", show_alert=True)
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Admin menu
# ─────────────────────────────────────────────────────────────────────────────

@admin_router.message(Command("admin"))
async def admin_entry(message: Message):
    if not await _require_admin_msg(message):
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="adm:stats")
    kb.button(text="🧩 Нове завдання", callback_data="adm:task_new")
    kb.button(text="📋 Список завдань", callback_data="adm:task_list:0")
    kb.button(text="📣 Розсилка", callback_data="adm:broadcast")
    kb.button(text="💸 Заявки на вивід", callback_data="adm:withdraws:0")
    kb.adjust(1)

    await message.answer("Адмін-меню:", reply_markup=kb.as_markup())


# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────

@admin_router.callback_query(F.data == "adm:stats")
async def adm_stats(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq):
        return

    total = (await session.execute(select(func.count()).select_from(Users))).scalar()
    active = (await session.execute(select(func.count()).select_from(Users).where(Users.status == "active"))).scalar()
    total_qc = (await session.execute(select(func.coalesce(func.sum(QCWallets.balance_qc), 0)))).scalar() or 0
    pays = (await session.execute(select(func.count()).select_from(Payments))).scalar()

    await cq.message.edit_text(
        f"👥 Users: {total}\n"
        f"✅ Active: {active}\n"
        f"💰 QC total: {total_qc}\n"
        f"💳 Payments: {pays}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast
# ─────────────────────────────────────────────────────────────────────────────

class BroadcastSG(StatesGroup):
    text = State()


@admin_router.callback_query(F.data == "adm:broadcast")
async def adm_broadcast_start(cq: CallbackQuery, state: FSMContext):
    if not await _require_admin_cq(cq):
        return
    await state.set_state(BroadcastSG.text)
    await cq.message.edit_text("Введіть текст розсилки (HTML дозволено)")


@admin_router.message(BroadcastSG.text)
async def adm_broadcast_send(m: Message, state: FSMContext, session: AsyncSession):
    if not await _require_admin_msg(m):
        return

    text = m.html_text or m.text
    ids = (await session.execute(select(Users.tg_id))).scalars().all()
    ok = 0

    for uid in ids:
        try:
            await m.bot.send_message(uid, text)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.075)  # 75 мс антифлуд

    await state.clear()
    await m.answer(f"Розсилка завершена. Доставлено {ok}/{len(ids)}")


# ─────────────────────────────────────────────────────────────────────────────
# Tasks (chains-first UX)
# ─────────────────────────────────────────────────────────────────────────────
class ChainAddStepSG(StatesGroup):
    chain_key = State()
    desc_uk = State()
    desc_ru = State()
    desc_en = State()
    url = State()

class ChainCreateSG(StatesGroup):
    desc_uk = State()
    desc_ru = State()
    desc_en = State()
    url = State()

def _title_placeholder() -> str:
    return ""

@admin_router.callback_query(F.data == "adm:task_new")  # залишаємо старий entry як alias
@admin_router.callback_query(F.data == "adm:tasks")
async def adm_tasks_home(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq):
        return

    # групуємо за chain_key; соло-кей = NULL вважаємо окремими ланцюгами (по одному кроку)
    rows = (
        await session.execute(
            select(Tasks.chain_key, func.count(Tasks.id), func.bool_or(Tasks.is_active))
            .group_by(Tasks.chain_key)
            .order_by(func.min(Tasks.created_at).asc())
        )
    ).all()

    if not rows:
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Створити ланцюг", callback_data="chain:create")
        await cq.message.edit_text("Немає завдань. Створити перший ланцюг?", reply_markup=kb.as_markup())
        return

    lines = ["Ланцюги завдань:\n"]
    kb = InlineKeyboardBuilder()
    for chain_key, cnt, any_active in rows:
        ck = chain_key or f"solo:{uuid.uuid4().hex[:6]}"
        lines.append(f"• {chain_key or 'SOLO'} — кроків: {cnt} — {'✅ активні' if any_active else '⛔️ вимкн'}")
        kb.button(text=f"Керуати [{chain_key or 'SOLO'}]", callback_data=f"chain:view:{chain_key or 'NULL'}")
    kb.button(text="➕ Новий ланцюг", callback_data="chain:create")
    kb.adjust(1)
    await cq.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())

@admin_router.callback_query(F.data == "chain:create")
async def chain_create_start(cq: CallbackQuery, state: FSMContext):
    if not await _require_admin_cq(cq):
        return
    await state.set_state(ChainCreateSG.desc_uk)
    await cq.message.edit_text("Опис (укр) для першого кроку:")

@admin_router.message(ChainCreateSG.desc_uk)
async def chain_create_desc_uk(m: Message, state: FSMContext):
    await state.update_data(desc_uk=m.text.strip())
    await state.set_state(ChainCreateSG.desc_ru)
    await m.answer("Описание (рус) первого шага:")

@admin_router.message(ChainCreateSG.desc_ru)
async def chain_create_desc_ru(m: Message, state: FSMContext):
    await state.update_data(desc_ru=m.text.strip())
    await state.set_state(ChainCreateSG.desc_en)
    await m.answer("Description (en) of the first step:")

@admin_router.message(ChainCreateSG.desc_en)
async def chain_create_desc_en(m: Message, state: FSMContext):
    await state.update_data(desc_en=m.text.strip())
    await state.set_state(ChainCreateSG.url)
    await m.answer("URL (t.me/... або будь-який):")

@admin_router.message(ChainCreateSG.url)
async def chain_create_url(m: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    chain_key = f"chain:{uuid.uuid4().hex[:12]}"
    session.add(
        Tasks(
            title_uk=_title_placeholder(),
            title_ru=_title_placeholder(),
            title_en=_title_placeholder(),
            desc_uk=data["desc_uk"],
            desc_ru=data["desc_ru"],
            desc_en=data["desc_en"],
            url=m.text.strip(),
            reward_qc=1,
            chain_key=chain_key,
            cooldown_sec=1800,  # 30 хв
            is_active=True,
        )
    )
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Додати крок", callback_data=f"chain:add:{chain_key}")
    kb.button(text="⬅️ До списку", callback_data="adm:tasks")
    kb.adjust(1)
    await m.answer(f"✅ Створено ланцюг {chain_key} з першим кроком.", reply_markup=kb.as_markup())

@admin_router.callback_query(F.data.startswith("chain:view:"))
async def chain_view(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq):
        return
    raw = cq.data.split(":", 2)[2]
    chain_key = None if raw == "NULL" else raw

    steps = (
        await session.execute(
            select(Tasks).where(Tasks.chain_key == chain_key).order_by(Tasks.created_at.asc())
        )
    ).scalars().all()

    if not steps:
        await cq.answer("Порожньо.", show_alert=True)
        return

    lines = [f"Ланцюг [{chain_key or 'SOLO'}]: {len(steps)} крок(ів)\n"]
    for i, t in enumerate(steps, 1):
        lines.append(f"{i}. #{t.id} {'✅' if t.is_active else '⛔️'} url={t.url}")

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Додати крок", callback_data=f"chain:add:{chain_key or 'NULL'}")
    kb.button(text="🗑 Видалити останній", callback_data=f"chain:del_last:{chain_key or 'NULL'}")
    kb.button(text="⛔️/✅ Toggle всі", callback_data=f"chain:tgl:{chain_key or 'NULL'}")
    kb.button(text="⬅️ Назад", callback_data="adm:tasks")
    kb.adjust(1)
    await cq.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())

@admin_router.callback_query(F.data.startswith("chain:add:"))
async def chain_add_step_start(cq: CallbackQuery, state: FSMContext):
    if not await _require_admin_cq(cq):
        return
    raw = cq.data.split(":", 2)[2]
    chain_key = None if raw == "NULL" else raw
    await state.set_state(ChainAddStepSG.desc_uk)
    await state.update_data(chain_key=chain_key)
    await cq.message.edit_text(f"Додаємо крок у [{chain_key or 'SOLO'}]\n\nОпис (укр):")

@admin_router.message(ChainAddStepSG.desc_uk)
async def chain_add_step_desc_uk(m: Message, state: FSMContext):
    await state.update_data(desc_uk=m.text.strip())
    await state.set_state(ChainAddStepSG.desc_ru)
    await m.answer("Описание (рус):")

@admin_router.message(ChainAddStepSG.desc_ru)
async def chain_add_step_desc_ru(m: Message, state: FSMContext):
    await state.update_data(desc_ru=m.text.strip())
    await state.set_state(ChainAddStepSG.desc_en)
    await m.answer("Description (en):")

@admin_router.message(ChainAddStepSG.desc_en)
async def chain_add_step_desc_en(m: Message, state: FSMContext):
    await state.update_data(desc_en=m.text.strip())
    await state.set_state(ChainAddStepSG.url)
    await m.answer("URL:")

@admin_router.message(ChainAddStepSG.url)
async def chain_add_step_url(m: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    chain_key = data["chain_key"]
    session.add(
        Tasks(
            title_uk=_title_placeholder(),
            title_ru=_title_placeholder(),
            title_en=_title_placeholder(),
            desc_uk=data["desc_uk"],
            desc_ru=data["desc_ru"],
            desc_en=data["desc_en"],
            url=m.text.strip(),
            reward_qc=1,
            chain_key=chain_key,
            cooldown_sec=1800,
            is_active=True,
        )
    )
    await state.clear()
    await m.answer(f"✅ Додано крок у [{chain_key or 'SOLO'}].")
    # повернемося до перегляду ланцюга
    fake_cq = CallbackQuery(id=cq.id, from_user=m.from_user, chat_instance="", data=f"chain:view:{chain_key or 'NULL'}", message=m)  # type: ignore
    await chain_view(fake_cq, session)

@admin_router.callback_query(F.data.startswith("chain:del_last:"))
async def chain_del_last(cq: CallbackQuery, session: AsyncSession):
    from sqlalchemy import delete
    if not await _require_admin_cq(cq):
        return
    raw = cq.data.split(":", 2)[2]
    chain_key = None if raw == "NULL" else raw
    last = (
        await session.execute(
            select(Tasks).where(Tasks.chain_key == chain_key).order_by(Tasks.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if not last:
        await cq.answer("Нема що видаляти.", show_alert=True)
        return
    await session.execute(delete(Tasks).where(Tasks.id == last.id))
    await cq.answer("Видалено останній крок.")
    cq.data = f"chain:view:{raw}"
    await chain_view(cq, session)

@admin_router.callback_query(F.data.startswith("chain:tgl:"))
async def chain_toggle_all(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq):
        return
    raw = cq.data.split(":", 2)[2]
    chain_key = None if raw == "NULL" else raw

    # визначимо поточний стан за першим кроком
    first = (
        await session.execute(
            select(Tasks.is_active).where(Tasks.chain_key == chain_key).order_by(Tasks.created_at.asc()).limit(1)
        )
    ).scalar_one_or_none()
    new_state = not bool(first)
    await session.execute(
        update(Tasks).where(Tasks.chain_key == chain_key).values(is_active=new_state)
    )
    await cq.answer("Оновлено.")
    cq.data = f"chain:view:{raw}"
    await chain_view(cq, session)



# ─────────────────────────────────────────────────────────────────────────────
# Withdraws moderation
# ─────────────────────────────────────────────────────────────────────────────

@admin_router.callback_query(F.data.startswith("adm:withdraws:"))
async def adm_withdraws(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq):
        return

    page = int(cq.data.split(":")[2])
    rows = (
        await session.execute(
            select(Withdrawals).where(Withdrawals.status == "pending")
            .order_by(Withdrawals.created_at.asc())
            .limit(PAGE_SIZE)
            .offset(page * PAGE_SIZE)
        )
    ).scalars().all()

    if not rows:
        await cq.message.edit_text("Pending заявок немає." if page == 0 else "Порожньо.")
        return

    lines = ["Pending заявки:\n"]
    for w in rows:
        lines.append(
            f"#{w.id} | uid={w.user_id} | {w.amount_qc} QC | {w.country} / {w.method}\n{w.details or ''}"
        )
    text = "\n\n".join(lines)

    kb = InlineKeyboardBuilder()
    for w in rows:
        kb.button(text=f"✉️ Написати #{w.id}", callback_data=f"wd:msg:{w.id}:{page}")
        kb.button(text=f"✅ Виплачено #{w.id}", callback_data=f"wd:paid:{w.id}:{page}")
    if page > 0:
        kb.button(text="⬅️", callback_data=f"adm:withdraws:{page-1}")
    kb.button(text="➡️", callback_data=f"adm:withdraws:{page+1}")
    kb.adjust(2)

    await cq.message.edit_text(text, reply_markup=kb.as_markup())


@admin_router.callback_query(F.data.startswith("wd:msg:"))
async def adm_wd_msg(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq):
        return
    _, _, wid, page = cq.data.split(":")
    wid = int(wid)

    w = (await session.execute(select(Withdrawals).where(Withdrawals.id == wid))).scalar_one_or_none()
    if not w:
        await cq.answer("Заявку не знайдено.", show_alert=True)
        return

    uid_tg = (await session.execute(select(Users.tg_id).where(Users.id == w.user_id))).scalar_one_or_none()
    if not uid_tg:
        await cq.answer("Користувача не знайдено.", show_alert=True)
        return

    try:
        await cq.bot.send_message(uid_tg, "Адмін зв’язується щодо вашої заявки на вивід. Перевірте реквізити у чаті.")
        await cq.answer("Повідомлення надіслано")
    except Exception as e:
        await cq.answer(f"Помилка відправки: {e}", show_alert=True)

    cq.data = f"adm:withdraws:{page}"
    await adm_withdraws(cq, session)


@admin_router.callback_query(F.data.startswith("wd:paid:"))
async def adm_wd_paid(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq):
        return
    _, _, wid, page = cq.data.split(":")
    wid = int(wid)

    w = (await session.execute(select(Withdrawals).where(Withdrawals.id == wid))).scalar_one_or_none()
    if not w:
        await cq.answer("Заявку не знайдено.", show_alert=True)
        return

    # списуємо QC і позначаємо paid
    await ensure_wallet(session, w.user_id)
    await session.execute(
        update(QCWallets)
        .where(QCWallets.user_id == w.user_id)
        .values(balance_qc=QCWallets.balance_qc - w.amount_qc)
    )
    w.status = "paid"
    w.processed_at = now_utc()

    await cq.answer("Позначено як виплачено")

    # оновлюємо список
    cq.data = f"adm:withdraws:{page}"
    await adm_withdraws(cq, session)

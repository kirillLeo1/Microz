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
# Task creation wizard
# ─────────────────────────────────────────────────────────────────────────────

class TaskNewSG(StatesGroup):
    title_uk = State()
    title_ru = State()
    title_en = State()
    desc_uk = State()
    desc_ru = State()
    desc_en = State()
    url = State()
    chain = State()
    cooldown = State()
    copies = State()


@admin_router.callback_query(F.data == "adm:task_new")
async def adm_task_new(cq: CallbackQuery, state: FSMContext):
    if not await _require_admin_cq(cq):
        return
    await state.set_state(TaskNewSG.title_uk)
    await cq.message.edit_text("Введіть ЗАГОЛОВОК (укр)")


@admin_router.message(TaskNewSG.title_uk)
async def tnew_title_uk(m: Message, state: FSMContext):
    await state.update_data(title_uk=m.text.strip())
    await state.set_state(TaskNewSG.title_ru)
    await m.answer("ЗАГОЛОВОК (рус)")


@admin_router.message(TaskNewSG.title_ru)
async def tnew_title_ru(m: Message, state: FSMContext):
    await state.update_data(title_ru=m.text.strip())
    await state.set_state(TaskNewSG.title_en)
    await m.answer("TITLE (en)")


@admin_router.message(TaskNewSG.title_en)
async def tnew_title_en(m: Message, state: FSMContext):
    await state.update_data(title_en=m.text.strip())
    await state.set_state(TaskNewSG.desc_uk)
    await m.answer("ОПИС (укр)")


@admin_router.message(TaskNewSG.desc_uk)
async def tnew_desc_uk(m: Message, state: FSMContext):
    await state.update_data(desc_uk=m.text.strip())
    await state.set_state(TaskNewSG.desc_ru)
    await m.answer("ОПИСАНИЕ (рус)")


@admin_router.message(TaskNewSG.desc_ru)
async def tnew_desc_ru(m: Message, state: FSMContext):
    await state.update_data(desc_ru=m.text.strip())
    await state.set_state(TaskNewSG.desc_en)
    await m.answer("DESCRIPTION (en)")


@admin_router.message(TaskNewSG.desc_en)
async def tnew_desc_en(m: Message, state: FSMContext):
    await state.update_data(desc_en=m.text.strip())
    await state.set_state(TaskNewSG.url)
    await m.answer("URL (t.me/... або будь-який)")


@admin_router.message(TaskNewSG.url)
async def tnew_url(m: Message, state: FSMContext):
    await state.update_data(url=m.text.strip())
    await state.set_state(TaskNewSG.chain)

    kb = InlineKeyboardBuilder()
    kb.button(text="Без ланцюга", callback_data="tnew:chain:off")
    kb.button(text="Ланцюг ON", callback_data="tnew:chain:on")
    kb.adjust(1)

    await m.answer("Чи включати ланцюг (chain_key)?", reply_markup=kb.as_markup())


@admin_router.callback_query(F.data.startswith("tnew:chain:"))
async def tnew_chain(cq: CallbackQuery, state: FSMContext):
    if not await _require_admin_cq(cq):
        return
    on = cq.data.endswith("on")
    await state.update_data(chain_on=on)
    await state.set_state(TaskNewSG.cooldown)
    await cq.message.edit_text("Cooldown у секундах (за замовчуванням 1800). Введіть число.")


@admin_router.message(TaskNewSG.cooldown)
async def tnew_cooldown(m: Message, state: FSMContext):
    try:
        cd = int(m.text.strip())
    except Exception:
        cd = 1800
    await state.update_data(cooldown=cd)
    await state.set_state(TaskNewSG.copies)
    await m.answer("Скільки копій створити? (N; 1 — одиночне)")


@admin_router.message(TaskNewSG.copies)
async def tnew_copies(m: Message, state: FSMContext, session: AsyncSession):
    try:
        copies = max(1, int(m.text.strip()))
    except Exception:
        copies = 1

    data = await state.get_data()
    chain_key: Optional[str] = None
    if data.get("chain_on"):
        chain_key = f"chain:{uuid.uuid4().hex[:12]}"

    for _ in range(copies):
        session.add(
            Tasks(
                title_uk=data["title_uk"],
                title_ru=data["title_ru"],
                title_en=data["title_en"],
                desc_uk=data["desc_uk"],
                desc_ru=data["desc_ru"],
                desc_en=data["desc_en"],
                url=data["url"],
                reward_qc=1,
                chain_key=chain_key,
                cooldown_sec=int(data["cooldown"]),
                is_active=True,
            )
        )

    await state.clear()
    await m.answer(f"✅ Створено {copies} завдання(нь){' у ланцюгу' if chain_key else ''}.")


# ─────────────────────────────────────────────────────────────────────────────
# Task list / toggle / delete
# ─────────────────────────────────────────────────────────────────────────────

@admin_router.callback_query(F.data.startswith("adm:task_list:"))
async def adm_task_list(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq):
        return

    page = int(cq.data.split(":")[2])
    rows = (
        await session.execute(
            select(Tasks).order_by(Tasks.created_at.desc()).limit(PAGE_SIZE).offset(page * PAGE_SIZE)
        )
    ).scalars().all()

    if not rows and page > 0:
        await cq.answer("Немає більше сторінок.")
        return

    text_lines = ["Список завдань:\n"]
    for t in rows:
        text_lines.append(
            f"#{t.id} {'✅' if t.is_active else '⛔️'} | chain={t.chain_key or '-'} | cd={t.cooldown_sec}s | url={t.url}"
        )
    text = "\n".join(text_lines)

    kb = InlineKeyboardBuilder()
    for t in rows:
        kb.button(text=f"Toggle #{t.id}", callback_data=f"adm:tgl:{t.id}:{page}")
        kb.button(text=f"Del #{t.id}", callback_data=f"adm:del:{t.id}:{page}")
    if page > 0:
        kb.button(text="⬅️", callback_data=f"adm:task_list:{page-1}")
    kb.button(text="➡️", callback_data=f"adm:task_list:{page+1}")
    kb.adjust(2)

    await cq.message.edit_text(text, reply_markup=kb.as_markup())


@admin_router.callback_query(F.data.startswith("adm:tgl:"))
async def adm_task_toggle(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq):
        return

    _, _, tid, page = cq.data.split(":")
    tid = int(tid)
    page = int(page)

    task = (await session.execute(select(Tasks).where(Tasks.id == tid))).scalar_one_or_none()
    if not task:
        await cq.answer("Не знайдено.", show_alert=True)
        return

    task.is_active = not task.is_active
    await cq.answer("OK")
    # перерендеримо список поточної сторінки
    cq.data = f"adm:task_list:{page}"
    await adm_task_list(cq, session)


@admin_router.callback_query(F.data.startswith("adm:del:"))
async def adm_task_delete(cq: CallbackQuery, session: AsyncSession):
    if not await _require_admin_cq(cq):
        return

    _, _, tid, page = cq.data.split(":")
    tid = int(tid)
    page = int(page)

    await session.execute(delete(Tasks).where(Tasks.id == tid))
    await cq.answer("Deleted")
    cq.data = f"adm:task_list:{page}"
    await adm_task_list(cq, session)


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

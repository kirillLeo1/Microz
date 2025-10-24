# app/routers/user.py
from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings, ADMINS_LIST
from ..models import (
    Users,
    Payments,
    UserStatus,
    QCWallets,
    Withdrawals,
    Tasks,
)
from ..keyboards import main_menu
from ..i18n import I18N
from ..services.cryptocloud import create_invoice, get_invoice_info
from ..services.tasks import next_task_for_user, complete_task
from ..services.utils_tg import check_telegram_membership

user_router = Router()

# ─────────────────────────────────────────────────────────────────────────────
# Withdraw wizard
# ─────────────────────────────────────────────────────────────────────────────
class WithdrawSG(StatesGroup):
    country = State()
    method = State()
    details = State()
    amount = State()
    confirm = State()


# ─────────────────────────────────────────────────────────────────────────────
# /start + language + payment flow
# ─────────────────────────────────────────────────────────────────────────────
@user_router.message(CommandStart())
async def start(message: Message, session: AsyncSession):
    tg_id = message.from_user.id

    # deep link payload: "/start <payload>"
    payload = None
    if " " in message.text:
        payload = message.text.split(" ", 1)[1].strip()

    referrer_id = None
    if payload and payload.startswith("start="):
        try:
            referrer_id = int(payload.split("=", 1)[1])
        except Exception:
            referrer_id = None

    # upsert user (referrer_id фіксуємо лише під час першого /start)
    res = await session.execute(select(Users).where(Users.tg_id == tg_id))
    user = res.scalar()
    if not user:
        user = Users(tg_id=tg_id, referrer_id=referrer_id)
        session.add(user)
        await session.flush()

    # ask language once
    if not user.lang:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🇺🇦 УКР", callback_data="lang:uk")],
                [InlineKeyboardButton(text="🇷🇺 РУ", callback_data="lang:ru")],
                [InlineKeyboardButton(text="🇬🇧 EN", callback_data="lang:en")],
            ]
        )
        await message.answer(I18N["choose_lang"]["uk"], reply_markup=kb)
        return

    # if inactive → create CryptoCloud invoice and show link
    if str(user.status) in {"inactive", UserStatus.inactive}:
        try:
            inv = await create_invoice(settings.ENTRY_AMOUNT_USD, order_id=f"u{user.id}")
            uuid = inv.get("result", {}).get("uuid")
            link = inv.get("result", {}).get("link")
        except Exception as e:
            uuid = link = None
        if uuid and link:
            session.add(
                Payments(
                    user_id=user.id,
                    uuid=uuid,
                    amount_usd=settings.ENTRY_AMOUNT_USD,
                    status="created",
                )
            )
            await message.answer(
                I18N["pay_1"][user.lang] + f"\n\n{link}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Я оплатив(ла)", callback_data=f"paid:{uuid}")]
                    ]
                ),
            )
            return

    # else show main menu
    await message.answer(I18N["menu"][user.lang], reply_markup=main_menu(user.lang))
    if message.from_user.id in ADMINS_LIST:
        await message.answer("Ти адмін 👉 /admin")


@user_router.callback_query(F.data.startswith("lang:"))
async def set_lang(cq: CallbackQuery, session: AsyncSession):
    lang = cq.data.split(":", 1)[1]
    # зберігаємо мову
    await session.execute(
        update(Users).where(Users.tg_id == cq.from_user.id).values(lang=lang)
    )

    # перезавантажимо юзера, щоб знати статус
    user = (
        await session.execute(select(Users).where(Users.tg_id == cq.from_user.id))
    ).scalar_one()

    # якщо юзер не активний — одразу пропонуємо оплату $1
    if str(user.status) in {"inactive", UserStatus.inactive}:
        try:
            inv = await create_invoice(settings.ENTRY_AMOUNT_USD, order_id=f"u{user.id}")
            uuid = inv.get("result", {}).get("uuid")
            link = inv.get("result", {}).get("link")
        except Exception:
            uuid = link = None

        if uuid and link:
            session.add(
                Payments(
                    user_id=user.id,
                    uuid=uuid,
                    amount_usd=settings.ENTRY_AMOUNT_USD,
                    status="created",
                )
            )
            # оновимо повідомлення й дамо кнопку оплатити
            try:
                await cq.message.edit_text(I18N["pay_1"][lang] + f"\n\n{link}")
            except Exception:
                await cq.message.answer(I18N["pay_1"][lang] + f"\n\n{link}")
            await cq.message.answer(
                "Після оплати натисніть:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Я оплатив(ла)", callback_data=f"paid:{uuid}")]
                    ]
                ),
            )
            await cq.answer()
            return

    # якщо юзер уже активний або інвойс не створився — просто показуємо меню з клавіатурою
    try:
        await cq.message.edit_text(I18N["menu"][lang])
    except Exception:
        pass
    await cq.message.answer(I18N["menu"][lang], reply_markup=main_menu(lang))
    if cq.from_user.id in ADMINS_LIST:
        await cq.message.answer("Ти адмін 👉 /admin")
    await cq.answer()


@user_router.callback_query(F.data.startswith("paid:"))
async def check_paid(cq: CallbackQuery, session: AsyncSession):
    uuid = cq.data.split(":", 1)[1]
    data = await get_invoice_info([uuid])
    items = (data or {}).get("result", [])
    status = items[0].get("status") if items else None

    if status in {"paid", "overpaid", "partial"}:
        await session.execute(
            update(Users).where(Users.tg_id == cq.from_user.id).values(status="active")
        )
        await cq.message.edit_text("✅ Активовано! Відкрийте меню та продовжуйте.")
        # покажемо меню
        user = (
            await session.execute(select(Users).where(Users.tg_id == cq.from_user.id))
        ).scalar_one()
        await cq.message.answer(I18N["menu"][user.lang], reply_markup=main_menu(user.lang))
        if cq.from_user.id in ADMINS_LIST:
            await cq.message.answer("Ти адмін 👉 /admin")
    else:
        await cq.answer("Платіж ще не підтверджено. Спробуйте пізніше.", show_alert=True)


# ─────────────────────────────────────────────────────────────────────────────
# Tasks flow
# ─────────────────────────────────────────────────────────────────────────────
@user_router.message(F.text.in_({I18N["btn_tasks"][k] for k in ("uk", "ru", "en")}))
async def handle_tasks(message: Message, session: AsyncSession):
    user = (
        await session.execute(select(Users).where(Users.tg_id == message.from_user.id))
    ).scalar_one()

    task, tag = await next_task_for_user(session, user.id)
    if tag == "limit":
        await message.answer(I18N["limit_reached"][user.lang])
        return

    if not task:
        await message.answer("Поки немає доступних завдань. Зайдіть трохи згодом.")
        return

    title = getattr(task, f"title_{user.lang}")
    desc = getattr(task, f"desc_{user.lang}")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Відкрити", url=task.url)],
            [InlineKeyboardButton(text="Перевірити", callback_data=f"chk:{task.id}")],
        ]
    )
    await message.answer(f"<b>{title}</b>\n\n{desc}", reply_markup=kb, parse_mode="HTML")


@user_router.callback_query(F.data.startswith("chk:"))
async def check_task(cq: CallbackQuery, session: AsyncSession):
    task_id = int(cq.data.split(":", 1)[1])
    task = (
        await session.execute(select(Tasks).where(Tasks.id == task_id))
    ).scalar_one()

    ok = await check_telegram_membership(cq.bot, task.url, cq.from_user.id) or True

    if ok:
        uid = (
            await session.execute(
                select(Users.id).where(Users.tg_id == cq.from_user.id)
            )
        ).scalar_one()
        await complete_task(session, user_id=uid, task_id=task_id)
        await cq.message.edit_text("✅ Зараховано +1 QC")


# ─────────────────────────────────────────────────────────────────────────────
# Profile
# ─────────────────────────────────────────────────────────────────────────────
@user_router.message(F.text.in_({I18N["btn_profile"][k] for k in ("uk", "ru", "en")}))
async def profile(message: Message, session: AsyncSession):
    bot = message.bot
    me = await bot.get_me()
    user = (
        await session.execute(select(Users).where(Users.tg_id == message.from_user.id))
    ).scalar_one()
    w = (
        await session.execute(select(QCWallets).where(QCWallets.user_id == user.id))
    ).scalar()
    bal = w.balance_qc if w else 0
    link = f"https://t.me/{me.username}?start={message.from_user.id}"
    await message.answer(
        f"Баланс: <b>{bal} QC</b>\n1 QC = 0.5¢ США (100 QC = $0.50)\nРеф-посилання: {link}",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Withdraw wizard
# ─────────────────────────────────────────────────────────────────────────────
@user_router.message(F.text.in_({I18N["btn_withdraw"][k] for k in ("uk", "ru", "en")}))
async def withdraw_start(message: Message, state: FSMContext, session: AsyncSession):
    user = (
        await session.execute(select(Users).where(Users.tg_id == message.from_user.id))
    ).scalar_one()
    w = (
        await session.execute(select(QCWallets).where(QCWallets.user_id == user.id))
    ).scalar()
    bal = w.balance_qc if w else 0

    if bal < 1000:
        await message.answer("Мінімум для виводу — 1000 QC ($5)")
        return

    await state.set_state(WithdrawSG.country)
    await message.answer("Країна отримувача?")


@user_router.message(WithdrawSG.country)
async def withdraw_country(message: Message, state: FSMContext):
    await state.update_data(country=message.text.strip())
    await state.set_state(WithdrawSG.method)
    await message.answer("Спосіб отримання? (криптовалюта / банківська картка / інше)")


@user_router.message(WithdrawSG.method)
async def withdraw_method(message: Message, state: FSMContext):
    await state.update_data(method=message.text.strip())
    await state.set_state(WithdrawSG.details)
    await message.answer(
        "Реквізити (мережа+адреса USDT або дані картки/платіжна система). Можна коротко."
    )


@user_router.message(WithdrawSG.details)
async def withdraw_details(message: Message, state: FSMContext, session: AsyncSession):
    await state.update_data(details=message.text.strip())

    user = (
        await session.execute(select(Users).where(Users.tg_id == message.from_user.id))
    ).scalar_one()
    w = (
        await session.execute(select(QCWallets).where(QCWallets.user_id == user.id))
    ).scalar()
    bal = w.balance_qc if w else 0

    await state.set_state(WithdrawSG.amount)
    await message.answer(
        f"Яку суму вивести (QC)? За замовчуванням — <b>{bal}</b> QC. "
        f"Вкажіть число або напишіть 'все'.",
        parse_mode="HTML",
    )


@user_router.message(WithdrawSG.amount)
async def withdraw_amount(message: Message, state: FSMContext, session: AsyncSession):
    user = (
        await session.execute(select(Users).where(Users.tg_id == message.from_user.id))
    ).scalar_one()
    w = (
        await session.execute(select(QCWallets).where(QCWallets.user_id == user.id))
    ).scalar()
    bal = w.balance_qc if w else 0

    text = message.text.strip().lower()
    if text in {"все", "all"}:
        amount = bal
    else:
        try:
            amount = int(text)
        except Exception:
            await message.answer("Вкажіть ціле число або 'все'")
            return
        if amount <= 0 or amount > bal:
            await message.answer("Невірна сума. Вкажіть число >0 і ≤ вашого балансу")
            return

    await state.update_data(amount_qc=amount)
    data = await state.get_data()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Підтвердити", callback_data="wd:ok")],
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="wd:cancel")],
        ]
    )
    await state.set_state(WithdrawSG.confirm)
    await message.answer(
        f"Підтвердити заявку?\n"
        f"Країна: {data['country']}\n"
        f"Метод: {data['method']}\n"
        f"Реквізити: {data['details']}\n"
        f"Сума: {amount} QC",
        reply_markup=kb,
    )


@user_router.callback_query(F.data == "wd:cancel")
async def withdraw_cancel(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.message.edit_text("Скасовано.")


@user_router.callback_query(F.data == "wd:ok")
async def withdraw_ok(cq: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    user = (
        await session.execute(select(Users).where(Users.tg_id == cq.from_user.id))
    ).scalar_one()

    session.add(
        Withdrawals(
            user_id=user.id,
            amount_qc=int(data["amount_qc"]),
            country=data["country"],
            method=data["method"],
            details=data["details"],
            status="pending",
        )
    )
    await state.clear()
    await cq.message.edit_text("Заявку створено. Адмін зв’яжеться з вами для виплати.")


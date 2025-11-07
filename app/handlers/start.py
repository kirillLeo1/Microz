from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import CommandStart
from ..utils.i18n import i18n
from ..utils.keyboards import lang_kb, activation_kb, main_menu_kb
from ..services.tasks_service import (
    ensure_user, set_language, get_user,
    award_referral_if_needed, activate_user
)
from ..config import settings
from ..db import execute, fetchrow
from ..utils.tg import replace_message

# Новые провайдеры оплаты
from ..utils.payments import (
    create_monopay_invoice,
    create_cryptobot_invoice,
    get_cryptobot_invoice
)

import time
import re

router = Router()

# ======= Антидубль /start =======
_last_start = {}  # user_id -> ts
DEBOUNCE_SEC = 1.2


# ======= Утилиты =======
def parse_ref(payload: str | None) -> int | None:
    """
    Принимает варианты: 'start=<tg_id>', '<tg_id>', 'payloaddigits'
    """
    if not payload:
        return None
    m = re.search(r"(?:start=)?(\d{5,})", payload)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


async def _get_or_create_invoices(user_row, locale_code: str):
    """
    Возвращает ссылки pay_url_mono, pay_url_crypto.
    Если актуальных инвойсов нет — создаёт и сохраняет их в БД.
    """
    user_id = user_row["id"]
    tg_id = user_row["tg_id"]

    # 1) пробуем найти свежие "created/pending"
    mono = await fetchrow(
        """SELECT link, uuid FROM payments
           WHERE user_id=$1 AND provider='monopay' AND status IN ('created','pending')
           ORDER BY id DESC LIMIT 1""",
        user_id,
    )
    crypto = await fetchrow(
        """SELECT link, uuid FROM payments
           WHERE user_id=$1 AND provider='cryptobot' AND status IN ('created','pending')
           ORDER BY id DESC LIMIT 1""",
        user_id,
    )

    pay_url_mono = mono["link"] if mono else None
    pay_url_crypto = crypto["link"] if crypto else None

    # 2) если нет — создаём
    order_suffix = str(int(time.time()))
    description = "Activation"

    if not pay_url_mono and settings.MONOPAY_TOKEN:
        inv_mono = await create_monopay_invoice(
            order_id=f"ACT-MONO:{tg_id}:{order_suffix}",
            description=description
        )
        await execute(
            """INSERT INTO payments (user_id, provider, uuid, link, status, currency, amount_usd, order_id)
               VALUES ($1,'monopay',$2,$3,'created','UAH',$4,$5)""",
            user_id, inv_mono.invoice_id, inv_mono.pay_url, settings.PRICE_USD,
            f"ACT-MONO:{tg_id}:{order_suffix}",
        )
        pay_url_mono = inv_mono.pay_url

    if not pay_url_crypto and settings.CRYPTO_PAY_TOKEN:
        inv_crypto = await create_cryptobot_invoice(
            order_id=f"ACT-CRYPTO:{tg_id}:{order_suffix}",
            description=description
        )
        await execute(
            """INSERT INTO payments (user_id, provider, uuid, link, status, currency, amount_usd, order_id)
               VALUES ($1,'cryptobot',$2,$3,'created','USD',$4,$5)""",
            user_id, inv_crypto.invoice_id, inv_crypto.pay_url, settings.PRICE_USD,
            f"ACT-CRYPTO:{tg_id}:{order_suffix}",
        )
        pay_url_crypto = inv_crypto.pay_url

    return pay_url_mono, pay_url_crypto


async def _activation_screen(message_or_cb, texts, pay_url_mono: str | None, pay_url_crypto: str | None):
    """
    Показывает экран активации с двумя URL-кнопками (MonoPay/CryptoBot) и кнопкой «Я оплатил».
    """
    if hasattr(message_or_cb, "answer") and hasattr(message_or_cb, "message_id"):
        await message_or_cb.answer("\u2063", reply_markup=ReplyKeyboardRemove())
        await message_or_cb.answer(
            f"<b>{texts.get('activate_title', 'Активация')}</b>\n{texts.get('activate_text', 'Оплатите и доступ откроется автоматически.')}",
            reply_markup=activation_kb(pay_url_mono, pay_url_crypto, texts),
        )
    else:
        # cb.message
        await message_or_cb.message.answer("\u2063", reply_markup=ReplyKeyboardRemove())
        await replace_message(
            message_or_cb.message,
            f"<b>{texts.get('activate_title', 'Активация')}</b>\n{texts.get('activate_text', 'Оплатите и доступ откроется автоматически.')}",
            reply_markup=activation_kb(pay_url_mono, pay_url_crypto, texts),
        )


# ======= /start =======
@router.message(CommandStart())
async def on_start(msg: Message):
    now = time.time()
    ts = _last_start.get(msg.from_user.id, 0)
    if now - ts < DEBOUNCE_SEC:
        return  # игнорируем дубль
    _last_start[msg.from_user.id] = now

    # реферал из payload
    payload = msg.text.split(maxsplit=1)[1] if msg.text and len(msg.text.split()) > 1 else None
    ref = parse_ref(payload)
    user = await ensure_user(msg.from_user.id, referrer_tg=ref)

    # если язык ещё не выбран — покажем выбор
    if not user["language"]:
        await msg.answer("\u2063", reply_markup=ReplyKeyboardRemove())
        await msg.answer(i18n.t("en", "lang_prompt"), reply_markup=lang_kb())
        return

    # если не активирован — экран активации
    if user["status"] != "active":
        lang = user["language"]
        texts = i18n._texts[lang]
        pay_url_mono, pay_url_crypto = await _get_or_create_invoices(user, lang)
        await _activation_screen(msg, texts, pay_url_mono, pay_url_crypto)
        return

    # активен → главное меню
    lang = user["language"]
    texts = i18n._texts[lang]
    await msg.answer(texts["main_menu"], reply_markup=main_menu_kb(texts))


# ======= Выбор языка =======
@router.callback_query(F.data.startswith("lang:"))
async def set_lang_cb(cb: CallbackQuery):
    code = cb.data.split(":")[1]

    # сохраняем язык пользователя (если функция есть)
    try:
        await set_language(cb.from_user.id, code)
    except Exception:
        pass

    # гарантируем пользователя
    user = await get_user(cb.from_user.id) or await ensure_user(cb.from_user.id)
    texts = i18n._texts[code]

    # создаём (или берём) инвойсы для выбранного языка
    pay_url_mono, pay_url_crypto = await _get_or_create_invoices(user, code)

    # экран активации
    await cb.message.answer("\u2063", reply_markup=ReplyKeyboardRemove())
    await replace_message(
        cb.message,
        f"<b>{texts.get('activate_title', 'Активация')}</b>\n\n{texts.get('activate_text', 'Оплатите и доступ откроется автоматически.')}",
        reply_markup=activation_kb(pay_url_mono, pay_url_crypto, texts),
    )
    await cb.answer()


# ======= Ручная проверка оплаты («Я оплатил») =======
async def _check_paid_and_activate(user_row) -> bool:
    """
    Унифицированная логика для 'activation:check' и 'paid_check'.
    1) Сначала ищем уже 'paid' в БД (MonoPay/CryptoBot) — это основной путь через вебхуки.
    2) Если нет — пробуем подтянуть статус последнего инвойса CryptoBot по API.
       (MonoPay статус тянем вебхуком: подпись X-Sign проверяет сервер.)
    """
    # 1) есть ли уже paid?
    paid = await fetchrow(
        """SELECT 1 FROM payments
           WHERE user_id=$1 AND status='paid'
           ORDER BY id DESC LIMIT 1""",
        user_row["id"],
    )
    if paid:
        await execute("UPDATE users SET status='active' WHERE id=$1", user_row["id"])
        await award_referral_if_needed(user_row["tg_id"])
        return True

    # 2) CryptoBot: проверим по API последний созданный инвойс
    inv_crypto = await fetchrow(
        """SELECT uuid FROM payments
           WHERE user_id=$1 AND provider='cryptobot' AND status IN ('created','pending')
           ORDER BY id DESC LIMIT 1""",
        user_row["id"],
    )
    if inv_crypto and inv_crypto["uuid"]:
        try:
            info = await get_cryptobot_invoice(inv_crypto["uuid"])
            status = (getattr(info, "status", None) or "").lower()
            if status in ("paid", "completed"):
                await execute("UPDATE payments SET status='paid' WHERE uuid=$1", inv_crypto["uuid"])
                await execute("UPDATE users SET status='active' WHERE id=$1", user_row["id"])
                await award_referral_if_needed(user_row["tg_id"])
                return True
        except Exception:
            # молча даём вебхуку завершить
            pass

    return False


@router.callback_query(F.data == "activation:check")
async def activation_check(cb: CallbackQuery):
    user = await get_user(cb.from_user.id)
    if user["status"] == "active":
        await cb.answer("Уже активировано ✅", show_alert=True)
        return

    ok = await _check_paid_and_activate(user)
    texts = i18n._texts[user["language"] or "en"]

    if ok:
        await cb.answer("Готово ✅", show_alert=True)
        await replace_message(cb.message, texts.get("activated", "✅ Доступ активирован."))
        await cb.message.answer(texts["main_menu"], reply_markup=main_menu_kb(texts))
    else:
        await cb.answer(texts.get("not_confirmed", "Платёж ещё не подтверждён, попробуйте позже 🙏"), show_alert=True)


# Сохраняем старый хендлер имени, чтобы ничего не отвалилось в меню/кнопках
@router.callback_query(F.data == "paid_check")
async def paid_check_alias(cb: CallbackQuery):
    await activation_check(cb)

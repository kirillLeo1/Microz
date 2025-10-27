from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from ..utils.i18n import i18n
from ..utils.keyboards import lang_kb, activation_kb, main_menu_kb
from ..services.tasks_service import ensure_user, set_language, get_user, award_referral_if_needed, activate_user
from ..config import settings
from ..utils.payments import create_invoice as cc_create, get_invoices_info
from ..db import fetchrow
from ..services.tasks_service import create_invoice as db_create_invoice, set_payment_status
from ..utils.tg import replace_message
from aiogram.types import ReplyKeyboardRemove
import time
from aiogram.types import PreCheckoutQuery
from aiogram.types import LabeledPrice
router = Router()

_last_start = {}  # user_id -> ts

DEBOUNCE_SEC = 1.2

def parse_ref(payload: str | None) -> int | None:
    if not payload:
        return None
    # Accept formats: "start=<tg_id>", "<tg_id>", "payloaddigits"
    import re
    m = re.search(r"(?:start=)?(\d{5,})", payload)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None

@router.message(CommandStart())
async def on_start(msg: Message):
    now = time.time()
    ts = _last_start.get(msg.from_user.id, 0)
    if now - ts < DEBOUNCE_SEC:
        return  # ігноруємо дубльований /start
    _last_start[msg.from_user.id] = now

    # 1) фіксуємо можливого реферала
    payload = msg.text.split(maxsplit=1)[1] if msg.text and len(msg.text.split()) > 1 else None
    ref = parse_ref(payload)
    user = await ensure_user(msg.from_user.id, referrer_tg=ref)

    # 2) немає мови → показуємо вибір (інлайни), попередньо скидаємо reply-клаву
    if not user["language"]:
        await msg.answer("\u2063", reply_markup=ReplyKeyboardRemove())
        await msg.answer(i18n.t("en", "lang_prompt"), reply_markup=lang_kb())
        return

    # 3) якщо не активний → екран активації з кнопкою "Оплатити $1" (якщо інвойс вже є)
    if user["status"] != "active":
        lang = user["language"]
        texts = i18n._texts[lang]

        inv = await fetchrow(
            "SELECT link FROM payments WHERE user_id=$1 AND status='created' ORDER BY id DESC LIMIT 1",
            user["id"],
        )
        pay_url = inv["link"] if inv else None

        # прибираємо залиплу reply-клаву й показуємо інлайн-кнопки активації
        await msg.answer("\u2063", reply_markup=ReplyKeyboardRemove())
        await msg.answer(
            f"<b>{texts['activate_title']}</b>\n{texts['activate_text']}",
            reply_markup=activation_kb(pay_url, texts),
        )
        return

    # 4) активний → показуємо ГОЛОВНЕ МЕНЮ з reply-клавою
    lang = user["language"]
    texts = i18n._texts[lang]
    await msg.answer(texts["main_menu"], reply_markup=main_menu_kb(texts))

@router.callback_query(F.data == "pay:stars")
async def pay_stars(cb: CallbackQuery):
    user = await get_user(cb.from_user.id)
    payload = f"ACT-STAR:{user['tg_id']}:{int(time.time())}"  # наш order_id

    await cb.message.bot.send_invoice(
        chat_id=cb.from_user.id,
        title=settings.STARS_TITLE,
        description=settings.STARS_DESCRIPTION,
        payload=payload,
        currency="XTR",                                   # Telegram Stars
        prices=[LabeledPrice(label=settings.STARS_TITLE, amount=int(settings.STARS_PRICE_XTR))],
        provider_token="",                                # для XTR не потрібен
    )
    await cb.answer()

@router.pre_checkout_query()
async def on_pre_checkout(pcq: PreCheckoutQuery):
    # можеш перевірити pcq.currency == "XTR" і payload починається з ACT-STAR:
    await pcq.answer(ok=True)


@router.callback_query(F.data.startswith("lang:"))
async def set_lang(cb: CallbackQuery):
    code = cb.data.split(":")[1]

    # 1) зберігаємо мову (якщо є така функція; якщо ні — прибери try/except)
    try:
        from ..services.tasks_service import set_language  # локальний імпорт, якщо є
        await set_language(cb.from_user.id, code)
    except Exception:
        pass

    # 2) гарантуємо наявність юзера БЕЗ referrer_tg_id
    user = await get_user(cb.from_user.id)
    if not user:
        user = await ensure_user(cb.from_user.id)

    texts = i18n._texts[code]
    pay_url_crypto = None

    # 3) створюємо інвойс CryptoCloud (як і раніше) і кладемо його в БД
    if not settings.TEST_MODE:
        inv = await cc_create(
            amount_usd=settings.CRYPTOCLOUD_PRICE_USD,
            order_id=f"ACT-CC-{user['tg_id']}",
            description="Activation",
            locale=code,
        )
        # твій utils/payments.create_invoice повертає {"uuid","link"}
        cc_uuid = inv["uuid"]
        cc_link = inv["link"]
        pay_url_crypto = cc_link

        await execute("""
            INSERT INTO payments (user_id, provider, uuid, link, status, amount_usd, currency)
            VALUES ($1,'cryptocloud',$2,$3,'created',$4,'USD')
        """, user["id"], cc_uuid, cc_link, settings.CRYPTOCLOUD_PRICE_USD)

    # 4) показуємо екран активації з двома кнопками:
    #    ⭐️ Stars (callback "pay:stars") та CryptoCloud (url)
    await cb.message.answer("\u2063", reply_markup=ReplyKeyboardRemove())  # скинути reply-клаву
    await replace_message(
        cb.message,
        f"<b>{texts['activate_title']}</b>\n\n{texts['activate_text']}",
        reply_markup=activation_kb(pay_url_crypto, texts, include_stars=settings.STARS_ENABLED),
    )
    await cb.answer()


@router.message(F.successful_payment)
async def on_successful_payment(msg: Message):
    sp = msg.successful_payment
    if sp.currency != "XTR":
        return
    payload = sp.invoice_payload or ""
    if not payload.startswith("ACT-STAR:"):
        return

    user = await get_user(msg.from_user.id)

    # зафіксувати платіж Stars (ідемпотентно)
    await execute("""
        INSERT INTO payments (user_id, provider, order_id, status, currency, amount_stars)
        VALUES ($1,'stars',$2,'paid','XTR',$3)
        ON CONFLICT (provider, order_id)
        DO UPDATE SET status='paid', amount_stars=EXCLUDED.amount_stars
    """, user["id"], payload, sp.total_amount)

    # активуємо акаунт + одноразовий реф-бонус
    await execute("UPDATE users SET status='active' WHERE id=$1", user["id"])
    await execute("""
        UPDATE users u SET earned_total_qc = earned_total_qc + 60
        FROM users r
        WHERE r.id = $1 AND r.referrer_id = u.id
          AND r.referral_bonus_given IS DISTINCT FROM TRUE
    """, user["id"])
    await execute("UPDATE users SET referral_bonus_given=TRUE WHERE id=$1", user["id"])

    texts = i18n._texts[user["language"]]
    await msg.answer(texts["activated"])
    await msg.answer(texts["main_menu"], reply_markup=main_menu_kb(texts))

@router.callback_query(F.data == "activation:check")
async def activation_check(cb: CallbackQuery):
    user = await get_user(cb.from_user.id)
    if user["status"] == "active":
        await cb.answer("Вже активовано ✅", show_alert=True)
        return

    ok = False

    # 1) Раптом вже прийшов successful_payment (Stars)
    row_star = await fetchrow("""
        SELECT 1 FROM payments
         WHERE user_id=$1 AND provider='stars' AND status='paid'
         ORDER BY id DESC LIMIT 1
    """, user["id"])
    if row_star:
        ok = True

    # 2) Якщо ні — перевіряємо CryptoCloud
    if not ok:
        inv = await fetchrow("""
            SELECT uuid FROM payments
             WHERE user_id=$1 AND provider='cryptocloud' AND status='created'
             ORDER BY id DESC LIMIT 1
        """, user["id"])
        if inv and inv["uuid"]:
            try:
                info = await get_invoices_info([inv["uuid"]])   # список інвойсів
                status = (info[0].get("status") or "").lower() if info else ""
                if status in ("paid", "overpaid", "partial"):
                    ok = True
                    await execute("UPDATE payments SET status='paid' WHERE uuid=$1", inv["uuid"])
            except Exception:
                pass

    if settings.TEST_MODE:
        ok = True

    if ok:
        await execute("UPDATE users SET status='active' WHERE id=$1", user["id"])
        await cb.answer("Готово ✅", show_alert=True)
        texts = i18n._texts[user["language"]]
        await replace_message(cb.message, texts["activated"])
        await cb.message.answer(texts["main_menu"], reply_markup=main_menu_kb(texts))
    else:
        await cb.answer("Платіж ще не підтверджено, спробуйте пізніше 🙏", show_alert=True)


@router.callback_query(F.data=='paid_check')
async def on_paid(cb: CallbackQuery):
    user = await get_user(cb.from_user.id)
    lang = user['language'] or 'en'
    texts = i18n._texts[lang]

    if settings.TEST_MODE:
        await activate_user(cb.from_user.id)
        await award_referral_if_needed(cb.from_user.id)
        await replace_message(cb.message, texts['activated'])
        # показуємо меню
        await cb.message.answer(texts['main_menu'], reply_markup=main_menu_kb(texts))
        await cb.answer()
        return

    inv = await fetchrow("SELECT * FROM payments WHERE user_id=$1 ORDER BY id DESC LIMIT 1", user["id"])
    if not inv:
        await cb.answer("No invoice.")
        return

    data = await get_invoices_info([inv['uuid']])
    status = None
    if isinstance(data, list) and data:
        status = data[0].get('status') or data[0].get('status_invoice')

    if status in ('paid','overpaid','partial'):
        await set_payment_status(inv['uuid'], status)
        await activate_user(cb.from_user.id)
        await award_referral_if_needed(cb.from_user.id)
        await replace_message(cb.message, texts['activated'])
        # меню після активації
        await cb.message.answer(texts['main_menu'], reply_markup=main_menu_kb(texts))
    else:
        await cb.answer(texts['not_confirmed'], show_alert=True)


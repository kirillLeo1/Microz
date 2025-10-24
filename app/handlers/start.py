from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from ..utils.i18n import i18n
from ..utils.keyboards import lang_kb, activation_kb, main_menu_kb
from ..services.tasks_service import ensure_user, set_language, get_user, award_referral_if_needed, activate_user
from ..config import settings
from ..utils.payments import create_invoice, get_invoices_info
from ..db import fetchrow
from ..services.tasks_service import create_invoice as db_create_invoice, set_payment_status
from ..utils.tg import replace_message
from aiogram.types import ReplyKeyboardRemove
router = Router()

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
    # 1) фіксуємо можливого реферала
    payload = msg.text.split(maxsplit=1)[1] if msg.text and len(msg.text.split()) > 1 else None
    ref = parse_ref(payload)
    user = await ensure_user(msg.from_user.id, referrer_tg=ref)

    # 2) немає мови → показуємо вибір (інлайни), попередньо скидаємо reply-клаву
    if not user["language"]:
        await msg.answer(" ", reply_markup=ReplyKeyboardRemove())
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
        await msg.answer(" ", reply_markup=ReplyKeyboardRemove())
        await msg.answer(
            f"<b>{texts['activate_title']}</b>\n{texts['activate_text']}",
            reply_markup=activation_kb(pay_url, texts),
        )
        return

    # 4) активний → показуємо ГОЛОВНЕ МЕНЮ з reply-клавою
    lang = user["language"]
    texts = i18n._texts[lang]
    await msg.answer(texts["main_menu"], reply_markup=main_menu_kb(texts))


@router.callback_query(F.data.startswith("lang:"))
async def set_lang(cb: CallbackQuery):
    """
    Обробка вибору мови:
    - зберігаємо мову
    - створюємо інвойс у CryptoCloud (якщо не TEST_MODE)
    - показуємо екран активації з кнопкою “Оплатити $1” (або без, якщо створення інвойса не вдалося)
    """
    code = cb.data.split(":")[1].strip()
    await set_language(cb.from_user.id, code)

    texts = i18n._texts.get(code, i18n._texts["en"])
    pay_url = None

    if not settings.TEST_MODE:
        try:
            user = await get_user(cb.from_user.id)
            inv = await create_invoice(
                amount_usd=settings.CRYPTOCLOUD_PRICE_USD,
                order_id=f"ACT-{user['tg_id']}",
                description="Activation",
                locale=code,
            )
            # inv = {"uuid": "...", "link": "https://pay.cryptocloud.plus/invoice/...."}
            await db_create_invoice(user["id"], inv["uuid"], inv["link"], settings.CRYPTOCLOUD_PRICE_USD)
            pay_url = inv["link"]
        except CryptoCloudError as e:
            # Показуємо юзеру коротке пояснення, а ти деталі побачиш у логах
            await cb.message.answer(f"❗️Не вдалось створити інвойс: {e}")
        except Exception as e:
            await cb.message.answer(f"❗️Помилка створення інвойса. Спробуйте ще раз пізніше.")

    text = f"<b>{texts['activate_title']}</b>\n{texts['activate_text']}"
    kb = activation_kb(pay_url, texts)

    # 1) скидаємо reply-клавіатуру (це окреме повідомлення)
    await cb.message.answer(" ", reply_markup=ReplyKeyboardRemove())

    # 2) показуємо екран активації: delete + send (інлайн-кнопки всередині)
    await replace_message(cb.message, text, reply_markup=kb)
    
    # 3) закриваємо callback
    await cb.answer()


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


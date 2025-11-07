from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


def lang_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🇺🇦", callback_data="lang:uk"),
        InlineKeyboardButton(text="🇷🇺", callback_data="lang:ru"),
        InlineKeyboardButton(text="🇬🇧", callback_data="lang:en"),
    )
    return kb.as_markup()


def activation_kb(
    pay_url_mono: str | None,
    pay_url_crypto: str | None,
    texts: dict,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pay_url_mono:
        rows.append([
            InlineKeyboardButton(
                text=texts.get("pay_mono", "💳 Оплатить картой (MonoPay)"),
                url=pay_url_mono,
            )
        ])
    if pay_url_crypto:
        rows.append([
            InlineKeyboardButton(
                text=texts.get("pay_crypto", "🪙 Оплатить криптой (CryptoBot)"),
                url=pay_url_crypto,
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text=texts.get("i_paid", "✅ Я оплатил"),
            callback_data="activation:check",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tasks_chain_kb(items) -> InlineKeyboardMarkup:
    """
    items: iterable of tuples (text, callback_data, disabled_bool_or_none)
    disabled сейчас игнорируем, оставляю сигнатуру совместимой.
    """
    kb = InlineKeyboardBuilder()
    for text, cb, _ in items:
        kb.row(
            InlineKeyboardButton(
                text=text,
                callback_data=cb or "noop",
            )
        )
    return kb.as_markup()


def step_kb(
    open_url: str,
    check_text: str,
    open_text: str,
    step_id: int,
    chain_id: int,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=open_text, url=open_url))
    kb.row(
        InlineKeyboardButton(
            text=check_text,
            callback_data=f"step_check:{step_id}:{chain_id}",
        )
    )
    return kb.as_markup()


def main_menu_kb(texts: dict) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.get("tasks_btn", "🧩 Задания"))],
            [KeyboardButton(text=texts.get("profile_btn", "👤 Профиль"))],
            [KeyboardButton(text=texts.get("withdraw_btn", "💸 Вывод"))],
        ],
        resize_keyboard=True,
    )


def admin_menu_kb(texts: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=texts.get("admin_stats", "Статистика"),
            callback_data="admin:stats",
        ),
        InlineKeyboardButton(
            text=texts.get("admin_tasks", "Задания"),
            callback_data="admin:tasks",
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text=texts.get("admin_broadcast", "Рассылка"),
            callback_data="admin:broadcast",
        ),
        InlineKeyboardButton(
            text=texts.get("admin_withdraws", "Выводы"),
            callback_data="admin:withdraws",
        ),
    )
    return kb.as_markup()


def step_check_kb(check_text: str, step_id: int, chain_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=check_text,
            callback_data=f"step_check:{step_id}:{chain_id}",
        )
    )
    return kb.as_markup()

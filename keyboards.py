from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def lang_kb():
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🇺🇦", callback_data="lang:uk"),
        InlineKeyboardButton(text="🇷🇺", callback_data="lang:ru"),
        InlineKeyboardButton(text="🇬🇧", callback_data="lang:en"),
    )
    return kb.as_markup()

def activation_kb(pay_url: str | None, texts):
    kb = InlineKeyboardBuilder()
    if pay_url:
        kb.row(InlineKeyboardButton(text=texts["pay_btn"], url=pay_url))
    kb.row(InlineKeyboardButton(text=texts["i_paid_btn"], callback_data="paid_check"))
    return kb.as_markup()

def tasks_chain_kb(items):
    # items: list of tuples (text, cbdata or None, disabled)
    kb = InlineKeyboardBuilder()
    for text, cb, disabled in items:
        if cb:
            kb.row(InlineKeyboardButton(text=text, callback_data=cb))
        else:
            # disabled line as label
            kb.row(InlineKeyboardButton(text=text, callback_data="noop"))
    return kb.as_markup()

def step_kb(open_url: str, check_text: str, open_text: str = "Open"):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=open_text, url=open_url))
    kb.row(InlineKeyboardButton(text=check_text, callback_data="step_check"))
    return kb.as_markup()

def admin_menu_kb(texts):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=texts["admin_stats"], callback_data="admin:stats"),
        InlineKeyboardButton(text=texts["admin_tasks"], callback_data="admin:tasks"),
    )
    kb.row(
        InlineKeyboardButton(text=texts["admin_broadcast"], callback_data="admin:broadcast"),
        InlineKeyboardButton(text=texts["admin_withdraws"], callback_data="admin:withdraws"),
    )
    return kb.as_markup()

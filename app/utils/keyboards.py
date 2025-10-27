from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
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

def step_kb(open_url: str, check_text: str, open_text: str, step_id: int, chain_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=open_text, url=open_url))
    kb.row(InlineKeyboardButton(text=check_text, callback_data=f"step_check:{step_id}:{chain_id}"))
    return kb.as_markup()
    
def main_menu_kb(texts: dict) -> ReplyKeyboardMarkup:
    """
    Reply-клава головного меню. texts — це i18n._texts[lang].
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts["tasks_btn"])],
            [KeyboardButton(text=texts["profile_btn"])],
            [KeyboardButton(text=texts["withdraw_btn"])],
        ],
        resize_keyboard=True
    )

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

def step_check_kb(check_text: str, step_id: int, chain_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=check_text,
            callback_data=f"step_check:{step_id}:{chain_id}"
        )
    )
    return kb.as_markup()

def activation_kb(pay_url_crypto: str | None, texts: dict, include_stars: bool = True):
    rows = []
    if include_stars:
        rows.append([InlineKeyboardButton(text=texts["pay_stars"], callback_data="pay:stars")])
    if pay_url_crypto:
        rows.append([InlineKeyboardButton(text=texts["pay_crypto"], url=pay_url_crypto)])
    rows.append([InlineKeyboardButton(text=texts["i_paid"], callback_data="activation:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

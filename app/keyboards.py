from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from .i18n import I18N

def main_menu(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=I18N["btn_tasks"][lang])],
        [KeyboardButton(text=I18N["btn_profile"][lang])],
        [KeyboardButton(text=I18N["btn_withdraw"][lang])],
    ], resize_keyboard=True)
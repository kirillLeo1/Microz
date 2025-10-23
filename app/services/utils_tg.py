import re
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

_TME_RE = re.compile(r"https?://t\.me/(?:c/)?([A-Za-z0-9_]+)/?")

async def check_telegram_membership(bot: Bot, url: str, user_id: int) -> bool:
    m = _TME_RE.match(url)
    if not m:
        return False  # не TG-канал, перевірка не застосовується (UX-перевірка зробить свою справу)
    username = m.group(1)
    try:
        chat = await bot.get_chat(username)
        member = await bot.get_chat_member(chat.id, user_id)
        return member.status in {"member", "administrator", "creator"}
    except TelegramBadRequest:
        return False
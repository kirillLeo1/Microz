from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from ..services.tasks_service import get_user
from ..utils.i18n import i18n
from ..config import settings

router = Router()

@router.message(Command("help"))
async def help_cmd(msg: Message):
    user = await get_user(msg.from_user.id)
    lang = (user and user["language"]) or "en"
    await msg.answer(i18n.t(lang, "help"))

@router.message(F.text.in_({"👤 Профіль","👤 Профиль","👤 Profile"}))
async def profile_btn(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user or not user["language"]:
        await msg.answer("Use /start")
        return
    lang = user["language"]
    text = i18n.t(lang, "balance", qc=user["balance_qc"], earned=user["earned_total_qc"],
                  status=i18n.t(lang, "status_active" if user["status"]=="active" else "status_inactive"),
                  lang=lang, done=user["today_count"])
    ref = i18n.t(lang, "ref_link", bot=msg.bot.username, tg_id=user["tg_id"])
    await msg.answer(f"{i18n.t(lang,'profile_title')}\n\n{text}\n\n{ref}")

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from ..utils.i18n import i18n
from ..utils.keyboards import tasks_chain_kb, step_kb
from ..services.tasks_service import (get_user, list_chains, list_chain_steps, user_next_step,
                                      get_cooldown_left, set_cooldown, inc_today_and_check_limit,
                                      award_qc, mark_step_completed)
from aiogram.exceptions import TelegramBadRequest
from ..utils.tg import replace_message
from ..utils.keyboards import step_check_kb
from ..utils.links import normalize_url
router = Router()

@router.message(F.text.in_({"🎯 Завдання","🎯 Задания","🎯 Tasks"}))
async def open_tasks(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user or not user["language"]:
        await msg.answer("Use /start")
        return
    lang = user["language"]
    if user["status"] != "active":
        await msg.answer("Please activate first via /start")
        return
    chains = await list_chains()
    items = []
    for ch in chains:
        steps = await list_chain_steps(ch["id"])
        nxt = await user_next_step(msg.from_user.id, ch["id"])
        cd_sec = await get_cooldown_left(msg.from_user.id, ch["id"])
        if not nxt:
            items.append((f"{ch['key']} ✅", None, True))
            continue
        if cd_sec > 0:
            mm = int(cd_sec//60); ss=int(cd_sec%60)
            items.append((i18n.t(lang,"cooldown_timer", mm=f"{mm:02d}", ss=f"{ss:02d}"), None, True))
        else:
            reward = nxt["reward_qc"]
            items.append((i18n.t(lang,"chain_open", name=ch["key"], qc=reward), f"open_chain:{ch['id']}:{nxt['id']}", False))
    await msg.answer(i18n.t(lang,"chains_list"), reply_markup=tasks_chain_kb(items))

@router.callback_query(F.data.startswith("open_chain:"))
async def open_chain(cb: CallbackQuery):
    """
    Очікує callback_data формату: open_chain:<chain_id>:<step_id>
    Показує опис кроку + КЛІКАБЕЛЬНИЙ ЛІНК у тексті, без кнопки-url.
    В клавіатурі лишається тільки кнопка 'Перевірити'.
    """
    try:
        _, chain_id, step_id = cb.data.split(":")
        chain_id = int(chain_id)
        step_id = int(step_id)
    except Exception:
        await cb.answer("Bad data", show_alert=True)
        return

    user = await get_user(cb.from_user.id)
    lang = (user and user.get("language")) or "en"

    st = await fetchrow("SELECT * FROM steps WHERE id=$1", step_id)
    if not st:
        await cb.answer(i18n.t(lang, "not_found"), show_alert=True)
        return

    title = st.get(f"title_{lang}") or ""
    desc = st.get(f"desc_{lang}") or ""
    reward = st["reward_qc"]
    open_url = normalize_url(st["url"])

    # Текст кроку: (заголовок — опційно) + опис + кликабельный лінк + нагорода
    parts = []
    if title and title != "-":
        parts.append(f"<b>{title}</b>")
    if desc:
        parts.append(desc)
    parts.append(f"🔗 {open_url}")
    parts.append(i18n.t(lang, "reward", qc=reward))
    text = "\n\n".join(parts)

    await replace_message(
        cb.message,
        text,
        reply_markup=step_check_kb(i18n.t(lang, "check_btn"), step_id, chain_id),
    )
    await cb.answer()

    # Store context for "step_check":

@router.callback_query(F.data.startswith("step_check:"))
async def check_step(cb: CallbackQuery):
    _, step_id, chain_id = cb.data.split(":")
    step_id = int(step_id); chain_id = int(chain_id)

    user = await get_user(cb.from_user.id)
    lang = user["language"]

    from ..db import fetchrow
    st = await fetchrow("SELECT * FROM steps WHERE id=$1", step_id)
    if not st:
        await cb.answer(i18n.t(lang, "not_done"), show_alert=True)
        return

    ok = True
    if st["verify_chat_id"]:
        try:
            member = await cb.bot.get_chat_member(st["verify_chat_id"], cb.from_user.id)
            ok = (member is not None and getattr(member, "status", None) in ("member", "administrator", "creator"))
        except TelegramBadRequest:
            ok = False
    if not ok:
        await cb.answer(i18n.t(lang, "not_done"), show_alert=True)
        return

    if not await inc_today_and_check_limit(cb.from_user.id):
        await cb.answer(i18n.t(lang, "daily_limit_hit"), show_alert=True)
        return

    await award_qc(cb.from_user.id, st["reward_qc"])
    await mark_step_completed(cb.from_user.id, st["id"])
    await set_cooldown(cb.from_user.id, chain_id)
    await cb.answer("OK ✅")
    try:
        await cb.message.delete()
    except Exception:
        pass


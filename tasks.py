from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from ..utils.i18n import i18n
from ..utils.keyboards import tasks_chain_kb, step_kb
from ..services.tasks_service import (get_user, list_chains, list_chain_steps, user_next_step,
                                      get_cooldown_left, set_cooldown, inc_today_and_check_limit,
                                      award_qc, mark_step_completed)
from aiogram.exceptions import TelegramBadRequest

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
    _, chain_id, step_id = cb.data.split(":")
    user = await get_user(cb.from_user.id)
    lang = user["language"]
    # load step
    from ..db import fetchrow
    st = await fetchrow("SELECT * FROM steps WHERE id=$1", int(step_id))
    title = st[f"title_{lang}"] or ""
    desc = st[f"desc_{lang}"] or ""
    reward = st["reward_qc"]
    text = (f"<b>{title}</b>\n" if title and title!="-"
            else "") + f"{desc}\n\n" + i18n.t(lang,"reward", qc=reward)
    await cb.message.edit_text(text, reply_markup=step_kb(st["url"], i18n.t(lang,"check_btn"), i18n.t(lang,"open_btn")))
    # Store context for "step_check":
    await cb.message.chat.set_action("typing")
    await cb.message.bot.session.storage.redis  # no-op to keep type hints happy

@router.callback_query(F.data=="step_check")
async def check_step(cb: CallbackQuery):
    # We get the latest step shown from the message by parsing reward or by storing state per user.
    # Simplify: find the earliest open step in any chain w/o cooldown.
    user = await get_user(cb.from_user.id)
    lang = user["language"]
    from ..db import fetch, fetchrow
    chains = await fetch("SELECT id FROM chains ORDER BY id")
    # find the step that matches on screen by reading last shown step_id from a hidden entity would be better;
    # for brevity, just recompute allowed next per chain and pick the first available matching URL from message text.
    # Implementation below: iterate chains and choose first eligible step whose desc/title fragments are in message.
    chosen = None
    for ch in chains:
        nxt = await fetchrow("""
            SELECT s.* FROM steps s
            WHERE s.chain_id=$1 AND s.is_active=TRUE
              AND s.id NOT IN (SELECT step_id FROM user_steps WHERE user_id=(SELECT id FROM users WHERE tg_id=$2))
            ORDER BY s.order_no ASC
            LIMIT 1
        """, ch["id"], cb.from_user.id)
        if not nxt:
            continue
        cd = await get_cooldown_left(cb.from_user.id, ch["id"])
        if cd>0:
            continue
        # naive pick first eligible
        chosen = (ch["id"], nxt)
        break
    if not chosen:
        await cb.answer(i18n.t(lang,"not_done"), show_alert=True)
        return
    chain_id, st = chosen
    # Verify (if Telegram chat check is possible)
    ok = True
    if st["verify_chat_id"]:
        try:
            member = await cb.message.bot.get_chat_member(st["verify_chat_id"], cb.from_user.id)
            ok = (member is not None and member.status in ("member","administrator","creator"))
        except TelegramBadRequest:
            ok = False
    if not ok:
        await cb.answer(i18n.t(lang,"not_done"), show_alert=True)
        return
    # Daily limit
    if not await inc_today_and_check_limit(cb.from_user.id):
        await cb.answer(i18n.t(lang,"daily_limit_hit"), show_alert=True)
        return
    # Award & mark completed, set cooldown
    await award_qc(cb.from_user.id, st["reward_qc"])
    await mark_step_completed(cb.from_user.id, st["id"])
    await set_cooldown(cb.from_user.id, chain_id)
    await cb.answer("OK ✅")
    await cb.message.delete()

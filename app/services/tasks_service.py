from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from ..db import fetch, fetchrow, execute, fetchval
from ..config import settings

KYIV = ZoneInfo(settings.TZ_KYIV)

DAILY_LIMIT = 10
CHAIN_COOLDOWN = timedelta(minutes=30)

async def ensure_user(tg_id: int, referrer_tg: int | None = None):
    # 1) вже існує — віддаємо як є
    row = await fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)
    if row:
        return row

    # 2) знаходимо реферера (якщо він є і це не self)
    ref_id = None
    if referrer_tg and referrer_tg != tg_id:
        ref_id = await fetchval("SELECT id FROM users WHERE tg_id=$1", referrer_tg)

    # 3) Акуратне вставлення: якщо запис уже створився паралельно, просто ігноруємо
    await execute(
        """
        INSERT INTO users (tg_id, referrer_id)
        VALUES ($1, $2)
        ON CONFLICT (tg_id) DO NOTHING
        """,
        tg_id,
        ref_id,
    )

    # 4) Повертаємо актуальний запис
    return await fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)

async def set_language(tg_id: int, lang: str):
    await execute("UPDATE users SET language=$1 WHERE tg_id=$2", lang, tg_id)

async def get_user(tg_id: int):
    return await fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)

async def get_or_create_chain(key: str):
    row = await fetchrow("SELECT * FROM chains WHERE key=$1", key)
    if row:
        return row
    await fetchrow("INSERT INTO chains (key) VALUES ($1) RETURNING id", key)
    return await fetchrow("SELECT * FROM chains WHERE key=$1", key)

async def list_chains():
    return await fetch("SELECT * FROM chains ORDER BY id ASC")

async def list_chain_steps(chain_id: int):
    return await fetch("SELECT * FROM steps WHERE chain_id=$1 AND is_active=TRUE ORDER BY order_no ASC", chain_id)

async def user_next_step(tg_id: int, chain_id: int):
    user = await get_user(tg_id)
    completed_ids = await fetch("SELECT step_id FROM user_steps WHERE user_id=$1", user["id"])
    completed_set = {r["step_id"] for r in completed_ids}
    steps = await list_chain_steps(chain_id)
    for s in steps:
        if s["id"] not in completed_set:
            return s
    return None

async def get_cooldown_left(tg_id: int, chain_id: int) -> float:
    user = await get_user(tg_id)
    st = await fetchrow("SELECT next_available_at FROM user_chain_state WHERE user_id=$1 AND chain_id=$2", user["id"], chain_id)
    now = datetime.now(tz=KYIV)
    if not st:
        return 0
    naa = st["next_available_at"]
    if naa is None or naa <= now:
        return 0
    return (naa - now).total_seconds()

async def set_cooldown(tg_id: int, chain_id: int):
    user = await get_user(tg_id)
    naa = datetime.now(tz=KYIV) + CHAIN_COOLDOWN
    await fetchrow("""
        INSERT INTO user_chain_state (user_id, chain_id, next_available_at)
        VALUES ($1,$2,$3)
        ON CONFLICT (user_id, chain_id) DO UPDATE SET next_available_at=EXCLUDED.next_available_at
    """, user["id"], chain_id, naa)

async def award_qc(tg_id: int, qc: int):
    await execute("""
        UPDATE users SET balance_qc = balance_qc + $1,
                         earned_total_qc = earned_total_qc + $1
        WHERE tg_id=$2
    """, qc, tg_id)

async def mark_step_completed(tg_id: int, step_id: int):
    user_id = await fetchval("SELECT id FROM users WHERE tg_id=$1", tg_id)
    await fetchrow("INSERT INTO user_steps (user_id, step_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", user_id, step_id)

async def inc_today_and_check_limit(tg_id: int) -> bool:
    # returns True if limit is OK (not exceeded)
    now = datetime.now(tz=KYIV)
    today = now.date()
    row = await fetchrow("SELECT today_date, today_count FROM users WHERE tg_id=$1", tg_id)
    if not row:
        return False
    if row["today_date"] != today:
        await execute("UPDATE users SET today_date=$1, today_count=0 WHERE tg_id=$2", today, tg_id)
        count = 0
    else:
        count = row["today_count"]
    if count >= DAILY_LIMIT:
        return False
    await execute("UPDATE users SET today_count=today_count+1, today_date=$1 WHERE tg_id=$2", today, tg_id)
    return True

async def create_invoice(user_id: int, uuid: str, link: str, amount: float):
    await fetchrow("""
        INSERT INTO payments (user_id, uuid, link, amount_usd, status)
        VALUES ($1,$2,$3,$4,'created')
        ON CONFLICT (uuid) DO NOTHING
    """, user_id, uuid, link, amount)

async def set_payment_status(uuid: str, status: str):
    await execute("UPDATE payments SET status=$1, updated_at=NOW() WHERE uuid=$2", status, uuid)

async def get_payment_by_uuid(uuid: str):
    return await fetchrow("SELECT * FROM payments WHERE uuid=$1", uuid)

async def activate_user(tg_id: int):
    await execute("UPDATE users SET status='active' WHERE tg_id=$1", tg_id)

async def award_referral_if_needed(tg_id: int):
    # Award +60 QC to referrer when this user becomes active, once.
    u = await get_user(tg_id)
    if not u or not u["referrer_id"]:
        return
    exists = await fetchrow("SELECT * FROM referral_rewards WHERE referee_id=$1", u["id"])
    if exists:
        return
    ref_qc = 60
    # Add reward
    await execute("UPDATE users SET balance_qc=balance_qc+$1, earned_total_qc=earned_total_qc+$1 WHERE id=$2", ref_qc, u["referrer_id"])
    await fetchrow("INSERT INTO referral_rewards (referrer_id, referee_id, awarded, awarded_at) VALUES ($1,$2,TRUE,NOW())",
                   u["referrer_id"], u["id"])

# app/services/tasks.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Tasks, UserTasks, QCWallets

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

async def daily_completed_count(sess: AsyncSession, user_id: int, tz_offset_minutes: int = 180) -> int:
    """Скільки виконано сьогодні (доба по Києву, UTC+2/+3 = +180 хв як грубо)."""
    # спростимо: віднімемо offset і порівняємо by date
    base = now_utc() + timedelta(minutes=tz_offset_minutes)
    day_start = datetime(base.year, base.month, base.day, tzinfo=base.tzinfo) - timedelta(minutes=tz_offset_minutes)
    return (await sess.execute(
        select(func.count(UserTasks.id)).where(
            UserTasks.user_id==user_id,
            UserTasks.status=="completed",
            UserTasks.completed_at >= day_start
        )
    )).scalar_one()

async def next_tasks_per_chain(sess: AsyncSession, user_id: int) -> list[tuple[str|None, Tasks, datetime|None]]:
    """
    Повертає список (chain_key, task, locked_until) — по ОДНОМУ «наступному» для кожного ланцюга.
    Якщо зараз кулдаун — locked_until != None.
    """
    # беремо всі активні таски
    tasks = (await sess.execute(
        select(Tasks).where(Tasks.is_active==True).order_by(Tasks.created_at.asc(), Tasks.id.asc())
    )).scalars().all()

    by_chain: dict[str|None, list[Tasks]] = defaultdict(list)
    for t in tasks:
        by_chain[t.chain_key].append(t)

    results: list[tuple[str|None, Tasks, datetime|None]] = []
    for chain_key, arr in by_chain.items():
        # скільки вже виконано кроків у цьому ланцюгу
        done_cnt = (await sess.execute(
            select(func.count(UserTasks.id))
            .join(Tasks, Tasks.id==UserTasks.task_id)
            .where(UserTasks.user_id==user_id, UserTasks.status=="completed", Tasks.chain_key==chain_key)
        )).scalar_one()
        if done_cnt >= len(arr):
            continue  # ланцюг завершено

        candidate = arr[done_cnt]  # наступний крок
        ut = (await sess.execute(
            select(UserTasks).where(UserTasks.user_id==user_id, UserTasks.task_id==candidate.id)
        )).scalar_one_or_none()

        if ut and ut.status=="pending" and ut.available_at and ut.available_at > now_utc():
            # кулдаун — ще не можна
            results.append((chain_key, candidate, ut.available_at))
        else:
            # доступно зараз
            results.append((chain_key, candidate, None))

    return results

async def complete_task(sess: AsyncSession, user_id: int, task_id: int) -> None:
    """Позначає task виконаним, нараховує reward, створює pending для наступного з кулдауном."""
    task = (await sess.execute(select(Tasks).where(Tasks.id==task_id))).scalar_one()
    now = now_utc()

    ut = (await sess.execute(
        select(UserTasks).where(UserTasks.user_id==user_id, UserTasks.task_id==task_id)
    )).scalar_one_or_none()

    if ut and ut.status=="completed":
        return  # вже виконаний

    if ut:
        await sess.execute(
            update(UserTasks).where(UserTasks.id==ut.id).values(status="completed", completed_at=now)
        )
    else:
        sess.add(UserTasks(user_id=user_id, task_id=task_id, status="completed", completed_at=now))

    # нараховуємо нагороду
    await sess.execute(
        update(QCWallets)
        .where(QCWallets.user_id==user_id)
        .values(balance_qc=QCWallets.balance_qc + task.reward_qc,
                total_earned_qc=QCWallets.total_earned_qc + task.reward_qc)
    )

    # створюємо pending-замок для наступного кроку (кулдаун)
    if task.chain_key is not None:
        # всі кроки цього ланцюга у порядку
        steps = (await sess.execute(
            select(Tasks).where(Tasks.chain_key==task.chain_key, Tasks.is_active==True).order_by(Tasks.created_at.asc(), Tasks.id.asc())
        )).scalars().all()
        try:
            idx = [t.id for t in steps].index(task_id)
        except ValueError:
            idx = -1
        if idx != -1 and idx+1 < len(steps):
            next_task = steps[idx+1]
            lock_until = now + timedelta(seconds=task.cooldown_sec or 0)
            exists = (await sess.execute(
                select(UserTasks).where(UserTasks.user_id==user_id, UserTasks.task_id==next_task.id)
            )).scalar_one_or_none()
            if not exists:
                sess.add(UserTasks(
                    user_id=user_id, task_id=next_task.id, status="pending", available_at=lock_until
                ))
            else:
                await sess.execute(
                    update(UserTasks).where(UserTasks.id==exists.id).values(status="pending", available_at=lock_until)
                )

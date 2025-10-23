# app/services/tasks.py
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, func, update, and_, exists
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert  # для on_conflict_do_nothing

from ..config import settings
from ..utils import today_kyiv_bounds, now_utc
from ..models import Tasks, UserTasks, QCWallets, TaskStatus


async def completed_today(sess: AsyncSession, user_id: int) -> int:
    """
    Скільки завдань юзер завершив сьогодні (за Europe/Kyiv).
    """
    start, end = today_kyiv_bounds()
    q = (
        select(func.count())
        .select_from(UserTasks)
        .where(
            and_(
                UserTasks.user_id == user_id,
                UserTasks.status == TaskStatus.completed,
                UserTasks.completed_at >= start,
                UserTasks.completed_at < end,
            )
        )
    )
    return (await sess.execute(q)).scalar_one()


async def daily_limit_reached(sess: AsyncSession, user_id: int) -> bool:
    """
    Перевірка денного ліміту (settings.DAILY_TASK_LIMIT).
    """
    return (await completed_today(sess, user_id)) >= settings.DAILY_TASK_LIMIT


async def ensure_wallet(sess: AsyncSession, user_id: int) -> None:
    """
    Переконуємось, що гаманець існує (idempotent).
    """
    stmt = (
        pg_insert(QCWallets)
        .values(user_id=user_id, balance_qc=0, total_earned_qc=0)
        .on_conflict_do_nothing(index_elements=[QCWallets.user_id])
    )
    await sess.execute(stmt)


async def get_pending_available(sess: AsyncSession, user_id: int):
    """
    Повертає перший доступний pending-таск для користувача.
    """
    q = (
        select(Tasks, UserTasks)
        .join(UserTasks, UserTasks.task_id == Tasks.id)
        .where(
            and_(
                UserTasks.user_id == user_id,
                UserTasks.status == TaskStatus.pending,
                (UserTasks.available_at.is_(None)) | (UserTasks.available_at <= now_utc()),
                Tasks.is_active.is_(True),
            )
        )
        .order_by(Tasks.created_at.asc())
        .limit(1)
    )
    return (await sess.execute(q)).first()


async def assign_first_in_chain(sess: AsyncSession, user_id: int):
    """
    Призначає користувачу перший підходящий таск:
    - якщо chain_key: одночасно на юзера 1 pending у цьому chain;
    - якщо одиночний: призначаємо, якщо ще не призначали.
    """
    t = Tasks
    ut = UserTasks

    rows = (
        await sess.execute(select(t).where(t.is_active.is_(True)).order_by(t.created_at.asc()))
    ).scalars().all()

    for task in rows:
        if task.chain_key:
            # Чи є вже pending у цьому chain?
            has_pending_in_chain = (
                await sess.execute(
                    select(
                        exists().where(
                            and_(
                                ut.user_id == user_id,
                                ut.status == TaskStatus.pending,
                                exists(
                                    select(Tasks.id).where(and_(Tasks.id == ut.task_id, Tasks.chain_key == task.chain_key))
                                ),
                            )
                        )
                    )
                )
            ).scalar()

            if has_pending_in_chain:
                continue

            # Чи вже призначали саме цей таск?
            already_assigned = (
                await sess.execute(select(exists().where(and_(ut.user_id == user_id, ut.task_id == task.id))))
            ).scalar()
            if already_assigned:
                continue

            await sess.execute(
                pg_insert(ut)
                .values(user_id=user_id, task_id=task.id, status=TaskStatus.pending, available_at=now_utc())
                .on_conflict_do_nothing(index_elements=[ut.user_id, ut.task_id])
            )
            return task
        else:
            # Одиночний
            already_assigned = (
                await sess.execute(select(exists().where(and_(ut.user_id == user_id, ut.task_id == task.id))))
            ).scalar()
            if not already_assigned:
                await sess.execute(
                    pg_insert(ut)
                    .values(user_id=user_id, task_id=task.id, status=TaskStatus.pending, available_at=now_utc())
                    .on_conflict_do_nothing(index_elements=[ut.user_id, ut.task_id])
                )
                return task

    return None


async def next_task_for_user(sess: AsyncSession, user_id: int):
    """
    Головний «видавач» задач:
    1) якщо денний ліміт — повертаємо (None, "limit");
    2) якщо є доступний pending — повертаємо його;
    3) інакше намагаємось щось призначити (chain-aware).
    """
    if await daily_limit_reached(sess, user_id):
        return None, "limit"

    row = await get_pending_available(sess, user_id)
    if row:
        task, _ = row
        return task, None

    t = await assign_first_in_chain(sess, user_id)
    return (t, None) if t else (None, None)


async def complete_task(sess: AsyncSession, user_id: int, task_id: int) -> None:
    """
    Позначаємо таск як виконаний, нараховуємо 1 QC, і якщо це chain —
    призначаємо наступну копію з cooldown.
    """
    await ensure_wallet(sess, user_id)

    # Позначити completed (тільки якщо був pending)
    await sess.execute(
        update(UserTasks)
        .where(
            and_(
                UserTasks.user_id == user_id,
                UserTasks.task_id == task_id,
                UserTasks.status == TaskStatus.pending,
            )
        )
        .values(status=TaskStatus.completed, completed_at=now_utc())
    )

    # Дістати таск та нарахувати QC
    task = (await sess.execute(select(Tasks).where(Tasks.id == task_id))).scalar_one()
    await sess.execute(
        update(QCWallets)
        .where(QCWallets.user_id == user_id)
        .values(
            balance_qc=QCWallets.balance_qc + task.reward_qc,
            total_earned_qc=QCWallets.total_earned_qc + task.reward_qc,
        )
    )

    # Якщо є ланцюг — призначити наступну копію з cooldown
    if task.chain_key and task.cooldown_sec > 0:
        nxt = (
            await sess.execute(
                select(Tasks)
                .where(
                    and_(
                        Tasks.chain_key == task.chain_key,
                        Tasks.is_active.is_(True),
                        ~exists(
                            select(UserTasks.id).where(
                                and_(UserTasks.user_id == user_id, UserTasks.task_id == Tasks.id)
                            )
                        ),
                    )
                )
                .order_by(Tasks.created_at.asc())
                .limit(1)
            )
        ).scalar()

        if nxt:
            await sess.execute(
                pg_insert(UserTasks)
                .values(
                    user_id=user_id,
                    task_id=nxt.id,
                    status=TaskStatus.pending,
                    available_at=now_utc() + timedelta(seconds=task.cooldown_sec),
                )
                .on_conflict_do_nothing(index_elements=[UserTasks.user_id, UserTasks.task_id])
            )

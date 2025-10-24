# app/services/referrals.py
from __future__ import annotations
from sqlalchemy import select, insert, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import Users, Referrals, QCWallets

BONUS_QC = 60  # 30 центів

async def grant_referral_bonus(sess: AsyncSession, referee_id: int) -> None:
    """
    Дає рефереру +60 QC, якщо:
    - у реферала (referee_id) є referrer_id,
    - бонус ще НЕ видавався (ідемпотентність через unique на referee_id та ON CONFLICT DO NOTHING).
    """
    # дізнаємось реферера
    referrer_id = (
        await sess.execute(select(Users.referrer_id).where(Users.id == referee_id))
    ).scalar_one_or_none()
    if not referrer_id:
        return

    # запис у referrals (unique по referee_id, щоб не дублювати)
    ins = pg_insert(Referrals).values(referrer_id=referrer_id, referee_id=referee_id)
    ins = ins.on_conflict_do_nothing(index_elements=[Referrals.referee_id])
    res = await sess.execute(ins)

    # якщо запис не вставився (вже був) — значить бонус уже видавали
    if res.rowcount == 0:
        return

    # переконуємось, що є гаманець
    await sess.execute(
        pg_insert(QCWallets)
        .values(user_id=referrer_id, balance_qc=0, total_earned_qc=0)
        .on_conflict_do_nothing(index_elements=[QCWallets.user_id])
    )

    # нараховуємо кошти
    await sess.execute(
        update(QCWallets)
        .where(QCWallets.user_id == referrer_id)
        .values(
            balance_qc=QCWallets.balance_qc + BONUS_QC,
            total_earned_qc=QCWallets.total_earned_qc + BONUS_QC,
        )
    )

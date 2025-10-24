# app/services/referrals.py
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ..models import Users, Referrals, QCWallets

BONUS_QC = 60

async def grant_referral_bonus(sess: AsyncSession, referee_id: int) -> None:
    referrer_id = (await sess.execute(select(Users.referrer_id).where(Users.id==referee_id))).scalar_one_or_none()
    if not referrer_id: return
    ins = pg_insert(Referrals).values(referrer_id=referrer_id, referee_id=referee_id)
    res = await sess.execute(ins.on_conflict_do_nothing(index_elements=[Referrals.referee_id]))
    if res.rowcount == 0: return
    await sess.execute(
        pg_insert(QCWallets).values(user_id=referrer_id, balance_qc=0, total_earned_qc=0)
        .on_conflict_do_nothing(index_elements=[QCWallets.user_id])
    )
    await sess.execute(
        update(QCWallets).where(QCWallets.user_id==referrer_id).values(
            balance_qc=QCWallets.balance_qc+BONUS_QC,
            total_earned_qc=QCWallets.total_earned_qc+BONUS_QC
        )
    )


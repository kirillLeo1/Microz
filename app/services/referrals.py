from sqlalchemy import select, insert, update
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import Users, Referrals, QCWallets

async def grant_referral_bonus(sess: AsyncSession, referee_id: int):
    referrer_id = (await sess.execute(select(Users.referrer_id).where(Users.id == referee_id))).scalar()
    if not referrer_id:
        return
    await sess.execute(insert(Referrals).values(referrer_id=referrer_id, referee_id=referee_id)
                       .prefix_with("ON CONFLICT DO NOTHING"))
    await sess.execute(insert(QCWallets).values(user_id=referrer_id, balance_qc=0, total_earned_qc=0)
                       .prefix_with("ON CONFLICT DO NOTHING"))
    await sess.execute(update(QCWallets).where(QCWallets.user_id == referrer_id)
                     .values(balance_qc=QCWallets.balance_qc + 60,
                             total_earned_qc=QCWallets.total_earned_qc + 60))
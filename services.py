"""Shared business logic (referral settlement) used by handlers and workers.

Referral money is only paid once the referred user's task has passed the
retention check (i.e. the user really stayed in the channel/group). Paying
earlier lets fraudsters farm bonuses with throw-away accounts.
"""
from decimal import Decimal
from sqlalchemy import select

from config import BusinessRules
from database import User, Transaction, ReferralEarning, TaskCompletion, TransactionType


async def _lock_user(session, uid):
    r = await session.execute(select(User).where(User.id == uid).with_for_update())
    return r.scalar_one_or_none()


async def settle_referrals(session, earner: User, tc: TaskCompletion) -> None:
    """Pay the one-time base referral reward (first verified task) and tier bonuses."""
    if not earner.referred_by:
        return

    # 1) one-time base reward, paid when the referred user completes a verified task
    seen = await session.execute(select(ReferralEarning.id).where(
        ReferralEarning.referrer_id == earner.referred_by,
        ReferralEarning.referred_user_id == earner.id,
        ReferralEarning.tier == 0,
    ))
    if seen.first() is None:
        ref = await _lock_user(session, earner.referred_by)
        if ref and not ref.is_blocked:
            base = BusinessRules.REFERRAL_BASE_REWARD
            ref.balance += base
            ref.total_earned += base
            ref.referral_earnings += base
            ref.add_xp(BusinessRules.REFERRAL_XP_BASE)
            tx = Transaction(
                user_id=ref.id, amount=base, type=TransactionType.REFERRAL_REWARD,
                description="Referral bonus", reference_id=tc.id,
                reference_type="completion", balance_after=ref.balance,
            )
            session.add(tx)
            session.add(ReferralEarning(
                referrer_id=ref.id, referred_user_id=earner.id, tier=0,
                source_amount=Decimal("0"), bonus_amount=base,
                xp_bonus=BusinessRules.REFERRAL_XP_BASE, transaction_id=tx.id,
            ))

    # 2) tier bonuses on the verified reward
    current = earner.id
    for tier in (1, 2, 3):
        u = await session.get(User, current)
        if not u or not u.referred_by:
            break
        ref_id = u.referred_by
        pct = BusinessRules.REFERRAL_TIER_BONUSES.get(tier, Decimal("0"))
        if pct > 0:
            bonus = tc.reward * pct
            xp_bonus = int((bonus / 1000) * BusinessRules.XP_PER_1000_COINS)
            ref = await _lock_user(session, ref_id)
            if ref and not ref.is_blocked and bonus > 0:
                ref.balance += bonus
                ref.total_earned += bonus
                ref.referral_earnings += bonus
                ref.add_xp(xp_bonus)
                tx = Transaction(
                    user_id=ref_id, amount=bonus,
                    type=TransactionType.REFERRAL_TIER_BONUS,
                    description=f"Tier {tier} bonus", reference_id=tc.id,
                    reference_type="completion", balance_after=ref.balance,
                )
                session.add(tx)
                session.add(ReferralEarning(
                    referrer_id=ref_id, referred_user_id=earner.id,
                    tier=tier, source_amount=tc.reward, bonus_amount=bonus,
                    xp_bonus=xp_bonus, transaction_id=tx.id,
                ))
        current = ref_id


async def locked_amount(session, uid: int) -> Decimal:
    """Rewards still inside their retention window. They can be revoked, so
    they must not be withdrawable yet."""
    from sqlalchemy import func
    from database import RetentionStatus
    r = await session.execute(select(func.coalesce(func.sum(TaskCompletion.reward), 0)).where(
        TaskCompletion.user_id == uid,
        TaskCompletion.retention_status == RetentionStatus.PENDING,
    ))
    return Decimal(r.scalar() or 0)


async def withdrawable(session, user: User) -> Decimal:
    locked = await locked_amount(session, user.id)
    return max(Decimal("0"), user.balance - locked)


async def pending_withdrawals(session, uid: int) -> int:
    from sqlalchemy import func
    from database import WithdrawalRequest
    r = await session.execute(select(func.count(WithdrawalRequest.id)).where(
        WithdrawalRequest.user_id == uid, WithdrawalRequest.status == "pending"))
    return r.scalar() or 0

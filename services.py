"""Shared business logic (referral settlement) used by handlers and workers.

Referral money is only paid once the referred user's task has passed the
retention check (i.e. the user really stayed in the channel/group). Paying
earlier lets fraudsters farm bonuses with throw-away accounts.
"""
from decimal import Decimal
from sqlalchemy import select

from config import BusinessRules
from datetime import datetime, timezone
from database import (
    User, Transaction, ReferralEarning, TaskCompletion, TransactionType, TaskSubmission,
    Campaign, CampaignStatus, RetentionStatus,
)


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


# ---------------------------------------------------------------------------------------
# Proof-based tasks (post view / bot start / reaction)
# The reward slot is RESERVED when the proof is submitted (campaign.completed_count and
# spent_budget are increased) so the advertiser's escrow can never be oversubscribed.
# ---------------------------------------------------------------------------------------
async def reserve_slot(session, campaign: Campaign) -> bool:
    """Caller must hold a row lock on the campaign."""
    if campaign.status != CampaignStatus.ACTIVE or campaign.is_full:
        return False
    if campaign.spent_budget + campaign.reward_per_user > campaign.total_budget:
        return False
    campaign.completed_count += 1
    campaign.spent_budget += campaign.reward_per_user
    return True


async def release_slot(session, campaign: Campaign) -> None:
    campaign.completed_count = max(0, campaign.completed_count - 1)
    campaign.spent_budget = max(Decimal("0"), campaign.spent_budget - campaign.reward_per_user)
    if campaign.status == CampaignStatus.COMPLETED:
        campaign.status = CampaignStatus.ACTIVE


async def _lock_sub(session, sid):
    r = await session.execute(select(TaskSubmission).where(TaskSubmission.id == sid).with_for_update())
    return r.scalar_one_or_none()


async def approve_submission(session, sid: str, decided_by: int, allowed=("pending", "disputed")):
    """Pay the worker. Idempotent: a submission that is no longer in an allowed state is ignored.
    Returns (ok, reward, worker_id, worker_balance)."""
    sub = await _lock_sub(session, sid)
    if not sub or sub.status not in allowed:
        await session.rollback()
        return False, None, None, None
    c = (await session.execute(select(Campaign).where(Campaign.id == sub.campaign_id).with_for_update())).scalar_one()
    user = (await session.execute(select(User).where(User.id == sub.user_id).with_for_update())).scalar_one_or_none()
    if not user:
        await session.rollback()
        return False, None, None, None
    reward = c.reward_per_user
    xp = int((reward / 1000) * BusinessRules.XP_PER_1000_COINS)
    tc = TaskCompletion(user_id=user.id, campaign_id=c.id, reward=reward, xp_earned=xp,
                        retention_status=RetentionStatus.VERIFIED)
    session.add(tc)
    user.balance += reward
    user.total_earned += reward
    user.completed_tasks_count += 1
    user.add_xp(xp)
    session.add(Transaction(
        user_id=user.id, amount=reward, type=TransactionType.TASK_REWARD,
        description=f"Task: {c.title}"[:255], reference_id=c.id,
        reference_type="campaign", balance_after=user.balance))
    sub.status = "approved"
    sub.decided_by = decided_by
    sub.decided_at = datetime.now(timezone.utc)
    await session.flush()
    await settle_referrals(session, user, tc)
    await _maybe_complete(session, c)
    out = (True, reward, user.id, user.balance)
    await session.commit()
    return out


async def _maybe_complete(session, c: Campaign) -> None:
    """Mark a proof campaign COMPLETED once every slot is paid and nothing is pending."""
    from sqlalchemy import func
    if c.completed_count < c.max_completions:
        return
    open_n = (await session.execute(select(func.count(TaskSubmission.id)).where(
        TaskSubmission.campaign_id == c.id,
        TaskSubmission.status.in_(("pending", "rejected", "disputed"))))).scalar() or 0
    if open_n == 0 and c.status == CampaignStatus.ACTIVE:
        c.status = CampaignStatus.COMPLETED


async def reject_submission(session, sid: str, decided_by: int):
    """Advertiser rejects. Slot stays reserved during the dispute window."""
    sub = await _lock_sub(session, sid)
    if not sub or sub.status != "pending":
        await session.rollback()
        return False
    sub.status = "rejected"
    sub.decided_by = decided_by
    sub.decided_at = datetime.now(timezone.utc)
    await session.commit()
    return True


async def finalize_rejection(session, sid: str, decided_by: int, penalize_worker: bool,
                             allowed=("rejected", "disputed")):
    """Make a rejection final: release the slot back to the campaign."""
    sub = await _lock_sub(session, sid)
    if not sub or sub.status not in allowed:
        await session.rollback()
        return False
    c = (await session.execute(select(Campaign).where(Campaign.id == sub.campaign_id).with_for_update())).scalar_one()
    await release_slot(session, c)
    if c.status == CampaignStatus.CANCELLED:
        # the campaign's budget was already refunded when it was deleted; give this slot back too
        adv = (await session.execute(select(User).where(User.id == c.advertiser_id).with_for_update())).scalar_one_or_none()
        if adv:
            adv.balance += c.reward_per_user
            c.refunded_budget += c.reward_per_user
            session.add(Transaction(
                user_id=adv.id, amount=c.reward_per_user, type=TransactionType.CAMPAIGN_REFUND,
                description=f"Refund (rejected proof): {c.title}"[:255], reference_id=c.id,
                reference_type="campaign", balance_after=adv.balance))
    sub.status = "final_rejected"
    sub.decided_by = decided_by
    sub.decided_at = datetime.now(timezone.utc)
    if penalize_worker:
        u = (await session.execute(select(User).where(User.id == sub.user_id).with_for_update())).scalar_one_or_none()
        if u:
            u.risk_score += 10
    await _maybe_complete(session, c)
    await session.commit()
    return True


def campaign_cost(reward: Decimal, slots: int):
    """(budget, platform fee, total charge) for a campaign."""
    budget = reward * slots
    fee = (budget * BusinessRules.PLATFORM_COMMISSION).to_integral_value(rounding="ROUND_UP")
    return budget, fee, budget + fee

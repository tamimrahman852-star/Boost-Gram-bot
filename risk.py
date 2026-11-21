"""Risk shield: modular signals -> RiskEvent audit trail -> withdrawal hold queue.

Design rules
- Never blocks normal users: the existing hard block (risk_score >= RISK_BLOCK_SCORE) is untouched. The shield only
  routes a withdrawal to the review queue; the admin clears or rejects it.
- Every signal is deduplicated per user+kind for 24 h and expires after RISK_EVENT_DAYS, so one bad hour is not forever.
"""
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select

from config import GrowthRules as G, logger
from database import EarlyLeave, RiskEvent, TaskCompletion, TaskSubmission, User, WithdrawalRequest

SIGNALS = {"fast_completion": 8, "burst": 8, "high_drop": 10, "high_reject": 8, "shared_payout": 20, "referral_farm": 12}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def record(session, uid: int, kind: str, detail: str = "", score: Optional[int] = None) -> bool:
    """Store a signal unless the same kind was already recorded for this user in the last 24 h."""
    since = _now() - timedelta(hours=24)
    if (await session.execute(select(RiskEvent.id).where(RiskEvent.user_id == uid, RiskEvent.kind == kind, RiskEvent.created_at > since))).first():
        return False
    session.add(RiskEvent(user_id=uid, kind=kind, score=SIGNALS.get(kind, 5) if score is None else score, detail=detail[:255]))
    return True


async def event_score(session, uid: int) -> int:
    since = _now() - timedelta(days=G.RISK_EVENT_DAYS)
    return int((await session.execute(select(func.coalesce(func.sum(RiskEvent.score), 0)).where(
        RiskEvent.user_id == uid, RiskEvent.created_at > since))).scalar() or 0)


async def effective_score(session, user: User) -> int:
    return int(user.risk_score or 0) + await event_score(session, user.id)


def payout_fingerprint(details: str) -> str:
    return hashlib.sha256((details or "").strip().lower().encode()).hexdigest()[:24]


async def scan_user(session, user: User) -> list:
    """Evaluate every signal for one user. Returns the kinds that fired (recorded, deduplicated)."""
    uid, now, fired = user.id, _now(), []

    async def fire(kind, detail):
        await record(session, uid, kind, detail)
        fired.append(kind)

    times = [r[0] for r in (await session.execute(select(TaskCompletion.completed_at).where(TaskCompletion.user_id == uid)
                                                   .order_by(TaskCompletion.completed_at.desc()).limit(12))).all()]
    if len(times) >= 6:
        gaps = sorted(abs((times[i] - times[i + 1]).total_seconds()) for i in range(len(times) - 1))
        if gaps[len(gaps) // 2] < G.FAST_GAP_SEC:
            await fire("fast_completion", f"median gap {gaps[len(gaps) // 2]:.1f}s over {len(times)} tasks")
    n10 = (await session.execute(select(func.count(TaskCompletion.id)).where(
        TaskCompletion.user_id == uid, TaskCompletion.completed_at > now - timedelta(minutes=10)))).scalar() or 0
    if n10 >= G.BURST_10MIN:
        await fire("burst", f"{n10} completions in 10 min")
    total = user.completed_tasks_count or 0
    if total >= G.MIN_SAMPLE:
        leaves = (await session.execute(select(func.count(EarlyLeave.id)).where(EarlyLeave.user_id == uid))).scalar() or 0
        if Decimal(leaves) / Decimal(total) >= G.DROP_RATE:
            await fire("high_drop", f"{leaves}/{total} early leaves")
    subs = (await session.execute(select(func.count(TaskSubmission.id)).where(TaskSubmission.user_id == uid))).scalar() or 0
    if subs >= G.MIN_SAMPLE:
        bad = (await session.execute(select(func.count(TaskSubmission.id)).where(
            TaskSubmission.user_id == uid, TaskSubmission.status == "rejected"))).scalar() or 0
        if Decimal(bad) / Decimal(subs) >= G.REJECT_RATE:
            await fire("high_reject", f"{bad}/{subs} proofs rejected")
    if user.referred_by:   # this user belongs to a ring: the referrer got many signups within an hour
        ring = (await session.execute(select(func.count(User.id)).where(
            User.referred_by == user.referred_by, User.created_at > user.created_at - timedelta(hours=1),
            User.created_at <= user.created_at + timedelta(hours=1)))).scalar() or 0
        if ring >= G.REFERRAL_FARM:
            await fire("referral_farm", f"{ring} signups under referrer {user.referred_by} within 2h")
    return fired


async def withdrawal_check(session, user: User, method: str, details: str):
    """-> (hold: bool, reason: str). Called with the user row locked, before the request is stored."""
    reasons = []
    fp = payout_fingerprint(details)
    others = (await session.execute(select(WithdrawalRequest.user_id).where(
        WithdrawalRequest.user_id != user.id, WithdrawalRequest.payment_method == method,
        func.lower(func.trim(WithdrawalRequest.payment_details)) == (details or "").strip().lower()).limit(3))).all()
    if others:
        await record(session, user.id, "shared_payout", f"same {method} destination as user {others[0][0]} (fp {fp[:8]})")
        reasons.append("shared payout destination")
    fired = await scan_user(session, user)
    reasons += [k.replace("_", " ") for k in fired]
    score = await effective_score(session, user)
    if score >= G.RISK_HOLD_SCORE:
        reasons.append(f"risk score {score}")
    hold = score >= G.RISK_HOLD_SCORE
    return hold, "; ".join(dict.fromkeys(reasons))[:250]


async def on_completion(session, user: User) -> None:
    """Cheap hook after every completed task (fast/burst only). Never raises."""
    try:
        times = [r[0] for r in (await session.execute(select(TaskCompletion.completed_at).where(TaskCompletion.user_id == user.id)
                                                       .order_by(TaskCompletion.completed_at.desc()).limit(G.BURST_10MIN + 1))).all()]
        if len(times) > G.BURST_10MIN and (times[0] - times[G.BURST_10MIN]).total_seconds() < 600:
            await record(session, user.id, "burst", f"{G.BURST_10MIN + 1}+ completions in 10 min")
            await session.commit()
    except Exception as e:
        logger.warning(f"risk.on_completion failed: {e}")
        await session.rollback()

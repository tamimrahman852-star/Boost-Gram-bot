import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError,
)

from config import logger, BusinessRules
from datetime import timedelta
from database import (
    AsyncSessionLocal, TaskCompletion, Campaign, User, Transaction, FraudReport, TaskSubmission, AutoRenew, CampaignPost,
    RetentionStatus, CampaignStatus, TransactionType, TaskType
)
from localization import t
from helpers import alert_admins, esc
from services import settle_referrals, approve_submission, finalize_rejection, campaign_cost

CHECK_INTERVAL = 3600  # seconds between retention sweeps


async def _still_member(bot: Bot, target, uid: int) -> bool:
    m = await bot.get_chat_member(target, uid)
    if m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        return True
    if m.status == ChatMemberStatus.RESTRICTED:
        return bool(getattr(m, "is_member", False))
    return False


async def _sweep(bot: Bot) -> int:
    handled = 0
    async with AsyncSessionLocal() as session:
        now = datetime.now(timezone.utc)
        r = await session.execute(select(TaskCompletion).where(
            TaskCompletion.retention_status == RetentionStatus.PENDING,
            TaskCompletion.retention_deadline <= now,
            TaskCompletion.penalty_applied == False,  # noqa: E712
        ).limit(50))
        rows = r.scalars().all()
        for tc in rows:
            c = (await session.execute(select(Campaign).where(Campaign.id == tc.campaign_id))).scalar_one_or_none()
            u = (await session.execute(select(User).where(User.id == tc.user_id).with_for_update())).scalar_one_or_none()
            if not c or not u:
                tc.retention_status = RetentionStatus.VERIFIED
                continue
            target = c.target_chat_id or (f"@{c.target_username}" if c.target_username else None)
            if not target:
                tc.retention_status = RetentionStatus.VERIFIED
                tc.retention_checked_at = now
                await settle_referrals(session, u, tc)
                continue
            try:
                if c.task_type == TaskType.BOOST_7DAY:
                    from handlers.earnings import has_active_boost
                    ok = await has_active_boost(bot, target, u.id)
                else:
                    ok = await _still_member(bot, target, u.id)
            except (TelegramRetryAfter, TelegramNetworkError):
                continue          # temporary problem: retry next sweep, never punish
            except (TelegramBadRequest, TelegramForbiddenError):
                ok = True         # bot lost access => not the user's fault
            handled += 1
            if ok:
                tc.retention_status = RetentionStatus.VERIFIED
                tc.retention_checked_at = now
                await settle_referrals(session, u, tc)
                continue

            penalty = tc.reward
            u.balance -= penalty
            u.total_earned -= penalty
            u.completed_tasks_count = max(0, u.completed_tasks_count - 1)
            u.risk_score += 10
            c.spent_budget = max(0, c.spent_budget - penalty)   # slot is re-opened for another user
            c.completed_count = max(0, c.completed_count - 1)
            if c.status == CampaignStatus.COMPLETED:
                c.status = CampaignStatus.ACTIVE
            elif c.status == CampaignStatus.CANCELLED:
                # budget was already refunded on cancel; the revoked reward goes back too
                adv = (await session.execute(select(User).where(User.id == c.advertiser_id).with_for_update())).scalar_one_or_none()
                if adv:
                    adv.balance += penalty
                    c.refunded_budget += penalty
                    session.add(Transaction(
                        user_id=adv.id, amount=penalty, type=TransactionType.CAMPAIGN_REFUND,
                        description=f"Revoked reward refund: {c.title}"[:255],
                        reference_id=c.id, reference_type="campaign", balance_after=adv.balance,
                    ))
            tc.retention_status = RetentionStatus.PENALIZED
            tc.retention_checked_at = now
            tc.penalty_applied = True
            session.add(Transaction(
                user_id=u.id, amount=-penalty,
                type=TransactionType.PENALTY_REVOKE,
                description=f"Retention fail: {c.title}"[:255],
                reference_id=c.id, reference_type="campaign",
                balance_after=u.balance,
            ))
            session.add(FraudReport(
                user_id=u.id, campaign_id=c.id, completion_id=tc.id,
                report_type="retention_fail", description=f"Left {target} early",
            ))
            auto_blocked = u.risk_score >= BusinessRules.RISK_BLOCK_SCORE
            try:
                await bot.send_message(
                    u.id,
                    t(u.language, "retention_penalty", target=c.target_title or target, reward=penalty),
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await alert_admins(
                bot,
                f"🚨 <b>Retention Penalty</b>\n\n👤 <code>{u.id}</code> ({esc(u.first_name)})\n"
                f"📢 {esc(c.title)}\n💰 -{penalty:,.0f}\n"
                f"⚠️ risk {u.risk_score}" + (" — tasks auto-disabled" if auto_blocked else ""),
            )
        await session.commit()
    return handled


async def _sweep_proofs(bot: Bot) -> None:
    """Auto-approve proofs the advertiser ignored, and make expired rejections final."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as s:
        pending = (await s.execute(select(TaskSubmission.id, TaskSubmission.advertiser_id).where(
            TaskSubmission.status == "pending",
            TaskSubmission.created_at <= now - timedelta(hours=BusinessRules.PROOF_REVIEW_HOURS)).limit(50))).all()
        expired = (await s.execute(select(TaskSubmission.id, TaskSubmission.user_id).where(
            TaskSubmission.status == "rejected",
            TaskSubmission.decided_at <= now - timedelta(hours=BusinessRules.PROOF_DISPUTE_HOURS)).limit(50))).all()
    for sid, adv in pending:
        async with AsyncSessionLocal() as s:
            ok, reward, worker, bal = await approve_submission(s, sid, 0, allowed=("pending",))
        if ok:
            for uid, msg in ((worker, f"✅ Proof approved automatically. +{reward:,.0f} GRAM"),
                             (adv, "ℹ️ A proof you did not review within the deadline was approved automatically.")):
                try:
                    await bot.send_message(uid, msg)
                except Exception:
                    pass
    for sid, worker in expired:
        async with AsyncSessionLocal() as s:
            ok = await finalize_rejection(s, sid, 0, penalize_worker=False, allowed=("rejected",))
        if ok:
            try:
                await bot.send_message(worker, "ℹ️ The dispute window has ended — the rejection of your proof is final.")
            except Exception:
                pass


async def _sweep_auto_renew(bot: Bot) -> None:
    """Re-publish completed campaigns that the owner opted in to auto-renew."""
    from config import BusinessRules as BR
    async with AsyncSessionLocal() as s:
        ids = [r[0] for r in (await s.execute(
            select(AutoRenew.campaign_id).join(Campaign, Campaign.id == AutoRenew.campaign_id)
            .where(AutoRenew.renewals_left > 0, Campaign.status == CampaignStatus.COMPLETED).limit(25))).all()]
    for cid in ids:
        msg = None
        owner_id = None
        async with AsyncSessionLocal() as s:
            ar = (await s.execute(select(AutoRenew).where(AutoRenew.campaign_id == cid).with_for_update())).scalar_one_or_none()
            c = (await s.execute(select(Campaign).where(Campaign.id == cid).with_for_update())).scalar_one_or_none()
            if not ar or ar.renewals_left <= 0 or not c or c.status != CampaignStatus.COMPLETED:
                await s.rollback()
                continue
            owner = (await s.execute(select(User).where(User.id == c.advertiser_id).with_for_update())).scalar_one_or_none()
            owner_id = c.advertiser_id
            budget, fee, total = campaign_cost(c.reward_per_user, c.max_completions)
            from sqlalchemy import func as F_
            active_n = (await s.execute(select(F_.count(Campaign.id)).where(
                Campaign.advertiser_id == owner_id,
                Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).scalar() or 0
            clash = False
            if c.target_chat_id is not None:
                clash = (await s.execute(select(Campaign.id).where(
                    Campaign.target_chat_id == c.target_chat_id,
                    Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).first() is not None
            if not owner or owner.is_blocked or owner.balance < total or active_n >= BR.MAX_ACTIVE_CAMPAIGNS or clash:
                ar.renewals_left = 0
                msg = (f"🔁 Auto-renew stopped for “{c.title}”: not enough balance ({total:,.0f} GRAM needed) or the campaign limit was reached.")
            else:
                new = Campaign(
                    advertiser_id=owner_id, title=c.title, task_type=c.task_type, target_chat_id=c.target_chat_id,
                    target_username=c.target_username, target_link=c.target_link, target_title=c.target_title,
                    premium_only=c.premium_only, reward_per_user=c.reward_per_user, base_reward=c.base_reward,
                    max_completions=c.max_completions, total_budget=budget, retention_days=c.retention_days,
                    requires_retention_check=c.requires_retention_check)
                s.add(new)
                await s.flush()
                post = (await s.execute(select(CampaignPost).where(CampaignPost.campaign_id == c.id))).scalar_one_or_none()
                if post:
                    s.add(CampaignPost(campaign_id=new.id, source_chat_id=post.source_chat_id, source_message_id=post.source_message_id))
                owner.balance -= total
                s.add(Transaction(
                    user_id=owner_id, amount=-total, type=TransactionType.CAMPAIGN_PAYMENT,
                    description=f"Auto-renew: {c.title} (fee {fee:,.0f})"[:255], reference_id=new.id,
                    reference_type="campaign", balance_after=owner.balance))
                left = ar.renewals_left - 1
                ar.renewals_left = 0
                if left > 0:
                    s.add(AutoRenew(campaign_id=new.id, renewals_left=left))
                msg = f"🔁 “{c.title}” finished and was renewed automatically (−{total:,.0f} GRAM). Renewals left: {left}."
            await s.commit()
        if msg and owner_id:
            try:
                await bot.send_message(owner_id, msg)
            except Exception:
                pass


async def retention_worker(bot: Bot):
    while True:
        try:
            await _sweep(bot)
            await _sweep_proofs(bot)
            await _sweep_auto_renew(bot)
            await asyncio.sleep(CHECK_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Retention worker error: {e}")
            await asyncio.sleep(300)

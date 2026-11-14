import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError,
)

from config import logger, BusinessRules
from database import (
    AsyncSessionLocal, TaskCompletion, Campaign, User, Transaction, FraudReport,
    RetentionStatus, CampaignStatus, TransactionType
)
from localization import t
from helpers import alert_admins, esc
from services import settle_referrals

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


async def retention_worker(bot: Bot):
    while True:
        try:
            await _sweep(bot)
            await asyncio.sleep(CHECK_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Retention worker error: {e}")
            await asyncio.sleep(300)

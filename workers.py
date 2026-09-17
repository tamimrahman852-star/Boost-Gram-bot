import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from aiogram import Bot
from aiogram.enums import ChatMemberStatus

from config import logger
from database import (
    AsyncSessionLocal, TaskCompletion, Campaign, User, Transaction, FraudReport,
    RetentionStatus, CampaignStatus, TransactionType
)
from localization import t
from helpers import alert_admins, esc


async def retention_worker(bot: Bot):
    while True:
        try:
            await asyncio.sleep(6 * 3600)
            async with AsyncSessionLocal() as session:
                now = datetime.now(timezone.utc)
                r = await session.execute(select(TaskCompletion).where(
                    TaskCompletion.retention_status == RetentionStatus.PENDING,
                    TaskCompletion.retention_deadline <= now,
                    TaskCompletion.penalty_applied == False,
                ).limit(50))
                rows = r.scalars().all()
                for tc in rows:
                    cr = await session.execute(select(Campaign).where(Campaign.id == tc.campaign_id))
                    c = cr.scalar_one_or_none()
                    ur = await session.execute(select(User).where(User.id == tc.user_id))
                    u = ur.scalar_one_or_none()
                    if not c or not u:
                        tc.retention_status = RetentionStatus.PENALIZED
                        continue
                    target = c.target_chat_id or (f"@{c.target_username}" if c.target_username else None)
                    if not target:
                        tc.retention_status = RetentionStatus.VERIFIED
                        continue
                    try:
                        m = await bot.get_chat_member(target, u.id)
                        ok = m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                                          ChatMemberStatus.CREATOR, ChatMemberStatus.RESTRICTED)
                    except Exception:
                        ok = False
                    if ok:
                        tc.retention_status = RetentionStatus.VERIFIED
                        tc.retention_checked_at = now
                    else:
                        penalty = tc.reward
                        u.balance -= penalty
                        u.total_earned -= penalty
                        u.completed_tasks_count = max(0, u.completed_tasks_count - 1)
                        u.risk_score += 10
                        c.spent_budget -= penalty
                        c.completed_count = max(0, c.completed_count - 1)
                        c.refunded_budget += penalty
                        if c.status == CampaignStatus.COMPLETED and not c.is_full:
                            c.status = CampaignStatus.ACTIVE
                        tc.retention_status = RetentionStatus.PENALIZED
                        tc.penalty_applied = True
                        session.add(Transaction(
                            user_id=u.id, amount=-penalty,
                            type=TransactionType.PENALTY_REVOKE,
                            description=f"Retention fail: {c.title}",
                            reference_id=c.id, reference_type="campaign",
                            balance_after=u.balance,
                        ))
                        session.add(FraudReport(
                            user_id=u.id, campaign_id=c.id, completion_id=tc.id,
                            report_type="retention_fail",
                            description=f"Left {target} early",
                        ))
                        try:
                            await bot.send_message(u.id,
                                t(u.language, "retention_penalty",
                                  target=c.target_title or target, reward=penalty),
                                parse_mode="HTML")
                        except Exception:
                            pass
                        await alert_admins(bot,
                            f"🚨 <b>Retention Penalty</b>\n\n"
                            f"👤 <code>{u.id}</code> ({esc(u.first_name)})\n"
                            f"📢 {esc(c.title)}\n💰 -{penalty:,.0f}")
                await session.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Retention worker error: {e}")
            await asyncio.sleep(300)

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import select, func
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import BOT_USERNAME, BusinessRules, logger
from database import (
    User, Campaign, TaskCompletion, Transaction, ReferralEarning,
    CampaignStatus, TaskType, RetentionStatus, TransactionType, VERIFIABLE_TASKS, redis_client
)
from localization import L10N, t
from keyboards import category_kb, task_detail_kb
from helpers import esc

router = Router()


async def get_user(session, uid):
    r = await session.execute(select(User).where(User.id == uid))
    return r.scalar_one_or_none()


@router.message(F.text.in_([L10N.T["en"]["menu_earnings"], L10N.T["bn"]["menu_earnings"]]))
async def earnings_cmd(message: Message, session):
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer(t("en", "not_started"))
        return
    await _render_categories(message, session, user, edit=False)


async def _render_categories(message, session, user, edit=False):
    counts = {}
    for key, tt in [
        ("channel", TaskType.CHANNEL_SUB),
        ("group", TaskType.GROUP_JOIN),
        ("post", TaskType.POST_VIEW),
        ("bot", TaskType.BOT_START),
        ("reaction", TaskType.REACTION),
        ("boost", TaskType.BOOST_7DAY),
    ]:
        r = await session.execute(
            select(func.count(Campaign.id)).where(
                Campaign.task_type == tt,
                Campaign.status == CampaignStatus.ACTIVE,
                Campaign.completed_count < Campaign.max_completions,
            )
        )
        counts[key] = r.scalar() or 0

    text = "💰 <b>Select Task Category</b>\n\nComplete tasks to earn GRAM coins:"
    kb = category_kb(user.language, counts)
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "earn:rules")
async def earn_rules(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(
        t(lang, "rules_title") + "\n\n" + t(lang, "rules_forbidden") + "\n\n" + t(lang, "rules_penalty"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="earn:back")]
        ]),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "earn:back")
async def earn_back(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    if not user:
        await query.answer()
        return
    await _render_categories(query.message, session, user, edit=True)
    await query.answer()


@router.callback_query(F.data.startswith("earn:cat:"))
async def earn_category(query: CallbackQuery, session):
    tt = query.data.split(":")[2]
    await _show_task_list(query, session, tt, 0)
    await query.answer()


async def _show_task_list(query, session, task_type, page):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"

    done_sub = select(TaskCompletion.campaign_id).where(TaskCompletion.user_id == query.from_user.id)
    base = [
        Campaign.status == CampaignStatus.ACTIVE,
        Campaign.completed_count < Campaign.max_completions,
        Campaign.id.not_in(done_sub),
    ]

    # Fixed: Safely convert task_type string to TaskType Enum only when non-empty
    if task_type:
        try:
            enum_tt = TaskType(task_type) if isinstance(task_type, str) else task_type
            base.append(Campaign.task_type == enum_tt)
        except ValueError:
            pass  # Empty or invalid string skip filter

    if not (user and user.is_premium):
        base.append(Campaign.premium_only == False)

    total_res = await session.execute(select(func.count(Campaign.id)).where(*base))
    total = total_res.scalar() or 0
    pages = max(1, (total + BusinessRules.TASKS_PAGE_SIZE - 1) // BusinessRules.TASKS_PAGE_SIZE)

    stmt = (
        select(Campaign)
        .where(*base)
        .order_by(Campaign.created_at.desc())
        .offset(page * BusinessRules.TASKS_PAGE_SIZE)
        .limit(BusinessRules.TASKS_PAGE_SIZE)
    )
    r = await session.execute(stmt)
    items = r.scalars().all()

    buttons = []
    for c in items:
        star = "⭐ " if c.premium_only else ""
        buttons.append([InlineKeyboardButton(
            text=f"{star}{c.title[:30]} · +{c.reward_per_user:,.0f}",
            callback_data=f"earn:view:{c.id}",
        )])

    if items:
        if pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"earn:page:{task_type}:{page-1}"))
            nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="noop"))
            if page < pages - 1:
                nav.append(InlineKeyboardButton(text="➡️", callback_data=f"earn:page:{task_type}:{page+1}"))
            buttons.append(nav)

    buttons.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="earn:back")])
    text = f"🎯 <b>Tasks</b> ({total})" if items else t(lang, "no_tasks")
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await query.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("earn:page:"))
async def earn_page(query: CallbackQuery, session):
    _, _, tt, p = query.data.split(":")
    await _show_task_list(query, session, tt, int(p))
    await query.answer()


@router.callback_query(F.data.startswith("earn:view:"))
async def earn_view(query: CallbackQuery, session):
    cid = query.data.split(":")[2]
    r = await session.execute(select(Campaign).where(Campaign.id == cid))
    c = r.scalar_one_or_none()
    if not c or c.status != CampaignStatus.ACTIVE:
        await query.answer("Unavailable", show_alert=True)
        return
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"

    link = c.target_link or (f"https://t.me/{c.target_username}" if c.target_username else f"https://t.me/{BOT_USERNAME}")
    tag = "⭐ <b>PREMIUM ONLY</b>\n\n" if c.premium_only else ""
    text = (
        f"{tag}📢 <b>{esc(c.title)}</b>\n\n"
        f"💰 Reward: <code>{c.reward_per_user:,.0f}</code> GRAM\n"
        f"👥 Slots: <code>{c.slots_remaining}</code>\n"
        f"⏰ Retention: {c.retention_days} days"
    )
    await query.message.edit_text(text, reply_markup=task_detail_kb(lang, c.id, link), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data.startswith("earn:verify:"))
async def earn_verify(query: CallbackQuery, session, bot: Bot):
    cid = query.data.split(":")[2]
    uid = query.from_user.id
    lock = f"lock:task:{uid}:{cid}"
    if not await redis_client.set(lock, "1", nx=True, ex=15):
        await query.answer("⏳ Processing...", show_alert=True)
        return
    try:
        ok, msg, current_task_type = await _process_task(session, bot, uid, cid)
        await query.answer(msg[:200], show_alert=True)
        if ok:
            # Fixed: Pass the campaign's task type string back instead of empty string ""
            await _show_task_list(query, session, current_task_type or "", 0)
    finally:
        await redis_client.delete(lock)


async def _process_task(session, bot, uid, cid):
    try:
        async with session.begin_nested():
            r = await session.execute(select(Campaign).where(Campaign.id == cid).with_for_update())
            c = r.scalar_one_or_none()
            if not c or c.status != CampaignStatus.ACTIVE:
                return False, "❌ Task inactive.", None
            
            # Save task type value string for redirection
            task_type_val = c.task_type.value if hasattr(c.task_type, "value") else str(c.task_type)

            if c.is_full:
                c.status = CampaignStatus.COMPLETED
                return False, t("en", "task_limit"), task_type_val

            ru = await session.execute(select(User).where(User.id == uid).with_for_update())
            user = ru.scalar_one_or_none()
            if not user or user.is_blocked:
                return False, "❌ Account issue.", task_type_val

            rc = await session.execute(select(TaskCompletion).where(
                TaskCompletion.user_id == uid, TaskCompletion.campaign_id == cid
            ))
            if rc.scalar_one_or_none():
                return False, t(user.language, "task_already"), task_type_val

            if c.premium_only and not user.is_premium:
                return False, t(user.language, "task_premium_only"), task_type_val

            if c.task_type in VERIFIABLE_TASKS:
                target = c.target_chat_id or (f"@{c.target_username}" if c.target_username else None)
                if not target:
                    return False, "❌ Misconfigured.", task_type_val
                try:
                    m = await bot.get_chat_member(target, uid)
                    if m.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                                        ChatMemberStatus.CREATOR, ChatMemberStatus.RESTRICTED):
                        return False, t(user.language, "task_failed"), task_type_val
                except (TelegramBadRequest, TelegramForbiddenError):
                    return False, t(user.language, "task_failed"), task_type_val

            reward = c.reward_per_user
            xp = int((reward / 1000) * BusinessRules.XP_PER_1000_COINS)

            deadline = None
            if c.requires_retention_check and c.task_type in VERIFIABLE_TASKS:
                deadline = datetime.now(timezone.utc) + timedelta(days=c.retention_days)

            session.add(TaskCompletion(
                user_id=uid, campaign_id=cid, reward=reward, xp_earned=xp,
                retention_status=RetentionStatus.PENDING if deadline else RetentionStatus.VERIFIED,
                retention_deadline=deadline,
            ))

            user.balance += reward
            user.total_earned += reward
            user.completed_tasks_count += 1
            user.add_xp(xp)

            tx = Transaction(
                user_id=uid, amount=reward, type=TransactionType.TASK_REWARD,
                description=f"Task: {c.title}", reference_id=cid,
                reference_type="campaign", balance_after=user.balance,
            )
            session.add(tx)

            c.completed_count += 1
            c.spent_budget += reward
            if c.is_full:
                c.status = CampaignStatus.COMPLETED

            await _referral_tiers(session, uid, reward, tx.id)

        await session.commit()
        return True, t(user.language, "task_verified", reward=reward, xp=xp), task_type_val
    except Exception as e:
        await session.rollback()
        logger.exception(f"Task error: {e}")
        return False, t("en", "error_generic"), None


async def _referral_tiers(session, earner_id, amount, tx_id):
    current = earner_id
    for tier in (1, 2, 3):
        ru = await session.execute(select(User).where(User.id == current))
        u = ru.scalar_one_or_none()
        if not u or not u.referred_by:
            break
        ref_id = u.referred_by
        pct = BusinessRules.REFERRAL_TIER_BONUSES.get(tier, Decimal("0"))
        if pct > 0:
            bonus = amount * pct
            xp_bonus = int((bonus / 1000) * BusinessRules.XP_PER_1000_COINS)
            rr = await session.execute(select(User).where(User.id == ref_id).with_for_update())
            ref = rr.scalar_one_or_none()
            if ref and not ref.is_blocked:
                ref.balance += bonus
                ref.total_earned += bonus
                ref.referral_earnings += bonus
                ref.add_xp(xp_bonus)
                session.add(Transaction(
                    user_id=ref_id, amount=bonus,
                    type=TransactionType.REFERRAL_TIER_BONUS,
                    description=f"Tier {tier} bonus",
                    reference_id=tx_id, reference_type="referral_bonus",
                    balance_after=ref.balance,
                ))
                session.add(ReferralEarning(
                    referrer_id=ref_id, referred_user_id=earner_id,
                    tier=tier, source_amount=amount, bonus_amount=bonus,
                    xp_bonus=xp_bonus, transaction_id=tx_id,
                ))
        current = ref_id

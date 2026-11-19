import asyncio
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import select, func
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError,
)

from config import BOT_USERNAME, BusinessRules, logger
from database import (
    User, Campaign, TaskCompletion, Transaction, ReferralEarning, FraudReport, TaskSubmission, CampaignPost, AsyncSessionLocal,
    CampaignStatus, TaskType, RetentionStatus, TransactionType, VERIFIABLE_TASKS, redis_client
)
from i18n_filters import MenuText
from localization import L10N, t
from keyboards import category_kb, task_detail_kb
from helpers import esc, L, alert_admins
from services import settle_referrals
from handlers.retention import block_if_held

router = Router()


async def get_user(session, uid):
    r = await session.execute(select(User).where(User.id == uid))
    return r.scalar_one_or_none()


@router.message(MenuText("menu_earnings"))
async def earnings_cmd(message: Message, session):
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer(t("en", "not_started"))
        return
    if await block_if_held(message, session, user):
        return
    await _render_categories(message, session, user, edit=False)


async def _render_categories(message, session, user, edit=False):
    counts = {}
    for key, tt in [
        ("channel", TaskType.CHANNEL_SUB),
        ("group", TaskType.GROUP_JOIN),
        ("view", TaskType.POST_VIEW),
        ("bot", TaskType.BOT_START),
        ("reaction", TaskType.REACTION),
        ("boost", TaskType.BOOST_7DAY),
    ]:
        r = await session.execute(
            select(func.count(Campaign.id)).where(
                Campaign.task_type == tt,
                Campaign.status == CampaignStatus.ACTIVE,
                Campaign.completed_count < Campaign.max_completions,
                Campaign.advertiser_id != user.id,
            )
        )
        counts[key] = r.scalar() or 0

    text = t(user.language, "earn_choose")
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
    if await block_if_held(query, session, await get_user(session, query.from_user.id)):
        return
    await _show_task_list(query, session, tt, 0)
    await query.answer()


def _task_filters(uid: int, user, task_type):
    done_sub = select(TaskCompletion.campaign_id).where(TaskCompletion.user_id == uid)
    sub_sub = select(TaskSubmission.campaign_id).where(TaskSubmission.user_id == uid)
    base = [
        Campaign.status == CampaignStatus.ACTIVE,
        Campaign.completed_count < Campaign.max_completions,
        Campaign.id.not_in(done_sub),
        Campaign.id.not_in(sub_sub),
        Campaign.advertiser_id != uid,
        Campaign.task_type.in_([TaskType(x) for x in BusinessRules.EARNABLE_TASK_TYPES]),
    ]
    if task_type:
        try:
            base.append(Campaign.task_type == (TaskType(task_type) if isinstance(task_type, str) else task_type))
        except ValueError:
            pass
    if not (user and user.is_premium):
        base.append(Campaign.premium_only == False)  # noqa: E712
    return base


async def _page_items(session, uid, user, task_type, page):
    base = _task_filters(uid, user, task_type)
    total = (await session.execute(select(func.count(Campaign.id)).where(*base))).scalar() or 0
    size = BusinessRules.TASKS_PAGE_SIZE
    pages = max(1, (total + size - 1) // size)
    page = min(max(page, 0), pages - 1)
    items = (await session.execute(
        select(Campaign).where(*base).order_by(Campaign.reward_per_user.desc(), Campaign.created_at.desc())
        .offset(page * size).limit(size))).scalars().all()
    return items, total, pages, page


def _open_link(c) -> str:
    if c.task_type == TaskType.BOOST_7DAY and c.target_username:
        return f"https://t.me/{c.target_username}?boost"
    link = c.target_link or (f"https://t.me/{c.target_username}" if c.target_username else None)
    return link if link and link.startswith("http") else f"https://t.me/{BOT_USERNAME}"


async def _show_task_list(query, session, task_type, page):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    items, total, pages, page = await _page_items(session, query.from_user.id, user, task_type, page)

    buttons = []
    VERBS = {
        TaskType.CHANNEL_SUB: ("সাবস্ক্রাইব", "Subscribe"), TaskType.GROUP_JOIN: ("জয়েন", "Join"),
        TaskType.POST_VIEW: ("দেখুন", "View"), TaskType.BOT_START: ("স্টার্ট", "Start"),
        TaskType.REACTION: ("রিঅ্যাক্ট", "React"), TaskType.BOOST_7DAY: ("বুস্ট", "Boost"),
    }
    for c in items:
        bn, en = VERBS.get(c.task_type, ("খুলুন", "Open"))
        star = "⭐ " if c.premium_only else ""
        if c.task_type == TaskType.POST_VIEW:
            buttons.append([InlineKeyboardButton(
                text=f"👁 {star}{L(lang, 'পোস্ট দেখুন', 'View post')} +{c.reward_per_user:,.0f} GRAM", callback_data=f"earn:pv:{c.id}")])
            continue
        open_btn = InlineKeyboardButton(text=f"💲 {star}+{c.reward_per_user:,.0f} | {L(lang, bn, en)} ↗", url=_open_link(c))
        if c.task_type in (TaskType.POST_VIEW, TaskType.BOT_START, TaskType.REACTION):
            second = InlineKeyboardButton(text="📸 " + L(lang, "প্রমাণ", "Proof"), callback_data=f"earn:proof:{c.id}")
        else:
            second = InlineKeyboardButton(text="🔄 " + L(lang, "যাচাই", "Check"), callback_data=f"earn:verify:{c.id}")
        buttons.append([open_btn, second])
    if items:
        if pages > 1:
            buttons.append([
                InlineKeyboardButton(text="1", callback_data=f"earn:page:{task_type}:0"),
                InlineKeyboardButton(text="<", callback_data=f"earn:page:{task_type}:{max(page - 1, 0)}"),
                InlineKeyboardButton(text=str(page + 1), callback_data="noop"),
                InlineKeyboardButton(text=">", callback_data=f"earn:page:{task_type}:{min(page + 1, pages - 1)}"),
                InlineKeyboardButton(text=str(pages), callback_data=f"earn:page:{task_type}:{pages - 1}"),
            ])
        buttons.append([InlineKeyboardButton(text="🚩 " + L(lang, "রিপোর্ট", "Report"), callback_data=f"earn:rpt:{task_type}:{page}")])
    buttons.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="earn:back")])
    text = (
        L(lang, "🎯 <b>উপলব্ধ টাস্ক</b>\n\nবাটনে চেপে কাজটি করুন, তারপর <b>যাচাই</b> বা <b>প্রমাণ</b> (স্ক্রিনশট) চাপুন।\n"
                f"⚠️ {BusinessRules.RETENTION_DAYS} দিনের আগে চ্যানেল/গ্রুপ ছাড়বেন না। নাহলে টাস্ক সম্পন্ন করা ব্লক হবে এবং তা থেকে অর্জিত GRAM বাতিল হবে।",
                "🎯 <b>Available tasks</b>\n\nTap the button to do the task, then press <b>Check</b> or <b>Proof</b> (screenshot).\n"
                f"⚠️ Don't leave channels/groups earlier than {BusinessRules.RETENTION_DAYS} days. Otherwise task completion will be blocked and the GRAM earned from them will be revoked.",
                _tpl="🎯 <b>Available tasks</b>\n\nTap the button to do the task, then press <b>Check</b> or <b>Proof</b> (screenshot).\n"
                     "⚠️ Don't leave channels/groups earlier than {v0} days. Otherwise task completion will be blocked and the GRAM earned from them will be revoked.",
                _kw={"v0": BusinessRules.RETENTION_DAYS})
        if items else t(lang, "no_tasks")
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await query.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("earn:page:"))
async def earn_page(query: CallbackQuery, session):
    if await block_if_held(query, session, await get_user(session, query.from_user.id)):
        return
    try:
        _, _, tt, p = query.data.split(":")
        await _show_task_list(query, session, tt, int(p))
    except ValueError:
        pass
    await query.answer()


@router.callback_query(F.data == "earn:soon")
async def earn_soon(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.answer(L(lang, "🔜 এই ধরনের টাস্ক নিরাপদে যাচাই করার ব্যবস্থা তৈরি হলে চালু হবে।",
                         "🔜 This task type will open once it can be verified securely."), show_alert=True)


@router.callback_query(F.data.startswith("earn:rpt:"))
async def earn_report_menu(query: CallbackQuery, session):
    try:
        _, _, tt, p = query.data.split(":")
        page = int(p)
    except ValueError:
        await query.answer()
        return
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    items, _, _, page = await _page_items(session, query.from_user.id, user, tt, page)
    rows = [[InlineKeyboardButton(text=f"🚩 {c.title[:28]} · +{c.reward_per_user:,.0f}", callback_data=f"earn:report:{c.id}")] for c in items]
    rows.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data=f"earn:page:{tt}:{page}")])
    await query.message.edit_text(
        L(lang, "🚩 <b>কোন টাস্কটি রিপোর্ট করবেন?</b>\nশুধু ভুয়া বা বিভ্রান্তিকর টাস্ক রিপোর্ট করুন।",
                "🚩 <b>Which task do you want to report?</b>\nOnly report fake or misleading tasks."),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode=ParseMode.HTML)
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
        + L(lang,
            f"💰 পুরস্কার: <code>{c.reward_per_user:,.0f}</code> GRAM\n"
            f"👥 বাকি স্লট: <code>{c.slots_remaining}</code>\n"
            f"⏰ কমপক্ষে <b>{c.retention_days} দিন</b> থাকতে হবে, নাহলে পুরস্কার বাতিল ও ব্যালেন্স থেকে কাটা হবে।\n\n"
            "১) <b>খুলুন</b> চাপ দিয়ে যোগ দিন\n২) তারপর <b>যাচাই করুন</b> চাপুন",
            f"💰 Reward: <code>{c.reward_per_user:,.0f}</code> GRAM\n"
            f"👥 Slots left: <code>{c.slots_remaining}</code>\n"
            f"⏰ You must stay for <b>{c.retention_days} days</b>, otherwise the reward is revoked.\n\n"
            "1) Tap <b>Open</b> and join\n2) Then tap <b>Verify</b>", _tpl='💰 Reward: <code>{v0:,.0f}</code> GRAM\n👥 Slots left: <code>{v1}</code>\n⏰ You must stay for <b>{v2} days</b>, otherwise the reward is revoked.\n\n1) Tap <b>Open</b> and join\n2) Then tap <b>Verify</b>', _kw={"v0": c.reward_per_user, "v1": c.slots_remaining, "v2": c.retention_days})
    )
    await query.message.edit_text(text, reply_markup=task_detail_kb(lang, c.id, link), parse_mode=ParseMode.HTML)
    await query.answer()


from membership import has_active_boost, is_member as _is_member  # noqa: E402,F401  (kept importable from here)


@router.callback_query(F.data.startswith("earn:verify:"))
async def earn_verify(query: CallbackQuery, session, bot: Bot):
    cid = query.data.split(":")[2]
    uid = query.from_user.id
    if await block_if_held(query, session, await get_user(session, uid)):
        return
    lock = f"lock:task:{uid}:{cid}"
    if not await redis_client.set(lock, "1", nx=True, ex=15):
        await query.answer("⏳", show_alert=False)
        return
    try:
        ok, msg, _tt = await _process_task(session, bot, uid, cid)
        user = await get_user(session, uid)
        lang = user.language if user else "en"
        if ok:
            await query.answer()
            await query.message.answer(msg, parse_mode=ParseMode.HTML)
            await _render_categories(query.message, session, user, edit=False)
        elif msg.startswith("ℹ️") or msg.startswith("📸"):
            await query.answer()
            await query.message.answer(msg)
        else:
            await query.answer(msg[:200], show_alert=True)
    finally:
        await redis_client.delete(lock)


async def _process_task(session, bot, uid, cid):
    """Verify membership with Telegram, then pay. Never pays without real proof."""
    try:
        # ---- stage 1: cheap read-only validation (no locks held during API call) ----
        c = (await session.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
        user = (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if not c or c.status != CampaignStatus.ACTIVE:
            return False, "❌ Task inactive.", None
        task_type_val = c.task_type.value if hasattr(c.task_type, "value") else str(c.task_type)
        if not user:
            return False, t("en", "not_started"), task_type_val
        lang = user.language
        if user.is_blocked or user.risk_score >= BusinessRules.RISK_BLOCK_SCORE:
            return False, L(lang, "❌ আপনার অ্যাকাউন্টে কাজ করা বন্ধ আছে।", "❌ Tasks are disabled for your account."), task_type_val
        if c.advertiser_id == uid:
            return False, L(lang, "❌ নিজের ক্যাম্পেইনে কাজ করা যায় না।", "❌ You cannot complete your own campaign."), task_type_val
        if task_type_val not in BusinessRules.API_VERIFIED_TASK_TYPES + BusinessRules.VIEW_TASK_TYPES:
            return False, L(lang, "📸 এই টাস্কে স্ক্রিনশট প্রমাণ দিন।", "📸 Send a screenshot proof for this task."), task_type_val
        if c.premium_only and not user.is_premium:
            return False, t(lang, "task_premium_only"), task_type_val
        if c.is_full:
            return False, t(lang, "task_limit"), task_type_val
        done = (await session.execute(select(TaskCompletion.id).where(
            TaskCompletion.user_id == uid, TaskCompletion.campaign_id == cid))).first()
        if done:
            return False, t(lang, "task_already"), task_type_val
        day_key = f"dcap:{uid}"
        used = int(await redis_client.get(day_key) or 0)
        if used >= BusinessRules.MAX_TASKS_PER_DAY:
            return False, L(lang, "⏳ আজকের টাস্কের সীমা শেষ। কাল আবার চেষ্টা করুন।", "⏳ Daily task limit reached. Try again tomorrow."), task_type_val

        view_key = f"pv:{uid}:{cid}"
        if task_type_val == "post_view":
            # The "proof" is that the bot really forwarded the post to this user a few seconds ago.
            started = await redis_client.get(view_key)
            if not started:
                return False, L(lang, "❌ আগে পোস্টটি দেখুন।", "❌ Open the post first."), task_type_val
            if time.time() - float(started) < BusinessRules.VIEW_DWELL_SECONDS:
                return False, L(lang, "⏳ আরেকটু অপেক্ষা করুন।", "⏳ Please wait a moment."), task_type_val
            target = "view"
        else:
            target = c.target_chat_id or (f"@{c.target_username}" if c.target_username else None)
        if not target:
            c.status = CampaignStatus.PAUSED
            await session.commit()
            return False, "❌ Misconfigured.", task_type_val

        # ---- real Telegram verification ----
        try:
            if task_type_val == "post_view":
                member = True
            elif task_type_val == "boost_7day":
                member = await has_active_boost(bot, target, uid)
            else:
                member = await _is_member(bot, target, uid)
        except (TelegramRetryAfter, TelegramNetworkError):
            return False, L(lang, "⏳ টেলিগ্রাম ব্যস্ত, একটু পরে আবার চেষ্টা করুন।", "⏳ Telegram is busy, please retry shortly."), task_type_val
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            # Bot lost access to the chat => advertiser problem, not the user's fault.
            logger.warning(f"Campaign {cid} cannot be verified ({e}); pausing it")
            c.status = CampaignStatus.PAUSED
            await session.commit()
            await alert_admins(bot, f"⚠️ Campaign <code>{esc(cid)}</code> paused: bot cannot verify membership.")
            return False, L(lang, "⚠️ এই টাস্কটি সাময়িকভাবে বন্ধ। অন্য টাস্ক চেষ্টা করুন।", "⚠️ This task is temporarily unavailable."), task_type_val
        if not member:
            if task_type_val == "boost_7day":
                return False, L(lang, "ℹ️ আপনি এখনো এই চ্যানেল/গ্রুপে বুস্ট করেননি। বুস্ট করে আবার চেষ্টা করুন।",
                                "ℹ️ You have not boosted this channel/group yet. Boost it and try again."), task_type_val
            return False, t(lang, "task_failed"), task_type_val

        # ---- stage 2: lock rows and pay atomically ----
        c = (await session.execute(select(Campaign).where(Campaign.id == cid).with_for_update())).scalar_one()
        user = (await session.execute(select(User).where(User.id == uid).with_for_update())).scalar_one()
        if c.status != CampaignStatus.ACTIVE or c.is_full:
            await session.rollback()
            return False, t(lang, "task_limit"), task_type_val
        if user.is_blocked:
            await session.rollback()
            return False, "❌ Account issue.", task_type_val
        if (await session.execute(select(TaskCompletion.id).where(
                TaskCompletion.user_id == uid, TaskCompletion.campaign_id == cid))).first():
            await session.rollback()
            return False, t(lang, "task_already"), task_type_val

        reward = c.reward_per_user
        if c.spent_budget + reward > c.total_budget:
            c.status = CampaignStatus.COMPLETED
            await session.commit()
            return False, t(lang, "task_limit"), task_type_val
        xp = int((reward / 1000) * BusinessRules.XP_PER_1000_COINS)

        deadline = None
        if c.requires_retention_check:
            deadline = datetime.now(timezone.utc) + timedelta(days=c.retention_days)

        tc = TaskCompletion(
            user_id=uid, campaign_id=cid, reward=reward, xp_earned=xp,
            retention_status=RetentionStatus.PENDING if deadline else RetentionStatus.VERIFIED,
            retention_deadline=deadline,
        )
        session.add(tc)
        user.balance += reward
        user.total_earned += reward
        user.completed_tasks_count += 1
        user.add_xp(xp)
        session.add(Transaction(
            user_id=uid, amount=reward, type=TransactionType.TASK_REWARD,
            description=f"Task: {c.title}"[:255], reference_id=cid,
            reference_type="campaign", balance_after=user.balance,
        ))
        c.completed_count += 1
        c.spent_budget += reward
        if c.is_full:
            c.status = CampaignStatus.COMPLETED

        if not deadline:
            await session.flush()
            await settle_referrals(session, user, tc)

        await session.commit()

        pipe = redis_client.pipeline()
        pipe.incr(day_key)
        pipe.expire(day_key, 86400)
        pipe.delete(view_key)
        await pipe.execute()
        return True, t(lang, "task_completed", tid=cid[:8].upper(), reward=reward, balance=user.balance), task_type_val
    except Exception as e:
        await session.rollback()
        logger.exception(f"Task error: {e}")
        return False, t("en", "error_generic"), None


@router.callback_query(F.data.startswith("earn:report:"))
async def earn_report(query: CallbackQuery, session):
    cid = query.data.split(":")[2]
    uid = query.from_user.id
    user = await get_user(session, uid)
    lang = user.language if user else "en"
    c = (await session.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
    if not c:
        await query.answer("❌", show_alert=True)
        return
    dup = (await session.execute(select(FraudReport.id).where(
        FraudReport.user_id == uid, FraudReport.campaign_id == cid))).first()
    if dup:
        await query.answer(L(lang, "আপনি ইতিমধ্যে রিপোর্ট করেছেন।", "You already reported this task."), show_alert=True)
        return
    session.add(FraudReport(
        user_id=uid, campaign_id=cid, report_type="task_report",
        description=f"User reported campaign {c.title[:100]}",
    ))
    await session.commit()
    await query.answer(L(lang, "✅ রিপোর্ট জমা হয়েছে, ধন্যবাদ।", "✅ Report submitted, thank you."), show_alert=True)


# ----------------------------------------------------------------------------------------
# Post views: forwarded into the bot, paid automatically after a short dwell time
# ----------------------------------------------------------------------------------------
def _view_result_kb(lang: str, cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ " + L(lang, "পরবর্তী পোস্ট", "Next post"), callback_data="earn:pvnext")],
        [InlineKeyboardButton(text="🚩 " + L(lang, "রিপোর্ট", "Report"), callback_data=f"earn:report:{cid}")],
        [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="earn:back")],
    ])


async def _finish_view(bot: Bot, uid: int, cid: str, chat_id: int, lang: str):
    """Runs a few seconds after the post was forwarded: pays the reward (own DB session)."""
    try:
        await asyncio.sleep(BusinessRules.VIEW_DWELL_SECONDS + 0.5)
        async with AsyncSessionLocal() as session:
            ok, msg, _ = await _process_task(session, bot, uid, cid)
            if ok:
                c = (await session.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
                reward = c.reward_per_user if c else 0
                user = await get_user(session, uid)
                await bot.send_message(
                    chat_id,
                    L(lang, f"💲 পোস্ট #{cid[:6].upper()} দেখার জন্য আপনি <b>{reward:,.0f} GRAM</b> পেয়েছেন!\n💰 আপনার ব্যালেন্স: <b>{user.balance:,.1f}</b> GRAM",
                            f"💲 You earned <b>{reward:,.0f} GRAM</b> for viewing post #{cid[:6].upper()}!\n💰 Your balance: <b>{user.balance:,.1f}</b> GRAM", _tpl='💲 You earned <b>{v0:,.0f} GRAM</b> for viewing post #{v1}!\n💰 Your balance: <b>{v2:,.1f}</b> GRAM', _kw={"v0": reward, "v1": cid[:6].upper(), "v2": user.balance}),
                    reply_markup=_view_result_kb(lang, cid), parse_mode=ParseMode.HTML)
            elif not msg.startswith("❌ আগে") and "Open the post first" not in msg:
                await bot.send_message(chat_id, msg, reply_markup=_view_result_kb(lang, cid))
    except Exception as e:
        logger.warning(f"view payout failed for {uid}/{cid}: {e}")


async def _start_view(query: CallbackQuery, session, bot: Bot, cid: str):
    uid = query.from_user.id
    user = await get_user(session, uid)
    if not user:
        await query.answer()
        return
    if await block_if_held(query, session, user):
        return
    lang = user.language
    if not await redis_client.set(f"pvcool:{uid}", "1", nx=True, ex=max(1, int(BusinessRules.VIEW_COOLDOWN_SECONDS))):
        await query.answer("⏳", show_alert=False)
        return
    c = (await session.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
    post = (await session.execute(select(CampaignPost).where(CampaignPost.campaign_id == cid))).scalar_one_or_none()
    problem = None
    if not c or not post or c.status != CampaignStatus.ACTIVE or c.task_type != TaskType.POST_VIEW or c.is_full:
        problem = L(lang, "❌ টাস্কটি সক্রিয় নয়।", "❌ This task is not active.")
    elif user.is_blocked or user.risk_score >= BusinessRules.RISK_BLOCK_SCORE:
        problem = L(lang, "❌ আপনার অ্যাকাউন্টে কাজ করা বন্ধ আছে।", "❌ Tasks are disabled for your account.")
    elif c.advertiser_id == uid:
        problem = L(lang, "❌ নিজের ক্যাম্পেইনে কাজ করা যায় না।", "❌ You cannot complete your own campaign.")
    elif c.premium_only and not user.is_premium:
        problem = L(lang, "⭐ শুধু Telegram Premium ব্যবহারকারীদের জন্য।", "⭐ Telegram Premium users only.")
    elif (await session.execute(select(TaskCompletion.id).where(
            TaskCompletion.user_id == uid, TaskCompletion.campaign_id == cid))).first():
        problem = L(lang, "ℹ️ আপনি এই পোস্ট আগেই দেখেছেন।", "ℹ️ You already viewed this post.")
    elif int(await redis_client.get(f"dcap:{uid}") or 0) >= BusinessRules.MAX_TASKS_PER_DAY:
        problem = L(lang, "⏳ আজকের টাস্কের সীমা শেষ।", "⏳ Daily task limit reached.")
    if problem:
        await query.answer(problem[:200], show_alert=True)
        return
    try:
        await bot.forward_message(chat_id=query.message.chat.id, from_chat_id=post.source_chat_id, message_id=post.source_message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        # post deleted / forwarding restricted / bot has no access -> not the worker's fault
        logger.warning(f"Campaign {cid}: cannot forward post ({e}); pausing")
        c.status = CampaignStatus.PAUSED
        await session.commit()
        await alert_admins(bot, f"⚠️ View campaign <code>{esc(cid)}</code> paused: the post cannot be forwarded.")
        try:
            await bot.send_message(c.advertiser_id, L("bn", f"⚠️ “{esc(c.title)}” পোস্টটি ফরওয়ার্ড করা যাচ্ছে না (মুছে গেছে বা ফরওয়ার্ড বন্ধ)। ক্যাম্পেইনটি বিরতিতে রাখা হয়েছে।",
                                                          f"⚠️ The post of “{esc(c.title)}” can't be forwarded (deleted or forwarding restricted). The campaign was paused.", _tpl="⚠️ The post of “{v0}” can't be forwarded (deleted or forwarding restricted). The campaign was paused.", _kw={"v0": esc(c.title)}))
        except Exception:
            pass
        await query.answer(L(lang, "⚠️ এই পোস্টটি এখন পাওয়া যাচ্ছে না। অন্যটি চেষ্টা করুন।", "⚠️ This post is unavailable. Try another one."), show_alert=True)
        return
    await redis_client.set(f"pv:{uid}:{cid}", str(time.time()), ex=300)
    await query.answer()
    asyncio.create_task(_finish_view(bot, uid, cid, query.message.chat.id, lang))


@router.callback_query(F.data.startswith("earn:pv:"))
async def earn_view_post(query: CallbackQuery, session, bot: Bot):
    await _start_view(query, session, bot, query.data.split(":")[2])


@router.callback_query(F.data == "earn:pvnext")
async def earn_view_next(query: CallbackQuery, session, bot: Bot):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    items, total, _, _ = await _page_items(session, query.from_user.id, user, "post_view", 0)
    if not items:
        await query.answer(L(lang, "📭 এখন আর কোনো পোস্ট নেই।", "📭 No more posts right now."), show_alert=True)
        return
    await _start_view(query, session, bot, items[0].id)

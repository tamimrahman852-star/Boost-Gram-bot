from decimal import Decimal
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from sqlalchemy import select, func

from config import TaskPricing
from config import BusinessRules
from database import (
    User, Campaign, Transaction, CampaignStatus, TransactionType, TaskSubmission, AutoRenew,
)
from helpers import esc, L

router = Router()

PAGE_SIZE = 5
STATUS_ICON = {
    CampaignStatus.ACTIVE: "🟢", CampaignStatus.PAUSED: "⏸",
    CampaignStatus.COMPLETED: "✅", CampaignStatus.CANCELLED: "🚫",
}
TABS = {  # tab key -> (statuses, bn label, en label)
    "active": ([CampaignStatus.ACTIVE], "চলমান", "Active"),
    "done": ([CampaignStatus.COMPLETED], "সম্পন্ন", "Completed"),
    "paused": ([CampaignStatus.PAUSED], "বিরতিতে", "Paused"),
}


async def _user(session, uid):
    return (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()


async def _own_campaign(session, cid, uid, lock=False):
    """Fetch a campaign ONLY if it belongs to this user (prevents IDOR)."""
    q = select(Campaign).where(Campaign.id == cid, Campaign.advertiser_id == uid)
    if lock:
        q = q.with_for_update()
    return (await session.execute(q)).scalar_one_or_none()


async def _extras(session, cid: str):
    renew = (await session.execute(select(AutoRenew.renewals_left).where(AutoRenew.campaign_id == cid))).scalar()
    pending = (await session.execute(select(func.count(TaskSubmission.id)).where(
        TaskSubmission.campaign_id == cid, TaskSubmission.status == "pending"))).scalar() or 0
    return bool(renew and renew > 0), pending


def _remaining(c: Campaign) -> Decimal:
    return max(Decimal("0"), c.total_budget - c.spent_budget - c.refunded_budget)


async def build_list(session, uid: int, lang: str, tab: str, page: int):
    statuses = TABS[tab][0]
    total = (await session.execute(select(func.count(Campaign.id)).where(
        Campaign.advertiser_id == uid, Campaign.status.in_(statuses)))).scalar() or 0
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    rows_db = (await session.execute(
        select(Campaign).where(Campaign.advertiser_id == uid, Campaign.status.in_(statuses))
        .order_by(Campaign.created_at.desc()).offset(page * PAGE_SIZE).limit(PAGE_SIZE))).scalars().all()

    rows = [[InlineKeyboardButton(
        text=f"{STATUS_ICON[c.status]} {c.title[:20]} · {c.completed_count}/{c.max_completions} · {c.reward_per_user:,.0f}",
        callback_data=f"promote:ch:manage:{c.id}")] for c in rows_db]
    rows.append([
        InlineKeyboardButton(text=("• " if k == tab else "") + (v[1] if lang == "bn" else v[2]), callback_data=f"promote:mt:{k}:0")
        for k, v in TABS.items()
    ])
    if pages > 1:
        rows.append([
            InlineKeyboardButton(text="‹", callback_data=f"promote:mt:{tab}:{page - 1}"),
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="promote:noop"),
            InlineKeyboardButton(text="›", callback_data=f"promote:mt:{tab}:{page + 1}"),
        ])
    rows.append([InlineKeyboardButton(text=L(lang, "➕ নতুন কাজ তৈরি করুন", "➕ Create new task"), callback_data="menu:promote")])
    rows.append([InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="menu:promote")])
    text = L(lang, "📋 <b>আপনার কাজ</b>", "📋 <b>Your tasks</b>")
    if not rows_db:
        text += L(lang, "\n\nএই তালিকায় কোনো কাজ নেই।", "\n\nNothing in this list yet.")
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_list(query: CallbackQuery, session, tab: str, page: int):
    user = await _user(session, query.from_user.id)
    lang = user.language if user else "bn"
    text, kb = await build_list(session, query.from_user.id, lang, tab, page)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "promote:my_tasks")
async def show_my_tasks(query: CallbackQuery, session):
    await _show_list(query, session, "active", 0)


@router.callback_query(F.data.startswith("promote:mt:"))
async def tab_page(query: CallbackQuery, session):
    try:
        _, _, tab, page = query.data.split(":")
        if tab not in TABS:
            raise ValueError
        page = int(page)
    except ValueError:
        await query.answer()
        return
    await _show_list(query, session, tab, page)


@router.callback_query(F.data == "promote:noop")
async def noop(query: CallbackQuery):
    await query.answer()


def _manage_view(c: Campaign, lang: str, renew_on: bool = False, pending: int = 0):
    aud = L(lang, "শুধু Telegram Premium", "Telegram Premium only") if c.premium_only else L(lang, "সব ব্যবহারকারী", "All users")
    text = L(
        lang,
        f"{STATUS_ICON[c.status]} <b>{esc(c.title)}</b>\n<code>№{c.id[:8].upper()}</code>\n\n"
        f"👥 সম্পন্ন: <b>{c.completed_count}/{c.max_completions}</b>\n💲 প্রতি ইউজার: <b>{c.reward_per_user:,.0f}</b> GRAM\n"
        f"💰 খরচ: <b>{c.spent_budget:,.0f}</b> · বাকি বাজেট: <b>{_remaining(c):,.0f}</b>",
        f"{STATUS_ICON[c.status]} <b>{esc(c.title)}</b>\n<code>№{c.id[:8].upper()}</code>\n\n"
        f"👥 Done: <b>{c.completed_count}/{c.max_completions}</b>\n💲 Per user: <b>{c.reward_per_user:,.0f}</b> GRAM\n"
        f"💰 Spent: <b>{c.spent_budget:,.0f}</b> · Budget left: <b>{_remaining(c):,.0f}</b>",
    )
    rows = []
    live = c.status in (CampaignStatus.ACTIVE, CampaignStatus.PAUSED)
    ttype = c.task_type.value if hasattr(c.task_type, "value") else str(c.task_type)
    if ttype in BusinessRules.PROOF_TASK_TYPES and c.status != CampaignStatus.CANCELLED:
        rows.append([InlineKeyboardButton(
            text=L(lang, f"📥 প্রমাণ যাচাই ({pending})", f"📥 Review proofs ({pending})"), callback_data=f"rev:list:{c.id}")])
    if live:
        rows.append([InlineKeyboardButton(
            text=L(lang, f"🔁 অটো-রিনিউ: {'চালু' if renew_on else 'বন্ধ'}", f"🔁 Auto-renew: {'ON' if renew_on else 'OFF'}"),
            callback_data=f"promote:mng:renew:{c.id}")])
        if c.status == CampaignStatus.ACTIVE:
            rows.append([InlineKeyboardButton(text=L(lang, "⏸ বিরতি দিন", "⏸ Pause"), callback_data=f"promote:mng:pause:{c.id}")])
        else:
            rows.append([InlineKeyboardButton(text=L(lang, "▶️ চালু করুন", "▶️ Resume"), callback_data=f"promote:mng:resume:{c.id}")])
        rows.append([InlineKeyboardButton(text=L(lang, f"👤 অডিয়েন্স: {aud}", f"👤 Audience: {aud}"), callback_data=f"promote:mng:aud:{c.id}")])
        rows.append([InlineKeyboardButton(text=L(lang, "🗑 মুছুন ও রিফান্ড", "🗑 Delete & refund"), callback_data=f"promote:mng:cancel:{c.id}")])
    rows.append([InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="promote:my_tasks")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("promote:ch:manage:"))
async def manage_campaign(query: CallbackQuery, session):
    user = await _user(session, query.from_user.id)
    lang = user.language if user else "bn"
    c = await _own_campaign(session, query.data.split(":")[3], query.from_user.id)
    if not c:
        await query.answer("❌", show_alert=True)
        return
    renew_on, pending = await _extras(session, c.id)
    text, kb = _manage_view(c, lang, renew_on, pending)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data.startswith("promote:mng:"))
async def campaign_action(query: CallbackQuery, session):
    _, _, action, cid = query.data.split(":", 3)
    uid = query.from_user.id
    user = await _user(session, uid)
    lang = user.language if user else "bn"
    c = await _own_campaign(session, cid, uid, lock=True)
    if not c:
        await session.rollback()
        await query.answer("❌", show_alert=True)
        return

    if action == "cancel":
        camp_id, refund = c.id, _remaining(c)   # read BEFORE rollback (it expires ORM objects)
        await session.rollback()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=L(lang, "✅ হ্যাঁ, মুছুন", "✅ Yes, delete"), callback_data=f"promote:mng:cancelok:{camp_id}")],
            [InlineKeyboardButton(text=L(lang, "◀️ না", "◀️ No"), callback_data=f"promote:ch:manage:{camp_id}")],
        ])
        await query.message.edit_text(L(
            lang,
            f"🛑 কাজটি মুছলে <b>{refund:,.0f} GRAM</b> ফেরত পাবেন। (প্ল্যাটফর্ম কমিশন ফেরতযোগ্য নয়)",
            f"🛑 Deleting refunds <b>{refund:,.0f} GRAM</b>. (The platform commission is non-refundable.)"),
            reply_markup=kb, parse_mode=ParseMode.HTML)
        await query.answer()
        return

    if action == "cancelok" and c.status in (CampaignStatus.ACTIVE, CampaignStatus.PAUSED):
        remaining = _remaining(c)
        c.status = CampaignStatus.CANCELLED
        c.refunded_budget += remaining
        ru = await session.execute(select(User).where(User.id == uid).with_for_update())
        owner = ru.scalar_one()
        if remaining > 0:
            owner.balance += remaining
            session.add(Transaction(
                user_id=uid, amount=remaining, type=TransactionType.CAMPAIGN_REFUND,
                description=f"Refund: {c.title}"[:255], reference_id=c.id,
                reference_type="campaign", balance_after=owner.balance,
            ))
        await session.commit()
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="promote:my_tasks")]])
        await query.message.edit_text(L(
            lang,
            f"✅ <b>সম্পন্ন! টাস্কটি মুছে ফেলা হয়েছে।</b>\n\nফেরত দেওয়া হয়েছে: <b>{remaining:,.1f} GRAM</b> 💲\nআপনার ব্যালেন্স: <b>{owner.balance:,.1f} GRAM</b> 💰",
            f"✅ <b>Done! The task was deleted.</b>\n\nRefunded: <b>{remaining:,.1f} GRAM</b> 💲\nYour balance: <b>{owner.balance:,.1f} GRAM</b> 💰"),
            reply_markup=kb, parse_mode=ParseMode.HTML)
        await query.answer("✅")
        return

    if action == "renew" and c.status in (CampaignStatus.ACTIVE, CampaignStatus.PAUSED):
        now_on = await _toggle_renew(session, c.id)
        await query.answer(
            L(lang, f"🔁 অটো-রিনিউ চালু: টাস্ক শেষ হলে স্বয়ংক্রিয়ভাবে আরও {BusinessRules.AUTO_RENEW_DEFAULT} বার পুনরায় প্রকাশ হবে এবং আপনার ব্যালেন্স থেকে টাকা কাটা হবে।",
                    f"🔁 Auto-renew ON: when this task finishes it is re-published up to {BusinessRules.AUTO_RENEW_DEFAULT} times, charging your balance.")
            if now_on else L(lang, "🔁 অটো-রিনিউ বন্ধ করা হয়েছে।", "🔁 Auto-renew turned off."), show_alert=True)
    elif action == "pause" and c.status == CampaignStatus.ACTIVE:
        c.status = CampaignStatus.PAUSED
    elif action == "resume" and c.status == CampaignStatus.PAUSED and not c.is_full:
        c.status = CampaignStatus.ACTIVE
    elif action == "aud" and c.status in (CampaignStatus.ACTIVE, CampaignStatus.PAUSED):
        if not c.premium_only:
            ttype = c.task_type.value if hasattr(c.task_type, "value") else str(c.task_type)
            need = TaskPricing.get(ttype, True)["min"]
            if c.reward_per_user < need:
                await session.rollback()
                await query.answer(L(
                    lang, f"❌ Premium অডিয়েন্সের জন্য কমপক্ষে {need:,} GRAM পুরস্কার দরকার।",
                    f"❌ The Premium audience needs a reward of at least {need:,} GRAM."), show_alert=True)
                return
        c.premium_only = not c.premium_only
    await session.commit()
    renew_on, pending = await _extras(session, c.id)
    text, kb = _manage_view(c, lang, renew_on, pending)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    try:
        await query.answer("✅")
    except Exception:
        pass


async def _toggle_renew(session, cid: str) -> bool:
    """Flip auto-renew for a campaign (caller already verified ownership). Returns the new state."""
    row = (await session.execute(select(AutoRenew).where(AutoRenew.campaign_id == cid).with_for_update())).scalar_one_or_none()
    if row and row.renewals_left > 0:
        row.renewals_left = 0
        await session.commit()
        return False
    if row:
        row.renewals_left = BusinessRules.AUTO_RENEW_DEFAULT
    else:
        session.add(AutoRenew(campaign_id=cid, renewals_left=BusinessRules.AUTO_RENEW_DEFAULT))
    await session.commit()
    return True


# ----------------------------- Auto-task settings screen -----------------------------
async def _auto_view(session, uid: int, lang: str):
    camps = (await session.execute(
        select(Campaign).where(Campaign.advertiser_id == uid,
                               Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED, CampaignStatus.COMPLETED]))
        .order_by(Campaign.created_at.desc()).limit(10))).scalars().all()
    on = {r[0] for r in (await session.execute(
        select(AutoRenew.campaign_id).where(AutoRenew.campaign_id.in_([c.id for c in camps] or ["-"]),
                                            AutoRenew.renewals_left > 0))).all()}
    text = L(
        lang,
        "⚙️ <b>অটো-টাস্ক সেটিংস</b>\n\n🔁 <b>অটো-রিনিউ</b>: কাজ শেষ হলে একই কাজ স্বয়ংক্রিয়ভাবে আবার প্রকাশ হয়, ফলে আপনার প্রচার কখনো থামে না।\n"
        f"• সর্বোচ্চ {BusinessRules.AUTO_RENEW_DEFAULT} বার পুনরাবৃত্তি\n• প্রতিবার আপনার ব্যালেন্স থেকে খরচ কাটা হয়\n• ব্যালেন্স কম হলে নিজে থেকে বন্ধ হয় এবং আপনাকে জানানো হয়\n\n"
        "যে কাজে চালু করতে চান সেটি চাপুন:",
        "⚙️ <b>Auto-task settings</b>\n\n🔁 <b>Auto-renew</b>: when a task finishes, the same task is published again so your promotion never stops.\n"
        f"• Up to {BusinessRules.AUTO_RENEW_DEFAULT} repeats\n• Each repeat is charged to your balance\n• Stops by itself (and tells you) if the balance is too low\n\n"
        "Tap a task to switch it on or off:")
    if not camps:
        text += L(lang, "\n\n📭 আপনার কোনো কাজ নেই।", "\n\n📭 You have no tasks yet.")
    rows = [[InlineKeyboardButton(
        text=f"{'🔁 ON' if c.id in on else '⚪ OFF'} · {c.title[:24]} · {c.completed_count}/{c.max_completions}",
        callback_data=f"promote:renew:{c.id}")] for c in camps]
    rows.append([InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="menu:promote")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "promote:auto")
async def auto_settings(query: CallbackQuery, session):
    user = await _user(session, query.from_user.id)
    lang = user.language if user else "bn"
    text, kb = await _auto_view(session, query.from_user.id, lang)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data.startswith("promote:renew:"))
async def auto_toggle(query: CallbackQuery, session):
    uid = query.from_user.id
    c = await _own_campaign(session, query.data.split(":")[2], uid)
    if not c or c.status == CampaignStatus.CANCELLED:
        await query.answer("❌", show_alert=True)
        return
    await _toggle_renew(session, c.id)
    user = await _user(session, uid)
    lang = user.language if user else "bn"
    text, kb = await _auto_view(session, uid, lang)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer("✅")

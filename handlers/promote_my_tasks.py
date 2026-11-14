from decimal import Decimal
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from sqlalchemy import select

from database import User, Campaign, Transaction, CampaignStatus, TransactionType
from helpers import esc, L

router = Router()

STATUS_ICON = {
    CampaignStatus.ACTIVE: "🟢", CampaignStatus.PAUSED: "⏸",
    CampaignStatus.COMPLETED: "✅", CampaignStatus.CANCELLED: "🚫",
}


async def _user(session, uid):
    return (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()


async def _own_campaign(session, cid, uid, lock=False):
    """Fetch a campaign ONLY if it belongs to this user (prevents IDOR)."""
    q = select(Campaign).where(Campaign.id == cid, Campaign.advertiser_id == uid)
    if lock:
        q = q.with_for_update()
    return (await session.execute(q)).scalar_one_or_none()


@router.callback_query(F.data == "promote:my_tasks")
async def show_my_tasks(query: CallbackQuery, session):
    uid = query.from_user.id
    user = await _user(session, uid)
    lang = user.language if user else "bn"
    result = await session.execute(
        select(Campaign).where(Campaign.advertiser_id == uid).order_by(Campaign.created_at.desc()).limit(10))
    campaigns = result.scalars().all()
    back = [InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="menu:promote")]
    if not campaigns:
        await query.message.edit_text(
            L(lang, "📋 <b>আপনার কোনো ক্যাম্পেইন নেই।</b>", "📋 <b>You have no campaigns yet.</b>"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back]), parse_mode=ParseMode.HTML)
        await query.answer()
        return
    rows = [[InlineKeyboardButton(
        text=f"{STATUS_ICON[c.status]} {c.title[:24]} · {c.completed_count}/{c.max_completions}",
        callback_data=f"promote:ch:manage:{c.id}")] for c in campaigns]
    rows.append(back)
    await query.message.edit_text(
        L(lang, "📋 <b>আপনার ক্যাম্পেইন</b>\nম্যানেজ করতে একটি বেছে নিন:", "📋 <b>Your campaigns</b>\nPick one to manage:"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode=ParseMode.HTML)
    await query.answer()


def _manage_view(c: Campaign, lang: str):
    remaining = max(Decimal("0"), c.total_budget - c.spent_budget - c.refunded_budget)
    text = L(
        lang,
        f"{STATUS_ICON[c.status]} <b>{esc(c.title)}</b>\n\n"
        f"👥 সম্পন্ন: <b>{c.completed_count}/{c.max_completions}</b>\n💲 প্রতি ইউজার: <b>{c.reward_per_user:,.0f}</b>\n"
        f"💰 খরচ: <b>{c.spent_budget:,.0f}</b> · বাকি বাজেট: <b>{remaining:,.0f}</b>",
        f"{STATUS_ICON[c.status]} <b>{esc(c.title)}</b>\n\n"
        f"👥 Done: <b>{c.completed_count}/{c.max_completions}</b>\n💲 Per user: <b>{c.reward_per_user:,.0f}</b>\n"
        f"💰 Spent: <b>{c.spent_budget:,.0f}</b> · Budget left: <b>{remaining:,.0f}</b>",
    )
    rows = []
    if c.status == CampaignStatus.ACTIVE:
        rows.append([InlineKeyboardButton(text=L(lang, "⏸ বিরতি", "⏸ Pause"), callback_data=f"promote:mng:pause:{c.id}")])
    elif c.status == CampaignStatus.PAUSED:
        rows.append([InlineKeyboardButton(text=L(lang, "▶️ চালু করুন", "▶️ Resume"), callback_data=f"promote:mng:resume:{c.id}")])
    if c.status in (CampaignStatus.ACTIVE, CampaignStatus.PAUSED):
        rows.append([InlineKeyboardButton(text=L(lang, "🛑 বাতিল ও রিফান্ড", "🛑 Cancel & refund"), callback_data=f"promote:mng:cancel:{c.id}")])
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
    text, kb = _manage_view(c, lang)
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
        # ask for confirmation first
        await session.rollback()
        remaining = max(Decimal("0"), c.total_budget - c.spent_budget - c.refunded_budget)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=L(lang, "✅ হ্যাঁ, বাতিল করুন", "✅ Yes, cancel"), callback_data=f"promote:mng:cancelok:{c.id}")],
            [InlineKeyboardButton(text=L(lang, "◀️ না", "◀️ No"), callback_data=f"promote:ch:manage:{c.id}")],
        ])
        await query.message.edit_text(L(
            lang,
            f"🛑 ক্যাম্পেইনটি বাতিল করলে <b>{remaining:,.0f} GRAM</b> ফেরত পাবেন। (প্ল্যাটফর্ম ফি ফেরতযোগ্য নয়)",
            f"🛑 Cancelling refunds <b>{remaining:,.0f} GRAM</b>. (The platform fee is non-refundable.)"),
            reply_markup=kb, parse_mode=ParseMode.HTML)
        await query.answer()
        return

    if action == "pause" and c.status == CampaignStatus.ACTIVE:
        c.status = CampaignStatus.PAUSED
    elif action == "resume" and c.status == CampaignStatus.PAUSED and not c.is_full:
        c.status = CampaignStatus.ACTIVE
    elif action == "cancelok" and c.status in (CampaignStatus.ACTIVE, CampaignStatus.PAUSED):
        remaining = max(Decimal("0"), c.total_budget - c.spent_budget - c.refunded_budget)
        c.status = CampaignStatus.CANCELLED
        c.refunded_budget += remaining
        if remaining > 0:
            ru = await session.execute(select(User).where(User.id == uid).with_for_update())
            owner = ru.scalar_one()
            owner.balance += remaining
            session.add(Transaction(
                user_id=uid, amount=remaining, type=TransactionType.CAMPAIGN_REFUND,
                description=f"Refund: {c.title}"[:255], reference_id=c.id,
                reference_type="campaign", balance_after=owner.balance,
            ))
    await session.commit()
    text, kb = _manage_view(c, lang)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer("✅")

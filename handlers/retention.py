"""Leaving a channel/group early.

* chat_member updates tell the bot the moment someone leaves (the bot is admin there).
* The reward is held at once and the user is warned: re-subscribe within 24h and the coins come back.
* Until the user restores the subscription (or pays the penalty with 'Return coins') earning is blocked
  and the Earnings button shows the 'Restore subscriptions or pay a penalty' screen.
"""
from aiogram import Router, F, Bot
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from sqlalchemy import select, or_

from config import BusinessRules, logger
from database import Campaign, EarlyLeave, TaskCompletion, TaskType, RetentionStatus, User
from helpers import L, esc
from membership import has_active_boost, is_member
from services import hold_early_leave, restore_hold, finalize_hold, held_records

router = Router()
_IN = (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


def _inside(m) -> bool:
    return m.status in _IN or (m.status == ChatMemberStatus.RESTRICTED and bool(getattr(m, "is_member", False)))


def _link_kb(lang: str, link):
    if not link or not str(link).startswith("http"):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=L(lang, "🔄 আবার সাবস্ক্রাইব করুন", "🔄 Re-subscribe"), url=link)]])


async def notify_early_leave(bot: Bot, info: dict) -> None:
    lang, hours = info["language"], BusinessRules.EARLY_LEAVE_GRACE_HOURS
    tid, amount = info["campaign_id"][:8].upper(), info["amount"]
    text = L(
        lang,
        f"⚠️ আপনি প্রয়োজনের আগেই চ্যানেল/চ্যাট থেকে আনসাবস্ক্রাইব করেছেন (টাস্ক №{tid})\n"
        f"ব্লক এড়াতে অনুগ্রহ করে {hours} ঘণ্টার মধ্যে আবার সাবস্ক্রাইব করুন।\n\n"
        f"💲 আপনার ব্যালেন্স থেকে {amount:,.0f} GRAM কেটে রাখা হয়েছে। আবার সাবস্ক্রাইব করলে কয়েন ফেরত পাবেন।",
        f"⚠️ You unsubscribed from the channel/chat earlier than required (task №{tid})\n"
        f"To avoid blocking, please re-subscribe within {hours} hours.\n\n"
        f"💲 We deducted {amount:,.0f} GRAM from your balance. After re-subscribing, your coins will be returned.",
        _tpl="⚠️ You unsubscribed from the channel/chat earlier than required (task №{v0})\n"
             "To avoid blocking, please re-subscribe within {v1} hours.\n\n"
             "💲 We deducted {v2:,.0f} GRAM from your balance. After re-subscribing, your coins will be returned.",
        _kw={"v0": tid, "v1": hours, "v2": amount})
    try:
        await bot.send_message(info["user_id"], text, reply_markup=_link_kb(lang, info["link"]))
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


async def _matching_completions(session, uid: int, chat_id: int, username):
    cond = Campaign.target_chat_id == chat_id
    if username:
        cond = or_(cond, Campaign.target_username == username)
    rows = await session.execute(
        select(TaskCompletion.id).join(Campaign, Campaign.id == TaskCompletion.campaign_id).where(
            TaskCompletion.user_id == uid,
            TaskCompletion.retention_status == RetentionStatus.PENDING,
            Campaign.task_type.in_([TaskType.CHANNEL_SUB, TaskType.GROUP_JOIN]),
            cond))
    return [r[0] for r in rows.all()]


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated, session, bot: Bot):
    new, old = event.new_chat_member, event.old_chat_member
    uid, chat_id = new.user.id, event.chat.id
    if new.user.is_bot:
        return
    was_in, now_in = _inside(old), _inside(new)
    if was_in and not now_in:                                   # LEFT / REMOVED
        for cid in await _matching_completions(session, uid, chat_id, event.chat.username):
            info = await hold_early_leave(session, cid)
            if info:
                logger.info(f"early leave: user {uid} left chat {chat_id} (completion {cid})")
                await notify_early_leave(bot, info)
    elif now_in and not was_in:                                 # re-subscribed: give the coins back automatically
        rows = await held_records(session, uid)
        for el, c in rows:
            if c.target_chat_id == chat_id or (event.chat.username and c.target_username == event.chat.username):
                lang = (await session.execute(select(User.language).where(User.id == uid))).scalar() or "en"
                amount = el.amount
                if await restore_hold(session, el.id):
                    try:
                        await bot.send_message(uid, L(
                            lang, f"✅ সাবস্ক্রিপশন পুনরুদ্ধার হয়েছে! {amount:,.0f} GRAM আপনার ব্যালেন্সে ফেরত এসেছে।",
                            f"✅ Subscription restored! {amount:,.0f} GRAM were returned to your balance.",
                            _tpl="✅ Subscription restored! {v0:,.0f} GRAM were returned to your balance.", _kw={"v0": amount}))
                    except (TelegramBadRequest, TelegramForbiddenError):
                        pass


# ------------------------------------------------------------------------------------------
# Block screen
# ------------------------------------------------------------------------------------------
async def held_screen(session, uid: int, lang: str):
    """(text, keyboard) when the user must restore subscriptions first, otherwise None."""
    rows = await held_records(session, uid)
    if not rows:
        return None
    buttons = []
    for el, c in rows[:3]:
        link = c.target_link or (f"https://t.me/{c.target_username}" if c.target_username else None)
        if link and str(link).startswith("http"):
            tid = c.id[:8].upper()
            buttons.append([InlineKeyboardButton(
                text=L(lang, f"সাবস্ক্রাইব - №{tid}", f"Subscribe - №{tid}", _tpl="Subscribe - №{v0}", _kw={"v0": tid}), url=link)])
    buttons.append([InlineKeyboardButton(text=L(lang, "🔄 তালিকা রিফ্রেশ", "🔄 Refresh list"), callback_data="earn:restore:check")])
    buttons.append([InlineKeyboardButton(text=L(lang, "💲 কয়েন ফেরত দিন", "💲 Return coins"), callback_data="earn:restore:pay")])
    text = L(lang, "ℹ️ টাস্কে আবার প্রবেশাধিকার পেতে সাবস্ক্রিপশন পুনরুদ্ধার করুন অথবা জরিমানা দিন।",
             "ℹ️ Restore your subscriptions or pay a penalty to regain access to tasks.")
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def block_if_held(event, session, user) -> bool:
    """Call at the start of every earning entry point. True = the user is blocked and the screen was shown."""
    if not user:
        return False
    scr = await held_screen(session, user.id, user.language)
    if scr is None:
        return False
    text, kb = scr
    if hasattr(event, "data") and getattr(event, "message", None) is not None:      # a callback query
        await event.message.answer(text, reply_markup=kb)
        try:
            await event.answer()
        except TelegramBadRequest:
            pass
    else:                                                                            # a normal message
        await event.answer(text, reply_markup=kb)
    return True


@router.callback_query(F.data == "earn:restore:check")
async def restore_check(query: CallbackQuery, session, bot: Bot):
    uid = query.from_user.id
    user = (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    lang = user.language if user else "en"
    restored = 0.0
    for el, c in await held_records(session, uid):
        target = c.target_chat_id or (f"@{c.target_username}" if c.target_username else None)
        if not target:
            continue
        try:
            ok = await has_active_boost(bot, target, uid) if c.task_type == TaskType.BOOST_7DAY else await is_member(bot, target, uid)
        except (TelegramRetryAfter, TelegramNetworkError, TelegramBadRequest, TelegramForbiddenError):
            continue                                            # cannot verify right now: stay on the block screen
        if ok:
            amount = el.amount
            if await restore_hold(session, el.id):
                restored += float(amount)
    scr = await held_screen(session, uid, lang)
    if scr is None:
        await query.message.answer(L(
            lang, f"✅ সাবস্ক্রিপশন পুনরুদ্ধার হয়েছে! {restored:,.0f} GRAM আপনার ব্যালেন্সে ফেরত এসেছে।",
            f"✅ Subscription restored! {restored:,.0f} GRAM were returned to your balance.",
            _tpl="✅ Subscription restored! {v0:,.0f} GRAM were returned to your balance.", _kw={"v0": restored}))
        from handlers.earnings import _render_categories
        await _render_categories(query.message, session, user, edit=False)
        await query.answer()
        return
    await query.answer(L(lang, "ℹ️ আপনি এখনো সাবস্ক্রাইব করেননি। আগে সাবস্ক্রাইব করুন, তারপর তালিকা রিফ্রেশ করুন।",
                         "ℹ️ You are not subscribed yet. Subscribe first, then tap Refresh list."), show_alert=True)


@router.callback_query(F.data == "earn:restore:pay")
async def restore_pay(query: CallbackQuery, session):
    uid = query.from_user.id
    user = (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    lang = user.language if user else "en"
    total = 0.0
    for el, c in await held_records(session, uid):
        amount = el.amount
        if await finalize_hold(session, el.id, BusinessRules.EARLY_LEAVE_VOLUNTARY_RISK):
            total += float(amount)
    await query.message.answer(L(
        lang, f"✅ জরিমানা পরিশোধিত: {total:,.0f} GRAM। টাস্কে প্রবেশাধিকার ফিরে এসেছে।",
        f"✅ Penalty paid: {total:,.0f} GRAM. Access to tasks is restored.",
        _tpl="✅ Penalty paid: {v0:,.0f} GRAM. Access to tasks is restored.", _kw={"v0": total}))
    from handlers.earnings import _render_categories
    user = (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    await _render_categories(query.message, session, user, edit=False)
    await query.answer()

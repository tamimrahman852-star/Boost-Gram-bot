"""Bot side of channel monetization + legal/payment-support commands.

* my_chat_member: when someone adds the bot to a channel as admin, remember it for that user so the Mini App can offer
  the channel in one tap ("add the channel through the bot").
* admin review of new ad campaigns (Approve / Reject) — keeps scams and illegal content out of publishers' channels.
* /paysupport (required by Telegram for bots that take Stars), /terms and /privacy.
"""
import json

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo,
)
from sqlalchemy import select

from ads import close_campaign, fee_of, remaining
from config import ADMIN_IDS, BOT_USERNAME, PUBLIC_URL, WEBAPP_URL, logger
from database import AdCampaign, User, redis_client
from helpers import L, esc

router = Router()


# ------------------------------------------------------------------ channel candidates
@router.my_chat_member(F.chat.type == "channel")
async def bot_channel_status(ev: ChatMemberUpdated):
    """The bot was added to / removed from a channel. `ev.from_user` is who did it."""
    uid, chat = ev.from_user.id, ev.chat
    key = f"addch:{uid}"
    if ev.new_chat_member.status == "administrator":
        await redis_client.hset(key, str(chat.id), json.dumps({"title": chat.title, "username": chat.username}))
        await redis_client.expire(key, 7 * 86400)
        try:
            kb = None
            if WEBAPP_URL.startswith("https://"):
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                    text="📣 Monetize", web_app=WebAppInfo(url=WEBAPP_URL.rstrip("/") + "/?tab=ads&seg=publish"))]])
            await ev.bot.send_message(uid, f"✅ <b>{esc(chat.title or 'Channel')}</b> — the bot is now an admin. "
                                           f"Open the app to start earning from ads there.", parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
    elif ev.new_chat_member.status in ("left", "kicked"):
        await redis_client.hdel(key, str(chat.id))
        from database import AdChannel, AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            ch = (await s.execute(select(AdChannel).where(AdChannel.chat_id == chat.id))).scalar_one_or_none()
            if ch and ch.status == "active":
                ch.status, ch.pause_reason = "paused", "bot was removed from the channel"
                await s.commit()


# ------------------------------------------------------------------ admin review
async def notify_admins_new_campaign(bot: Bot, c: AdCampaign, file_id=None):
    cap = (f"📣 <b>New ad campaign — review</b>\n\n👤 <code>{c.advertiser_id}</code>\n🏷 {esc(c.title)}\n\n{esc(c.text)}\n\n"
           f"🔗 {esc(c.button_url)}  [{esc(c.button_text)}]\n💰 Budget {c.budget:,.0f} · max/post {c.max_price_per_post:,.0f} · {c.posts_per_day}/day")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Approve", callback_data=f"adm:ad:ok:{c.id}"),
        InlineKeyboardButton(text="❌ Reject + refund", callback_data=f"adm:ad:no:{c.id}")]])
    for aid in ADMIN_IDS:
        try:
            if file_id and len(cap) <= 1024:
                await bot.send_photo(aid, file_id, caption=cap, reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                await bot.send_message(aid, cap, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"ad review notify failed for {aid}: {e}")


@router.callback_query(F.data.startswith("adm:ad:"))
async def review_ad(query: CallbackQuery, session):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    _, _, action, cid = query.data.split(":", 3)
    c = (await session.execute(select(AdCampaign).where(AdCampaign.id == cid).with_for_update())).scalar_one_or_none()
    if not c or c.status != "pending_review":
        await session.rollback()
        await query.answer("Already processed.", show_alert=True)
        return
    adv = (await session.execute(select(User.language).where(User.id == c.advertiser_id))).scalar() or "en"
    title, advertiser = c.title, c.advertiser_id
    if action == "ok":
        c.status = "active"
        await session.commit()
        note, msg = "✅ Approved", L(adv, f"✅ আপনার বিজ্ঞাপন «{esc(title)}» অনুমোদিত হয়েছে এবং চালু হয়েছে।",
                                     f"✅ Your ad “{esc(title)}” was approved and is now running.")
    else:
        c.reject_reason = "rejected by moderation"
        await close_campaign(session, c, "rejected", "Ad rejected refund")
        await session.commit()
        note, msg = "❌ Rejected", L(adv, f"❌ আপনার বিজ্ঞাপন «{esc(title)}» গ্রহণযোগ্য নয়। পুরো বাজেট ফেরত দেওয়া হয়েছে।",
                                     f"❌ Your ad “{esc(title)}” was not accepted. The full budget was refunded.")
    try:
        base = query.message.caption or query.message.text or ""
        if query.message.caption is not None:
            await query.message.edit_caption(caption=f"{base}\n\n{note}")
        else:
            await query.message.edit_text(f"{base}\n\n{note}")
    except Exception:
        pass
    await query.answer(note)
    try:
        await query.bot.send_message(advertiser, msg, parse_mode=ParseMode.HTML)
    except Exception:
        pass


# ------------------------------------------------------------------ legal + payment support
def _legal_url(page: str) -> str:
    base = WEBAPP_URL.rstrip("/") if WEBAPP_URL else PUBLIC_URL
    return f"{base}/{page}.html" if WEBAPP_URL else f"{PUBLIC_URL}/{page}"


@router.message(Command("terms"))
async def cmd_terms(message: Message, session):
    lang = (await session.execute(select(User.language).where(User.id == message.from_user.id))).scalar() or "en"
    await message.answer(L(lang, "📄 <b>ব্যবহারের শর্তাবলী</b>", "📄 <b>Terms of Service</b>"),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                             text=L(lang, "খুলুন", "Open"), url=_legal_url("terms"))]]), parse_mode=ParseMode.HTML)


@router.message(Command("privacy"))
async def cmd_privacy(message: Message, session):
    lang = (await session.execute(select(User.language).where(User.id == message.from_user.id))).scalar() or "en"
    await message.answer(L(lang, "🔒 <b>গোপনীয়তা নীতি</b>", "🔒 <b>Privacy Policy</b>"),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                             text=L(lang, "খুলুন", "Open"), url=_legal_url("privacy"))]]), parse_mode=ParseMode.HTML)


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message, session):
    """Telegram requires every bot that sells for Stars to answer /paysupport."""
    u = message.from_user
    lang = (await session.execute(select(User.language).where(User.id == u.id))).scalar() or "en"
    await message.answer(L(
        lang,
        "💬 <b>পেমেন্ট সাপোর্ট</b>\n\nStars দিয়ে টপ-আপ বা অন্য কোনো পেমেন্ট নিয়ে সমস্যা হলে এখানে আপনার সমস্যাটি লিখে পাঠান — "
        "লেনদেনের তারিখ ও পরিমাণসহ। আমরা ২৪ ঘণ্টার মধ্যে দেখব। আপনার আইডি: <code>{id}</code>".format(id=u.id),
        "💬 <b>Payment support</b>\n\nIf something went wrong with a Stars top-up or any other payment, reply here with the date and "
        f"amount. We review every request within 24 hours. Your ID: <code>{u.id}</code>"), parse_mode=ParseMode.HTML)
    for aid in ADMIN_IDS:
        try:
            await message.bot.send_message(aid, f"💬 /paysupport from <code>{u.id}</code> (@{esc(u.username or '-')})", parse_mode=ParseMode.HTML)
        except Exception:
            pass

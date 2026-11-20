"""Channel monetization: ad scheduling, posting, statistics and settlement.

Money flow (all in GRAM)
  advertiser balance --(budget, escrow)--> campaign
  each placement reserves  price + fee  from the campaign   (fee = AdRules.COMMISSION * price, platform's cut)
  placement completed   -> publisher is credited `price`, the platform keeps `fee`
  placement failed / deleted early -> the reserved amount returns to the campaign (or the advertiser's balance
                                      if the campaign is already closed)
  campaign ends / is stopped / budget is used up -> unused escrow goes back to the advertiser

Measured numbers
  clicks      real: every ad button goes through <PUBLIC_URL>/r/<placement> (one count per visitor per day)
  bot starts  real: the "via @Bot" button carries a start payload (see handlers/start.py)
  views       read from Telegram's public post widget (t.me/<channel>/<id>?embed=1) for OUR OWN ad posts only
              (AD_VIEW_SYNC=0 turns this off). Never invented: unknown stays 0 / "n/a" in the UI.
"""
import asyncio
import html
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from config import BOT_USERNAME, PUBLIC_URL, AdRules, BusinessRules, logger
from database import (
    AdCampaign, AdChannel, AdPlacement, AsyncSessionLocal, Transaction, TransactionType, User,
)
from helpers import L, alert_admins, esc

OPEN_STATUSES = ("scheduled", "posted")
CLOSED_CAMPAIGN = ("stopped", "finished", "exhausted", "rejected")


# --------------------------------------------------------------------------------------
# pricing & scheduling helpers
# --------------------------------------------------------------------------------------
def fee_of(price: Decimal) -> Decimal:
    return (Decimal(price) * AdRules.COMMISSION).to_integral_value(rounding="ROUND_UP")


def _round(x: Decimal, step: int = 1000) -> Decimal:
    return (x / step).to_integral_value(rounding="ROUND_HALF_UP") * step


def price_bounds(members: int) -> dict:
    """Allowed price per post for a channel of this size (publisher picks inside this range)."""
    k = Decimal(max(members, 1)) / 1000
    lo = min(AdRules.PRICE_CEIL, max(AdRules.PRICE_FLOOR, _round(AdRules.PRICE_PER_1K_MIN * k)))
    hi = min(AdRules.PRICE_CEIL, max(lo, _round(AdRules.PRICE_PER_1K_MAX * k)))
    sug = min(hi, max(lo, _round((lo * hi).sqrt())))
    return {"min": int(lo), "suggested": int(sug), "max": int(hi)}


def local_hour(now: datetime, tz_offset_min: int) -> int:
    return (now + timedelta(minutes=tz_offset_min)).hour


def in_window(ch: AdChannel, now: datetime) -> bool:
    h = local_hour(now, ch.tz_offset_min or 0)
    a, b = ch.window_start, ch.window_end
    if a == b or (a == 0 and b >= 24):
        return True                      # whole day
    return a <= h < b if a < b else (h >= a or h < b)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def remaining(c: AdCampaign) -> Decimal:
    return max(Decimal("0"), c.budget - c.spent - c.refunded)


# --------------------------------------------------------------------------------------
# what is actually posted
# --------------------------------------------------------------------------------------
def tracker_url(pid: str) -> str:
    return f"{PUBLIC_URL}/r/{pid}"


def post_html(c: AdCampaign) -> str:
    return f"{esc(c.text)}\n\n<i>📣 Sponsored · via @{esc(BOT_USERNAME)}</i>"


def post_kb(c: AdCampaign, pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=c.button_text[:40], url=tracker_url(pid))],
        [InlineKeyboardButton(text=f"🤖 @{BOT_USERNAME}", url=f"https://t.me/{BOT_USERNAME}?start=ad_{pid[:32]}")],
    ])


_VIEWS_RE = re.compile(r'tgme_widget_message_views">\s*([\d.,]+)\s*([KkMm]?)\s*<')


def parse_views(page: str) -> Optional[int]:
    m = _VIEWS_RE.search(page or "")
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    mult = {"": 1, "k": 1000, "m": 1_000_000}[m.group(2).lower()]
    return int(n * mult)


async def fetch_views(username: str, message_id: int) -> Optional[int]:
    """Telegram's official embeddable post widget shows the public view counter."""
    import aiohttp
    url = f"https://t.me/{username}/{message_id}?embed=1&mode=tme"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(url, headers={"User-Agent": "Mozilla/5.0 BoostGramBot"}) as r:
                if r.status != 200:
                    return None
                return parse_views(await r.text())
    except Exception:
        return None


async def post_exists(bot: Bot, p: AdPlacement, c: AdCampaign, ch: AdChannel) -> Optional[bool]:
    """True/False if we can tell whether the ad post is still in the channel, None if unknown."""
    try:
        await bot.edit_message_reply_markup(chat_id=ch.chat_id, message_id=p.message_id, reply_markup=post_kb(c, p.id))
        return True
    except TelegramBadRequest as e:
        low = str(e).lower()
        if "not modified" in low:
            return True
        if "not found" in low or "message_id_invalid" in low or "can't be edited" in low and "deleted" in low:
            return False
        return None
    except (TelegramForbiddenError,):
        return None
    except (TelegramRetryAfter, TelegramNetworkError):
        return None


# --------------------------------------------------------------------------------------
# money helpers (caller commits)
# --------------------------------------------------------------------------------------
async def _user_locked(s, uid: int) -> Optional[User]:
    return (await s.execute(select(User).where(User.id == uid).with_for_update())).scalar_one_or_none()


async def refund_to_advertiser(s, c: AdCampaign, amount: Decimal, note: str) -> None:
    if amount <= 0:
        return
    u = await _user_locked(s, c.advertiser_id)
    if not u:
        return
    u.balance += amount
    c.refunded += amount
    s.add(Transaction(user_id=u.id, amount=amount, type=TransactionType.CAMPAIGN_REFUND,
                      description=f"{note}: {c.title}"[:255], reference_id=c.id, reference_type="ad_campaign",
                      balance_after=u.balance))


async def release_placement(s, p: AdPlacement, c: AdCampaign, why: str, note: str) -> None:
    """A placement did not happen: give its reserved money back."""
    p.status, p.failure = why, note[:200]
    cost = p.price + p.fee
    if c.status in CLOSED_CAMPAIGN:
        await refund_to_advertiser(s, c, cost, "Ad refund")
    else:
        c.spent = max(Decimal("0"), c.spent - cost)
    c.total_posts = max(0, c.total_posts - 1)


async def close_campaign(s, c: AdCampaign, status: str, note: str = "Ad budget refund") -> Decimal:
    """Move a campaign to a closed state and return the unused escrow."""
    c = (await s.execute(select(AdCampaign).where(AdCampaign.id == c.id).with_for_update())).scalar_one()
    if c.status in CLOSED_CAMPAIGN:
        return Decimal("0")
    left = remaining(c)
    c.status = status
    await refund_to_advertiser(s, c, left, note)
    return left


async def _notify(bot: Bot, uid: int, bn: str, en: str, lang: str = "en") -> None:
    try:
        await bot.send_message(uid, L(lang, bn, en), parse_mode=ParseMode.HTML)
    except Exception:
        pass


async def _lang(s, uid: int) -> str:
    return (await s.execute(select(User.language).where(User.id == uid))).scalar() or "en"


# --------------------------------------------------------------------------------------
# 1) scheduling: match campaigns with channels that are open right now
# --------------------------------------------------------------------------------------
async def schedule_once(now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    made = 0
    async with AsyncSessionLocal() as s:
        camps = (await s.execute(select(AdCampaign).where(
            AdCampaign.status == "active", AdCampaign.start_at <= now).limit(200))).scalars().all()
        channels = (await s.execute(select(AdChannel).where(AdChannel.status == "active"))).scalars().all()
        used = {}     # channel_id -> (posts in 24h, last placement time)
        for ch_id, n, last in (await s.execute(select(AdPlacement.channel_id, func.count(AdPlacement.id), func.max(AdPlacement.created_at)).where(
                AdPlacement.created_at >= day_ago, AdPlacement.status.in_(("scheduled", "posted", "completed"))).group_by(AdPlacement.channel_id))).all():
            used[ch_id] = (int(n), _aware(last))
        for c in camps:
            end = _aware(c.end_at)
            if end and end <= now:
                continue
            c = (await s.execute(select(AdCampaign).where(AdCampaign.id == c.id).with_for_update())).scalar_one()
            if c.status != "active":
                continue
            mine = (await s.execute(select(AdPlacement.channel_id, AdPlacement.created_at).where(
                AdPlacement.campaign_id == c.id, AdPlacement.created_at >= day_ago,
                AdPlacement.status.in_(("scheduled", "posted", "completed"))))).all()
            cap = c.posts_per_day - len(mine)
            if cap <= 0:
                continue
            seen = {r[0] for r in mine}
            cands = [ch for ch in channels
                     if ch.owner_id != c.advertiser_id and ch.id not in seen and ch.price_per_post <= c.max_price_per_post
                     and ch.members >= c.min_members and in_window(ch, now)]
            cands.sort(key=lambda ch: (ch.members / float(ch.price_per_post)), reverse=True)    # best reach per GRAM first
            for ch in cands:
                if cap <= 0:
                    break
                n, last = used.get(ch.id, (0, None))
                if n >= ch.max_posts_per_day or (last and now - last < timedelta(hours=ch.min_gap_hours)):
                    continue
                fee = fee_of(ch.price_per_post)
                cost = ch.price_per_post + fee
                if remaining(c) < cost:
                    continue
                s.add(AdPlacement(campaign_id=c.id, channel_id=ch.id, owner_id=ch.owner_id, advertiser_id=c.advertiser_id,
                                  price=ch.price_per_post, fee=fee, status="scheduled", scheduled_for=now))
                c.spent += cost
                c.total_posts += 1
                used[ch.id] = (n + 1, now)
                cap -= 1
                made += 1
            if remaining(c) < AdRules.PRICE_FLOOR + fee_of(AdRules.PRICE_FLOOR):
                pass     # exhausted is decided in finish_once once in-flight placements are known
        await s.commit()
    return made


# --------------------------------------------------------------------------------------
# 2) posting
# --------------------------------------------------------------------------------------
async def post_once(bot: Bot, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    posted = 0
    async with AsyncSessionLocal() as s:
        ids = [r[0] for r in (await s.execute(select(AdPlacement.id).where(
            AdPlacement.status == "scheduled", AdPlacement.scheduled_for <= now).limit(20))).all()]
    for pid in ids:
        async with AsyncSessionLocal() as s:
            p = (await s.execute(select(AdPlacement).where(AdPlacement.id == pid).with_for_update())).scalar_one_or_none()
            if not p or p.status != "scheduled":
                continue
            c = (await s.execute(select(AdCampaign).where(AdCampaign.id == p.campaign_id).with_for_update())).scalar_one()
            ch = (await s.execute(select(AdChannel).where(AdChannel.id == p.channel_id))).scalar_one()
            if c.status != "active" or ch.status != "active":
                await release_placement(s, p, c, "failed", "campaign or channel not active")
                await s.commit()
                continue
            try:
                kb = post_kb(c, p.id)
                if c.media_file_id:
                    msg = await bot.send_photo(ch.chat_id, c.media_file_id, caption=post_html(c), reply_markup=kb, parse_mode=ParseMode.HTML)
                else:
                    msg = await bot.send_message(ch.chat_id, post_html(c), reply_markup=kb, parse_mode=ParseMode.HTML,
                                                 disable_web_page_preview=True)
            except (TelegramRetryAfter, TelegramNetworkError):
                await s.rollback()
                continue                                     # try again next tick
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                logger.warning(f"ad post failed in {ch.chat_id}: {e}")
                await release_placement(s, p, c, "failed", str(e))
                ch.status, ch.pause_reason = "paused", "bot cannot post (check admin rights)"
                owner_lang = await _lang(s, ch.owner_id)
                title, owner = ch.title, ch.owner_id
                await s.commit()
                await _notify(bot, owner,
                              f"⚠️ <b>{esc(title)}</b> চ্যানেলে বট পোস্ট করতে পারেনি, তাই চ্যানেলটি বিরতিতে গেছে। বটকে অ্যাডমিন (পোস্ট করার অনুমতিসহ) বানিয়ে আবার চালু করুন।",
                              f"⚠️ The bot could not post in <b>{esc(title)}</b>, so the channel was paused. Make the bot an admin with permission to post, then resume it.",
                              owner_lang)
                continue
            p.status, p.message_id, p.posted_at = "posted", msg.message_id, now
            p.expires_at = now + timedelta(hours=ch.post_lifetime_hours)
            try:
                p.members_at_post = await bot.get_chat_member_count(ch.chat_id)
            except Exception:
                p.members_at_post = ch.members
            owner, title, hrs = ch.owner_id, ch.title, ch.post_lifetime_hours
            owner_lang = await _lang(s, owner)
            await s.commit()
            posted += 1
            await _notify(bot, owner, f"📣 <b>{esc(title)}</b> চ্যানেলে একটি বিজ্ঞাপন পোস্ট হয়েছে। এটি {hrs} ঘণ্টা থাকবে।",
                          f"📣 An ad was posted in <b>{esc(title)}</b>. It stays for {hrs}h, then you are paid.", owner_lang)
    return posted


# --------------------------------------------------------------------------------------
# 3) view counters + early-deletion detection
# --------------------------------------------------------------------------------------
async def sync_once(bot: Bot, now: Optional[datetime] = None, limit: int = 30) -> int:
    now = now or datetime.now(timezone.utc)
    stale = now - timedelta(minutes=20)
    n = 0
    async with AsyncSessionLocal() as s:
        ids = [r[0] for r in (await s.execute(select(AdPlacement.id).where(
            AdPlacement.status == "posted",
            (AdPlacement.views_checked_at.is_(None)) | (AdPlacement.views_checked_at <= stale)).limit(limit))).all()]
    for pid in ids:
        async with AsyncSessionLocal() as s:
            p = (await s.execute(select(AdPlacement).where(AdPlacement.id == pid).with_for_update())).scalar_one_or_none()
            if not p or p.status != "posted":
                continue
            c = (await s.execute(select(AdCampaign).where(AdCampaign.id == p.campaign_id).with_for_update())).scalar_one()
            ch = (await s.execute(select(AdChannel).where(AdChannel.id == p.channel_id))).scalar_one()
            exists = await post_exists(bot, p, c, ch)
            if exists is False:
                await _removed_early(bot, s, p, c, ch)
                await s.commit()
                n += 1
                continue
            if AdRules.VIEW_SYNC and ch.username and p.message_id:
                v = await fetch_views(ch.username, p.message_id)
                if v is not None:
                    p.views = max(p.views, v)
            p.views_checked_at = now
            await s.commit()
            n += 1
    return n


async def _removed_early(bot: Bot, s, p: AdPlacement, c: AdCampaign, ch: AdChannel) -> None:
    await release_placement(s, p, c, "removed_early", "ad removed before its time")
    ch.strikes += 1
    owner, title, strikes = ch.owner_id, ch.title, ch.strikes
    if ch.strikes >= AdRules.MAX_STRIKES:
        ch.status, ch.pause_reason = "paused", "too many early deletions"
    lang = await _lang(s, owner)
    asyncio.create_task(_notify(
        bot, owner,
        f"⚠️ <b>{esc(title)}</b> চ্যানেল থেকে বিজ্ঞাপন সময়ের আগে মুছে ফেলা হয়েছে (সতর্কতা {strikes}/{AdRules.MAX_STRIKES})। এর জন্য কোনো পেমেন্ট হবে না।",
        f"⚠️ An ad was removed from <b>{esc(title)}</b> before its time (warning {strikes}/{AdRules.MAX_STRIKES}). No payment for it.", lang))


# --------------------------------------------------------------------------------------
# 4) settlement of finished placements
# --------------------------------------------------------------------------------------
async def settle_once(bot: Bot, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    done = 0
    async with AsyncSessionLocal() as s:
        ids = [r[0] for r in (await s.execute(select(AdPlacement.id).where(
            AdPlacement.status == "posted", AdPlacement.expires_at <= now).limit(30))).all()]
    for pid in ids:
        async with AsyncSessionLocal() as s:
            p = (await s.execute(select(AdPlacement).where(AdPlacement.id == pid).with_for_update())).scalar_one_or_none()
            if not p or p.status != "posted":
                continue
            c = (await s.execute(select(AdCampaign).where(AdCampaign.id == p.campaign_id).with_for_update())).scalar_one()
            ch = (await s.execute(select(AdChannel).where(AdChannel.id == p.channel_id).with_for_update())).scalar_one()
            exists = await post_exists(bot, p, c, ch)
            if exists is None:
                if now - _aware(p.expires_at) < timedelta(hours=6):
                    await s.rollback()
                    continue                                     # cannot tell yet — retry for up to 6h, then trust
                exists = True
            if exists is False:
                await _removed_early(bot, s, p, c, ch)
                await s.commit()
                continue
            if AdRules.VIEW_SYNC and ch.username and p.message_id:
                v = await fetch_views(ch.username, p.message_id)
                if v is not None:
                    p.views = max(p.views, v)
            owner = await _user_locked(s, ch.owner_id)
            if owner:
                owner.balance += p.price
                owner.total_earned += p.price
                xp = int((p.price / 1000) * BusinessRules.XP_PER_1000_COINS)
                owner.add_xp(xp)
                s.add(Transaction(user_id=owner.id, amount=p.price, type=TransactionType.TASK_REWARD,
                                  description=f"Ad earnings: {ch.title}"[:255], reference_id=p.id, reference_type="ad_placement",
                                  balance_after=owner.balance))
            ch.total_posts += 1
            ch.total_earned += p.price
            p.status = "completed"
            title, owner_id, views, clicks, price = ch.title, ch.owner_id, p.views, p.clicks, p.price
            auto_delete, chat_id, mid = ch.auto_delete, ch.chat_id, p.message_id
            lang = await _lang(s, owner_id)
            await s.commit()
            done += 1
            if auto_delete and mid:
                try:
                    await bot.delete_message(chat_id, mid)
                except Exception:
                    pass
            await _notify(bot, owner_id,
                          f"💰 <b>{esc(title)}</b> চ্যানেলের বিজ্ঞাপন সম্পন্ন: <b>+{price:,.0f} GRAM</b>\n👁 {views:,} · 👆 {clicks:,}",
                          f"💰 Ad finished in <b>{esc(title)}</b>: <b>+{price:,.0f} GRAM</b>\n👁 {views:,} · 👆 {clicks:,}", lang)
    return done


# --------------------------------------------------------------------------------------
# 5) closing campaigns whose time or budget is over
# --------------------------------------------------------------------------------------
async def finish_once(bot: Bot, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    closed = 0
    async with AsyncSessionLocal() as s:
        camps = (await s.execute(select(AdCampaign).where(AdCampaign.status.in_(("active", "paused"))).limit(300))).scalars().all()
        for c in camps:
            end = _aware(c.end_at)
            status = None
            if end and end <= now:
                status = "finished"
            elif remaining(c) < AdRules.PRICE_FLOOR + fee_of(AdRules.PRICE_FLOOR):
                status = "exhausted"
            if not status:
                continue
            left = await close_campaign(s, c, status)
            closed += 1
            lang = await _lang(s, c.advertiser_id)
            await s.commit()
            why_bn, why_en = ("সময় শেষ", "its end date was reached") if status == "finished" else ("বাজেট শেষ", "its budget was used up")
            await _notify(bot, c.advertiser_id,
                          f"🏁 বিজ্ঞাপন «{esc(c.title)}» বন্ধ হয়েছে ({why_bn})।" + (f" অব্যবহৃত {left:,.0f} GRAM ফেরত দেওয়া হয়েছে।" if left > 0 else ""),
                          f"🏁 Your ad “{esc(c.title)}” stopped: {why_en}." + (f" {left:,.0f} unused GRAM were returned." if left > 0 else ""), lang)
    return closed


async def ads_worker(bot: Bot):
    """Runs forever next to the bot."""
    while True:
        try:
            await finish_once(bot)
            await schedule_once()
            await post_once(bot)
            await sync_once(bot)
            await settle_once(bot)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"ads worker error: {e}")
        await asyncio.sleep(AdRules.TICK)

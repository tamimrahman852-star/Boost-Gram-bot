"""Channel monetization: performance-based ads (CPM / CPC) with exact posting times chosen by the channel owner.

How it works
  * The channel owner picks the exact local times (HH:MM, any number up to AdRules.MAX_SLOTS) at which the bot posts an ad.
  * An advertiser funds a campaign and bids per 1,000 views (CPM) or per click (CPC). The budget is held in escrow.
  * At every posting time the bot runs a small auction between the campaigns that fit that channel (highest expected
    revenue per 1,000 views wins) and posts the winner. The ad carries the advertiser's button and a "via @Bot" button.
  * While the ad is live it is measured every few minutes and billed: spent = bid x views/1000  (CPM)  or  bid x clicks  (CPC).
    Every GRAM spent is split  COMMISSION (20%) -> platform,  the rest (80%) -> channel owner (paid when the ad ends).
  * When the budget is used up the campaign stops and its ads are removed from the channels; unused budget goes back to the advertiser.
  * Ads deleted early by the channel owner are not paid (the money returns to the campaign) and earn a strike.

Measured numbers
  clicks      real: every ad button goes through <PUBLIC_URL>/r/<placement> (one count per visitor per day)
  bot starts  real: the "via @Bot" button carries a start payload (see handlers/start.py)
  views       read from Telegram's public post widget (t.me/<channel>/<id>?embed=1) for OUR OWN ad posts only
              (AD_VIEW_SYNC=0 turns this off and CPM campaigns with it). Never invented.
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
def split_spend(amount: Decimal) -> tuple[Decimal, Decimal]:
    """Split money spent by the advertiser into (publisher share, platform fee)."""
    fee = (Decimal(amount) * AdRules.COMMISSION).to_integral_value(rounding="ROUND_HALF_UP")
    return Decimal(amount) - fee, fee


def bid_bounds(pricing: str) -> dict:
    if pricing == "cpc":
        return {"min": int(AdRules.CPC_MIN), "default": int(AdRules.CPC_DEFAULT), "max": int(AdRules.CPC_MAX)}
    return {"min": int(AdRules.CPM_MIN), "default": int(AdRules.CPM_DEFAULT), "max": int(AdRules.CPM_MAX)}


def allowed_pricing() -> list:
    return ["cpm", "cpc"] if AdRules.VIEW_SYNC else ["cpc"]


def gross_for(c: AdCampaign, views: int, clicks: int) -> Decimal:
    """What the advertiser owes for this much delivery."""
    if c.pricing == "cpc":
        return (Decimal(c.bid) * clicks).to_integral_value(rounding="ROUND_DOWN")
    return (Decimal(c.bid) * views / 1000).to_integral_value(rounding="ROUND_DOWN")


def need_to_serve(c: AdCampaign) -> Decimal:
    """Smallest remaining budget at which a new ad is still worth starting."""
    return Decimal(c.bid) if c.pricing == "cpm" else Decimal(c.bid) * 3


def ecpm(c: AdCampaign, ctr: Decimal) -> Decimal:
    """Expected revenue per 1,000 views, used to rank CPM and CPC bids against each other."""
    return Decimal(c.bid) if c.pricing == "cpm" else Decimal(c.bid) * ctr * 1000


_SLOT_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_slots(raw: Optional[str]) -> list:
    return [x for x in (raw or "").split(",") if _SLOT_RE.match(x)]


def clean_slots(items) -> Optional[list]:
    """Validate a list of "HH:MM" strings: unique, sorted, at least MIN_SLOT_GAP_MIN apart. None if invalid."""
    if not isinstance(items, (list, tuple)):
        return None
    out = sorted({str(x).strip() for x in items})
    if not 1 <= len(out) <= AdRules.MAX_SLOTS or not all(_SLOT_RE.match(x) for x in out):
        return None
    mins = [int(x[:2]) * 60 + int(x[3:]) for x in out]
    gaps = [b - a for a, b in zip(mins, mins[1:])] + [mins[0] + 1440 - mins[-1]] if len(mins) > 1 else [1440]
    return out if min(gaps) >= AdRules.MIN_SLOT_GAP_MIN else None


def slot_instants(ch: AdChannel, now: datetime):
    """Posting times of this channel that are due right now: [(key, utc_instant)]. Key = owner-local "YYYY-MM-DD HH:MM"."""
    tz = timedelta(minutes=ch.tz_offset_min or 0)
    today = (now + tz).date()
    out = []
    for day in (today, today - timedelta(days=1)):
        for hhmm in parse_slots(ch.slots):
            local = datetime(day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[3:]))
            utc = (local - tz).replace(tzinfo=timezone.utc)
            if utc <= now < utc + timedelta(minutes=AdRules.SLOT_GRACE_MIN):
                out.append((f"{day.isoformat()} {hhmm}", utc))
    return out


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
    cost = p.price + p.fee                                   # what was billed so far (0 if nothing was delivered yet)
    p.price = p.fee = Decimal("0")
    c.spent = max(Decimal("0"), c.spent - cost)
    if c.status in CLOSED_CAMPAIGN:
        await refund_to_advertiser(s, c, cost, "Ad refund")
    c.total_posts = max(0, c.total_posts - 1)


async def close_campaign(s, c: AdCampaign, status: str, note: str = "Ad budget refund") -> Decimal:
    """Move a campaign to a closed state, take its ads down and return the unused escrow."""
    c = (await s.execute(select(AdCampaign).where(AdCampaign.id == c.id).with_for_update())).scalar_one()
    if c.status in CLOSED_CAMPAIGN:
        return Decimal("0")
    c.status = status
    now = datetime.now(timezone.utc)
    for p in (await s.execute(select(AdPlacement).where(
            AdPlacement.campaign_id == c.id, AdPlacement.status.in_(OPEN_STATUSES)).with_for_update())).scalars().all():
        if p.status == "scheduled":
            p.status, p.failure = "failed", "campaign closed"
        else:
            p.expires_at = now                                   # settle_once removes the post and pays what was earned
    left = remaining(c)
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
# 1) scheduling: at each posting time chosen by a channel owner, run the auction and queue the winner
# --------------------------------------------------------------------------------------
async def schedule_once(now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    made = 0
    async with AsyncSessionLocal() as s:
        channels = (await s.execute(select(AdChannel).where(AdChannel.status == "active", AdChannel.slots != ""))).scalars().all()
        due = []
        for ch in channels:
            due += [(utc, key, ch) for key, utc in slot_instants(ch, now)]
        if not due:
            return 0
        done_keys = {(r[0], r[1]) for r in (await s.execute(select(AdPlacement.channel_id, AdPlacement.slot).where(
            AdPlacement.slot.in_({k for _, k, _ in due}), AdPlacement.channel_id.in_({ch.id for _, _, ch in due})))).all()}
        camps = (await s.execute(select(AdCampaign).where(
            AdCampaign.status == "active", AdCampaign.start_at <= now).limit(500))).scalars().all()
        camps = [c for c in camps if (not c.end_at or _aware(c.end_at) > now) and c.pricing in allowed_pricing()]
        if not camps:
            return 0
        recent = (await s.execute(select(AdPlacement.campaign_id, AdPlacement.channel_id).where(
            AdPlacement.created_at >= day_ago, AdPlacement.status.in_(("scheduled", "posted", "completed"))))).all()
        per_camp, per_pair = {}, set()
        for cid, chid in recent:
            per_camp[cid] = per_camp.get(cid, 0) + 1
            per_pair.add((cid, chid))
        ctr = {}                                           # observed click-through rate per channel (last 30 days)
        for chid, v, k in (await s.execute(select(AdPlacement.channel_id, func.sum(AdPlacement.views), func.sum(AdPlacement.clicks)).where(
                AdPlacement.created_at >= now - timedelta(days=30), AdPlacement.status.in_(("posted", "completed"))).group_by(AdPlacement.channel_id))).all():
            if v and v >= 1000:
                ctr[chid] = min(Decimal("0.2"), Decimal(int(k or 0)) / Decimal(int(v)))
        for utc, key, ch in sorted(due, key=lambda x: x[0]):
            if (ch.id, key) in done_keys:
                continue
            best, best_score = None, Decimal("-1")
            for c in camps:
                if (c.advertiser_id == ch.owner_id or ch.members < c.min_members or per_camp.get(c.id, 0) >= c.posts_per_day
                        or (c.id, ch.id) in per_pair or remaining(c) < need_to_serve(c)):
                    continue
                score = ecpm(c, ctr.get(ch.id, AdRules.DEFAULT_CTR))
                if score > best_score:
                    best, best_score = c, score
            if not best:
                continue                                   # nothing to show; tried again every tick until the grace time ends
            s.add(AdPlacement(campaign_id=best.id, channel_id=ch.id, owner_id=ch.owner_id, advertiser_id=best.advertiser_id,
                              status="scheduled", scheduled_for=utc, slot=key))
            best.total_posts += 1
            per_camp[best.id] = per_camp.get(best.id, 0) + 1
            per_pair.add((best.id, ch.id))
            made += 1
        await s.commit()
    return made


# --------------------------------------------------------------------------------------
# 2) posting
# --------------------------------------------------------------------------------------
async def _send_ad(bot: Bot, c: AdCampaign, ch: AdChannel, pid: str):
    kb, cap = post_kb(c, pid), post_html(c)
    if c.media_file_id and c.media_type == "video":
        return await bot.send_video(ch.chat_id, c.media_file_id, caption=cap, reply_markup=kb, parse_mode=ParseMode.HTML, supports_streaming=True)
    if c.media_file_id and c.media_type == "animation":
        return await bot.send_animation(ch.chat_id, c.media_file_id, caption=cap, reply_markup=kb, parse_mode=ParseMode.HTML)
    if c.media_file_id:
        return await bot.send_photo(ch.chat_id, c.media_file_id, caption=cap, reply_markup=kb, parse_mode=ParseMode.HTML)
    return await bot.send_message(ch.chat_id, cap, reply_markup=kb, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


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
            if c.status != "active" or ch.status != "active" or remaining(c) < need_to_serve(c):
                await release_placement(s, p, c, "failed", "campaign or channel not active")
                await s.commit()
                continue
            try:
                msg = await _send_ad(bot, c, ch, p.id)
            except (TelegramRetryAfter, TelegramNetworkError):
                if now - _aware(p.scheduled_for) > timedelta(minutes=AdRules.SLOT_GRACE_MIN):
                    await release_placement(s, p, c, "failed", "telegram unavailable")
                    await s.commit()
                else:
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
            await s.commit()
            posted += 1
    return posted


# --------------------------------------------------------------------------------------
# 3) measuring + billing + early-deletion detection
# --------------------------------------------------------------------------------------
async def bill_placement(s, p: AdPlacement, c: AdCampaign, ch: AdChannel) -> Decimal:
    """Charge the campaign for what this ad has delivered so far (never more than its remaining budget).
    The publisher's share is only recorded here; it is paid when the ad ends."""
    owed = gross_for(c, p.views, p.clicks) - (p.price + p.fee)
    delta = min(owed, remaining(c))
    if delta < 1:
        return Decimal("0")
    pub, fee = split_spend(delta)
    p.price += pub
    p.fee += fee
    p.billed_views, p.billed_clicks = p.views, p.clicks
    c.spent += delta
    return delta


async def _exhaust_if_needed(s, c: AdCampaign) -> None:
    if c.status == "active" and remaining(c) <= 0:
        await close_campaign(s, c, "exhausted")


async def sync_once(bot: Bot, now: Optional[datetime] = None, limit: int = 40) -> int:
    now = now or datetime.now(timezone.utc)
    stale = now - timedelta(minutes=AdRules.SYNC_MINUTES)
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
            await bill_placement(s, p, c, ch)
            p.views_checked_at = now
            await _exhaust_if_needed(s, c)
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
        f"⚠️ <b>{esc(title)}</b> চ্যানেল থেকে বিজ্ঞাপন সময়ের আগে মুছে ফেলা হয়েছে (সতর্কতা {strikes}/{AdRules.MAX_STRIKES})। এর জন্য কোনো আয় হবে না।",
        f"⚠️ An ad was removed from <b>{esc(title)}</b> before its time (warning {strikes}/{AdRules.MAX_STRIKES}). It earns nothing.", lang))


# --------------------------------------------------------------------------------------
# 4) settlement of finished placements: final measurement, bill, pay the channel owner
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
            if exists is False and c.status not in CLOSED_CAMPAIGN:
                await _removed_early(bot, s, p, c, ch)
                await s.commit()
                continue
            if AdRules.VIEW_SYNC and ch.username and p.message_id:
                v = await fetch_views(ch.username, p.message_id)
                if v is not None:
                    p.views = max(p.views, v)
            await bill_placement(s, p, c, ch)
            earn = p.price
            owner = await _user_locked(s, ch.owner_id)
            if owner and earn > 0:
                owner.balance += earn
                owner.total_earned += earn
                owner.add_xp(int((earn / 1000) * BusinessRules.XP_PER_1000_COINS))
                s.add(Transaction(user_id=owner.id, amount=earn, type=TransactionType.TASK_REWARD,
                                  description=f"Ad earnings: {ch.title}"[:255], reference_id=p.id, reference_type="ad_placement",
                                  balance_after=owner.balance))
            ch.total_posts += 1
            ch.total_earned += earn
            p.status = "completed"
            await _exhaust_if_needed(s, c)
            title, owner_id, views, clicks = ch.title, ch.owner_id, p.views, p.clicks
            auto_delete, chat_id, mid = ch.auto_delete, ch.chat_id, p.message_id
            lang = await _lang(s, owner_id)
            await s.commit()
            done += 1
            if (auto_delete or c.status in CLOSED_CAMPAIGN) and mid:
                try:
                    await bot.delete_message(chat_id, mid)
                except Exception:
                    pass
            await _notify(bot, owner_id,
                          f"💰 <b>{esc(title)}</b> চ্যানেলের বিজ্ঞাপন সম্পন্ন: <b>+{earn:,.0f} GRAM</b>\n👁 {views:,} · 👆 {clicks:,}",
                          f"💰 Ad finished in <b>{esc(title)}</b>: <b>+{earn:,.0f} GRAM</b>\n👁 {views:,} · 👆 {clicks:,}", lang)
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
            elif remaining(c) <= 0:
                status = "exhausted"
            elif remaining(c) < need_to_serve(c):
                open_n = (await s.execute(select(func.count(AdPlacement.id)).where(
                    AdPlacement.campaign_id == c.id, AdPlacement.status.in_(OPEN_STATUSES)))).scalar() or 0
                if not open_n:
                    status = "exhausted"                         # what is left is too little to run another ad
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

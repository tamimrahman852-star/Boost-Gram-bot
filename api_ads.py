"""Mini App API for channel monetization (publishers) and ad campaigns (advertisers).
Mounted by api.install(); uses the same initData authentication as the rest of the API."""
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from sqlalchemy import func, select

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from api import ApiError, Ctx, auth, get_bot, iso, num, _upload_photo, MAX_IMAGE_BYTES
from ads import (
    OPEN_STATUSES, close_campaign, fee_of, post_html, price_bounds, remaining, _aware,
)
from config import BOT_USERNAME, AdRules, logger
from database import (
    AdCampaign, AdChannel, AdPlacement, Transaction, TransactionType, User, redis_client,
)
from helpers import alert_admins, esc

router = APIRouter(prefix="/api")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
LIVE_CAMPAIGN = ("pending_review", "active", "paused")


# --------------------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------------------
def _stats(rows) -> dict:
    """Aggregate placements. Only posted/completed ones carry delivery numbers."""
    done = [p for p in rows if p.status in ("posted", "completed")]
    views = sum(p.views for p in done)
    clicks = sum(p.clicks for p in done)
    starts = sum(p.bot_starts for p in done)
    paid = sum((p.price + p.fee for p in done), Decimal("0"))
    earned = sum((p.price for p in rows if p.status == "completed"), Decimal("0"))
    return {
        "posts": len(done), "completed": sum(1 for p in done if p.status == "completed"),
        "live": sum(1 for p in done if p.status == "posted"),
        "views": views, "clicks": clicks, "bot_starts": starts,
        "ctr": round(clicks / views * 100, 2) if views else 0,
        "spent": num(paid), "earned": num(earned),
        # cost the advertiser paid per click / per 1,000 views (price + platform fee)
        "cpc": num(round(paid / clicks, 2)) if clicks else None,
        "cpm": num(round(paid / views * 1000, 2)) if views else None,
        # what the publisher earned per 1,000 views / per click
        "rpm": num(round(earned / views * 1000, 2)) if views and earned else None,
        "epc": num(round(earned / clicks, 2)) if clicks and earned else None,
    }


def _series(rows, days: int) -> list:
    today = datetime.now(timezone.utc).date()
    keys = [(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    acc = {k: {"views": 0, "clicks": 0, "earned": Decimal("0"), "spent": Decimal("0")} for k in keys}
    for p in rows:
        if p.status not in ("posted", "completed") or not p.posted_at:
            continue
        d = _aware(p.posted_at).date()
        if d in acc:
            acc[d]["views"] += p.views
            acc[d]["clicks"] += p.clicks
            acc[d]["spent"] += p.price + p.fee
            if p.status == "completed":
                acc[d]["earned"] += p.price
    return [{"date": k.isoformat(), "views": v["views"], "clicks": v["clicks"], "earned": num(v["earned"]), "spent": num(v["spent"])}
            for k, v in acc.items()]


def _placement_dto(p: AdPlacement, ch: Optional[AdChannel] = None, c: Optional[AdCampaign] = None, side: str = "pub") -> dict:
    d = {"id": p.id, "status": p.status, "posted_at": iso(p.posted_at), "expires_at": iso(p.expires_at),
         "views": p.views, "clicks": p.clicks, "bot_starts": p.bot_starts, "members": p.members_at_post,
         "ctr": round(p.clicks / p.views * 100, 2) if p.views else 0, "failure": p.failure,
         "created_at": iso(p.created_at)}
    if side == "pub":
        d["earn"] = num(p.price)
        if c:
            d["campaign_title"] = c.title
    else:
        d["cost"] = num(p.price + p.fee)
        d["price"] = num(p.price)
        d["fee"] = num(p.fee)
        if ch:
            d["channel_title"] = ch.title
            d["channel_username"] = ch.username
    return d


def _channel_dto(ch: AdChannel, rows) -> dict:
    return {
        "id": ch.id, "title": ch.title, "username": ch.username, "members": ch.members, "status": ch.status,
        "pause_reason": ch.pause_reason, "price": num(ch.price_per_post), "bounds": price_bounds(ch.members),
        "tz_offset": ch.tz_offset_min, "window_start": ch.window_start, "window_end": ch.window_end,
        "max_posts": ch.max_posts_per_day, "min_gap": ch.min_gap_hours, "lifetime": ch.post_lifetime_hours,
        "auto_delete": ch.auto_delete, "strikes": ch.strikes, "total_posts": ch.total_posts,
        "earned": num(ch.total_earned), "stats": _stats(rows), "created_at": iso(ch.created_at),
    }


def _campaign_dto(c: AdCampaign, rows) -> dict:
    st = _stats(rows)
    return {
        "id": c.id, "title": c.title, "text": c.text, "has_image": bool(c.media_file_id), "button_text": c.button_text,
        "button_url": c.button_url, "status": c.status, "reject_reason": c.reject_reason,
        "budget": num(c.budget), "spent": num(c.spent), "refunded": num(c.refunded), "remaining": num(remaining(c)),
        "max_price": num(c.max_price_per_post), "posts_per_day": c.posts_per_day, "min_members": c.min_members,
        "start_at": iso(c.start_at), "end_at": iso(c.end_at), "posts": c.total_posts, "stats": st,
        "created_at": iso(c.created_at),
    }


async def _rows_by(session, col, ids):
    if not ids:
        return {}
    out = {i: [] for i in ids}
    for p in (await session.execute(select(AdPlacement).where(col.in_(ids)))).scalars().all():
        out[getattr(p, col.key)].append(p)
    return out


# --------------------------------------------------------------------------------------
# publisher: channels
# --------------------------------------------------------------------------------------
def _rules() -> dict:
    return {
        "commission": float(AdRules.COMMISSION), "min_members": AdRules.MIN_MEMBERS, "lifetimes": list(AdRules.LIFETIMES),
        "max_channels": AdRules.MAX_CHANNELS_PER_USER, "min_budget": num(AdRules.MIN_BUDGET),
        "max_strikes": AdRules.MAX_STRIKES, "external_links": AdRules.ALLOW_EXTERNAL_LINKS, "view_sync": AdRules.VIEW_SYNC,
        "add_link": f"https://t.me/{BOT_USERNAME}?startchannel=true&admin=post_messages+edit_messages+delete_messages",
    }


@router.get("/channels")
async def my_channels(ctx: Ctx = Depends(auth)):
    chans = (await ctx.session.execute(select(AdChannel).where(
        AdChannel.owner_id == ctx.uid, AdChannel.status != "removed").order_by(AdChannel.created_at.desc()))).scalars().all()
    by = await _rows_by(ctx.session, AdPlacement.channel_id, [c.id for c in chans])
    have = {c.chat_id for c in chans}
    cands = []
    raw = await redis_client.hgetall(f"addch:{ctx.uid}")
    for cid, js in (raw or {}).items():
        try:
            d = json.loads(js)
            if int(cid) not in have:
                cands.append({"chat_id": int(cid), "title": d.get("title"), "username": d.get("username")})
        except Exception:
            continue
    return {"items": [_channel_dto(c, by.get(c.id, [])) for c in chans], "candidates": cands, "rules": _rules()}


async def _verify_channel(ctx: Ctx, chat_ref) -> dict:
    """Check with Telegram that this is a public channel the user manages and the bot can post in."""
    bot = get_bot()
    try:
        chat = await bot.get_chat(chat_ref)
    except (TelegramBadRequest, TelegramForbiddenError):
        raise ctx.err("not_found", "❌ চ্যানেলটি পাওয়া যায়নি।", "❌ Channel not found.", 404)
    if chat.type != "channel":
        raise ctx.err("bad_link", "❌ এটি চ্যানেল নয়।", "❌ That is not a channel.")
    if not chat.username:
        raise ctx.err("public_required", "❌ চ্যানেলটি পাবলিক (username সহ) হতে হবে।", "❌ The channel must be public (have a username) so views and clicks can be verified.")
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
    except (TelegramBadRequest, TelegramForbiddenError):
        me = None
    if not me or me.status != "administrator" or not getattr(me, "can_post_messages", False):
        raise ctx.err("not_channel_admin", f"❌ @{BOT_USERNAME} কে চ্যানেলে অ্যাডমিন (পোস্ট করার অনুমতিসহ) বানান।",
                      f"❌ Add @{BOT_USERNAME} to the channel as an administrator with permission to post messages.")
    try:
        who = await bot.get_chat_member(chat.id, ctx.uid)
    except (TelegramBadRequest, TelegramForbiddenError):
        who = None
    if not who or who.status not in ("creator", "administrator"):
        raise ctx.err("not_owner", "❌ আপনি এই চ্যানেলের অ্যাডমিন নন।", "❌ You are not an administrator of that channel.", 403)
    try:
        members = await bot.get_chat_member_count(chat.id)
    except Exception:
        members = 0
    if members < AdRules.MIN_MEMBERS:
        raise ctx.err("too_small", f"❌ কমপক্ষে {AdRules.MIN_MEMBERS} জন সদস্য লাগবে।", f"❌ A channel needs at least {AdRules.MIN_MEMBERS} members to monetize.")
    return {"chat_id": chat.id, "title": (chat.title or chat.username)[:200], "username": chat.username, "members": members}


def _settings(ctx: Ctx, body: dict, members: int, cur: Optional[AdChannel] = None) -> dict:
    """Validate the publisher's settings (price inside the allowed range, schedule sane)."""
    def pick(key, default):
        return body.get(key, default)
    try:
        b = price_bounds(members)
        price = Decimal(str(pick("price", cur.price_per_post if cur else b["suggested"])))
        ws = int(pick("window_start", cur.window_start if cur else 8))
        we = int(pick("window_end", cur.window_end if cur else 23))
        tz = int(pick("tz_offset", cur.tz_offset_min if cur else 0))
        mp = int(pick("max_posts", cur.max_posts_per_day if cur else 2))
        gap = int(pick("min_gap", cur.min_gap_hours if cur else 6))
        life = int(pick("lifetime", cur.post_lifetime_hours if cur else 24))
        auto = bool(pick("auto_delete", cur.auto_delete if cur else True))
    except (InvalidOperation, ValueError, TypeError):
        raise ctx.err("range", "❌ ভুল মান।", "❌ Invalid value.")
    if not price.is_finite() or price != price.to_integral_value() or not (b["min"] <= price <= b["max"]):
        raise ctx.err("range", f"❌ মূল্য {b['min']:,}–{b['max']:,} এর মধ্যে হতে হবে।", f"❌ Price must be between {b['min']:,} and {b['max']:,} GRAM.")
    if not (0 <= ws <= 23 and 1 <= we <= 24 and -720 <= tz <= 840 and 1 <= mp <= 6 and 1 <= gap <= 24 and life in AdRules.LIFETIMES):
        raise ctx.err("range", "❌ সময়সূচির মান ঠিক নয়।", "❌ The schedule values are out of range.")
    return dict(price_per_post=price, window_start=ws, window_end=we, tz_offset_min=tz, max_posts_per_day=mp,
                min_gap_hours=gap, post_lifetime_hours=life, auto_delete=auto)


@router.post("/channels")
async def channel_add(body: dict = Body(...), ctx: Ctx = Depends(auth)):
    ref = body.get("chat_id") or str(body.get("username", "")).strip()
    if isinstance(ref, str):
        m = re.match(r"^(?:https?://)?(?:t(?:elegram)?\.me/)?@?([A-Za-z][A-Za-z0-9_]{3,31})/?$", ref)
        if not m:
            raise ctx.err("bad_link", "❌ সঠিক @username দিন।", "❌ Enter a valid @username.")
        ref = "@" + m.group(1)
    else:
        ref = int(ref)
    info = await _verify_channel(ctx, ref)
    mine = (await ctx.session.execute(select(func.count(AdChannel.id)).where(
        AdChannel.owner_id == ctx.uid, AdChannel.status != "removed"))).scalar() or 0
    existing = (await ctx.session.execute(select(AdChannel).where(AdChannel.chat_id == info["chat_id"]).with_for_update())).scalar_one_or_none()
    if existing and existing.status != "removed" and existing.owner_id != ctx.uid:
        await ctx.session.rollback()
        raise ctx.err("duplicate", "❌ চ্যানেলটি আরেকজন ইতিমধ্যে যোগ করেছেন।", "❌ This channel is already listed by someone else.")
    if existing and existing.status != "removed":
        await ctx.session.rollback()
        raise ctx.err("duplicate", "❌ চ্যানেলটি ইতিমধ্যে যোগ করা আছে।", "❌ You already added this channel.")
    if mine >= AdRules.MAX_CHANNELS_PER_USER:
        await ctx.session.rollback()
        raise ctx.err("too_many", "❌ চ্যানেলের সীমা শেষ।", "❌ Channel limit reached.")
    st = _settings(ctx, body, info["members"], None)
    if existing:                         # re-activating a channel that was removed earlier
        existing.owner_id, existing.title, existing.username, existing.members = ctx.uid, info["title"], info["username"], info["members"]
        for k, v in st.items():
            setattr(existing, k, v)
        existing.status, existing.pause_reason, existing.strikes = "active", None, 0
        ch = existing
    else:
        ch = AdChannel(owner_id=ctx.uid, chat_id=info["chat_id"], title=info["title"], username=info["username"],
                       members=info["members"], status="active", **st)
        ctx.session.add(ch)
    await ctx.session.commit()
    await redis_client.hdel(f"addch:{ctx.uid}", str(info["chat_id"]))
    return {"channel": _channel_dto(ch, [])}


async def _own_channel(ctx: Ctx, cid: str, lock: bool = False) -> AdChannel:
    if not UUID_RE.match(cid):
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    q = select(AdChannel).where(AdChannel.id == cid, AdChannel.owner_id == ctx.uid, AdChannel.status != "removed")
    ch = (await ctx.session.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
    if not ch:
        await ctx.session.rollback()
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    return ch


@router.post("/channels/{cid}/update")
async def channel_update(cid: str, body: dict = Body(...), ctx: Ctx = Depends(auth)):
    ch = await _own_channel(ctx, cid, lock=True)
    for k, v in _settings(ctx, body, ch.members, ch).items():
        setattr(ch, k, v)
    await ctx.session.commit()
    rows = (await ctx.session.execute(select(AdPlacement).where(AdPlacement.channel_id == cid))).scalars().all()
    return {"channel": _channel_dto(ch, rows)}


@router.post("/channels/{cid}/{action}")
async def channel_action(cid: str, action: str, ctx: Ctx = Depends(auth)):
    if action not in ("pause", "resume", "remove", "refresh"):
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    ch = await _own_channel(ctx, cid, lock=True)
    if action == "pause":
        ch.status, ch.pause_reason = "paused", None
    elif action == "resume":
        info = await _verify_channel(ctx, ch.chat_id)        # the bot must still be able to post
        ch.members, ch.title, ch.username = info["members"], info["title"], info["username"]
        ch.status, ch.pause_reason = "active", None
    elif action == "refresh":
        info = await _verify_channel(ctx, ch.chat_id)
        ch.members, ch.title, ch.username = info["members"], info["title"], info["username"]
    else:
        open_n = (await ctx.session.execute(select(func.count(AdPlacement.id)).where(
            AdPlacement.channel_id == cid, AdPlacement.status.in_(OPEN_STATUSES)))).scalar() or 0
        if open_n:
            await ctx.session.rollback()
            raise ctx.err("busy", "❌ চলমান বিজ্ঞাপন শেষ হওয়া পর্যন্ত অপেক্ষা করুন।", "❌ Wait until the running ads finish, then remove the channel.")
        ch.status = "removed"
    await ctx.session.commit()
    return {"status": ch.status}


@router.get("/channels/{cid}/stats")
async def channel_stats(cid: str, days: int = 14, ctx: Ctx = Depends(auth)):
    ch = await _own_channel(ctx, cid)
    days = min(max(days, 7), 60)
    rows = (await ctx.session.execute(select(AdPlacement).where(AdPlacement.channel_id == cid)
                                      .order_by(AdPlacement.created_at.desc()).limit(500))).scalars().all()
    camps = {c.id: c for c in (await ctx.session.execute(select(AdCampaign).where(
        AdCampaign.id.in_({p.campaign_id for p in rows} or {"-"})))).scalars().all()}
    return {"channel": _channel_dto(ch, rows), "series": _series(rows, days),
            "recent": [_placement_dto(p, c=camps.get(p.campaign_id), side="pub") for p in rows[:25]]}


# --------------------------------------------------------------------------------------
# advertiser: campaigns
# --------------------------------------------------------------------------------------
def _clean_link(ctx: Ctx, url: str) -> str:
    url = (url or "").strip()
    if len(url) > 400 or not re.match(r"^https?://", url, re.I):
        raise ctx.err("bad_link", "❌ সঠিক লিংক দিন (https://...)।", "❌ Enter a valid link starting with https://")
    host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].lower()
    if not AdRules.ALLOW_EXTERNAL_LINKS and host not in ("t.me", "telegram.me", "www.t.me"):
        raise ctx.err("bad_link", "❌ আপাতত শুধু t.me লিংক চলবে।", "❌ Only t.me links are accepted for ads.")
    return url


async def _eligible(session, uid: int, max_price: Decimal, min_members: int):
    return [c for c in (await session.execute(select(AdChannel).where(AdChannel.status == "active"))).scalars().all()
            if c.owner_id != uid and c.price_per_post <= max_price and c.members >= min_members]


@router.get("/ads/estimate")
async def ads_estimate(budget: float = 0, max_price: float = 0, min_members: int = 0, days: int = 7, posts_per_day: int = 3,
                       ctx: Ctx = Depends(auth)):
    chans = await _eligible(ctx.session, ctx.uid, Decimal(str(max(max_price, 0))), max(min_members, 0))
    out = {"channels": len(chans), "price_min": None, "price_max": None, "posts": 0, "reach": 0, "avg_price": 0, "fee": 0, "avg_cost": 0}
    if chans:
        prices = [c.price_per_post for c in chans]
        avg = sum(prices) / len(prices)
        fee = fee_of(avg)
        possible = int(Decimal(str(max(budget, 0))) // (avg + fee))
        cap = max(1, min(posts_per_day, 20)) * max(1, min(days, 90))
        posts = min(possible, cap, len(chans) * max(1, min(days, 90)))
        out.update({"price_min": num(min(prices)), "price_max": num(max(prices)), "avg_price": num(avg), "fee": num(fee),
                    "avg_cost": num(avg + fee), "posts": posts, "reach": int(posts * sum(c.members for c in chans) / len(chans))})
    out["commission"] = float(AdRules.COMMISSION)
    return out


@router.get("/ads/campaigns")
async def ads_list(ctx: Ctx = Depends(auth)):
    camps = (await ctx.session.execute(select(AdCampaign).where(AdCampaign.advertiser_id == ctx.uid)
                                       .order_by(AdCampaign.created_at.desc()).limit(100))).scalars().all()
    by = await _rows_by(ctx.session, AdPlacement.campaign_id, [c.id for c in camps])
    tot = _stats([p for rows in by.values() for p in rows])
    return {"items": [_campaign_dto(c, by.get(c.id, [])) for c in camps], "totals": tot, "rules": _rules()}


@router.post("/ads/campaigns")
async def ads_create(title: str = Form(...), text: str = Form(...), button_text: str = Form(...), button_url: str = Form(...),
                     budget: str = Form(...), max_price: str = Form(...), posts_per_day: int = Form(3), days: int = Form(7),
                     min_members: int = Form(0), start_in_hours: int = Form(0), image: Optional[UploadFile] = File(None),
                     ctx: Ctx = Depends(auth)):
    title, text, button_text = title.strip()[:120], text.strip(), button_text.strip()
    if not 10 <= len(text) <= 900 or not 2 <= len(title) <= 120 or not 2 <= len(button_text) <= 32:
        raise ctx.err("invalid", "❌ লেখা ছোট বা বড়।", "❌ Check the length of your title, text (10–900 characters) and button.")
    url = _clean_link(ctx, button_url)
    try:
        bud, mp = Decimal(budget), Decimal(max_price)
        if not (bud.is_finite() and mp.is_finite()) or bud != bud.to_integral_value() or mp != mp.to_integral_value():
            raise InvalidOperation
    except InvalidOperation:
        raise ctx.err("range", "❌ ভুল সংখ্যা।", "❌ Invalid number.")
    if not (AdRules.MIN_BUDGET <= bud <= AdRules.MAX_BUDGET) or not (AdRules.PRICE_FLOOR <= mp <= AdRules.PRICE_CEIL) \
            or not (1 <= posts_per_day <= 20 and 1 <= days <= 90 and 0 <= min_members <= 10_000_000 and 0 <= start_in_hours <= 24 * 30):
        raise ctx.err("range", f"❌ বাজেট কমপক্ষে {AdRules.MIN_BUDGET:,.0f} GRAM।", f"❌ Budget, price or schedule is out of range (minimum budget {AdRules.MIN_BUDGET:,.0f} GRAM).")
    if bud < mp + fee_of(mp):
        raise ctx.err("range", "❌ বাজেট অন্তত একটি পোস্টের খরচের সমান হতে হবে।", "❌ The budget must cover at least one post at your maximum price.")
    file_id = None
    if image is not None and image.filename:
        file_id, _u = await _upload_photo(ctx, await image.read(MAX_IMAGE_BYTES + 1))
    lock = f"lock:adcamp:{ctx.uid}"
    if not await redis_client.set(lock, "1", nx=True, ex=20):
        raise ctx.err("cooldown", "⏳ একটু অপেক্ষা করুন।", "⏳ One moment please.", 429)
    try:
        user = (await ctx.session.execute(select(User).where(User.id == ctx.uid).with_for_update())).scalar_one()
        if user.is_blocked:
            await ctx.session.rollback()
            raise ctx.err("blocked", "❌ অ্যাকাউন্ট ব্লক।", "❌ Account blocked.", 403)
        live = (await ctx.session.execute(select(func.count(AdCampaign.id)).where(
            AdCampaign.advertiser_id == ctx.uid, AdCampaign.status.in_(LIVE_CAMPAIGN)))).scalar() or 0
        if live >= AdRules.MAX_ACTIVE_CAMPAIGNS:
            await ctx.session.rollback()
            raise ctx.err("too_many", "❌ সক্রিয় বিজ্ঞাপনের সীমা শেষ।", f"❌ You can run at most {AdRules.MAX_ACTIVE_CAMPAIGNS} ad campaigns at once.")
        if user.balance < bud:
            await ctx.session.rollback()
            raise ctx.err("insufficient_balance", f"❌ ব্যালেন্স কম। দরকার {bud:,.0f}।", f"❌ Not enough balance. Need {bud:,.0f} GRAM.")
        start = datetime.now(timezone.utc) + timedelta(hours=start_in_hours)
        is_admin = ctx.uid in __import__("config").ADMIN_IDS
        c = AdCampaign(advertiser_id=ctx.uid, title=title, text=text, media_file_id=file_id, button_text=button_text,
                       button_url=url, status="active" if is_admin else "pending_review", budget=bud, max_price_per_post=mp,
                       posts_per_day=posts_per_day, min_members=min_members, start_at=start, end_at=start + timedelta(days=days))
        ctx.session.add(c)
        await ctx.session.flush()
        user.balance -= bud
        ctx.session.add(Transaction(user_id=ctx.uid, amount=-bud, type=TransactionType.CAMPAIGN_PAYMENT,
                                    description=f"Ad campaign: {title}"[:255], reference_id=c.id, reference_type="ad_campaign",
                                    balance_after=user.balance))
        await ctx.session.commit()
        cid, bal = c.id, user.balance
    finally:
        await redis_client.delete(lock)
    if not is_admin:
        try:
            from handlers.ads import notify_admins_new_campaign
            await notify_admins_new_campaign(get_bot(), c, file_id)
        except Exception as e:
            logger.warning(f"ad review notify failed: {e}")
    return {"campaign": _campaign_dto(c, []), "balance": num(bal)}


async def _own_campaign(ctx: Ctx, cid: str, lock: bool = False) -> AdCampaign:
    if not UUID_RE.match(cid):
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    q = select(AdCampaign).where(AdCampaign.id == cid, AdCampaign.advertiser_id == ctx.uid)
    c = (await ctx.session.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
    if not c:
        await ctx.session.rollback()
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    return c


@router.get("/ads/campaigns/{cid}")
async def ads_detail(cid: str, days: int = 14, ctx: Ctx = Depends(auth)):
    c = await _own_campaign(ctx, cid)
    rows = (await ctx.session.execute(select(AdPlacement).where(AdPlacement.campaign_id == cid)
                                      .order_by(AdPlacement.created_at.desc()).limit(500))).scalars().all()
    chans = {x.id: x for x in (await ctx.session.execute(select(AdChannel).where(
        AdChannel.id.in_({p.channel_id for p in rows} or {"-"})))).scalars().all()}
    return {"campaign": _campaign_dto(c, rows), "series": _series(rows, min(max(days, 7), 60)),
            "placements": [_placement_dto(p, ch=chans.get(p.channel_id), side="adv") for p in rows[:40]],
            "preview": post_html(c).replace("<i>", "").replace("</i>", "")}


@router.post("/ads/campaigns/{cid}/{action}")
async def ads_action(cid: str, action: str, body: Optional[dict] = Body(default=None), ctx: Ctx = Depends(auth)):
    if action not in ("pause", "resume", "stop", "fund"):
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    c = await _own_campaign(ctx, cid, lock=True)
    out = {}
    if action == "pause" and c.status == "active":
        c.status = "paused"
    elif action == "resume" and c.status == "paused":
        c.status = "active"
    elif action == "stop" and c.status in LIVE_CAMPAIGN:
        out["refunded"] = num(await close_campaign(ctx.session, c, "stopped", "Ad budget refund"))
    elif action == "fund" and c.status in ("active", "paused", "pending_review"):
        try:
            add = Decimal(str((body or {}).get("amount")))
            if not add.is_finite() or add != add.to_integral_value() or add < 1000 or c.budget + add > AdRules.MAX_BUDGET:
                raise InvalidOperation
        except InvalidOperation:
            await ctx.session.rollback()
            raise ctx.err("range", "❌ ভুল পরিমাণ।", "❌ Invalid amount.")
        user = (await ctx.session.execute(select(User).where(User.id == ctx.uid).with_for_update())).scalar_one()
        if user.balance < add:
            await ctx.session.rollback()
            raise ctx.err("insufficient_balance", "❌ ব্যালেন্স কম।", "❌ Not enough balance.")
        user.balance -= add
        c.budget += add
        ctx.session.add(Transaction(user_id=ctx.uid, amount=-add, type=TransactionType.CAMPAIGN_PAYMENT,
                                    description=f"Ad budget top-up: {c.title}"[:255], reference_id=c.id, reference_type="ad_campaign",
                                    balance_after=user.balance))
        out["balance"] = num(user.balance)
    else:
        await ctx.session.rollback()
        raise ctx.err("invalid", "❌ এখন এটি করা যাবে না।", "❌ That is not possible right now.")
    await ctx.session.commit()
    if "balance" not in out:
        out["balance"] = num((await ctx.session.execute(select(User.balance).where(User.id == ctx.uid))).scalar())
    return {"status": c.status, "remaining": num(remaining(c)), **out}

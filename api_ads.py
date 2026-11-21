"""Mini App API for channel monetization (publishers) and ad campaigns (advertisers).
Mounted by api.install(); uses the same initData authentication as the rest of the API."""
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from sqlalchemy import func, select

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import BufferedInputFile

from api import ApiError, Ctx, auth, get_bot, iso, num, _upload_photo
from ads import (
    OPEN_STATUSES, allowed_pricing, bid_bounds, clean_slots, close_campaign, eligible, next_slot, parse_slots, post_html, remaining,
    self_ad_ok, send_preview, split_spend, _aware,
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
    spent = sum((p.price + p.fee for p in done), Decimal("0"))               # what advertisers paid (100%)
    fee = sum((p.fee for p in done), Decimal("0"))                           # platform's 20%
    earned = sum((p.price for p in rows if p.status == "completed"), Decimal("0"))
    pending = sum((p.price for p in rows if p.status == "posted"), Decimal("0"))
    return {
        "posts": len(done), "completed": sum(1 for p in done if p.status == "completed"),
        "live": sum(1 for p in done if p.status == "posted"),
        "views": views, "impressions": views, "clicks": clicks, "bot_starts": starts,
        "ctr": round(clicks / views * 100, 2) if views else 0,
        "spent": num(spent), "fee": num(fee), "earned": num(earned), "pending": num(pending),
        # advertiser side: effective cost per click / per 1,000 views (fee included)
        "cpc": num(round(spent / clicks, 2)) if clicks else None,
        "cpm": num(round(spent / views * 1000, 2)) if views else None,
        # publisher side: earned (paid + pending) per 1,000 views / per click
        "rpm": num(round((earned + pending) / views * 1000, 2)) if views and (earned + pending) else None,
        "epc": num(round((earned + pending) / clicks, 2)) if clicks and (earned + pending) else None,
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
            acc[d]["earned"] += p.price
    return [{"date": k.isoformat(), "views": v["views"], "clicks": v["clicks"], "earned": num(v["earned"]), "spent": num(v["spent"])}
            for k, v in acc.items()]


def _placement_dto(p: AdPlacement, ch: Optional[AdChannel] = None, c: Optional[AdCampaign] = None, side: str = "pub") -> dict:
    d = {"id": p.id, "status": p.status, "posted_at": iso(p.posted_at), "expires_at": iso(p.expires_at),
         "views": p.views, "clicks": p.clicks, "bot_starts": p.bot_starts, "members": p.members_at_post,
         "ctr": round(p.clicks / p.views * 100, 2) if p.views else 0, "failure": p.failure,
         "created_at": iso(p.created_at)}
    d["slot"] = p.slot
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
        "pause_reason": ch.pause_reason, "tz_offset": ch.tz_offset_min, "slots": parse_slots(ch.slots),
        "lifetime": ch.post_lifetime_hours, "auto_delete": ch.auto_delete, "strikes": ch.strikes, "total_posts": ch.total_posts,
        "earned": num(ch.total_earned), "stats": _stats(rows), "created_at": iso(ch.created_at),
    }


def _campaign_dto(c: AdCampaign, rows) -> dict:
    st = _stats(rows)
    return {
        "id": c.id, "title": c.title, "text": c.text, "has_image": bool(c.media_file_id), "media_type": c.media_type if c.media_file_id else None,
        "button_text": c.button_text, "button_url": c.button_url, "status": c.status, "reject_reason": c.reject_reason,
        "pricing": c.pricing, "bid": num(c.bid),
        "budget": num(c.budget), "spent": num(c.spent), "refunded": num(c.refunded), "remaining": num(remaining(c)),
        "posts_per_day": c.posts_per_day, "min_members": c.min_members,
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
        "default_lifetime": AdRules.DEFAULT_LIFETIME, "max_channels": AdRules.MAX_CHANNELS_PER_USER, "min_budget": num(AdRules.MIN_BUDGET),
        "max_strikes": AdRules.MAX_STRIKES, "external_links": AdRules.ALLOW_EXTERNAL_LINKS, "view_sync": AdRules.VIEW_SYNC,
        "models": allowed_pricing(), "cpm": bid_bounds("cpm"), "cpc": bid_bounds("cpc"),
        "max_slots": AdRules.MAX_SLOTS, "slot_gap": AdRules.MIN_SLOT_GAP_MIN, "slot_grace": AdRules.SLOT_GRACE_MIN,
        "video_mb": AdRules.VIDEO_MAX_BYTES // (1024 * 1024), "image_mb": AdRules.IMAGE_MAX_BYTES // (1024 * 1024),
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


def _settings(ctx: Ctx, body: dict, cur: Optional[AdChannel] = None) -> dict:
    """Validate the publisher's settings: exact posting times (owner-local), time zone, how long each ad stays."""
    try:
        tz = int(body.get("tz_offset", cur.tz_offset_min if cur else 0))
        life = int(body.get("lifetime", cur.post_lifetime_hours if cur else AdRules.DEFAULT_LIFETIME))
        auto = bool(body.get("auto_delete", cur.auto_delete if cur else True))
    except (ValueError, TypeError):
        raise ctx.err("range", "❌ ভুল মান।", "❌ Invalid value.")
    raw = body.get("slots", parse_slots(cur.slots) if cur else ["10:00", "16:00", "21:00"])
    slots = clean_slots(raw)
    if slots is None:
        raise ctx.err("bad_slots", f"❌ ১ থেকে {AdRules.MAX_SLOTS} টি সময় দিন, প্রতিটির মাঝে কমপক্ষে {AdRules.MIN_SLOT_GAP_MIN} মিনিট ব্যবধান রেখে।",
                      f"❌ Pick 1–{AdRules.MAX_SLOTS} posting times (HH:MM), at least {AdRules.MIN_SLOT_GAP_MIN} minutes apart.")
    if not (-720 <= tz <= 840) or life not in AdRules.LIFETIMES:
        raise ctx.err("range", "❌ সময়সূচির মান ঠিক নয়।", "❌ The schedule values are out of range.")
    return dict(slots=",".join(slots), tz_offset_min=tz, max_posts_per_day=len(slots), post_lifetime_hours=life, auto_delete=auto,
                price_per_post=Decimal("0"))


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
    st = _settings(ctx, body, None)
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
    for k, v in _settings(ctx, body, ch).items():
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
    now = datetime.now(timezone.utc)
    live = (await ctx.session.execute(select(AdCampaign).where(AdCampaign.status == "active").limit(500))).scalars().all()
    ok = [c for c in live if eligible(c, ch, now)]
    diag = []                                              # why nothing may be posted right now
    if ch.status != "active":
        diag.append("paused")
    if not parse_slots(ch.slots):
        diag.append("no_slots")
    if not ok:
        diag.append("own_only" if any(c.advertiser_id == ch.owner_id and not self_ad_ok(c, ch) for c in live) else "no_campaign")
    nxt = next_slot(ch, now)
    return {"channel": _channel_dto(ch, rows), "series": _series(rows, days), "next_at": iso(nxt) if nxt else None,
            "eligible_campaigns": len(ok), "diag": diag,
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


async def _upload_media(ctx: Ctx, data: bytes) -> tuple:
    """Photo (JPG/PNG/WebP, 8 MB) or video (MP4/MOV, 10 MB) for an ad. Telegram only hands out a file_id for files it has seen, so
    the file goes to the advertiser's own private chat once and is deleted again. Returns (file_id, media_type)."""
    head = data[:12]
    is_video = head[4:8] == b"ftyp"
    if not data or len(data) > (AdRules.VIDEO_MAX_BYTES if is_video else AdRules.IMAGE_MAX_BYTES):
        mb = AdRules.VIDEO_MAX_BYTES // (1024 * 1024) if is_video else AdRules.IMAGE_MAX_BYTES // (1024 * 1024)
        raise ctx.err("bad_file", f"ফাইলটি পড়া যায়নি বা বড় ({mb}MB এর বেশি)।", f"That file is empty or larger than {mb} MB.")
    if not is_video:
        file_id, _u = await _upload_photo(ctx, data)
        return file_id, "photo"
    bot = get_bot()
    try:
        msg = await bot.send_video(ctx.uid, BufferedInputFile(data, filename="ad.mp4"), supports_streaming=True, disable_notification=True)
    except TelegramForbiddenError:
        raise ctx.err("not_bot", "আগে বটে /start দিন।", "Open the bot chat and press /start first.")
    except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
        raise ctx.err("bad_file", "ভিডিওটি আপলোড হয়নি। MP4 (H.264) ব্যবহার করুন।", "The video could not be uploaded. Use an MP4 (H.264) file.")
    media, kind = (msg.video, "video") if msg.video else ((msg.animation, "animation") if msg.animation else (None, None))
    try:
        await bot.delete_message(ctx.uid, msg.message_id)
    except Exception:
        pass
    if not media:
        raise ctx.err("bad_file", "ভিডিওটি গ্রহণযোগ্য নয়।", "That video is not supported. Use an MP4 (H.264) file.")
    return media.file_id, kind


def _bid_ok(ctx: Ctx, pricing: str, bid: Decimal) -> None:
    if pricing not in allowed_pricing():
        raise ctx.err("invalid", "❌ এই মডেল এখন চালু নেই।", "❌ That pricing model is not available right now.")
    b = bid_bounds(pricing)
    if not bid.is_finite() or bid != bid.to_integral_value() or not (b["min"] <= bid <= b["max"]):
        unit = "প্রতি ১০০০ ভিউ" if pricing == "cpm" else "প্রতি ক্লিক"
        unit_en = "per 1,000 views" if pricing == "cpm" else "per click"
        raise ctx.err("range", f"❌ বিড {unit} {b['min']:,}–{b['max']:,} GRAM এর মধ্যে হতে হবে।", f"❌ A bid {unit_en} must be between {b['min']:,} and {b['max']:,} GRAM.")


@router.get("/ads/estimate")
async def ads_estimate(pricing: str = "cpm", bid: float = 0, budget: float = 0, min_members: int = 0, days: int = 7,
                       ctx: Ctx = Depends(auth)):
    """What this campaign can reach. Everything here is arithmetic on the numbers you enter plus the real channels that fit."""
    chans = [c for c in (await ctx.session.execute(select(AdChannel).where(AdChannel.status == "active", AdChannel.slots != ""))).scalars().all()
             if c.owner_id != ctx.uid and c.members >= max(min_members, 0)]
    bud, bidd = Decimal(str(max(budget, 0))), Decimal(str(max(bid, 0)))
    pub, fee = split_spend(bud) if bud else (Decimal(0), Decimal(0))
    out = {"channels": len(chans), "slots_per_day": sum(len(parse_slots(c.slots)) for c in chans), "audience": sum(c.members for c in chans),
           "commission": float(AdRules.COMMISSION), "publisher_share": num(pub), "platform_fee": num(fee),
           "views": None, "clicks": None, "bid_min": None}
    if bidd > 0 and bud > 0:
        if pricing == "cpc":
            out["clicks"] = int(bud // bidd)
        else:
            out["views"] = int(bud / bidd * 1000)
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
                     pricing: str = Form("cpm"), bid: str = Form(...), budget: str = Form(...), posts_per_day: int = Form(6),
                     days: int = Form(7), min_members: int = Form(0), start_in_hours: int = Form(0),
                     media: Optional[UploadFile] = File(None), ctx: Ctx = Depends(auth)):
    title, text, button_text, pricing = title.strip()[:120], text.strip(), button_text.strip(), pricing.strip().lower()
    if not 10 <= len(text) <= 900 or not 2 <= len(title) <= 120 or not 2 <= len(button_text) <= 32:
        raise ctx.err("invalid", "❌ লেখা ছোট বা বড়।", "❌ Check the length of your title, text (10–900 characters) and button.")
    url = _clean_link(ctx, button_url)
    try:
        bud, bidd = Decimal(budget), Decimal(bid)
        if not bud.is_finite() or bud != bud.to_integral_value():
            raise InvalidOperation
    except InvalidOperation:
        raise ctx.err("range", "❌ ভুল সংখ্যা।", "❌ Invalid number.")
    _bid_ok(ctx, pricing, bidd)
    if not (AdRules.MIN_BUDGET <= bud <= AdRules.MAX_BUDGET) \
            or not (1 <= posts_per_day <= 100 and 1 <= days <= 90 and 0 <= min_members <= 10_000_000 and 0 <= start_in_hours <= 24 * 30):
        raise ctx.err("range", f"❌ বাজেট কমপক্ষে {AdRules.MIN_BUDGET:,.0f} GRAM।", f"❌ Budget or schedule is out of range (minimum budget {AdRules.MIN_BUDGET:,.0f} GRAM).")
    if bud < bidd * (1 if pricing == "cpm" else 3):
        raise ctx.err("range", "❌ বাজেট আপনার বিডের চেয়ে অন্তত কয়েক গুণ বেশি হতে হবে।", "❌ The budget must be larger than your bid so it can buy at least a few results.")
    file_id = media_type = None
    if media is not None and media.filename:
        file_id, media_type = await _upload_media(ctx, await media.read(AdRules.VIDEO_MAX_BYTES + 1))
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
        c = AdCampaign(advertiser_id=ctx.uid, title=title, text=text, media_file_id=file_id, media_type=media_type, button_text=button_text,
                       button_url=url, status="active" if is_admin else "pending_review", budget=bud, pricing=pricing, bid=bidd,
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


# --------------------------------------------------------------------------------------
# preview: the advertiser gets a demo of the ad in his own Telegram chat
# --------------------------------------------------------------------------------------
async def _preview_guard(ctx: Ctx):
    if not await redis_client.set(f"lock:adprev:{ctx.uid}", "1", nx=True, ex=8):
        raise ctx.err("cooldown", "⏳ একটু অপেক্ষা করুন।", "⏳ One moment please.", 429)


async def _send_preview(ctx: Ctx, text: str, button_text: str, button_url: str, file_id, media_type) -> dict:
    try:
        await send_preview(get_bot(), ctx.uid, text, button_text, button_url, file_id, media_type)
    except TelegramForbiddenError:
        raise ctx.err("not_bot", "আগে বটে /start দিন।", "Open the bot chat and press /start first.")
    except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter) as e:
        logger.warning(f"ad preview failed: {e}")
        raise ctx.err("preview_failed", "প্রিভিউ পাঠানো যায়নি।", "The preview could not be sent.")
    return {"ok": True}


@router.post("/ads/preview")
async def ads_preview(text: str = Form(...), button_text: str = Form(...), button_url: str = Form(...),
                      media: Optional[UploadFile] = File(None), ctx: Ctx = Depends(auth)):
    text, button_text = text.strip(), button_text.strip()
    if not 10 <= len(text) <= 900 or not 2 <= len(button_text) <= 32:
        raise ctx.err("invalid", "❌ লেখা ছোট বা বড়।", "❌ Check the length of your text (10–900 characters) and button.")
    url = _clean_link(ctx, button_url)
    await _preview_guard(ctx)
    file_id = media_type = None
    if media is not None and media.filename:
        file_id, media_type = await _upload_media(ctx, await media.read(AdRules.VIDEO_MAX_BYTES + 1))
    return await _send_preview(ctx, text, button_text, url, file_id, media_type)


@router.post("/ads/preview/{cid}")
async def ads_campaign_preview(cid: str, ctx: Ctx = Depends(auth)):
    c = await _own_campaign(ctx, cid)
    await _preview_guard(ctx)
    return await _send_preview(ctx, c.text, c.button_text, c.button_url, c.media_file_id, c.media_type)

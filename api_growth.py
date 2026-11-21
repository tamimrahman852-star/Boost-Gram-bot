"""Growth endpoints: advertiser ROI analytics, daily streak, Lucky Spin (coin sink), VIP/Boost passes, featured slots."""
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Body, Depends
from sqlalchemy import func, select

import api
from api import ApiError, Ctx, auth, num
from config import GrowthRules as G
from database import (Campaign, CampaignStatus, EarlyLeave, RetentionStatus, TaskCompletion, TaskSubmission, Transaction,
                      TransactionType, User, UserPass, redis_client)
from growth import active_pass, aw

router = APIRouter(prefix="/api/growth")


def _now():
    return datetime.now(timezone.utc)


async def _locked_user(ctx: Ctx) -> User:
    return (await ctx.session.execute(select(User).where(User.id == ctx.uid).with_for_update())).scalar_one()


def _tx(user: User, amount: Decimal, ref_type: str, desc: str):
    return Transaction(user_id=user.id, amount=amount, type=TransactionType.ADMIN_ADJUSTMENT, description=desc[:255],
                       reference_type=ref_type, balance_after=user.balance)


async def _pass_expiry(session, uid, kind):
    r = (await session.execute(select(func.max(UserPass.expires_at)).where(UserPass.user_id == uid, UserPass.kind == kind))).scalar()
    r = aw(r)
    return r if r and r > _now() else None


async def _free_spin(session, uid):
    return (await session.execute(select(UserPass).where(UserPass.user_id == uid, UserPass.kind == "spin_free", UserPass.expires_at > _now())
                                  .order_by(UserPass.expires_at).limit(1).with_for_update())).scalar_one_or_none()


@router.get("/perks")
async def perks(ctx: Ctx = Depends(auth)):
    u, today = ctx.user, date.today().isoformat()
    vip, boost = await _pass_expiry(ctx.session, u.id, "vip"), await _pass_expiry(ctx.session, u.id, "boost")
    used = int(await redis_client.get(f"spin:{u.id}:{_now().date()}") or 0)
    return {
        "streak": u.streak_days, "claimed_today": u.streak_last == _now().date().isoformat(),
        "vip_until": vip.isoformat() if vip else None, "boost_until": boost.isoformat() if boost else None,
        "free_spin": (await _free_spin(ctx.session, u.id)) is not None, "spins_left": max(0, G.SPIN_MAX_PER_DAY - used),
        "prizes": [p for p, _ in G.SPIN_PRIZES], "spin_cost": num(G.SPIN_COST),
    }


@router.post("/streak")
async def streak(ctx: Ctx = Depends(auth)):
    user = await _locked_user(ctx)
    today = _now().date()
    if user.streak_last == today.isoformat():
        await ctx.session.rollback()
        raise ctx.err("already", "ℹ️ আজকের বোনাস নেওয়া হয়ে গেছে।", "ℹ️ You already claimed today.")
    user.streak_days = (user.streak_days or 0) + 1 if user.streak_last == (today - timedelta(days=1)).isoformat() else 1
    user.streak_last = today.isoformat()
    vip = await active_pass(ctx.session, user.id, "vip")
    xp = G.STREAK_XP * min(user.streak_days, 7) * (2 if vip else 1)
    user.add_xp(xp)
    free = user.streak_days % 7 == 0
    if free:
        ctx.session.add(UserPass(user_id=user.id, kind="spin_free", price=Decimal("0"), expires_at=_now() + timedelta(days=7)))
    await ctx.session.commit()
    return {"streak": user.streak_days, "xp": xp, "free_spin": free, "user": await api.user_dict(ctx)}


@router.post("/spin")
async def spin(ctx: Ctx = Depends(auth)):
    key = f"spin:{ctx.uid}:{_now().date()}"
    n = await redis_client.incr(key)
    if n == 1:
        await redis_client.expire(key, 90000)
    if n > G.SPIN_MAX_PER_DAY:
        raise ctx.err("limit", "⏳ আজকের স্পিন শেষ।", "⏳ No spins left today.", 429)
    user = await _locked_user(ctx)
    if user.is_blocked:
        await ctx.session.rollback()
        raise ctx.err("blocked", "❌ অ্যাকাউন্ট ব্লক।", "❌ Account blocked.", 403)
    free = await _free_spin(ctx.session, user.id)
    cost = Decimal("0") if free else G.SPIN_COST
    if user.balance < cost:
        await ctx.session.rollback()
        await redis_client.decr(key)
        raise ctx.err("insufficient_balance", "❌ ব্যালেন্স কম।", "❌ Insufficient balance.")
    if free:
        free.expires_at = _now() - timedelta(seconds=1)      # consume the ticket
    total = sum(w for _, w in G.SPIN_PRIZES)
    roll, prize = secrets.randbelow(total), 0
    for p, w in G.SPIN_PRIZES:
        if roll < w:
            prize = p
            break
        roll -= w
    if cost:
        user.balance -= cost
        ctx.session.add(_tx(user, -cost, "spin", "Lucky Spin"))
    if prize:
        user.balance += Decimal(prize)            # NOT counted as earned => never withdrawable (pure in-app credit)
        ctx.session.add(_tx(user, Decimal(prize), "spin", "Lucky Spin prize"))
    await ctx.session.commit()
    return {"prize": prize, "cost": num(cost), "free": bool(free), "balance": num(user.balance), "user": await api.user_dict(ctx)}


@router.post("/pass")
async def buy_pass(body: dict = Body(...), ctx: Ctx = Depends(auth)):
    kind = str(body.get("kind", ""))
    price, days = {"vip": (G.VIP_PRICE, G.VIP_DAYS), "boost": (G.BOOST_PRICE, G.BOOST_DAYS)}.get(kind, (None, None))
    if price is None:
        raise ctx.err("invalid", "❌ ভুল পাস।", "❌ Unknown pass.")
    user = await _locked_user(ctx)
    if user.balance < price:
        await ctx.session.rollback()
        raise ctx.err("insufficient_balance", "❌ ব্যালেন্স কম।", "❌ Insufficient balance.")
    start = max(_now(), await _pass_expiry(ctx.session, user.id, kind) or _now())
    user.balance -= price
    ctx.session.add(UserPass(user_id=user.id, kind=kind, price=price, expires_at=start + timedelta(days=days)))
    ctx.session.add(_tx(user, -price, "pass", f"{kind.upper()} pass {days}d"))
    await ctx.session.commit()
    return {"ok": True, "balance": num(user.balance), "user": await api.user_dict(ctx)}


@router.post("/feature/{cid}")
async def feature(cid: str, body: dict = Body(...), ctx: Ctx = Depends(auth)):
    try:
        days = int(body.get("days"))
    except (TypeError, ValueError):
        days = 0
    if days not in G.FEATURE_DAYS:
        raise ctx.err("invalid", "❌ ভুল সময়।", "❌ Invalid duration.")
    c = await api._get_campaign(ctx.session, cid)
    if not c or c.advertiser_id != ctx.uid or c.status != CampaignStatus.ACTIVE:
        raise ctx.err("unavailable", "❌ ক্যাম্পেইন সক্রিয় নয়।", "❌ Campaign is not active.", 404)
    user = await _locked_user(ctx)
    cost = G.FEATURE_PER_DAY * days
    if await active_pass(ctx.session, user.id, "vip"):
        cost = (cost * (1 - G.VIP_FEATURE_DISCOUNT)).quantize(Decimal("1"))
    if user.balance < cost:
        await ctx.session.rollback()
        raise ctx.err("insufficient_balance", f"❌ ব্যালেন্স কম। দরকার {cost:,.0f} GRAM।", f"❌ Insufficient balance. Need {cost:,.0f} GRAM.")
    c = (await ctx.session.execute(select(Campaign).where(Campaign.id == cid).with_for_update())).scalar_one()
    cur = aw(c.featured_until)
    start = cur if cur and cur > _now() else _now()
    c.featured_until = start + timedelta(days=days)
    user.balance -= cost
    ctx.session.add(_tx(user, -cost, "feature", f"Featured {days}d: {c.title}"))
    await ctx.session.commit()
    return {"ok": True, "until": c.featured_until.isoformat(), "charged": num(cost), "balance": num(user.balance)}


async def _stats(session, c: Campaign) -> dict:
    done = c.completed_count or 0
    by = dict((await session.execute(select(TaskCompletion.retention_status, func.count(TaskCompletion.id)).where(
        TaskCompletion.campaign_id == c.id).group_by(TaskCompletion.retention_status))).all())
    cnt = lambda s: int(by.get(s, 0))
    leaves = (await session.execute(select(func.count(EarlyLeave.id)).where(EarlyLeave.campaign_id == c.id))).scalar() or 0
    dropped = cnt(RetentionStatus.FAILED) + cnt(RetentionStatus.PENALIZED)
    drop = max(dropped, int(leaves))
    subs = dict((await session.execute(select(TaskSubmission.status, func.count(TaskSubmission.id)).where(
        TaskSubmission.campaign_id == c.id).group_by(TaskSubmission.status))).all())
    spent = Decimal(c.spent_budget or 0)
    remaining = max(Decimal("0"), Decimal(c.total_budget) - spent - Decimal(c.refunded_budget or 0))
    return {
        "id": c.id, "title": c.title, "type": c.task_type.value, "status": c.status.value,
        "completions": done, "slots": c.max_completions, "progress": round(done / c.max_completions, 4) if c.max_completions else 0,
        "spent": num(spent), "budget": num(c.total_budget), "remaining": num(remaining),
        "cpa": num(spent / done) if done else 0,
        "retention": {"verified": cnt(RetentionStatus.VERIFIED), "pending": cnt(RetentionStatus.PENDING), "dropped": drop,
                      "drop_rate": round(drop / done, 4) if done else 0},
        "proofs": {"pending": int(subs.get("pending", 0)), "approved": int(subs.get("approved", 0)), "rejected": int(subs.get("rejected", 0))},
        "targeting": {"langs": [x for x in (c.target_langs or "").split(",") if x], "countries": [x for x in (c.target_countries or "").split(",") if x],
                      "niche": c.target_niche},
        "featured_until": aw(c.featured_until).isoformat() if c.featured_until and aw(c.featured_until) > _now() else None,
    }


@router.get("/analytics")
async def analytics(ctx: Ctx = Depends(auth)):
    rows = (await ctx.session.execute(select(Campaign).where(Campaign.advertiser_id == ctx.uid)
                                      .order_by(Campaign.created_at.desc()).limit(50))).scalars().all()
    items = [await _stats(ctx.session, c) for c in rows]
    done, spent = sum(i["completions"] for i in items), sum(i["spent"] for i in items)
    drops = sum(i["retention"]["dropped"] for i in items)
    return {"items": items, "totals": {"campaigns": len(items), "completions": done, "spent": spent,
                                       "cpa": round(spent / done) if done else 0, "drop_rate": round(drops / done, 4) if done else 0,
                                       "remaining": sum(i["remaining"] for i in items)}}


@router.get("/analytics/{cid}")
async def analytics_one(cid: str, ctx: Ctx = Depends(auth)):
    c = await api._get_campaign(ctx.session, cid)
    if not c or c.advertiser_id != ctx.uid:
        raise ctx.err("unavailable", "❌ পাওয়া যায়নি।", "❌ Not found.", 404)
    out = await _stats(ctx.session, c)
    since = _now() - timedelta(days=14)
    times = [r[0] for r in (await ctx.session.execute(select(TaskCompletion.completed_at).where(
        TaskCompletion.campaign_id == c.id, TaskCompletion.completed_at > since).limit(5000))).all()]
    days = {(date.today() - timedelta(days=i)).isoformat(): 0 for i in range(13, -1, -1)}
    for tm in times:
        k = tm.date().isoformat()
        if k in days:
            days[k] += 1
    out["daily"] = [{"d": k, "n": v} for k, v in days.items()]
    return out

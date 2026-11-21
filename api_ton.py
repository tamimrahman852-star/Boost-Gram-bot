"""Mini App endpoints for TON deposits."""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from api import Ctx, auth, iso, num
from config import GRAM_PER_USD, TON_DEPOSIT_MIN_USD, TON_WALLET
from database import TonDeposit, redis_client
from ton import memo_for, poll_once, ton_usd

router = APIRouter(prefix="/api")


@router.get("/ton")
async def ton_info(ctx: Ctx = Depends(auth)):
    rows = (await ctx.session.execute(select(TonDeposit).where(TonDeposit.user_id == ctx.uid)
                                      .order_by(TonDeposit.created_at.desc()).limit(10))).scalars().all()
    rate = await ton_usd() if TON_WALLET else None
    return {
        "enabled": bool(TON_WALLET), "wallet": TON_WALLET or None, "memo": memo_for(ctx.uid),
        "min_usd": num(TON_DEPOSIT_MIN_USD), "gram_per_usd": num(GRAM_PER_USD),
        "ton_usd": float(rate) if rate else None, "gram_per_ton": num(rate * GRAM_PER_USD) if rate else None,
        "history": [{"id": d.id, "ton": float(d.ton_amount), "usd": num(d.usd), "coins": num(d.coins), "status": d.status,
                     "created_at": iso(d.created_at)} for d in rows],
    }


@router.post("/ton/check")
async def ton_check(ctx: Ctx = Depends(auth)):
    """\"I paid\" button: look at the blockchain right now instead of waiting for the 30 s worker."""
    if not TON_WALLET:
        raise ctx.err("unavailable", "❌ TON ডিপোজিট এখন বন্ধ।", "❌ TON deposits are not available right now.")
    if not await redis_client.set(f"lock:tonchk:{ctx.uid}", "1", nx=True, ex=10):
        raise ctx.err("cooldown", "⏳ একটু অপেক্ষা করুন।", "⏳ One moment please.", 429)
    from api import get_bot
    return {"credited": await poll_once(get_bot())}

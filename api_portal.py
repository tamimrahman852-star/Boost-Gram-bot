"""Mini App -> web portal hand-off. The Mini App asks for a one-time link; the portal exchanges its token for a short session."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends
from sqlalchemy import update

import api
from config import PORTAL_ENABLED, PUBLIC_URL
from database import AsyncSessionLocal, PortalLoginToken, User, redis_client
from sqlalchemy import select

router = APIRouter(prefix="/api")
TOKEN_TTL = 180   # seconds a link stays valid


def _h(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


@router.post("/portal/link")
async def portal_link(ctx: api.Ctx = Depends(api.auth)):
    """Mini App only (a portal session cannot mint further links)."""
    if not PORTAL_ENABLED:
        raise ctx.err("disabled", "❌ পোর্টাল বন্ধ আছে।", "❌ The payment portal is turned off.", 403)
    if ctx.tg.get("_portal"):
        raise ctx.err("forbidden", "❌ অ্যাপ থেকে খুলুন।", "❌ Open this from the Mini App.", 403)
    n = await redis_client.incr(f"rl:plink:{ctx.uid}")
    if n == 1:
        await redis_client.expire(f"rl:plink:{ctx.uid}", 60)
    if n > 5:
        raise ctx.err("cooldown", "⏳ একটু অপেক্ষা করুন।", "⏳ Slow down a little.", 429)
    tok = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    ctx.session.add(PortalLoginToken(user_id=ctx.uid, token_hash=_h(tok), expires_at=now + timedelta(seconds=TOKEN_TTL)))
    await ctx.session.commit()
    base = (PUBLIC_URL or "").rstrip("/")
    return {"url": f"{base}/portal#t={tok}", "expires_in": TOKEN_TTL}


@router.post("/portal/session")
async def portal_session(body: dict = Body(...)):
    """Exchange the one-time token for a short portal session. Atomic: a token can be used exactly once."""
    tok = str(body.get("token", ""))
    if not PORTAL_ENABLED or len(tok) < 20 or len(tok) > 200:
        raise api.ApiError("unauthorized", "Link is invalid or expired. Reopen it from the app.", 401)
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as s:
        res = await s.execute(
            update(PortalLoginToken)
            .where(PortalLoginToken.token_hash == _h(tok), PortalLoginToken.used_at.is_(None), PortalLoginToken.expires_at > now)
            .values(used_at=now).returning(PortalLoginToken.user_id))
        row = res.first()
        await s.commit()
    if not row:
        raise api.ApiError("unauthorized", "Link is invalid or expired. Reopen it from the app.", 401)
    return {"session": api.portal_session_make(int(row[0])), "ttl": api.PORTAL_TTL}

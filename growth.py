"""Segmentation, targeting prices and pass multipliers shared by the bot, the API and the workers."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import and_, func, or_, select

from config import GrowthRules as G


def aw(dt):
    """Timezone-aware UTC (SQLite hands back naive datetimes; Postgres aware ones)."""
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def csv(v: Optional[str]) -> list:
    return [x for x in (v or "").split(",") if x]


def clean_list(vals, allowed: Optional[Iterable] = None, upper=False, maxn=10) -> list:
    out = []
    for x in vals or []:
        x = str(x).strip()
        x = x.upper() if upper else x.lower()
        if not x or x in out or (allowed is not None and x not in allowed):
            continue
        if upper and not (len(x) == 2 and x.isalpha()):
            continue
        out.append(x)
    return out[:maxn]


def targeting_ok(c, user) -> bool:
    """Server-side rule: untargeted campaigns are open; a targeted one needs the user's matching profile field."""
    langs, countries = csv(c.target_langs), csv(c.target_countries)
    if langs and (user.language or "en") not in langs:
        return False
    if countries and (user.country or "") not in countries:
        return False
    if c.target_niche and c.target_niche not in csv(user.interests):
        return False
    return True


def targeting_filters(user) -> list:
    """The same rule as targeting_ok, as SQL conditions for the task list."""
    from database import Campaign
    out = []
    lang = (user.language or "en") if user else "en"
    out.append(or_(Campaign.target_langs.is_(None), Campaign.target_langs == "",
                   func.concat(",", Campaign.target_langs, ",").like(f"%,{lang},%")))
    cc = (getattr(user, "country", None) or "") if user else ""
    out.append(or_(Campaign.target_countries.is_(None), Campaign.target_countries == "",
                   func.concat(",", Campaign.target_countries, ",").like(f"%,{cc},%") if cc else False))
    ints = csv(getattr(user, "interests", None)) if user else []
    out.append(or_(Campaign.target_niche.is_(None), Campaign.target_niche == "",
                   Campaign.target_niche.in_(ints) if ints else False))
    return out


def targeting_surcharge(budget: Decimal, langs, countries, niche) -> Decimal:
    rate = (G.FEE_LANG if langs else 0) + (G.FEE_COUNTRY if countries else 0) + (G.FEE_NICHE if niche else 0)
    return (budget * Decimal(rate)).quantize(Decimal("1"))


async def active_pass(session, uid: int, kind: str) -> bool:
    from database import UserPass
    return (await session.execute(select(UserPass.id).where(
        UserPass.user_id == uid, UserPass.kind == kind, UserPass.expires_at > datetime.now(timezone.utc)).limit(1))).first() is not None


async def xp_multiplier(session, uid: int) -> Decimal:
    m = Decimal("1")
    if await active_pass(session, uid, "vip"):
        m *= G.VIP_XP_MULT
    if await active_pass(session, uid, "boost"):
        m *= G.BOOST_XP_MULT
    return m

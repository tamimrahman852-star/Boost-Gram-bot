"""Telegram Mini App REST API.

Runs inside the same process as the bot (see web.py) and re-uses the bot's own business logic
(`handlers.earnings._process_task`, `services.*`, `TaskPricing`, `BusinessRules`...), so the app and
the bot can never disagree about money.

Security model
  * Every request carries `Authorization: tma <Telegram.WebApp.initData>`.
  * initData is verified with HMAC-SHA256 (key = HMAC("WebAppData", BOT_TOKEN)) and must be fresh.
  * The user id is taken ONLY from the verified initData, never from the request body.
  * Same anti-abuse rules as the bot: Redis locks, rate limits, daily task cap, risk score, blocked users.
  * Proof screenshots are never exposed through bot-token file URLs: they are streamed through a
    short-lived HMAC-signed endpoint that only the advertiser's page receives.
"""
import asyncio
import hashlib
import hmac
import html
import json
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, Body, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import i18n
from config import (
    ADMIN_IDS, BOT_TOKEN, BOT_USERNAME, DEPOSIT_MAX_BDT, DEPOSIT_MIN_BDT, DEPOSIT_RATE_PER_BDT,
    PAY_NUMBERS, BusinessRules, TaskPricing, WEBAPP_ORIGINS, logger,
)
from database import (
    AsyncSessionLocal, AutoRenew, Campaign, CampaignPost, CampaignStatus, Check, CheckActivation,
    CheckType, DepositRequest, ReferralEarning, TaskCompletion, TaskSubmission, TaskType, Transaction,
    TransactionType, User, Voucher, VoucherRedemption, WithdrawalRequest, redis_client,
)
from handlers.cabinet import ALLOWED_WD_METHODS
from handlers.deposits import MAX_OPEN_REQUESTS, METHOD_LABEL, TRX_RE, available_methods
from handlers.deposits import review_kb as deposit_review_kb
from handlers.earnings import _open_link, _process_task, _task_filters
from handlers.promote_channel import BOT_RE, POST_RE
from handlers.promote_channel import _USERNAME_RE
from handlers.proofs import review_kb as proof_review_kb
from handlers.start import get_or_create_user
from handlers.vouchers import CODE_RE as VOUCHER_RE
from helpers import esc, hash_password, is_expired, alert_admins, L
from keyboards import withdrawal_review_kb
from localization import L10N, t
from services import (
    approve_submission, campaign_cost, locked_amount, pending_withdrawals, reject_submission,
    reserve_slot, withdrawable,
)

router = APIRouter(prefix="/api")

INIT_DATA_MAX_AGE = 24 * 3600
PAGE = 20
MAX_IMAGE_BYTES = 8 * 1024 * 1024
PROOF_URL_TTL = 15 * 60
ALL_TASK_TYPES = set(BusinessRules.EARNABLE_TASK_TYPES)


# --------------------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 400):
        self.code, self.message, self.status = code, message or code, status


async def api_error_handler(_request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message})


def _strip(text: str) -> str:
    """Bot messages are Telegram-HTML; the app wants plain text."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def num(x):
    if x is None:
        return 0
    if not isinstance(x, Decimal):
        return x
    return int(x) if x == x.to_integral_value() else float(round(x, 4))


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def ttype(c: Campaign) -> str:
    return c.task_type.value if hasattr(c.task_type, "value") else str(c.task_type)


# --------------------------------------------------------------------------------------
# bot (separate client object, same token; used for membership checks, forwarding, uploads)
# --------------------------------------------------------------------------------------
_bot: Optional[Bot] = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=BOT_TOKEN)
    return _bot


async def close_bot() -> None:
    global _bot
    if _bot is not None:
        try:
            await _bot.session.close()
        finally:
            _bot = None


# --------------------------------------------------------------------------------------
# Telegram initData verification
# --------------------------------------------------------------------------------------
def verify_init_data(init_data: str, bot_token: str, max_age: int = INIT_DATA_MAX_AGE) -> dict:
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    got = pairs.pop("hash", "")
    if not got:
        raise ValueError("no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got):
        raise ValueError("bad hash")
    if time.time() - int(pairs.get("auth_date", "0")) > max_age:
        raise ValueError("expired")
    user = json.loads(pairs["user"])
    if not isinstance(user, dict) or "id" not in user:
        raise ValueError("no user")
    user["_start_param"] = pairs.get("start_param")
    return user


class Ctx:
    """Everything an endpoint needs about the caller."""

    def __init__(self, session, user: User, tg: dict):
        self.session, self.user, self.tg = session, user, tg
        self.uid = user.id
        self.lang = user.language or "en"      # cached: rollback() expires ORM objects, lazy loads would fail

    def err(self, code: str, bn: str, en: str, status: int = 400) -> ApiError:
        return ApiError(code, L(self.lang, bn, en), status)


async def _referrer_from(session, start: Optional[str], uid: int) -> Optional[int]:
    if not start or not start.startswith("ref_"):
        return None
    ref = (await session.execute(select(User).where(User.referral_code == start[4:68]))).scalar_one_or_none()
    if ref and ref.id != uid and not ref.is_blocked:
        return ref.id
    return None


async def auth(request: Request, authorization: str = Header(default="")):
    if not authorization.lower().startswith("tma "):
        raise ApiError("unauthorized", "Open this app from Telegram.", 401)
    try:
        tg = verify_init_data(authorization[4:].strip(), BOT_TOKEN)
    except Exception:
        raise ApiError("unauthorized", "Session expired. Reopen the app from Telegram.", 401)
    uid = int(tg["id"])

    key = f"rl:api:{uid}:{int(time.time())}"
    hits = await redis_client.incr(key)
    if hits == 1:
        await redis_client.expire(key, 3)
    if hits > 15:
        raise ApiError("cooldown", "Too fast", 429)

    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        referrer = None
        if user is None:
            start = request.query_params.get("start") or tg.get("_start_param")
            referrer = await _referrer_from(session, start, uid)
        tg_user = SimpleNamespace(
            id=uid, username=tg.get("username"), first_name=(tg.get("first_name") or "User")[:128],
            last_name=tg.get("last_name"), is_premium=bool(tg.get("is_premium")),
            language_code=tg.get("language_code"),
        )
        user, _new = await get_or_create_user(session, tg_user, referrer)
        await session.commit()
        if user.is_blocked and uid not in ADMIN_IDS:
            raise ApiError("blocked", "Your account is blocked.", 403)
        yield Ctx(session, user, tg)


# --------------------------------------------------------------------------------------
# helpers shared by several endpoints
# --------------------------------------------------------------------------------------
async def daily_used(uid: int) -> int:
    return int(await redis_client.get(f"dcap:{uid}") or 0)


async def daily_info(uid: int) -> dict:
    return {"used": await daily_used(uid), "max": BusinessRules.MAX_TASKS_PER_DAY}


async def user_dict(ctx: Ctx) -> dict:
    u = ctx.user
    locked = await locked_amount(ctx.session, u.id)
    wd = max(Decimal("0"), u.balance - locked)
    return {
        "id": u.id, "first_name": u.first_name, "username": u.username, "is_premium": u.is_premium,
        "photo_url": ctx.tg.get("photo_url"), "balance": num(u.balance), "xp": u.xp, "level": u.level,
        "current_level_xp": u.current_level_xp, "next_level_xp": u.next_level_xp,
        "total_earned": num(u.total_earned), "total_withdrawn": num(u.total_withdrawn),
        "completed_tasks": u.completed_tasks_count, "referral_earnings": num(u.referral_earnings),
        "referral_code": u.referral_code, "referral_tier": u.referral_tier, "language": u.language,
        "notifications": u.notifications_enabled, "locked": num(locked), "withdrawable": num(wd),
    }


async def _pending_reviews(session, uid: int) -> int:
    return (await session.execute(select(func.count(TaskSubmission.id)).where(
        TaskSubmission.advertiser_id == uid, TaskSubmission.status == "pending"))).scalar() or 0


def _remaining(c: Campaign) -> Decimal:
    return max(Decimal("0"), c.total_budget - c.spent_budget - c.refunded_budget)


async def _campaign_item(session, c: Campaign, renew: Optional[bool] = None, pending: Optional[int] = None) -> dict:
    if renew is None:
        left = (await session.execute(select(AutoRenew.renewals_left).where(AutoRenew.campaign_id == c.id))).scalar()
        renew = bool(left and left > 0)
    if pending is None:
        pending = (await session.execute(select(func.count(TaskSubmission.id)).where(
            TaskSubmission.campaign_id == c.id, TaskSubmission.status == "pending"))).scalar() or 0
    return {
        "id": c.id, "type": ttype(c), "title": c.title, "username": c.target_username,
        "link": c.target_link, "reward": num(c.reward_per_user), "slots_total": c.max_completions,
        "completed": c.completed_count, "status": c.status.value, "premium": c.premium_only,
        "auto_renew": renew, "spent": num(c.spent_budget), "remaining": num(_remaining(c)),
        "created_at": iso(c.created_at), "pending_proofs": pending,
    }


async def _upload_photo(ctx: Ctx, data: bytes):
    """Telegram can only hand out a file_id for files it has seen. Send the picture to the user's
    own private chat, remember the ids and delete the message again."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ctx.err("bad_file", "ছবিটি পড়া যায়নি বা বড় (৮MB এর বেশি)।", "That image is empty or larger than 8 MB.")
    head = data[:12]
    ok = head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    if not ok:
        raise ctx.err("bad_file", "শুধু JPG/PNG/WebP ছবি দিন।", "Please upload a JPG, PNG or WebP image.")
    try:
        msg = await get_bot().send_photo(ctx.uid, BufferedInputFile(data, filename="proof.jpg"), disable_notification=True)
    except TelegramForbiddenError:
        raise ctx.err("not_bot", "আগে বটে /start দিন।", "Open the bot chat and press /start first.")
    except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
        raise ctx.err("bad_file", "ছবিটি আপলোড হয়নি।", "The image could not be uploaded. Try another one.")
    photo = msg.photo[-1]
    try:
        await get_bot().delete_message(ctx.uid, msg.message_id)
    except Exception:
        pass
    return photo.file_id, photo.file_unique_id


def _mask_name(uid: int) -> str:
    return "User #" + hashlib.sha256(f"bg{uid}".encode()).hexdigest()[:4].upper()


def _sign_proof(sid: str, exp: int) -> str:
    return hmac.new(BOT_TOKEN.encode(), f"proof:{sid}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


# --------------------------------------------------------------------------------------
# bootstrap
# --------------------------------------------------------------------------------------
@router.get("/bootstrap")
async def bootstrap(ctx: Ctx = Depends(auth)):
    methods = [m for m in PAY_NUMBERS if PAY_NUMBERS[m]]
    cfg = {
        "bot_username": BOT_USERNAME,
        "rules": {
            "xp_per_level": BusinessRules.XP_PER_LEVEL, "withdraw_min_level": BusinessRules.WITHDRAWAL_MIN_LEVEL,
            "withdraw_min": num(BusinessRules.WITHDRAWAL_MIN_AMOUNT), "withdraw_max": num(BusinessRules.WITHDRAWAL_MAX_AMOUNT),
            "max_daily_tasks": BusinessRules.MAX_TASKS_PER_DAY, "view_dwell": BusinessRules.VIEW_DWELL_SECONDS,
            "proof_review_hours": BusinessRules.PROOF_REVIEW_HOURS, "retention_days": BusinessRules.RETENTION_DAYS,
            "slots_min": BusinessRules.CAMPAIGN_MIN_SLOTS, "slots_max": BusinessRules.CAMPAIGN_MAX_SLOTS,
            "max_active_campaigns": BusinessRules.MAX_ACTIVE_CAMPAIGNS,
            "commission": float(BusinessRules.PLATFORM_COMMISSION), "premium_multiplier": float(TaskPricing.PREMIUM_MULTIPLIER),
            "check_max": num(BusinessRules.CHECK_MAX_AMOUNT), "auto_renew": BusinessRules.AUTO_RENEW_DEFAULT,
        },
        "pricing": {k: dict(v) for k, v in TaskPricing.PRICING.items()},
        "langs": dict(L10N.LANGS),
        "pay": {m: PAY_NUMBERS[m] for m in methods},
        "deposit": {"rate": num(DEPOSIT_RATE_PER_BDT), "min": num(DEPOSIT_MIN_BDT), "max": num(DEPOSIT_MAX_BDT)},
        "withdraw": {"methods": ["usdt_trc20", "usdt_erc20", "bank", "mobile"]},
        "referral": {"base": num(BusinessRules.REFERRAL_BASE_REWARD), "tiers": {str(k): float(v) for k, v in BusinessRules.REFERRAL_TIER_BONUSES.items()}},
    }
    return {
        "user": await user_dict(ctx), "config": cfg, "daily": await daily_info(ctx.uid),
        "badges": {"reviews": await _pending_reviews(ctx.session, ctx.uid)},
    }


# --------------------------------------------------------------------------------------
# earning
# --------------------------------------------------------------------------------------
@router.get("/tasks")
async def list_tasks(type: str = "all", cursor: int = 0, ctx: Ctx = Depends(auth)):
    cursor = max(0, cursor)
    flt = None if type in ("", "all") or type not in ALL_TASK_TYPES else type
    base = _task_filters(ctx.uid, ctx.user, flt)
    rows = (await ctx.session.execute(
        select(Campaign).where(*base).order_by(Campaign.reward_per_user.desc(), Campaign.created_at.desc())
        .offset(cursor).limit(PAGE + 1))).scalars().all()
    more = len(rows) > PAGE
    items = [{
        "id": c.id, "type": ttype(c), "title": c.title, "username": c.target_username, "link": _open_link(c),
        "reward": num(c.reward_per_user), "slots_total": c.max_completions, "slots_left": c.slots_remaining,
        "premium": c.premium_only,
        "retention_days": c.retention_days if c.requires_retention_check else 0, "state": "new",
    } for c in rows[:PAGE]]
    return {"items": items, "daily": await daily_info(ctx.uid), "next": cursor + PAGE if more else None}


async def _task_problem(ctx: Ctx, c: Optional[Campaign], proof: bool) -> Optional[ApiError]:
    """The same gatekeeping the bot does before a task can be started."""
    u = ctx.user
    if not c or c.status != CampaignStatus.ACTIVE:
        return ctx.err("unavailable", "❌ টাস্কটি সক্রিয় নয়।", "❌ This task is not active.", 404)
    if u.is_blocked or u.risk_score >= BusinessRules.RISK_BLOCK_SCORE:
        return ctx.err("blocked", "❌ আপনার অ্যাকাউন্টে কাজ করা বন্ধ আছে।", "❌ Tasks are disabled for your account.", 403)
    if c.advertiser_id == ctx.uid:
        return ctx.err("own_task", "❌ নিজের ক্যাম্পেইনে কাজ করা যায় না।", "❌ You cannot complete your own campaign.")
    if c.premium_only and not u.is_premium:
        return ctx.err("premium_only", "⭐ শুধু Telegram Premium ব্যবহারকারীদের জন্য।", "⭐ Telegram Premium users only.")
    if c.is_full:
        return ctx.err("slots_full", "❌ সব স্লট পূর্ণ।", "❌ All slots are taken.")
    done = (await ctx.session.execute(select(TaskCompletion.id).where(
        TaskCompletion.user_id == ctx.uid, TaskCompletion.campaign_id == c.id))).first()
    sub = (await ctx.session.execute(select(TaskSubmission.id).where(
        TaskSubmission.user_id == ctx.uid, TaskSubmission.campaign_id == c.id))).first()
    if done or sub:
        return ctx.err("already_done", "ℹ️ আপনি এই টাস্ক আগেই করেছেন।", "ℹ️ You already did this task.")
    if await daily_used(ctx.uid) >= BusinessRules.MAX_TASKS_PER_DAY:
        return ctx.err("daily_limit", "⏳ আজকের টাস্কের সীমা শেষ।", "⏳ Daily task limit reached.")
    if proof:
        pending = (await ctx.session.execute(select(func.count(TaskSubmission.id)).where(
            TaskSubmission.user_id == ctx.uid, TaskSubmission.status == "pending"))).scalar() or 0
        if pending >= BusinessRules.PROOF_MAX_PENDING_PER_USER:
            return ctx.err("too_many", "⏳ আপনার অনেকগুলো প্রমাণ যাচাইয়ের অপেক্ষায় আছে।", "⏳ Too many proofs are waiting for review.")
    return None


async def _get_campaign(session, cid: str) -> Optional[Campaign]:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", cid or ""):
        return None
    return (await session.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()


@router.post("/tasks/{cid}/start")
async def task_start(cid: str, ctx: Ctx = Depends(auth)):
    c = await _get_campaign(ctx.session, cid)
    tt = ttype(c) if c else ""
    problem = await _task_problem(ctx, c, proof=tt in BusinessRules.PROOF_TASK_TYPES)
    if problem:
        raise problem
    token = secrets.token_hex(8)
    if tt == "post_view":
        if not await redis_client.set(f"pvcool:{ctx.uid}", "1", nx=True, ex=max(1, int(BusinessRules.VIEW_COOLDOWN_SECONDS))):
            raise ctx.err("cooldown", "⏳ একটু ধীরে।", "⏳ Slow down a little.", 429)
        post = (await ctx.session.execute(select(CampaignPost).where(CampaignPost.campaign_id == cid))).scalar_one_or_none()
        if not post:
            raise ctx.err("unavailable", "❌ টাস্কটি সক্রিয় নয়।", "❌ This task is not active.", 404)
        try:
            await get_bot().forward_message(chat_id=ctx.uid, from_chat_id=post.source_chat_id, message_id=post.source_message_id)
        except TelegramForbiddenError:
            raise ctx.err("not_bot", "আগে বটে /start দিন।", "Open the bot chat and press /start first.")
        except TelegramBadRequest as e:
            low = str(e).lower()
            if "chat not found" in low or "bot was blocked" in low or "deactivated" in low:
                raise ctx.err("not_bot", "আগে বটে /start দিন।", "Open the bot chat and press /start first.")
            logger.warning(f"Campaign {cid}: cannot forward post ({e}); pausing")
            c.status = CampaignStatus.PAUSED
            await ctx.session.commit()
            await alert_admins(get_bot(), f"⚠️ View campaign <code>{esc(cid)}</code> paused: the post cannot be forwarded.")
            raise ctx.err("unavailable", "⚠️ এই পোস্টটি এখন পাওয়া যাচ্ছে না। অন্যটি চেষ্টা করুন।", "⚠️ This post is unavailable right now. Try another one.")
        await redis_client.set(f"pv:{ctx.uid}:{cid}", str(time.time()), ex=300)
        return {"kind": "view", "url": None, "dwell": BusinessRules.VIEW_DWELL_SECONDS, "token": token}
    if tt in BusinessRules.PROOF_TASK_TYPES:
        return {"kind": "proof", "url": _open_link(c), "dwell": 0, "token": token}
    return {"kind": "join", "url": _open_link(c), "dwell": 0, "token": token}


@router.post("/tasks/{cid}/verify")
async def task_verify(cid: str, _body: Optional[dict] = Body(default=None), ctx: Ctx = Depends(auth)):
    c = await _get_campaign(ctx.session, cid)
    if not c:
        raise ctx.err("unavailable", "❌ টাস্কটি সক্রিয় নয়।", "❌ This task is not active.", 404)
    if await daily_used(ctx.uid) >= BusinessRules.MAX_TASKS_PER_DAY:
        raise ctx.err("daily_limit", "⏳ আজকের টাস্কের সীমা শেষ।", "⏳ Daily task limit reached.")
    lock = f"lock:task:{ctx.uid}:{cid}"
    if not await redis_client.set(lock, "1", nx=True, ex=15):
        raise ctx.err("cooldown", "⏳ একটু অপেক্ষা করুন।", "⏳ One moment please.", 429)
    try:
        lang, old_level = ctx.lang, ctx.user.level
        ok, msg, _tt = await _process_task(ctx.session, get_bot(), ctx.uid, cid)
        if not ok:
            code = "invalid"
            if msg == t(lang, "task_failed") or "boost" in msg.lower() and msg.startswith("ℹ️"):
                code = "not_member"
            elif msg == t(lang, "task_already"):
                code = "already_done"
            elif msg == t(lang, "task_limit"):
                code = "slots_full"
            elif msg == t(lang, "task_premium_only"):
                code = "premium_only"
            elif msg.startswith("⏳"):
                code = "cooldown"
            raise ApiError(code, _strip(msg), 400)
        await ctx.session.refresh(ctx.user)
        tc = (await ctx.session.execute(select(TaskCompletion).where(
            TaskCompletion.user_id == ctx.uid, TaskCompletion.campaign_id == cid))).scalar_one()
        return {
            "ok": True, "reward": num(tc.reward), "xp": tc.xp_earned, "level_up": ctx.user.level > old_level,
            "new_level": ctx.user.level, "balance": num(ctx.user.balance),
            "retention": c.retention_days if tc.retention_deadline else 0,
            "user": await user_dict(ctx), "daily": await daily_info(ctx.uid),
        }
    finally:
        await redis_client.delete(lock)


@router.post("/tasks/{cid}/proof")
async def task_proof(cid: str, file: UploadFile = File(...), ctx: Ctx = Depends(auth)):
    lock = f"lock:proof:{ctx.uid}"
    if not await redis_client.set(lock, "1", nx=True, ex=20):
        raise ctx.err("cooldown", "⏳ একটু অপেক্ষা করুন।", "⏳ One moment please.", 429)
    try:
        c = await _get_campaign(ctx.session, cid)
        if c is not None and ttype(c) not in BusinessRules.PROOF_TASK_TYPES:
            raise ctx.err("invalid", "❌ এই টাস্কে প্রমাণ লাগে না।", "❌ This task does not take a proof.")
        problem = await _task_problem(ctx, c, proof=True)
        if problem:
            raise problem
        data = await file.read(MAX_IMAGE_BYTES + 1)
        digest = hashlib.sha256(data).hexdigest()
        if not await redis_client.set(f"proofsha:{digest}", ctx.uid, nx=True, ex=30 * 86400):
            raise ctx.err("duplicate", "❌ এই স্ক্রিনশটটি আগেই ব্যবহার করা হয়েছে।", "❌ This screenshot was already used. Send a new one.")
        try:
            file_id, unique_id = await _upload_photo(ctx, data)
            if (await ctx.session.execute(select(TaskSubmission.id).where(TaskSubmission.proof_unique_id == unique_id))).first():
                raise ctx.err("duplicate", "❌ এই স্ক্রিনশটটি আগেই ব্যবহার করা হয়েছে।", "❌ This screenshot was already used. Send a new one.")

            cl = (await ctx.session.execute(select(Campaign).where(Campaign.id == cid).with_for_update())).scalar_one_or_none()
            exists = (await ctx.session.execute(select(TaskSubmission.id).where(
                TaskSubmission.user_id == ctx.uid, TaskSubmission.campaign_id == cid))).first()
            if not cl or exists or cl.advertiser_id == ctx.uid or (cl.premium_only and not ctx.user.is_premium):
                await ctx.session.rollback()
                raise ctx.err("already_done", "❌ এই টাস্কে আর প্রমাণ জমা দেওয়া যাবে না।", "❌ You can no longer submit proof for this task.")
            if not await reserve_slot(ctx.session, cl):
                await ctx.session.rollback()
                raise ctx.err("slots_full", "❌ সব স্লট পূর্ণ হয়ে গেছে।", "❌ All slots have just been taken.")
            sub = TaskSubmission(campaign_id=cid, user_id=ctx.uid, advertiser_id=cl.advertiser_id,
                                 proof_file_id=file_id, proof_unique_id=unique_id)
            ctx.session.add(sub)
            title, reward, advertiser = cl.title, cl.reward_per_user, cl.advertiser_id
            try:
                await ctx.session.commit()
            except IntegrityError:
                await ctx.session.rollback()
                raise ctx.err("duplicate", "❌ এই স্ক্রিনশটটি আগেই ব্যবহার করা হয়েছে।", "❌ This screenshot was already used.")
        except ApiError:
            await redis_client.delete(f"proofsha:{digest}")
            raise
        pipe = redis_client.pipeline()
        pipe.incr(f"dcap:{ctx.uid}")
        pipe.expire(f"dcap:{ctx.uid}", 86400)
        await pipe.execute()
        try:
            await get_bot().send_photo(
                advertiser, file_id, parse_mode=ParseMode.HTML, reply_markup=proof_review_kb(sub.id),
                caption=(f"📥 <b>New proof</b>\n\n📢 {esc(title)}\n👤 <code>{ctx.uid}</code>\n💲 {reward:,.0f} GRAM\n\n"
                         f"Approve or reject within {BusinessRules.PROOF_REVIEW_HOURS}h — otherwise it is approved automatically."))
        except Exception as e:
            logger.warning(f"could not notify advertiser {advertiser}: {e}")
        return {"status": "pending", "review_hours": BusinessRules.PROOF_REVIEW_HOURS}
    finally:
        await redis_client.delete(lock)


# --------------------------------------------------------------------------------------
# advertising
# --------------------------------------------------------------------------------------
@router.get("/campaigns")
async def my_campaigns(ctx: Ctx = Depends(auth)):
    rows = (await ctx.session.execute(select(Campaign).where(Campaign.advertiser_id == ctx.uid)
                                      .order_by(Campaign.created_at.desc()).limit(100))).scalars().all()
    ids = [c.id for c in rows] or ["-"]
    renew = {r[0] for r in (await ctx.session.execute(select(AutoRenew.campaign_id).where(
        AutoRenew.campaign_id.in_(ids), AutoRenew.renewals_left > 0))).all()}
    pend = dict((await ctx.session.execute(select(TaskSubmission.campaign_id, func.count(TaskSubmission.id)).where(
        TaskSubmission.campaign_id.in_(ids), TaskSubmission.status == "pending").group_by(TaskSubmission.campaign_id))).all())
    return {"items": [await _campaign_item(ctx.session, c, c.id in renew, int(pend.get(c.id, 0))) for c in rows]}


async def _resolve_target(ctx: Ctx, ttype_: str, link: str) -> dict:
    """Validate a link with Telegram. Returns everything the campaign needs (never trusts the client)."""
    if ttype_ not in ALL_TASK_TYPES:
        raise ctx.err("invalid", "❌ ভুল ধরন।", "❌ Invalid task type.")
    raw = (link or "").strip()[:300]
    bot = get_bot()
    bad = ctx.err("bad_link", "❌ সঠিক লিংক দিন।", "❌ That is not a valid link.")

    async def chat_of(name: str):
        try:
            return await bot.get_chat("@" + name)
        except (TelegramBadRequest, TelegramForbiddenError):
            raise ctx.err("not_found", "❌ পাওয়া যায়নি।", "❌ Nothing was found with that name.", 404)

    if ttype_ == "bot_start":
        m = BOT_RE.match(raw)
        if not m:
            raise ctx.err("not_bot", "❌ বটের নাম “bot” দিয়ে শেষ হতে হবে।", "❌ A bot username must end with “bot”.")
        name, param = m.group(1), m.group(2)
        await chat_of(name)
        return {"chat_id": None, "title": f"🤖 @{name}", "username": name, "members": 0, "bot_admin": True,
                "link": f"https://t.me/{name}" + (f"?start={param}" if param else ""), "kind": "bot", "post": None}

    if ttype_ in ("post_view", "reaction"):
        m = POST_RE.match(raw)
        if not m:
            raise ctx.err("no_post", "❌ নির্দিষ্ট পোস্টের লিংক দিন।", "❌ Paste the link of a specific post.")
        name, num_ = m.group(1), int(m.group(2))
        chat = await chat_of(name)
        if chat.type not in ("channel", "supergroup", "group"):
            raise bad
        icon = "👁" if ttype_ == "post_view" else "❤️"
        try:
            members = await bot.get_chat_member_count(chat.id)
        except Exception:
            members = 0
        return {"chat_id": None, "title": f"{icon} {(chat.title or name)[:60]} #{num_}", "username": name, "members": members,
                "bot_admin": True, "link": f"https://t.me/{name}/{num_}", "kind": "post",
                "post": {"chat_id": chat.id, "message_id": num_}}

    m = _USERNAME_RE.match(raw)
    if not m:
        raise bad
    chat = await chat_of(m.group(1))
    want_channel = ttype_ == "channel_sub"
    if ttype_ == "boost_7day":
        okt = chat.type in ("channel", "supergroup")
    else:
        okt = chat.type == "channel" if want_channel else chat.type in ("group", "supergroup")
    if not okt:
        raise ctx.err("bad_link", "❌ এটি সঠিক ধরনের চ্যাট নয়।", "❌ That chat is not the right type for this task.")
    admin = False
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        admin = me.status == "administrator"
    except (TelegramBadRequest, TelegramForbiddenError):
        admin = False
    try:
        members = await bot.get_chat_member_count(chat.id)
    except Exception:
        members = 0
    return {"chat_id": chat.id, "title": (chat.title or chat.username or "Chat")[:200], "username": chat.username,
            "members": members, "bot_admin": admin, "link": f"https://t.me/{chat.username}", "kind": "chat", "post": None}


@router.post("/campaigns/resolve")
async def campaign_resolve(body: dict = Body(...), ctx: Ctx = Depends(auth)):
    r = await _resolve_target(ctx, str(body.get("type", "")), str(body.get("link", "")))
    return {"ok": True, "title": r["title"], "username": r["username"], "members": r["members"],
            "bot_admin": r["bot_admin"], "kind": r["kind"]}


@router.post("/campaigns")
async def campaign_create(body: dict = Body(...), ctx: Ctx = Depends(auth)):
    task_type = str(body.get("type", ""))
    try:
        reward = Decimal(str(body.get("reward")))
        slots = int(body.get("slots"))
        if not reward.is_finite() or reward != reward.to_integral_value():
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        raise ctx.err("range", "❌ ভুল সংখ্যা।", "❌ Invalid number.")
    premium = bool(body.get("premium")) or task_type == "boost_7day"
    if task_type not in ALL_TASK_TYPES:
        raise ctx.err("invalid", "❌ ভুল ধরন।", "❌ Invalid task type.")
    ok, _m = TaskPricing.validate(task_type, reward, premium)
    if not ok or not BusinessRules.CAMPAIGN_MIN_SLOTS <= slots <= BusinessRules.CAMPAIGN_MAX_SLOTS:
        raise ctx.err("range", "❌ পুরস্কার বা স্লট সীমার বাইরে।", "❌ Reward or slots are out of range.")

    tgt = await _resolve_target(ctx, task_type, str(body.get("link", "")))
    if task_type in ("channel_sub", "group_join", "boost_7day") and not tgt["bot_admin"]:
        raise ctx.err("bot_not_admin", f"❌ @{BOT_USERNAME} কে চ্যাটে অ্যাডমিন বানান।", f"❌ Make @{BOT_USERNAME} an administrator of that chat first.")
    if task_type == "boost_7day" and not tgt["username"]:
        raise ctx.err("bad_link", "❌ বুস্টের জন্য পাবলিক চ্যাট দরকার।", "❌ Boost tasks need a public chat.")
    if task_type == "post_view":
        try:     # prove the bot can really show this post to workers, then clean the preview up
            fm = await get_bot().forward_message(ctx.uid, tgt["post"]["chat_id"], tgt["post"]["message_id"], disable_notification=True)
            try:
                await get_bot().delete_message(ctx.uid, fm.message_id)
            except Exception:
                pass
        except TelegramForbiddenError:
            raise ctx.err("not_bot", "আগে বটে /start দিন।", "Open the bot chat and press /start first.")
        except TelegramBadRequest:
            raise ctx.err("no_post", "❌ বট এই পোস্টটি ফরওয়ার্ড করতে পারছে না।", "❌ The bot can't forward this post (it must be public with forwarding allowed).")

    lock = f"lock:campaign:{ctx.uid}"
    if not await redis_client.set(lock, "1", nx=True, ex=20):
        raise ctx.err("cooldown", "⏳ একটু অপেক্ষা করুন।", "⏳ One moment please.", 429)
    try:
        user = (await ctx.session.execute(select(User).where(User.id == ctx.uid).with_for_update())).scalar_one()
        if user.is_blocked:
            await ctx.session.rollback()
            raise ctx.err("blocked", "❌ অ্যাকাউন্ট ব্লক।", "❌ Account blocked.", 403)
        active = (await ctx.session.execute(select(func.count(Campaign.id)).where(
            Campaign.advertiser_id == ctx.uid,
            Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).scalar() or 0
        if active >= BusinessRules.MAX_ACTIVE_CAMPAIGNS:
            await ctx.session.rollback()
            raise ctx.err("too_many", f"সর্বোচ্চ {BusinessRules.MAX_ACTIVE_CAMPAIGNS}টি সক্রিয় ক্যাম্পেইন।", f"Maximum {BusinessRules.MAX_ACTIVE_CAMPAIGNS} active campaigns allowed.")
        budget, fee, total = campaign_cost(reward, slots)
        if user.balance < total:
            await ctx.session.rollback()
            raise ctx.err("insufficient_balance", f"❌ ব্যালেন্স কম। দরকার {total:,.0f} GRAM।", f"❌ Insufficient balance. Need {total:,.0f} GRAM.")
        tt = TaskType(task_type)
        dup_cond = (Campaign.target_chat_id == tgt["chat_id"]) if tgt["chat_id"] is not None else (Campaign.target_link == tgt["link"])
        if (await ctx.session.execute(select(Campaign.id).where(
                dup_cond, Campaign.task_type == tt,
                Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).first():
            await ctx.session.rollback()
            raise ctx.err("duplicate", "❌ এর জন্য ইতিমধ্যে একটি সক্রিয় ক্যাম্পেইন আছে।", "❌ There is already an active campaign for this target.")
        c = Campaign(
            advertiser_id=ctx.uid, title=tgt["title"], task_type=tt, target_chat_id=tgt["chat_id"],
            target_username=tgt["username"], target_link=tgt["link"], target_title=tgt["title"],
            premium_only=premium, reward_per_user=reward, base_reward=reward, max_completions=slots,
            total_budget=budget, retention_days=BusinessRules.RETENTION_DAYS,
            requires_retention_check=task_type in BusinessRules.API_VERIFIED_TASK_TYPES,
        )
        ctx.session.add(c)
        await ctx.session.flush()
        if tgt["post"]:
            ctx.session.add(CampaignPost(campaign_id=c.id, source_chat_id=int(tgt["post"]["chat_id"]),
                                         source_message_id=int(tgt["post"]["message_id"])))
        if body.get("auto_renew"):
            ctx.session.add(AutoRenew(campaign_id=c.id, renewals_left=BusinessRules.AUTO_RENEW_DEFAULT))
        user.balance -= total
        ctx.session.add(Transaction(
            user_id=ctx.uid, amount=-total, type=TransactionType.CAMPAIGN_PAYMENT,
            description=f"Campaign: {tgt['title']} (fee {fee:,.0f})"[:255],
            reference_id=c.id, reference_type="campaign", balance_after=user.balance))
        await ctx.session.commit()
        return {"campaign": await _campaign_item(ctx.session, c, bool(body.get("auto_renew")), 0),
                "balance": num(user.balance), "charged": num(total)}
    except ApiError:
        raise
    except Exception as e:
        await ctx.session.rollback()
        logger.exception(f"api campaign create failed: {e}")
        raise ApiError("error", "Could not create the campaign.", 500)
    finally:
        await redis_client.delete(lock)


@router.post("/campaigns/{cid}/{action}")
async def campaign_action(cid: str, action: str, ctx: Ctx = Depends(auth)):
    if action not in ("pause", "resume", "cancel", "renew", "audience"):
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", cid):
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    c = (await ctx.session.execute(select(Campaign).where(
        Campaign.id == cid, Campaign.advertiser_id == ctx.uid).with_for_update())).scalar_one_or_none()   # owner only
    if not c:
        await ctx.session.rollback()
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    live = c.status in (CampaignStatus.ACTIVE, CampaignStatus.PAUSED)
    out: dict = {}
    if action == "cancel" and live:
        remaining = _remaining(c)
        c.status = CampaignStatus.CANCELLED
        c.refunded_budget += remaining
        owner = (await ctx.session.execute(select(User).where(User.id == ctx.uid).with_for_update())).scalar_one()
        if remaining > 0:
            owner.balance += remaining
            ctx.session.add(Transaction(
                user_id=ctx.uid, amount=remaining, type=TransactionType.CAMPAIGN_REFUND,
                description=f"Refund: {c.title}"[:255], reference_id=c.id, reference_type="campaign",
                balance_after=owner.balance))
        out = {"refunded": num(remaining), "balance": num(owner.balance)}
    elif action == "pause" and c.status == CampaignStatus.ACTIVE:
        c.status = CampaignStatus.PAUSED
    elif action == "resume" and c.status == CampaignStatus.PAUSED and not c.is_full:
        c.status = CampaignStatus.ACTIVE
    elif action == "renew" and live:
        row = (await ctx.session.execute(select(AutoRenew).where(AutoRenew.campaign_id == cid).with_for_update())).scalar_one_or_none()
        if row and row.renewals_left > 0:
            row.renewals_left = 0
            out = {"auto_renew": False}
        else:
            if row:
                row.renewals_left = BusinessRules.AUTO_RENEW_DEFAULT
            else:
                ctx.session.add(AutoRenew(campaign_id=cid, renewals_left=BusinessRules.AUTO_RENEW_DEFAULT))
            out = {"auto_renew": True}
    elif action == "audience" and live:
        if not c.premium_only:
            need = TaskPricing.get(ttype(c), True)["min"]
            if c.reward_per_user < need:
                await ctx.session.rollback()
                raise ctx.err("range", f"❌ Premium অডিয়েন্সের জন্য কমপক্ষে {need:,} GRAM দরকার।", f"❌ The Premium audience needs a reward of at least {need:,} GRAM.")
        c.premium_only = not c.premium_only
        out = {"premium": c.premium_only}
    else:
        await ctx.session.rollback()
        raise ctx.err("invalid", "❌ এখন এটি করা যাবে না।", "❌ That is not possible right now.")
    await ctx.session.commit()
    return {"status": c.status.value, **out}


# --------------------------------------------------------------------------------------
# proof review (advertiser)
# --------------------------------------------------------------------------------------
@router.get("/proofs/review")
async def proofs_review(ctx: Ctx = Depends(auth)):
    subs = (await ctx.session.execute(select(TaskSubmission, Campaign).join(Campaign, Campaign.id == TaskSubmission.campaign_id).where(
        TaskSubmission.advertiser_id == ctx.uid, TaskSubmission.status == "pending")
        .order_by(TaskSubmission.created_at.asc()).limit(50))).all()
    exp = int(time.time()) + PROOF_URL_TTL
    items = []
    for s, c in subs:
        created = s.created_at if s.created_at.tzinfo else s.created_at.replace(tzinfo=timezone.utc)
        items.append({
            "id": s.id, "campaign_id": c.id, "campaign_title": c.title, "type": ttype(c), "reward": num(c.reward_per_user),
            "worker": _mask_name(s.user_id), "created_at": iso(created),
            "expires_at": iso(created + timedelta(hours=BusinessRules.PROOF_REVIEW_HOURS)),
            "image": f"/api/proofs/{s.id}/image?exp={exp}&sig={_sign_proof(s.id, exp)}",
        })
    return {"items": items}


@router.get("/proofs/{sid}/image")
async def proof_image(sid: str, exp: int = Query(...), sig: str = Query(...)):
    """Authenticated by a short-lived signature (an <img> tag cannot send headers)."""
    if exp < time.time() or not hmac.compare_digest(_sign_proof(sid, exp), sig):
        raise ApiError("unauthorized", "Link expired", 403)
    async with AsyncSessionLocal() as s:
        file_id = (await s.execute(select(TaskSubmission.proof_file_id).where(TaskSubmission.id == sid))).scalar()
    if not file_id:
        raise ApiError("not_found", "Not found", 404)
    try:
        bot = get_bot()
        f = await bot.get_file(file_id)
        buf = await bot.download_file(f.file_path)
        data = buf.read()
    except Exception:
        raise ApiError("not_found", "Not found", 404)
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})


@router.post("/proofs/{sid}/{action}")
async def proof_decide(sid: str, action: str, ctx: Ctx = Depends(auth)):
    if action not in ("approve", "reject"):
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    sub = (await ctx.session.execute(select(TaskSubmission).where(
        TaskSubmission.id == sid, TaskSubmission.advertiser_id == ctx.uid))).scalar_one_or_none()   # only the owner decides
    if not sub:
        raise ctx.err("not_found", "পাওয়া যায়নি।", "Not found", 404)
    worker_id, bot = sub.user_id, get_bot()
    w = (await ctx.session.execute(select(User).where(User.id == worker_id))).scalar_one_or_none()
    wl = w.language if w else "en"
    if action == "approve":
        ok, reward, worker, bal = await approve_submission(ctx.session, sid, ctx.uid, allowed=("pending", "rejected"))
        if not ok:
            raise ctx.err("invalid", "Already processed.", "Already processed.")
        try:
            await bot.send_message(worker, L(wl, f"✅ আপনার প্রমাণ অনুমোদিত হয়েছে!\n💲 +{reward:,.0f} GRAM · 💰 {bal:,.1f}",
                                             f"✅ Your proof was approved!\n💲 +{reward:,.0f} GRAM · 💰 {bal:,.1f}",
                                             _tpl='✅ Your proof was approved!\n💲 +{v0:,.0f} GRAM · 💰 {v1:,.1f}', _kw={"v0": reward, "v1": bal}))
        except Exception:
            pass
        return {"ok": True}
    if not await reject_submission(ctx.session, sid, ctx.uid):
        raise ctx.err("invalid", "Already processed.", "Already processed.")
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=L(wl, "⚖️ আপিল করুন", "⚖️ Dispute"), callback_data=f"sub:dispute:{sid}")]])
        await bot.send_message(worker_id, L(
            wl, f"❌ আপনার প্রমাণ বাতিল করা হয়েছে। আপনি মনে করলে {BusinessRules.PROOF_DISPUTE_HOURS} ঘণ্টার মধ্যে আপিল করতে পারেন।",
            f"❌ Your proof was rejected. If you think this is wrong you can dispute it within {BusinessRules.PROOF_DISPUTE_HOURS}h — an admin will decide.",
            _tpl="❌ Your proof was rejected. If you think this is wrong you can dispute it within {v0}h — an admin will decide.",
            _kw={"v0": BusinessRules.PROOF_DISPUTE_HOURS}), reply_markup=kb)
    except Exception:
        pass
    return {"ok": True}


# --------------------------------------------------------------------------------------
# wallet
# --------------------------------------------------------------------------------------
@router.get("/wallet/history")
async def wallet_history(cursor: int = 0, kind: str = "all", ctx: Ctx = Depends(auth)):
    cursor = max(0, cursor)
    q = select(Transaction).where(Transaction.user_id == ctx.uid)
    if kind == "in":
        q = q.where(Transaction.amount > 0)
    elif kind == "out":
        q = q.where(Transaction.amount < 0)
    rows = (await ctx.session.execute(q.order_by(Transaction.created_at.desc()).offset(cursor).limit(PAGE + 1))).scalars().all()
    items = [{"id": x.id, "type": x.type.value, "amount": num(x.amount), "title": (x.description or x.type.value)[:80],
              "created_at": iso(x.created_at)} for x in rows[:PAGE]]
    return {"items": items, "next": cursor + PAGE if len(rows) > PAGE else None}


@router.get("/wallet/requests")
async def wallet_requests(ctx: Ctx = Depends(auth)):
    deps = (await ctx.session.execute(select(DepositRequest).where(DepositRequest.user_id == ctx.uid)
                                      .order_by(DepositRequest.created_at.desc()).limit(15))).scalars().all()
    wds = (await ctx.session.execute(select(WithdrawalRequest).where(WithdrawalRequest.user_id == ctx.uid)
                                     .order_by(WithdrawalRequest.created_at.desc()).limit(15))).scalars().all()
    items = [{"id": d.id, "kind": "deposit", "amount": num(d.coins), "amount_bdt": num(d.amount_bdt), "status": d.status,
              "method": METHOD_LABEL.get(d.method, d.method), "created_at": iso(d.created_at)} for d in deps]
    items += [{"id": w.id, "kind": "withdrawal", "amount": num(w.amount), "status": w.status,
               "method": w.payment_method, "created_at": iso(w.created_at)} for w in wds]
    items.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return {"items": items[:20]}


@router.post("/deposits")
async def deposit_create(method: str = Form(...), amount_bdt: str = Form(...), trx_id: str = Form(...),
                         screenshot: UploadFile = File(...), ctx: Ctx = Depends(auth)):
    if method not in available_methods():
        raise ctx.err("invalid", "❌ এই মাধ্যমটি বন্ধ।", "❌ This payment method is not available.")
    try:
        amt = Decimal(amount_bdt.replace(",", ""))
        if not amt.is_finite() or amt < DEPOSIT_MIN_BDT or amt > DEPOSIT_MAX_BDT:
            raise InvalidOperation
        amt = amt.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ctx.err("range", "❌ পরিমাণ সীমার বাইরে।", "❌ Amount is out of range.")
    trx = trx_id.strip().upper()
    if not TRX_RE.match(trx):
        raise ctx.err("bad_trx", "❌ TrxID সঠিক নয়।", "❌ That TrxID looks invalid.")
    lock = f"lock:dep:{ctx.uid}"
    if not await redis_client.set(lock, "1", nx=True, ex=20):
        raise ctx.err("cooldown", "⏳ একটু অপেক্ষা করুন।", "⏳ One moment please.", 429)
    try:
        open_n = (await ctx.session.execute(select(func.count(DepositRequest.id)).where(
            DepositRequest.user_id == ctx.uid, DepositRequest.status == "pending"))).scalar() or 0
        if open_n >= MAX_OPEN_REQUESTS:
            raise ctx.err("too_many", "⏳ আগের অনুরোধ যাচাই হওয়া পর্যন্ত অপেক্ষা করুন।", "⏳ Wait until your previous requests are reviewed.")
        if (await ctx.session.execute(select(DepositRequest.id).where(
                DepositRequest.method == method, DepositRequest.trx_id == trx))).first():
            raise ctx.err("bad_trx", "❌ এই TrxID আগেই ব্যবহার করা হয়েছে।", "❌ This TrxID has already been used.")
        file_id, _u = await _upload_photo(ctx, await screenshot.read(MAX_IMAGE_BYTES + 1))
        dep = DepositRequest(user_id=ctx.uid, method=method, amount_bdt=amt, coins=amt * DEPOSIT_RATE_PER_BDT,
                             trx_id=trx, proof_file_id=file_id)
        ctx.session.add(dep)
        try:
            await ctx.session.commit()
        except IntegrityError:
            await ctx.session.rollback()
            raise ctx.err("bad_trx", "❌ এই TrxID আগেই ব্যবহার করা হয়েছে।", "❌ This TrxID has already been used.")
        caption = (f"💳 <b>New deposit</b>\n\n👤 <code>{ctx.uid}</code> ({esc(ctx.user.first_name)})\n"
                   f"🏦 {METHOD_LABEL[method]} · <b>{amt:,.2f} BDT</b> → {dep.coins:,.0f} GRAM\n🔖 <code>{esc(trx)}</code>")
        for aid in ADMIN_IDS:
            try:
                await get_bot().send_photo(aid, file_id, caption=caption, reply_markup=deposit_review_kb(dep.id), parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"deposit notify failed for admin {aid}: {e}")
        return {"status": "pending", "gram": num(dep.coins)}
    finally:
        await redis_client.delete(lock)


@router.post("/withdrawals")
async def withdraw(body: dict = Body(...), ctx: Ctx = Depends(auth)):
    try:
        amount = Decimal(str(body.get("amount")))
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise ctx.err("range", "❌ ভুল পরিমাণ।", "❌ Invalid amount.")
    method, details = str(body.get("method", "")), str(body.get("details", "")).strip()
    if method not in ALLOWED_WD_METHODS or len(details) < 5:
        raise ctx.err("invalid", "❌ পেমেন্ট তথ্য দিন।", "❌ Enter your payout details.")
    if amount < BusinessRules.WITHDRAWAL_MIN_AMOUNT or amount > BusinessRules.WITHDRAWAL_MAX_AMOUNT:
        raise ctx.err("range", "❌ পরিমাণ সীমার বাইরে।", "❌ Amount is out of range.")
    lock = f"lock:wd:{ctx.uid}"
    if not await redis_client.set(lock, "1", nx=True, ex=20):
        raise ctx.err("cooldown", "⏳ একটু অপেক্ষা করুন।", "⏳ One moment please.", 429)
    try:
        user = (await ctx.session.execute(select(User).where(User.id == ctx.uid).with_for_update())).scalar_one()
        if user.is_blocked or user.risk_score >= BusinessRules.RISK_BLOCK_SCORE:
            await ctx.session.rollback()
            raise ctx.err("blocked", "❌ উইথড্র বন্ধ আছে।", "❌ Withdrawals are disabled for your account.", 403)
        if not user.can_withdraw:
            await ctx.session.rollback()
            raise ctx.err("level_locked", f"❌ লেভেল {BusinessRules.WITHDRAWAL_MIN_LEVEL} দরকার।", f"❌ Reach level {BusinessRules.WITHDRAWAL_MIN_LEVEL} to withdraw.")
        if await pending_withdrawals(ctx.session, ctx.uid) >= BusinessRules.MAX_PENDING_WITHDRAWALS:
            await ctx.session.rollback()
            raise ctx.err("too_many", "⏳ আগের উইথড্র শেষ হয়নি।", "⏳ Wait until your previous withdrawal is processed.")
        if amount > await withdrawable(ctx.session, user):
            await ctx.session.rollback()
            raise ctx.err("insufficient_balance", "❌ উইথড্রযোগ্য ব্যালেন্স কম।", "❌ Not enough withdrawable balance.")
        user.balance -= amount          # held now; refunded by the admin flow if rejected
        wd = WithdrawalRequest(user_id=ctx.uid, amount=amount, payment_method=method, payment_details=details[:500])
        ctx.session.add(wd)
        await ctx.session.flush()
        ctx.session.add(Transaction(user_id=ctx.uid, amount=-amount, type=TransactionType.WITHDRAWAL,
                                    description=f"Withdrawal: {method}", reference_id=wd.id,
                                    reference_type="withdrawal", balance_after=user.balance))
        wd_id, name, lvl, tasks, risk, bal = wd.id, user.first_name, user.level, user.completed_tasks_count, user.risk_score, user.balance
        await ctx.session.commit()
    finally:
        await redis_client.delete(lock)
    for aid in ADMIN_IDS:
        try:
            await get_bot().send_message(
                aid, f"💸 <b>Withdrawal</b>\n\n👤 <code>{ctx.uid}</code> ({esc(name)})\n💰 <code>{amount:,.0f}</code>\n💳 {esc(method)}\n"
                     f"📝 <code>{esc(details[:200])}</code>\n📊 Level {lvl} · tasks {tasks} · risk {risk}",
                reply_markup=withdrawal_review_kb(wd_id), parse_mode=ParseMode.HTML)
        except Exception:
            pass
    return {"status": "pending", "balance": num(bal)}


@router.post("/vouchers/redeem")
async def voucher_redeem(body: dict = Body(...), ctx: Ctx = Depends(auth)):
    code = str(body.get("code", "")).strip().upper()
    bad_key = f"vch_bad:{ctx.uid}"
    if int(await redis_client.get(bad_key) or 0) >= 8:
        raise ctx.err("too_many", "⏳ অনেকবার ভুল কোড। এক ঘণ্টা পরে চেষ্টা করুন।", "⏳ Too many invalid codes. Try again in an hour.", 429)

    async def bad():
        pipe = redis_client.pipeline()
        pipe.incr(bad_key)
        pipe.expire(bad_key, 3600)
        await pipe.execute()
        await ctx.session.rollback()
        return ctx.err("bad_code", "❌ কোডটি সঠিক নয় বা মেয়াদ শেষ।", "❌ Invalid or expired code.")

    if not VOUCHER_RE.match(code):
        raise await bad()
    v = (await ctx.session.execute(select(Voucher).where(Voucher.code == code).with_for_update())).scalar_one_or_none()
    if not v or not v.is_active or is_expired(v.expires_at):
        raise await bad()
    if v.used_count >= v.max_uses:
        await ctx.session.rollback()
        raise ctx.err("bad_code", "❌ এই কোডের ব্যবহার সীমা শেষ।", "❌ This code has been fully used.")
    if (await ctx.session.execute(select(VoucherRedemption.id).where(
            VoucherRedemption.code == code, VoucherRedemption.user_id == ctx.uid))).first():
        await ctx.session.rollback()
        raise ctx.err("bad_code", "❌ আপনি এই কোড আগেই ব্যবহার করেছেন।", "❌ You already used this code.")
    user = (await ctx.session.execute(select(User).where(User.id == ctx.uid).with_for_update())).scalar_one()
    amount = v.amount
    v.used_count += 1
    ctx.session.add(VoucherRedemption(code=code, user_id=ctx.uid))
    user.balance += amount
    ctx.session.add(Transaction(user_id=ctx.uid, amount=amount, type=TransactionType.ADMIN_ADJUSTMENT,
                                description=f"Voucher {code}"[:255], reference_id=code[:36],
                                reference_type="voucher", balance_after=user.balance))
    try:
        await ctx.session.commit()
    except IntegrityError:
        await ctx.session.rollback()
        raise ctx.err("bad_code", "❌ আপনি এই কোড আগেই ব্যবহার করেছেন।", "❌ You already used this code.")
    return {"amount": num(amount), "balance": num(user.balance)}


@router.post("/checks")
async def check_create(body: dict = Body(...), ctx: Ctx = Depends(auth)):
    try:
        amount = Decimal(str(body.get("amount")))
        uses = int(body.get("uses"))
        if not amount.is_finite() or amount < 1 or amount > BusinessRules.CHECK_MAX_AMOUNT or amount != amount.to_integral_value():
            raise ValueError
        if not 1 <= uses <= 10000:
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        raise ctx.err("range", "❌ ভুল সংখ্যা।", "❌ Invalid number.")
    pw = str(body.get("password") or "").strip()[:64] or None
    total = amount * uses
    user = (await ctx.session.execute(select(User).where(User.id == ctx.uid).with_for_update())).scalar_one()
    if user.is_blocked or user.balance < total:
        await ctx.session.rollback()
        raise ctx.err("insufficient_balance", f"❌ ব্যালেন্স কম। দরকার {total:,.0f}।", f"❌ Not enough balance. Need {total:,.0f}.")
    code = uuid.uuid4().hex[:12].upper()
    ck = Check(code=code, check_type=CheckType.SINGLE_USE if uses == 1 else CheckType.MULTI_USE, created_by=ctx.uid,
               amount_per_activation=amount, max_activations=uses, requires_password=pw is not None,
               password_hash=hash_password(pw, code) if pw else None, total_funded=total)
    ctx.session.add(ck)
    await ctx.session.flush()
    user.balance -= total
    ctx.session.add(Transaction(user_id=ctx.uid, amount=-total, type=TransactionType.CHECK_CREATE,
                                description=f"Check {code}", reference_id=ck.id, reference_type="check",
                                balance_after=user.balance))
    await ctx.session.commit()
    return {"code": code, "link": f"https://t.me/{BOT_USERNAME}?start=check_{code}", "balance": num(user.balance)}


@router.post("/checks/redeem")
async def check_redeem(body: dict = Body(...), ctx: Ctx = Depends(auth)):
    code = str(body.get("code", "")).strip().upper()[:32]
    pw = (str(body.get("password") or "").strip()) or None
    uid = ctx.uid
    bad_key = f"chk_bad:{uid}"
    if int(await redis_client.get(bad_key) or 0) >= 10:
        raise ctx.err("too_many", "⏳ অনেকবার ভুল কোড। এক ঘণ্টা পরে চেষ্টা করুন।", "⏳ Too many invalid codes. Try again in an hour.", 429)

    async def bad():
        pipe = redis_client.pipeline()
        pipe.incr(bad_key)
        pipe.expire(bad_key, 3600)
        await pipe.execute()
        await ctx.session.rollback()
        return ctx.err("bad_code", "❌ কোডটি সঠিক নয় বা মেয়াদ শেষ।", "❌ Invalid or expired code.")

    ck = (await ctx.session.execute(select(Check).where(Check.code == code).with_for_update())).scalar_one_or_none()
    if not ck or not ck.is_active or ck.is_exhausted or is_expired(ck.expires_at):
        raise await bad()
    if ck.requires_password:
        if not pw:
            await ctx.session.rollback()
            raise ctx.err("need_password", "🔒 এই চেকে পাসওয়ার্ড লাগবে।", "🔒 This check is password-protected.")
        tries_key = f"chk_pw_try:{uid}:{code}"
        if int(await redis_client.get(tries_key) or 0) >= BusinessRules.CHECK_MAX_PASSWORD_ATTEMPTS:
            await ctx.session.rollback()
            raise ctx.err("too_many", "⏳ অনেকবার ভুল পাসওয়ার্ড।", "⏳ Too many wrong passwords. Try later.", 429)
        if not ck.verify_password(pw):
            pipe = redis_client.pipeline()
            pipe.incr(tries_key)
            pipe.expire(tries_key, 900)
            await pipe.execute()
            await ctx.session.rollback()
            raise ctx.err("bad_code", "❌ ভুল পাসওয়ার্ড।", "❌ Wrong password.")
    if ck.created_by == uid:
        await ctx.session.rollback()
        raise ctx.err("invalid", "❌ নিজের চেক নিজে নেওয়া যায় না।", "❌ You can't redeem your own check.")
    if (await ctx.session.execute(select(CheckActivation).where(
            CheckActivation.check_id == ck.id, CheckActivation.user_id == uid))).scalar_one_or_none():
        await ctx.session.rollback()
        raise ctx.err("bad_code", "❌ আপনি এটি আগেই নিয়েছেন।", "❌ Already redeemed.")
    user = (await ctx.session.execute(select(User).where(User.id == uid).with_for_update())).scalar_one()
    amount = ck.amount_per_activation
    user.balance += amount            # moved between balances: not "earned", no XP (anti level-farming)
    ck.activations_count += 1
    if ck.is_exhausted:
        ck.is_active = False
    ctx.session.add(CheckActivation(check_id=ck.id, user_id=uid, amount=amount))
    ctx.session.add(Transaction(user_id=uid, amount=amount, type=TransactionType.CHECK_REDEEM,
                                description=f"Check {ck.code}", reference_id=ck.id, reference_type="check",
                                balance_after=user.balance))
    try:
        await ctx.session.commit()
    except IntegrityError:
        await ctx.session.rollback()
        raise ctx.err("bad_code", "❌ আপনি এটি আগেই নিয়েছেন।", "❌ Already redeemed.")
    return {"amount": num(amount), "balance": num(user.balance)}


# --------------------------------------------------------------------------------------
# referrals & settings
# --------------------------------------------------------------------------------------
@router.get("/referrals")
async def referrals(ctx: Ctx = Depends(auth)):
    s, uid = ctx.session, ctx.uid
    lvl1 = [r[0] for r in (await s.execute(select(User.id).where(User.referred_by == uid).limit(5000))).all()]
    lvl2 = [r[0] for r in (await s.execute(select(User.id).where(User.referred_by.in_(lvl1 or [0])).limit(5000))).all()] if lvl1 else []
    lvl3 = [r[0] for r in (await s.execute(select(User.id).where(User.referred_by.in_(lvl2 or [0])).limit(5000))).all()] if lvl2 else []
    earned = dict((await s.execute(select(ReferralEarning.tier, func.coalesce(func.sum(ReferralEarning.bonus_amount), 0)).where(
        ReferralEarning.referrer_id == uid).group_by(ReferralEarning.tier))).all())
    base_earned = Decimal(earned.get(0, 0) or 0)
    tiers = []
    for lv, ids in ((1, lvl1), (2, lvl2), (3, lvl3)):
        e = Decimal(earned.get(lv, 0) or 0) + (base_earned if lv == 1 else 0)
        tiers.append({"level": lv, "percent": float(BusinessRules.REFERRAL_TIER_BONUSES.get(lv, 0)) * 100,
                      "count": len(ids), "earned": num(e)})
    recent_rows = (await s.execute(select(User).where(User.referred_by == uid).order_by(User.created_at.desc()).limit(10))).scalars().all()
    per = dict((await s.execute(select(ReferralEarning.referred_user_id, func.coalesce(func.sum(ReferralEarning.bonus_amount), 0)).where(
        ReferralEarning.referrer_id == uid, ReferralEarning.referred_user_id.in_([r.id for r in recent_rows] or [0]))
        .group_by(ReferralEarning.referred_user_id))).all())
    return {
        "code": ctx.user.referral_code, "link": f"https://t.me/{BOT_USERNAME}?start=ref_{ctx.user.referral_code}",
        "count": len(lvl1), "earnings": num(ctx.user.referral_earnings), "tier": ctx.user.referral_tier, "tiers": tiers,
        "recent": [{"name": (r.first_name or "User")[:24], "joined": iso(r.created_at), "earned": num(Decimal(per.get(r.id, 0) or 0))}
                   for r in recent_rows],
    }


@router.post("/settings")
async def settings(body: dict = Body(...), ctx: Ctx = Depends(auth)):
    user = (await ctx.session.execute(select(User).where(User.id == ctx.uid).with_for_update())).scalar_one()
    lang = body.get("language")
    if lang is not None:
        if lang not in L10N.LANGS:
            await ctx.session.rollback()
            raise ctx.err("invalid", "❌ ভুল ভাষা।", "❌ Unsupported language.")
        user.language = lang
    if "notifications" in body:
        user.notifications_enabled = bool(body["notifications"])
    await ctx.session.commit()
    if lang and lang not in ("en", "bn"):
        try:
            if not await i18n.ensure_language(lang, wait=0.01):          # translate bot texts in the background
                asyncio.create_task(i18n.ensure_language(lang, wait=60))
        except Exception as e:
            logger.warning(f"ensure_language({lang}) failed: {e}")
    return {"ok": True}


# --------------------------------------------------------------------------------------
def install(app) -> None:
    """Wire the API into the FastAPI app (called from web.py)."""
    app.add_exception_handler(ApiError, api_error_handler)
    if WEBAPP_ORIGINS:
        app.add_middleware(
            CORSMiddleware, allow_origins=WEBAPP_ORIGINS, allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"], max_age=600)
    app.include_router(router)
    try:
        app.router.on_shutdown.append(close_bot)
    except Exception:
        pass

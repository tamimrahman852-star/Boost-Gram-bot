"""Proof-based tasks: post view, bot start, reaction.

Telegram's Bot API cannot tell whether a user opened a post, started another bot or reacted,
so these tasks are paid like this:
  worker sends a screenshot  ->  advertiser approves / rejects (48h, then auto-approve)
  rejected                    ->  worker may dispute once -> an admin decides
The reward is reserved from the advertiser's escrow at submission, and a screenshot can never be reused.
"""
from datetime import datetime, timedelta, timezone

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from config import ADMIN_IDS, BusinessRules, logger
from database import (
    User, Campaign, TaskCompletion, TaskSubmission, CampaignStatus, redis_client,
)
from helpers import esc, L, ProofState
from services import (
    reserve_slot, approve_submission, reject_submission, finalize_rejection,
)

router = Router()

INSTRUCTIONS = {
    "post_view": ("👁 পোস্টটি খুলে দেখুন, তারপর পোস্টসহ স্ক্রিনশট পাঠান।",
                  "👁 Open the post, view it, then send a screenshot showing the post."),
    "bot_start": ("🤖 বটটি খুলে <b>Start</b> চাপুন, তারপর বটের চ্যাটসহ স্ক্রিনশট পাঠান।",
                  "🤖 Open the bot and press <b>Start</b>, then send a screenshot of the bot chat."),
    "reaction": ("❤️ পোস্টে রিঅ্যাকশন দিন, তারপর রিঅ্যাকশনসহ স্ক্রিনশট পাঠান।",
                 "❤️ React to the post, then send a screenshot showing your reaction."),
}


async def _user(session, uid):
    return (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()


def _ttype(c: Campaign) -> str:
    return c.task_type.value if hasattr(c.task_type, "value") else str(c.task_type)


def review_kb(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Approve", callback_data=f"rev:ok:{sid}"),
        InlineKeyboardButton(text="❌ Reject", callback_data=f"rev:no:{sid}"),
    ]])


def _cancel_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=L(lang, "❌ বাতিল", "❌ Cancel"), callback_data="proof:cancel")]])


@router.callback_query(F.data == "proof:cancel")
async def proof_cancel(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text("❌")
    await query.answer()


# ------------------------------ worker: start ------------------------------
@router.callback_query(F.data.startswith("earn:proof:"))
async def proof_start(query: CallbackQuery, session, state: FSMContext):
    cid = query.data.split(":")[2]
    uid = query.from_user.id
    user = await _user(session, uid)
    if not user:
        await query.answer()
        return
    lang = user.language
    c = (await session.execute(select(Campaign).where(Campaign.id == cid))).scalar_one_or_none()
    problem = None
    if not c or c.status != CampaignStatus.ACTIVE or _ttype(c) not in BusinessRules.PROOF_TASK_TYPES:
        problem = L(lang, "❌ টাস্কটি সক্রিয় নয়।", "❌ This task is not active.")
    elif user.is_blocked or user.risk_score >= BusinessRules.RISK_BLOCK_SCORE:
        problem = L(lang, "❌ আপনার অ্যাকাউন্টে কাজ করা বন্ধ আছে।", "❌ Tasks are disabled for your account.")
    elif c.advertiser_id == uid:
        problem = L(lang, "❌ নিজের ক্যাম্পেইনে কাজ করা যায় না।", "❌ You cannot complete your own campaign.")
    elif c.premium_only and not user.is_premium:
        problem = L(lang, "⭐ শুধু Telegram Premium ব্যবহারকারীদের জন্য।", "⭐ Telegram Premium users only.")
    elif c.is_full:
        problem = L(lang, "❌ সব স্লট পূর্ণ।", "❌ All slots are taken.")
    else:
        done = (await session.execute(select(TaskCompletion.id).where(
            TaskCompletion.user_id == uid, TaskCompletion.campaign_id == cid))).first()
        sub = (await session.execute(select(TaskSubmission.id).where(
            TaskSubmission.user_id == uid, TaskSubmission.campaign_id == cid))).first()
        pending = (await session.execute(select(func.count(TaskSubmission.id)).where(
            TaskSubmission.user_id == uid, TaskSubmission.status == "pending"))).scalar() or 0
        if done or sub:
            problem = L(lang, "ℹ️ আপনি এই টাস্কে ইতিমধ্যে প্রমাণ জমা দিয়েছেন।", "ℹ️ You already submitted proof for this task.")
        elif pending >= BusinessRules.PROOF_MAX_PENDING_PER_USER:
            problem = L(lang, "⏳ আপনার অনেকগুলো প্রমাণ যাচাইয়ের অপেক্ষায় আছে।", "⏳ Too many of your proofs are awaiting review.")
        elif int(await redis_client.get(f"dcap:{uid}") or 0) >= BusinessRules.MAX_TASKS_PER_DAY:
            problem = L(lang, "⏳ আজকের টাস্কের সীমা শেষ।", "⏳ Daily task limit reached.")
    if problem:
        await query.answer(problem[:200], show_alert=True)
        return
    ttype = _ttype(c)
    bn, en = INSTRUCTIONS[ttype]
    await state.set_state(ProofState.photo)
    await state.update_data(cid=cid)
    await query.message.answer(
        L(lang, f"📸 <b>{esc(c.title)}</b>\n\n{bn}\n\n💲 পুরস্কার: <b>{c.reward_per_user:,.0f} GRAM</b>\n"
                f"⏳ বিজ্ঞাপনদাতা {BusinessRules.PROOF_REVIEW_HOURS} ঘণ্টার মধ্যে যাচাই করবেন।\n\n"
                "⚠️ একই স্ক্রিনশট দ্বিতীয়বার ব্যবহার করা যাবে না। ভুয়া প্রমাণ দিলে অ্যাকাউন্ট ব্লক হতে পারে।",
          f"📸 <b>{esc(c.title)}</b>\n\n{en}\n\n💲 Reward: <b>{c.reward_per_user:,.0f} GRAM</b>\n"
          f"⏳ The advertiser reviews within {BusinessRules.PROOF_REVIEW_HOURS} hours.\n\n"
          "⚠️ A screenshot can't be used twice. Fake proof can get your account blocked."),
        reply_markup=_cancel_kb(lang), parse_mode=ParseMode.HTML)
    await query.answer()


@router.message(ProofState.photo, F.photo)
async def proof_photo(message: Message, session, state: FSMContext, bot: Bot):
    uid = message.from_user.id
    lock = f"lock:proof:{uid}"
    if not await redis_client.set(lock, "1", nx=True, ex=15):
        return
    try:
        data = await state.get_data()
        cid = data.get("cid")
        user = await _user(session, uid)
        if not cid or not user or user.is_blocked:
            await state.clear()
            return
        lang = user.language
        photo = message.photo[-1]
        if (await session.execute(select(TaskSubmission.id).where(
                TaskSubmission.proof_unique_id == photo.file_unique_id))).first():
            await message.answer(L(lang, "❌ এই স্ক্রিনশটটি আগেই ব্যবহার করা হয়েছে। নতুন স্ক্রিনশট পাঠান।",
                                   "❌ This screenshot was already used. Send a new one."))
            return
        c = (await session.execute(select(Campaign).where(Campaign.id == cid).with_for_update())).scalar_one_or_none()
        exists = (await session.execute(select(TaskSubmission.id).where(
            TaskSubmission.user_id == uid, TaskSubmission.campaign_id == cid))).first()
        if (not c or exists or c.advertiser_id == uid or _ttype(c) not in BusinessRules.PROOF_TASK_TYPES
                or (c.premium_only and not user.is_premium)):
            await session.rollback()
            await state.clear()
            await message.answer(L(lang, "❌ এই টাস্কে আর প্রমাণ জমা দেওয়া যাবে না।", "❌ You can no longer submit proof for this task."))
            return
        if not await reserve_slot(session, c):
            await session.rollback()
            await state.clear()
            await message.answer(L(lang, "❌ সব স্লট পূর্ণ হয়ে গেছে।", "❌ All slots have just been taken."))
            return
        sub = TaskSubmission(campaign_id=cid, user_id=uid, advertiser_id=c.advertiser_id,
                             proof_file_id=photo.file_id, proof_unique_id=photo.file_unique_id)
        session.add(sub)
        title, ttype, reward, advertiser = c.title, _ttype(c), c.reward_per_user, c.advertiser_id
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            await message.answer(L(lang, "❌ এই স্ক্রিনশটটি আগেই ব্যবহার করা হয়েছে।", "❌ This screenshot was already used."))
            return
        sid = sub.id
        await state.clear()
        pipe = redis_client.pipeline()
        pipe.incr(f"dcap:{uid}")
        pipe.expire(f"dcap:{uid}", 86400)
        await pipe.execute()
        await message.answer(L(
            lang, f"✅ <b>প্রমাণ জমা হয়েছে</b>\n\nবিজ্ঞাপনদাতা যাচাই করলে <b>{reward:,.0f} GRAM</b> আপনার ব্যালেন্সে যোগ হবে।",
            f"✅ <b>Proof submitted</b>\n\n<b>{reward:,.0f} GRAM</b> will be added once the advertiser approves."), parse_mode=ParseMode.HTML)
        try:
            await bot.send_photo(
                advertiser, photo.file_id,
                caption=(f"📥 <b>New proof</b>\n\n📢 {esc(title)}\n👤 <code>{uid}</code>\n💲 {reward:,.0f} GRAM\n\n"
                         f"Approve or reject within {BusinessRules.PROOF_REVIEW_HOURS}h — otherwise it is approved automatically."),
                reply_markup=review_kb(sid), parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"could not notify advertiser {advertiser}: {e}")
    finally:
        await redis_client.delete(lock)


@router.message(ProofState.photo)
async def proof_not_photo(message: Message, session):
    user = await _user(session, message.from_user.id)
    lang = user.language if user else "bn"
    await message.answer(L(lang, "❌ অনুগ্রহ করে একটি ছবি (স্ক্রিনশট) পাঠান।", "❌ Please send a photo (screenshot)."))


# ------------------------------ advertiser review ------------------------------
async def _own_sub(session, sid, uid):
    return (await session.execute(select(TaskSubmission).where(
        TaskSubmission.id == sid, TaskSubmission.advertiser_id == uid))).scalar_one_or_none()


async def _edit_note(query: CallbackQuery, note: str):
    try:
        base = query.message.caption or query.message.text or ""
        if query.message.caption is not None:
            await query.message.edit_caption(caption=f"{base}\n\n{note}")
        else:
            await query.message.edit_text(f"{base}\n\n{note}")
    except Exception:
        pass


@router.callback_query(F.data.startswith("rev:ok:"))
async def review_approve(query: CallbackQuery, session, bot: Bot):
    sid = query.data.split(":")[2]
    sub = await _own_sub(session, sid, query.from_user.id)          # only the campaign owner can decide
    if not sub:
        await query.answer("❌", show_alert=True)
        return
    ok, reward, worker, bal = await approve_submission(session, sid, query.from_user.id, allowed=("pending", "rejected"))
    if not ok:
        await query.answer("Already processed.", show_alert=True)
        return
    await _edit_note(query, "✅ Approved")
    await query.answer("✅")
    try:
        w = await _user(session, worker)
        await bot.send_message(worker, L(w.language if w else "bn",
            f"✅ আপনার প্রমাণ অনুমোদিত হয়েছে!\n💲 +{reward:,.0f} GRAM · 💰 {bal:,.1f}",
            f"✅ Your proof was approved!\n💲 +{reward:,.0f} GRAM · 💰 {bal:,.1f}"))
    except Exception:
        pass


@router.callback_query(F.data.startswith("rev:no:"))
async def review_reject(query: CallbackQuery, session, bot: Bot):
    sid = query.data.split(":")[2]
    sub = await _own_sub(session, sid, query.from_user.id)
    if not sub:
        await query.answer("❌", show_alert=True)
        return
    worker = sub.user_id
    if not await reject_submission(session, sid, query.from_user.id):
        await query.answer("Already processed.", show_alert=True)
        return
    await _edit_note(query, "❌ Rejected")
    await query.answer("❌")
    try:
        w = await _user(session, worker)
        lang = w.language if w else "bn"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=L(lang, "⚖️ আপিল করুন", "⚖️ Dispute"), callback_data=f"sub:dispute:{sid}")]])
        await bot.send_message(worker, L(
            lang, f"❌ আপনার প্রমাণ বাতিল করা হয়েছে। আপনি মনে করলে {BusinessRules.PROOF_DISPUTE_HOURS} ঘণ্টার মধ্যে আপিল করতে পারেন — অ্যাডমিন বিচার করবেন।",
            f"❌ Your proof was rejected. If you think this is wrong you can dispute it within {BusinessRules.PROOF_DISPUTE_HOURS}h — an admin will decide."),
            reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data.startswith("rev:list:"))
async def review_list(query: CallbackQuery, session, bot: Bot):
    cid = query.data.split(":")[2]
    c = (await session.execute(select(Campaign).where(Campaign.id == cid, Campaign.advertiser_id == query.from_user.id))).scalar_one_or_none()
    if not c:
        await query.answer("❌", show_alert=True)
        return
    rows = (await session.execute(select(TaskSubmission).where(
        TaskSubmission.campaign_id == cid, TaskSubmission.status == "pending")
        .order_by(TaskSubmission.created_at).limit(5))).scalars().all()
    if not rows:
        await query.answer("✅ Nothing to review.", show_alert=True)
        return
    await query.answer()
    for sub in rows:
        try:
            await bot.send_photo(query.from_user.id, sub.proof_file_id,
                                 caption=f"📥 <b>{esc(c.title)}</b>\n👤 <code>{sub.user_id}</code>",
                                 reply_markup=review_kb(sub.id), parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ------------------------------ worker dispute / admin ------------------------------
@router.callback_query(F.data.startswith("sub:dispute:"))
async def dispute(query: CallbackQuery, session, bot: Bot):
    sid = query.data.split(":")[2]
    uid = query.from_user.id
    sub = (await session.execute(select(TaskSubmission).where(TaskSubmission.id == sid).with_for_update())).scalar_one_or_none()
    if not sub or sub.user_id != uid or sub.status != "rejected":
        await session.rollback()
        await query.answer("❌", show_alert=True)
        return
    decided = sub.decided_at
    if decided and decided.tzinfo is None:
        decided = decided.replace(tzinfo=timezone.utc)
    if decided and datetime.now(timezone.utc) > decided + timedelta(hours=BusinessRules.PROOF_DISPUTE_HOURS):
        await session.rollback()
        await query.answer("⏳ The dispute window is over.", show_alert=True)
        return
    sub.status = "disputed"
    file_id, adv = sub.proof_file_id, sub.advertiser_id
    await session.commit()
    await query.message.edit_text("⚖️ Dispute sent to the admins. You will be notified.")
    await query.answer()
    for aid in ADMIN_IDS:
        try:
            await bot.send_photo(aid, file_id, caption=f"⚖️ <b>Dispute</b>\n\n👤 worker <code>{uid}</code>\n📢 advertiser <code>{adv}</code>",
                                 reply_markup=admin_kb(sid), parse_mode=ParseMode.HTML)
        except Exception:
            pass


def admin_kb(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Pay worker", callback_data=f"adm:sub:ok:{sid}"),
        InlineKeyboardButton(text="❌ Reject proof", callback_data=f"adm:sub:no:{sid}"),
    ]])


@router.callback_query(F.data == "adm:disputes")
async def admin_disputes(query: CallbackQuery, session, bot: Bot):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    rows = (await session.execute(select(TaskSubmission).where(TaskSubmission.status == "disputed")
                                  .order_by(TaskSubmission.decided_at).limit(10))).scalars().all()
    if not rows:
        await query.answer("✅ No disputes.", show_alert=True)
        return
    await query.answer()
    for s_ in rows:
        try:
            await bot.send_photo(query.from_user.id, s_.proof_file_id,
                                 caption=f"⚖️ <b>Dispute</b>\n👤 worker <code>{s_.user_id}</code>\n📢 advertiser <code>{s_.advertiser_id}</code>",
                                 reply_markup=admin_kb(s_.id), parse_mode=ParseMode.HTML)
        except Exception:
            pass


@router.callback_query(F.data.startswith("adm:sub:"))
async def admin_decide(query: CallbackQuery, session, bot: Bot):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    _, _, action, sid = query.data.split(":", 3)
    sub = (await session.execute(select(TaskSubmission).where(TaskSubmission.id == sid))).scalar_one_or_none()
    if not sub:
        await query.answer("❌", show_alert=True)
        return
    worker = sub.user_id
    if action == "ok":
        ok, reward, _, bal = await approve_submission(session, sid, query.from_user.id, allowed=("disputed",))
        note, msg = "✅ Worker paid", f"✅ An admin approved your disputed proof. +{reward:,.0f} GRAM" if ok else None
    else:
        ok = await finalize_rejection(session, sid, query.from_user.id, penalize_worker=True, allowed=("disputed",))
        note, msg = "❌ Rejected", "❌ An admin reviewed your dispute and confirmed the rejection."
    if not ok:
        await query.answer("Already processed.", show_alert=True)
        return
    logger.info(f"Admin {query.from_user.id} {note} submission {sid}")
    await _edit_note(query, note)
    await query.answer(note)
    try:
        await bot.send_message(worker, msg)
    except Exception:
        pass

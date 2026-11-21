"""Manual deposits via bKash / Nagad / Rocket with proof + admin verification.

Flow: choose method -> amount (BDT) -> TrxID -> payment screenshot -> admin approves/rejects.
Safety: a TrxID can only ever be used once, max 2 open requests per user, amounts are bounded,
and coins are only credited by an admin (status re-checked under a row lock, so no double credit).
"""
import re
from decimal import Decimal, InvalidOperation

from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone

from config import (
    ADMIN_IDS, PAY_NUMBERS, DEPOSIT_RATE_PER_BDT, DEPOSIT_MIN_BDT, DEPOSIT_MAX_BDT, logger,
)
from database import User, DepositRequest, Transaction, TransactionType, redis_client
from helpers import esc, safe_text, L, DepositState

router = Router()

METHOD_LABEL = {"bkash": "bKash", "nagad": "Nagad", "rocket": "Rocket"}
TRX_RE = re.compile(r"^[A-Za-z0-9]{6,32}$")
MAX_OPEN_REQUESTS = 2


def available_methods():
    from config import LOCAL_DEPOSIT_ENABLED
    return [m for m, num in PAY_NUMBERS.items() if num] if LOCAL_DEPOSIT_ENABLED else []


async def _user(session, uid):
    return (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()


def _cancel_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=L(lang, "❌ বাতিল", "❌ Cancel"), callback_data="dep:cancel")]])


@router.callback_query(F.data == "dep:start")
async def dep_start(query: CallbackQuery, session, state: FSMContext):
    user = await _user(session, query.from_user.id)
    if not user or user.is_blocked:
        await query.answer()
        return
    lang = user.language
    methods = available_methods()
    if not methods:
        await query.answer(L(lang, "এই মুহূর্তে ম্যানুয়াল ডিপোজিট বন্ধ আছে।", "Manual deposits are currently unavailable."), show_alert=True)
        return
    open_n = (await session.execute(select(func.count(DepositRequest.id)).where(
        DepositRequest.user_id == user.id, DepositRequest.status == "pending"))).scalar() or 0
    if open_n >= MAX_OPEN_REQUESTS:
        await query.answer(L(lang, "আপনার আগের অনুরোধ যাচাই হওয়া পর্যন্ত অপেক্ষা করুন।",
                               "Please wait until your previous requests are reviewed."), show_alert=True)
        return
    await state.clear()
    rows = [[InlineKeyboardButton(text=f"💳 {METHOD_LABEL[m]}", callback_data=f"dep:m:{m}")] for m in methods]
    rows.append([InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="cab:replenish")])
    await query.message.edit_text(L(
        lang,
        f"💳 <b>ম্যানুয়াল ডিপোজিট</b>\n\nদর: ১ টাকা = <b>{DEPOSIT_RATE_PER_BDT:,.0f} GRAM</b>\n"
        f"সর্বনিম্ন {DEPOSIT_MIN_BDT:,.0f}৳ · সর্বোচ্চ {DEPOSIT_MAX_BDT:,.0f}৳\n\nপেমেন্ট মাধ্যম বেছে নিন:",
        f"💳 <b>Manual deposit</b>\n\nRate: 1 BDT = <b>{DEPOSIT_RATE_PER_BDT:,.0f} GRAM</b>\n"
        f"Min {DEPOSIT_MIN_BDT:,.0f} · Max {DEPOSIT_MAX_BDT:,.0f} BDT\n\nChoose a payment method:", _tpl='💳 <b>Manual deposit</b>\n\nRate: 1 BDT = <b>{v0:,.0f} GRAM</b>\nMin {v1:,.0f} · Max {v2:,.0f} BDT\n\nChoose a payment method:', _kw={"v0": DEPOSIT_RATE_PER_BDT, "v1": DEPOSIT_MIN_BDT, "v2": DEPOSIT_MAX_BDT}),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data.startswith("dep:m:"))
async def dep_method(query: CallbackQuery, session, state: FSMContext):
    method = query.data.split(":")[2]
    user = await _user(session, query.from_user.id)
    if method not in available_methods() or not user:
        await query.answer("❌", show_alert=True)
        return
    lang = user.language
    await state.set_state(DepositState.amount)
    await state.update_data(method=method)
    await query.message.edit_text(L(
        lang,
        f"💳 <b>{METHOD_LABEL[method]}</b>\n\nপ্রথমে এই নম্বরে <b>Send Money</b> করুন:\n<code>{esc(PAY_NUMBERS[method])}</code>\n\n"
        f"তারপর কত টাকা পাঠিয়েছেন তা লিখুন ({DEPOSIT_MIN_BDT:,.0f}–{DEPOSIT_MAX_BDT:,.0f}):",
        f"💳 <b>{METHOD_LABEL[method]}</b>\n\nFirst <b>Send Money</b> to this number:\n<code>{esc(PAY_NUMBERS[method])}</code>\n\n"
        f"Then enter the amount you sent in BDT ({DEPOSIT_MIN_BDT:,.0f}–{DEPOSIT_MAX_BDT:,.0f}):", _tpl='💳 <b>{v0}</b>\n\nFirst <b>Send Money</b> to this number:\n<code>{v1}</code>\n\nThen enter the amount you sent in BDT ({v2:,.0f}–{v3:,.0f}):', _kw={"v0": METHOD_LABEL[method], "v1": esc(PAY_NUMBERS[method]), "v2": DEPOSIT_MIN_BDT, "v3": DEPOSIT_MAX_BDT}),
        reply_markup=_cancel_kb(lang), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "dep:cancel")
async def dep_cancel(query: CallbackQuery, session, state: FSMContext):
    await state.clear()
    user = await _user(session, query.from_user.id)
    lang = user.language if user else "bn"
    await query.message.edit_text(L(lang, "❌ ডিপোজিট বাতিল করা হয়েছে।", "❌ Deposit cancelled."))
    await query.answer()


@router.message(DepositState.amount)
async def dep_amount(message: Message, session, state: FSMContext):
    user = await _user(session, message.from_user.id)
    lang = user.language if user else "bn"
    try:
        amt = Decimal((safe_text(message) or "").replace(",", ""))
        if not amt.is_finite() or amt < DEPOSIT_MIN_BDT or amt > DEPOSIT_MAX_BDT:
            raise InvalidOperation
        amt = amt.quantize(Decimal("0.01"))
    except InvalidOperation:
        await message.answer(L(lang, f"❌ {DEPOSIT_MIN_BDT:,.0f} থেকে {DEPOSIT_MAX_BDT:,.0f} এর মধ্যে সংখ্যা লিখুন।",
                               f"❌ Send a number between {DEPOSIT_MIN_BDT:,.0f} and {DEPOSIT_MAX_BDT:,.0f}.", _tpl='❌ Send a number between {v0:,.0f} and {v1:,.0f}.', _kw={"v0": DEPOSIT_MIN_BDT, "v1": DEPOSIT_MAX_BDT}))
        return
    await state.update_data(amount=str(amt))
    await state.set_state(DepositState.trx)
    await message.answer(L(lang, f"✅ {amt:,.2f}৳ = <b>{amt * DEPOSIT_RATE_PER_BDT:,.0f} GRAM</b>\n\nএখন পেমেন্টের <b>TrxID</b> লিখুন:",
                                 f"✅ {amt:,.2f} BDT = <b>{amt * DEPOSIT_RATE_PER_BDT:,.0f} GRAM</b>\n\nNow send the payment <b>TrxID</b>:", _tpl='✅ {v0:,.2f} BDT = <b>{v1:,.0f} GRAM</b>\n\nNow send the payment <b>TrxID</b>:', _kw={"v0": amt, "v1": amt * DEPOSIT_RATE_PER_BDT}),
                         parse_mode=ParseMode.HTML, reply_markup=_cancel_kb(lang))


@router.message(DepositState.trx)
async def dep_trx(message: Message, session, state: FSMContext):
    user = await _user(session, message.from_user.id)
    lang = user.language if user else "bn"
    trx = (safe_text(message) or "").strip().upper()
    if not TRX_RE.match(trx):
        await message.answer(L(lang, "❌ TrxID শুধু ইংরেজি অক্ষর ও সংখ্যা (৬–৩২ অক্ষর) হতে পারে।",
                               "❌ TrxID must be 6–32 letters/digits."))
        return
    data = await state.get_data()
    dup = (await session.execute(select(DepositRequest.id).where(
        DepositRequest.method == data.get("method"), DepositRequest.trx_id == trx))).first()
    if dup:
        await message.answer(L(lang, "❌ এই TrxID আগেই ব্যবহার করা হয়েছে।", "❌ This TrxID has already been used."))
        return
    await state.update_data(trx=trx)
    await state.set_state(DepositState.proof)
    await message.answer(L(lang, "📸 এবার পেমেন্টের <b>স্ক্রিনশট</b> পাঠান।", "📸 Now send a <b>screenshot</b> of the payment."),
                         parse_mode=ParseMode.HTML, reply_markup=_cancel_kb(lang))


@router.message(DepositState.proof, F.photo)
async def dep_proof(message: Message, session, state: FSMContext, bot: Bot):
    uid = message.from_user.id
    lock = f"lock:dep:{uid}"
    if not await redis_client.set(lock, "1", nx=True, ex=15):
        return
    try:
        data = await state.get_data()
        user = await _user(session, uid)
        if not user or user.is_blocked or not {"method", "amount", "trx"} <= data.keys():
            await state.clear()
            return
        lang = user.language
        open_n = (await session.execute(select(func.count(DepositRequest.id)).where(
            DepositRequest.user_id == uid, DepositRequest.status == "pending"))).scalar() or 0
        if open_n >= MAX_OPEN_REQUESTS:
            await state.clear()
            await message.answer(L(lang, "⏳ আগের অনুরোধ যাচাই হওয়া পর্যন্ত অপেক্ষা করুন।", "⏳ Wait for your previous requests to be reviewed."))
            return
        amt = Decimal(data["amount"])
        dep = DepositRequest(
            user_id=uid, method=data["method"], amount_bdt=amt, coins=amt * DEPOSIT_RATE_PER_BDT,
            trx_id=data["trx"], proof_file_id=message.photo[-1].file_id,
        )
        session.add(dep)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            await state.clear()
            await message.answer(L(lang, "❌ এই TrxID আগেই ব্যবহার করা হয়েছে।", "❌ This TrxID has already been used."))
            return
        await state.clear()
        dep_id, coins = dep.id, dep.coins
        await message.answer(L(
            lang,
            f"✅ <b>অনুরোধ জমা হয়েছে</b>\n\n💳 {METHOD_LABEL[data['method']]} · {amt:,.2f}৳\n🔖 <code>{esc(data['trx'])}</code>\n\n"
            "অ্যাডমিন যাচাই করলে GRAM আপনার ব্যালেন্সে যোগ হবে। সাধারণত অল্প সময় লাগে।",
            f"✅ <b>Request submitted</b>\n\n💳 {METHOD_LABEL[data['method']]} · {amt:,.2f} BDT\n🔖 <code>{esc(data['trx'])}</code>\n\n"
            "Once an admin verifies it, the GRAM is added to your balance.", _tpl='✅ <b>Request submitted</b>\n\n💳 {v0} · {v1:,.2f} BDT\n🔖 <code>{v2}</code>\n\nOnce an admin verifies it, the GRAM is added to your balance.', _kw={"v0": METHOD_LABEL[data['method']], "v1": amt, "v2": esc(data['trx'])}), parse_mode=ParseMode.HTML)
        caption = (f"💳 <b>New deposit</b>\n\n👤 <code>{uid}</code> ({esc(user.first_name)})\n"
                   f"🏦 {METHOD_LABEL[data['method']]} · <b>{amt:,.2f} BDT</b> → {coins:,.0f} GRAM\n🔖 <code>{esc(data['trx'])}</code>")
        kb = review_kb(dep_id)
        for aid in ADMIN_IDS:
            try:
                await bot.send_photo(aid, dep.proof_file_id, caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"deposit notify failed for admin {aid}: {e}")
    finally:
        await redis_client.delete(lock)


@router.message(DepositState.proof)
async def dep_proof_wrong(message: Message, session):
    user = await _user(session, message.from_user.id)
    lang = user.language if user else "bn"
    await message.answer(L(lang, "❌ অনুগ্রহ করে একটি ছবি (স্ক্রিনশট) পাঠান।", "❌ Please send a photo (screenshot)."))


# ------------------------------ admin side ------------------------------
def review_kb(dep_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Approve", callback_data=f"adm:dep:ok:{dep_id}"),
        InlineKeyboardButton(text="❌ Reject", callback_data=f"adm:dep:no:{dep_id}"),
    ]])


@router.callback_query(F.data == "adm:deposits")
async def adm_deposits(query: CallbackQuery, session, bot: Bot):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    rows = (await session.execute(select(DepositRequest).where(DepositRequest.status == "pending")
                                  .order_by(DepositRequest.created_at).limit(10))).scalars().all()
    if not rows:
        await query.answer("✅ No pending deposits.", show_alert=True)
        return
    await query.answer()
    for d in rows:
        cap = (f"💳 <b>Pending deposit</b>\n\n👤 <code>{d.user_id}</code>\n🏦 {METHOD_LABEL.get(d.method, d.method)} · "
               f"<b>{d.amount_bdt:,.2f} BDT</b> → {d.coins:,.0f} GRAM\n🔖 <code>{esc(d.trx_id)}</code>")
        try:
            await bot.send_photo(query.from_user.id, d.proof_file_id, caption=cap, reply_markup=review_kb(d.id), parse_mode=ParseMode.HTML)
        except Exception:
            await bot.send_message(query.from_user.id, cap, reply_markup=review_kb(d.id), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("adm:dep:"))
async def adm_dep_review(query: CallbackQuery, session, bot: Bot):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    _, _, action, dep_id = query.data.split(":", 3)
    dep = (await session.execute(select(DepositRequest).where(DepositRequest.id == dep_id).with_for_update())).scalar_one_or_none()
    if not dep or dep.status != "pending":          # re-checked under the row lock => no double credit
        await session.rollback()
        await query.answer("Already processed.", show_alert=True)
        return
    user = (await session.execute(select(User).where(User.id == dep.user_id).with_for_update())).scalar_one_or_none()
    uid, coins, amt, lang = dep.user_id, dep.coins, dep.amount_bdt, (user.language if user else "bn")
    dep.processed_by, dep.processed_at = query.from_user.id, datetime.now(timezone.utc)
    if action == "ok" and user:
        dep.status = "approved"
        user.balance += coins                       # purchased coins: no XP, not counted as "earned"
        session.add(Transaction(
            user_id=uid, amount=coins, type=TransactionType.ADMIN_ADJUSTMENT,
            description=f"Deposit {dep.method} {amt:,.2f} BDT"[:255], reference_id=dep.id,
            reference_type="deposit", balance_after=user.balance))
        note = "✅ Approved"
        msg = L(lang, f"✅ আপনার <b>{amt:,.2f}৳</b> ডিপোজিট অনুমোদিত হয়েছে।\n💲 যোগ হয়েছে: <b>{coins:,.0f} GRAM</b>",
                      f"✅ Your <b>{amt:,.2f} BDT</b> deposit was approved.\n💲 Added: <b>{coins:,.0f} GRAM</b>", _tpl='✅ Your <b>{v0:,.2f} BDT</b> deposit was approved.\n💲 Added: <b>{v1:,.0f} GRAM</b>', _kw={"v0": amt, "v1": coins})
    else:
        dep.status = "rejected"
        note = "❌ Rejected"
        msg = L(lang, f"❌ আপনার {amt:,.2f}৳ ডিপোজিট অনুরোধ যাচাই করা যায়নি। সঠিক TrxID ও স্ক্রিনশটসহ আবার চেষ্টা করুন।",
                      f"❌ Your {amt:,.2f} BDT deposit could not be verified. Please retry with the correct TrxID and screenshot.", _tpl='❌ Your {v0:,.2f} BDT deposit could not be verified. Please retry with the correct TrxID and screenshot.', _kw={"v0": amt})
    await session.commit()
    logger.info(f"Admin {query.from_user.id} {note} deposit {dep_id}")
    try:
        base = query.message.caption or query.message.text or ""
        if query.message.caption is not None:
            await query.message.edit_caption(caption=f"{base}\n\n{note}")
        else:
            await query.message.edit_text(f"{base}\n\n{note}")
    except Exception:
        pass
    try:
        await bot.send_message(uid, msg, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await query.answer(note)

"""Promo codes (vouchers). Admins create them; users redeem each code once."""
import re
import secrets
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import ADMIN_IDS
from database import (
    User, Voucher, VoucherRedemption, Transaction, TransactionType, redis_client,
)
from helpers import esc, safe_text, L, VoucherState, is_expired

router = Router()
CODE_RE = re.compile(r"^[A-Z0-9_-]{4,24}$")
MAX_BAD_PER_HOUR = 8


async def _user(session, uid):
    return (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()


# ----------------------------- user side -----------------------------
@router.callback_query(F.data == "voucher:start")
async def voucher_start(query: CallbackQuery, session, state: FSMContext):
    user = await _user(session, query.from_user.id)
    lang = user.language if user else "bn"
    await state.set_state(VoucherState.code)
    await query.message.edit_text(L(lang, "🎟 <b>প্রোমো কোড</b>\n\nআপনার কোডটি লিখুন:", "🎟 <b>Promo code</b>\n\nSend your code:"),
                                  parse_mode=ParseMode.HTML)
    await query.answer()


@router.message(VoucherState.code)
async def voucher_redeem(message: Message, session, state: FSMContext):
    uid = message.from_user.id
    user = await _user(session, uid)
    lang = user.language if user else "bn"
    await state.clear()
    if not user or user.is_blocked:
        return
    code = (safe_text(message) or "").strip().upper()

    bad_key = f"vch_bad:{uid}"
    if int(await redis_client.get(bad_key) or 0) >= MAX_BAD_PER_HOUR:
        await message.answer(L(lang, "⏳ অনেকবার ভুল কোড দিয়েছেন। এক ঘণ্টা পরে চেষ্টা করুন।", "⏳ Too many invalid codes. Try again in an hour."))
        return

    async def fail(text_bn, text_en, count=True):
        if count:
            pipe = redis_client.pipeline()
            pipe.incr(bad_key)
            pipe.expire(bad_key, 3600)
            await pipe.execute()
        await session.rollback()
        await message.answer(L(lang, text_bn, text_en))

    if not CODE_RE.match(code):
        await fail("❌ কোডটি সঠিক নয়।", "❌ Invalid code.")
        return
    v = (await session.execute(select(Voucher).where(Voucher.code == code).with_for_update())).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if not v or not v.is_active or is_expired(v.expires_at):
        await fail("❌ কোডটি সঠিক নয় বা মেয়াদ শেষ।", "❌ Invalid or expired code.")
        return
    if v.used_count >= v.max_uses:
        await fail("❌ এই কোডের ব্যবহার সীমা শেষ।", "❌ This code has been fully used.", count=False)
        return
    already = (await session.execute(select(VoucherRedemption.id).where(
        VoucherRedemption.code == code, VoucherRedemption.user_id == uid))).first()
    if already:
        await fail("❌ আপনি এই কোড আগেই ব্যবহার করেছেন।", "❌ You already used this code.", count=False)
        return
    ru = (await session.execute(select(User).where(User.id == uid).with_for_update())).scalar_one()
    amount = v.amount
    v.used_count += 1
    session.add(VoucherRedemption(code=code, user_id=uid))
    ru.balance += amount                      # promo coins: no XP, not counted as earned
    session.add(Transaction(
        user_id=uid, amount=amount, type=TransactionType.ADMIN_ADJUSTMENT,
        description=f"Voucher {code}"[:255], reference_id=code[:36],
        reference_type="voucher", balance_after=ru.balance))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await message.answer(L(lang, "❌ আপনি এই কোড আগেই ব্যবহার করেছেন।", "❌ You already used this code."))
        return
    await message.answer(L(lang, f"🎉 কোড সফল! <b>+{amount:,.0f} GRAM</b> যোগ হয়েছে।\n💰 ব্যালেন্স: <b>{ru.balance:,.0f}</b>",
                                 f"🎉 Code redeemed! <b>+{amount:,.0f} GRAM</b> added.\n💰 Balance: <b>{ru.balance:,.0f}</b>"),
                         parse_mode=ParseMode.HTML)


# ----------------------------- admin side -----------------------------
def _is_admin_pm(message: Message) -> bool:
    return message.from_user.id in ADMIN_IDS and message.chat.type == "private"


@router.message(Command("voucher"))
async def admin_create_voucher(message: Message, command: CommandObject, session):
    """/voucher AMOUNT MAX_USES [DAYS] [CODE]"""
    if not _is_admin_pm(message):
        return
    usage = "Usage: <code>/voucher AMOUNT MAX_USES [DAYS] [CODE]</code>\nExample: <code>/voucher 50000 100 7 EID2026</code>"
    parts = (command.args or "").split()
    try:
        amount = Decimal(parts[0])
        max_uses = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 and parts[2] != "-" else None
        code = (parts[3] if len(parts) > 3 else "BG" + secrets.token_hex(3)).upper()
        if not amount.is_finite() or not (0 < amount <= Decimal("10000000")) or not (1 <= max_uses <= 100000):
            raise ValueError
        if not CODE_RE.match(code):
            raise ValueError
    except (IndexError, ValueError, InvalidOperation):
        await message.answer(usage, parse_mode=ParseMode.HTML)
        return
    if (await session.execute(select(Voucher.code).where(Voucher.code == code))).first():
        await message.answer("❌ That code already exists.")
        return
    session.add(Voucher(
        code=code, amount=amount, max_uses=max_uses, created_by=message.from_user.id,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=days)) if days else None))
    await session.commit()
    await message.answer(f"✅ Voucher created\n\n🎟 <code>{esc(code)}</code>\n💲 {amount:,.0f} GRAM × {max_uses} uses"
                         + (f"\n⏳ expires in {days} days" if days else ""), parse_mode=ParseMode.HTML)


@router.message(Command("voucher_off"))
async def admin_disable_voucher(message: Message, command: CommandObject, session):
    if not _is_admin_pm(message):
        return
    code = (command.args or "").strip().upper()
    v = (await session.execute(select(Voucher).where(Voucher.code == code))).scalar_one_or_none()
    if not v:
        await message.answer("❌ Not found. Usage: <code>/voucher_off CODE</code>", parse_mode=ParseMode.HTML)
        return
    v.is_active = False
    await session.commit()
    await message.answer(f"🚫 <code>{esc(code)}</code> disabled.", parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "adm:vouchers")
async def admin_vouchers(query: CallbackQuery, session):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    rows = (await session.execute(select(Voucher).order_by(Voucher.created_at.desc()).limit(10))).scalars().all()
    lines = [f"{'🟢' if v.is_active else '🔴'} <code>{esc(v.code)}</code> · {v.amount:,.0f} · {v.used_count}/{v.max_uses}" for v in rows]
    await query.message.answer(
        "🎟 <b>Vouchers</b>\n\n" + ("\n".join(lines) if lines else "No vouchers yet.")
        + "\n\nCreate: <code>/voucher AMOUNT MAX_USES [DAYS] [CODE]</code>\nDisable: <code>/voucher_off CODE</code>",
        parse_mode=ParseMode.HTML)
    await query.answer()

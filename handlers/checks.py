import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy import select
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from config import BOT_USERNAME, BusinessRules
from database import User, Check, CheckActivation, Transaction, CheckType, TransactionType, redis_client
from localization import L10N, t
from keyboards import checks_kb
from helpers import esc, safe_text, CheckCreateState, CheckRedeemState, hash_password, L

router = Router()

MAX_BAD_CODES_PER_HOUR = 10


async def get_user(session, uid):
    r = await session.execute(select(User).where(User.id == uid))
    return r.scalar_one_or_none()


@router.message(F.text.in_([L10N.T["en"]["menu_checks"], L10N.T["bn"]["menu_checks"]]))
async def checks_menu(message: Message, session):
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer(t("en", "not_started"))
        return
    await message.answer(
        t(user.language, "checks_menu"),
        reply_markup=checks_kb(user.language),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "chk:create")
async def chk_create(query: CallbackQuery, session, state: FSMContext):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(t(lang, "check_amount"))
    await state.set_state(CheckCreateState.amount)
    await query.answer()


@router.message(CheckCreateState.amount)
async def chk_amount(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    if not raw:
        await message.answer(t(lang, "text_only"))
        return
    try:
        v = Decimal(raw.replace(",", ""))
        if not v.is_finite() or v < 1 or v > BusinessRules.CHECK_MAX_AMOUNT or v != v.to_integral_value():
            raise ValueError()
    except (InvalidOperation, ValueError):
        await message.answer(L(lang, f"❌ ১ থেকে {BusinessRules.CHECK_MAX_AMOUNT:,.0f} এর মধ্যে একটি পূর্ণ সংখ্যা দিন।",
                               f"❌ Send a whole number between 1 and {BusinessRules.CHECK_MAX_AMOUNT:,.0f}."))
        return
    await state.update_data(amount=str(v))
    await message.answer(t(lang, "check_activations"))
    await state.set_state(CheckCreateState.activations)


@router.message(CheckCreateState.activations)
async def chk_act(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    if not raw:
        await message.answer(t(lang, "text_only"))
        return
    try:
        n = int(raw)
        if n <= 0 or n > 10000:
            raise ValueError()
    except ValueError:
        await message.answer("❌ 1-10000.")
        return
    await state.update_data(activations=n)
    await message.answer(t(lang, "check_password"), parse_mode=ParseMode.HTML)
    await state.set_state(CheckCreateState.password)


@router.message(CheckCreateState.password)
async def chk_final(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    pw = None if not raw or raw.lower() == "skip" else raw[:64]
    data = await state.get_data()
    await state.clear()
    amount = Decimal(data["amount"])
    n = data["activations"]
    total = amount * n
    uid = message.from_user.id

    r = await session.execute(select(User).where(User.id == uid).with_for_update())
    user = r.scalar_one_or_none()
    if not user:
        await session.rollback()
        await message.answer(t("en", "not_started"))
        return
    if user.is_blocked or user.balance < total:
        await session.rollback()
        await message.answer(
            t(user.language, "promote_insufficient", required=total, balance=user.balance),
            parse_mode=ParseMode.HTML,
        )
        return
    code = uuid.uuid4().hex[:12].upper()
    ck = Check(
        code=code,
        check_type=CheckType.SINGLE_USE if n == 1 else CheckType.MULTI_USE,
        created_by=uid,
        amount_per_activation=amount,
        max_activations=n,
        requires_password=pw is not None,
        password_hash=hash_password(pw, code) if pw else None,
        total_funded=total,
    )
    session.add(ck)
    await session.flush()
    user.balance -= total
    session.add(Transaction(
        user_id=uid, amount=-total, type=TransactionType.CHECK_CREATE,
        description=f"Check {code}", reference_id=ck.id,
        reference_type="check", balance_after=user.balance,
    ))
    await session.commit()
    link = f"https://t.me/{BOT_USERNAME}?start=check_{code}"
    await message.answer(
        t(user.language, "check_created", code=code, amount=amount, activations=n, link=esc(link)),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "chk:redeem")
async def chk_redeem(query: CallbackQuery, session, state: FSMContext):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(t(lang, "check_redeem_prompt"))
    await state.set_state(CheckRedeemState.code)
    await query.answer()


@router.message(CheckRedeemState.code)
async def chk_redeem_code(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    await state.clear()
    if not raw:
        await message.answer(t("en", "text_only"))
        return
    await _redeem_check_deeplink(message, session, raw.upper().strip()[:32], state)


async def _bad_code_limited(uid: int) -> bool:
    n = int(await redis_client.get(f"chk_bad:{uid}") or 0)
    return n >= MAX_BAD_CODES_PER_HOUR


async def _count_bad_code(uid: int) -> None:
    pipe = redis_client.pipeline()
    pipe.incr(f"chk_bad:{uid}")
    pipe.expire(f"chk_bad:{uid}", 3600)
    await pipe.execute()


async def _redeem_check_deeplink(message, session, code, state: FSMContext = None):
    uid = message.from_user.id
    user = await get_user(session, uid)
    lang = user.language if user else "en"
    if not user or user.is_blocked:
        return
    if await _bad_code_limited(uid):
        await message.answer(L(lang, "⏳ অনেকবার ভুল কোড দিয়েছেন। এক ঘণ্টা পরে চেষ্টা করুন।",
                               "⏳ Too many invalid codes. Try again in an hour."))
        return
    r = await session.execute(select(Check).where(Check.code == code))
    ck = r.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if not ck or not ck.is_active or ck.is_exhausted or (ck.expires_at and ck.expires_at < now):
        await _count_bad_code(uid)
        await message.answer(t(lang, "check_invalid"))
        return
    if ck.requires_password and state is not None:
        await state.set_state(CheckRedeemState.password)
        await state.update_data(check_code=ck.code)
        await message.answer(t(lang, "check_password_prompt"))
        return
    ok, msg = await _do_redeem(message, session, ck, None)
    await message.answer(msg, parse_mode=ParseMode.HTML)


@router.message(CheckRedeemState.password)
async def chk_redeem_password(message: Message, session, state: FSMContext):
    uid = message.from_user.id
    pw = safe_text(message)
    data = await state.get_data()
    code = data.get("check_code")
    user = await get_user(session, uid)
    lang = user.language if user else "en"
    if not pw or not code:
        await state.clear()
        await message.answer(t(lang, "check_invalid"))
        return
    attempts_key = f"chk_pw_try:{uid}:{code}"
    tries = int(await redis_client.get(attempts_key) or 0)
    if tries >= BusinessRules.CHECK_MAX_PASSWORD_ATTEMPTS:
        await state.clear()
        await message.answer(L(lang, "⏳ অনেকবার ভুল পাসওয়ার্ড। পরে চেষ্টা করুন।", "⏳ Too many wrong passwords. Try later."))
        return
    ck = (await session.execute(select(Check).where(Check.code == code))).scalar_one_or_none()
    if not ck or not ck.verify_password(pw.strip()):
        pipe = redis_client.pipeline()
        pipe.incr(attempts_key)
        pipe.expire(attempts_key, 900)
        await pipe.execute()
        await message.answer(t(lang, "check_wrong_pw"))
        return
    await state.clear()
    ok, msg = await _do_redeem(message, session, ck, pw.strip())
    await message.answer(msg, parse_mode=ParseMode.HTML)


async def _do_redeem(message, session, ck, pw):
    uid = message.from_user.id
    r = await session.execute(select(Check).where(Check.id == ck.id).with_for_update())
    ck = r.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if not ck or not ck.is_active or ck.is_exhausted or (ck.expires_at and ck.expires_at < now):
        await session.rollback()
        return False, t("en", "check_invalid")
    if ck.created_by == uid:
        await session.rollback()
        return False, "❌ Can't redeem your own check."
    if ck.requires_password and (not pw or not ck.verify_password(pw)):
        await session.rollback()
        return False, t("en", "check_wrong_pw")
    ra = await session.execute(select(CheckActivation).where(
        CheckActivation.check_id == ck.id, CheckActivation.user_id == uid
    ))
    if ra.scalar_one_or_none():
        await session.rollback()
        return False, "❌ Already redeemed."
    ru = await session.execute(select(User).where(User.id == uid).with_for_update())
    user = ru.scalar_one_or_none()
    if not user or user.is_blocked:
        await session.rollback()
        return False, t("en", "not_started")
    amount = ck.amount_per_activation
    # Check money is only moved between balances. It is NOT "earned" and gives NO XP:
    # otherwise two accounts could pass a check back and forth to farm levels
    # and unlock withdrawals.
    user.balance += amount
    ck.activations_count += 1
    if ck.is_exhausted:
        ck.is_active = False
    session.add(CheckActivation(check_id=ck.id, user_id=uid, amount=amount))
    session.add(Transaction(
        user_id=uid, amount=amount, type=TransactionType.CHECK_REDEEM,
        description=f"Check {ck.code}", reference_id=ck.id,
        reference_type="check", balance_after=user.balance,
    ))
    await session.commit()
    return True, t(user.language, "check_redeemed", amount=amount)

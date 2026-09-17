import uuid
from decimal import Decimal
from sqlalchemy import select
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from config import BOT_USERNAME, BusinessRules
from database import User, Check, CheckActivation, Transaction, CheckType, TransactionType, redis_client
from localization import L10N, t
from keyboards import checks_kb
from helpers import esc, safe_text, CheckCreateState, CheckRedeemState, hash_sensitive

router = Router()


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
        v = Decimal(raw)
        if v <= 0: raise ValueError()
    except Exception:
        await message.answer("❌ Invalid.")
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
        if n <= 0 or n > 10000: raise ValueError()
    except Exception:
        await message.answer("❌ 1-10000.")
        return
    await state.update_data(activations=n)
    await message.answer(t(lang, "check_password"), parse_mode=ParseMode.HTML)
    await state.set_state(CheckCreateState.password)


@router.message(CheckCreateState.password)
async def chk_final(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    pw = None if not raw or raw.lower() == "skip" else raw
    data = await state.get_data()
    await state.clear()
    amount = Decimal(data["amount"])
    n = data["activations"]
    total = amount * n
    uid = message.from_user.id

    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = r.scalar_one_or_none()
        if not user:
            await message.answer(t("en", "not_started"))
            return
        if user.balance < total:
            await message.answer(
                t(user.language, "promote_insufficient", required=total, balance=user.balance),
                parse_mode=ParseMode.HTML,
            )
            return
        code = uuid.uuid4().hex[:10].upper()
        ck = Check(
            code=code,
            check_type=CheckType.SINGLE_USE if n == 1 else CheckType.MULTI_USE,
            created_by=uid,
            amount_per_activation=amount,
            max_activations=n,
            requires_password=pw is not None,
            password_hash=hash_sensitive(pw) if pw else None,
            total_funded=total,
        )
        session.add(ck)
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
    await _redeem_check_deeplink(message, session, raw.upper().strip())


async def _redeem_check_deeplink(message, session, code):
    r = await session.execute(select(Check).where(Check.code == code))
    ck = r.scalar_one_or_none()
    if not ck or not ck.is_active or ck.is_exhausted:
        await message.answer(t("en", "check_invalid"))
        return
    if ck.requires_password:
        await message.answer(t("en", "check_password_prompt"))
        await redis_client.setex(f"chk_pw:{message.from_user.id}", 300, code)
        return
    ok, msg = await _do_redeem(message, session, ck, None)
    await message.answer(msg, parse_mode=ParseMode.HTML)


async def _do_redeem(message, session, ck, pw):
    uid = message.from_user.id
    async with session.begin_nested():
        r = await session.execute(select(Check).where(Check.id == ck.id).with_for_update())
        ck = r.scalar_one_or_none()
        if not ck or not ck.is_active or ck.is_exhausted:
            return False, t("en", "check_invalid")
        if ck.created_by == uid:
            return False, "❌ Can't redeem your own check."
        if ck.requires_password and (not pw or not ck.verify_password(pw)):
            return False, t("en", "check_wrong_pw")
        ra = await session.execute(select(CheckActivation).where(
            CheckActivation.check_id == ck.id, CheckActivation.user_id == uid
        ))
        if ra.scalar_one_or_none():
            return False, "❌ Already redeemed."
        ru = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = ru.scalar_one_or_none()
        if not user:
            return False, t("en", "not_started")
        amount = ck.amount_per_activation
        user.balance += amount
        user.total_earned += amount
        user.add_xp(int((amount / 1000) * BusinessRules.XP_PER_1000_COINS))
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

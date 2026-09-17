from decimal import Decimal
from sqlalchemy import select, func
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from config import BOT_USERNAME, BusinessRules, ADMIN_IDS
from database import User, TaskCompletion, Campaign, WithdrawalRequest, Transaction, TransactionType
from localization import L10N, t
from keyboards import cabinet_kb, back_to_cabinet_kb, language_kb, main_menu, withdraw_method_kb
from helpers import esc, level_emoji, TopUpState, WithdrawState, safe_text

router = Router()


async def get_user(session, uid):
    r = await session.execute(select(User).where(User.id == uid))
    return r.scalar_one_or_none()


@router.message(F.text.in_([L10N.T["en"]["menu_cabinet"], L10N.T["bn"]["menu_cabinet"]]))
async def show_cabinet(message: Message, session):
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer(t("en", "not_started"))
        return
    await message.answer(
        t(user.language, "cabinet",
          user_id=user.id, level_emoji=level_emoji(user.level),
          level=user.level, balance=user.balance,
          xp=user.current_level_xp, next_xp=user.next_level_xp,
          tasks=user.completed_tasks_count),
        reply_markup=cabinet_kb(user.language, user.notifications_enabled, user.can_withdraw),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "cab:back")
async def cab_back(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    if not user:
        await query.answer()
        return
    await query.message.edit_text(
        t(user.language, "cabinet",
          user_id=user.id, level_emoji=level_emoji(user.level),
          level=user.level, balance=user.balance,
          xp=user.current_level_xp, next_xp=user.next_level_xp,
          tasks=user.completed_tasks_count),
        reply_markup=cabinet_kb(user.language, user.notifications_enabled, user.can_withdraw),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "cab:notif")
async def cab_notif(query: CallbackQuery, session):
    r = await session.execute(select(User).where(User.id == query.from_user.id).with_for_update())
    user = r.scalar_one_or_none()
    if not user:
        await query.answer()
        return
    user.notifications_enabled = not user.notifications_enabled
    await session.commit()
    await query.answer("✅ Updated", show_alert=True)
    try:
        await query.message.edit_reply_markup(
            reply_markup=cabinet_kb(user.language, user.notifications_enabled, user.can_withdraw)
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "cab:lang")
async def cab_lang(query: CallbackQuery):
    await query.message.edit_text(
        "🌐 <b>Select Language</b>",
        reply_markup=language_kb(),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data.startswith("lang:"))
async def set_lang(query: CallbackQuery, session):
    code = query.data.split(":")[1]
    if code not in L10N.LANGS:
        code = "en"
    r = await session.execute(select(User).where(User.id == query.from_user.id).with_for_update())
    user = r.scalar_one_or_none()
    if not user:
        await query.answer()
        return
    user.language = code
    await session.commit()
    await query.message.edit_text(t(code, "lang_changed"))
    await query.message.answer(
        t(code, "welcome", bot_name=esc(BOT_USERNAME), first_name=esc(user.first_name)),
        reply_markup=main_menu(code),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "cab:referral")
async def cab_ref(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    if not user:
        await query.answer()
        return
    r = await session.execute(select(func.count(User.id)).where(User.referred_by == user.id))
    direct = r.scalar() or 0
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.referral_code}"
    await query.message.edit_text(
        t(user.language, "ref_title",
          base=BusinessRules.REFERRAL_BASE_REWARD, direct=direct,
          earned=user.referral_earnings, link=esc(link)),
        reply_markup=back_to_cabinet_kb(user.language),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "cab:level")
async def cab_lv(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    if not user:
        await query.answer()
        return
    cur = user.xp % BusinessRules.XP_PER_LEVEL
    bar = "█" * (cur * 10 // BusinessRules.XP_PER_LEVEL) + "░" * (10 - cur * 10 // BusinessRules.XP_PER_LEVEL)
    await query.message.edit_text(
        t(user.language, "level_title",
          emoji=level_emoji(user.level), level=user.level, xp=user.xp,
          progress=f"[{bar}] {cur}/{BusinessRules.XP_PER_LEVEL}",
          min_level=BusinessRules.WITHDRAWAL_MIN_LEVEL),
        reply_markup=back_to_cabinet_kb(user.language),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "cab:tasks")
async def cab_tasks(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    r = await session.execute(
        select(TaskCompletion, Campaign)
        .join(Campaign, Campaign.id == TaskCompletion.campaign_id)
        .where(TaskCompletion.user_id == query.from_user.id)
        .order_by(TaskCompletion.completed_at.desc())
        .limit(15)
    )
    rows = r.all()
    if not rows:
        text = "📋 No tasks yet."
    else:
        lines = ["📋 <b>My Tasks</b>\n"]
        for tc, c in rows:
            ic = {"pending": "⏳", "verified": "✅", "penalized": "🚫", "failed": "❌"}.get(tc.retention_status.value, "❓")
            lines.append(f"{ic} {esc(c.title[:30])} · +{tc.reward:,.0f}")
        text = "\n".join(lines)
    await query.message.edit_text(text, reply_markup=back_to_cabinet_kb(lang), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "cab:withdraw")
async def cab_wd(query: CallbackQuery, session, state: FSMContext):
    user = await get_user(session, query.from_user.id)
    if not user:
        await query.answer()
        return
    if not user.can_withdraw:
        await query.message.edit_text(
            t(user.language, "wd_locked",
              required=BusinessRules.WITHDRAWAL_MIN_LEVEL, current=user.level),
            reply_markup=back_to_cabinet_kb(user.language),
            parse_mode=ParseMode.HTML,
        )
        await query.answer()
        return
    await query.message.edit_text(
        t(user.language, "wd_prompt",
          min=BusinessRules.WITHDRAWAL_MIN_AMOUNT,
          max=BusinessRules.WITHDRAWAL_MAX_AMOUNT,
          balance=user.balance),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(WithdrawState.amount)
    await query.answer()


@router.message(WithdrawState.amount)
async def wd_amt(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    if not raw:
        await message.answer(t(lang, "text_only"))
        return
    try:
        v = Decimal(raw)
    except Exception:
        await message.answer("❌ Invalid.")
        return
    if v < BusinessRules.WITHDRAWAL_MIN_AMOUNT or v > BusinessRules.WITHDRAWAL_MAX_AMOUNT:
        await message.answer(
            t(lang, "wd_invalid",
              min=BusinessRules.WITHDRAWAL_MIN_AMOUNT,
              max=BusinessRules.WITHDRAWAL_MAX_AMOUNT))
        return
    if v > user.balance:
        await message.answer(t(lang, "wd_insufficient"))
        return
    await state.update_data(amount=str(v))
    await message.answer(t(lang, "wd_method"), reply_markup=withdraw_method_kb())
    await state.set_state(WithdrawState.method)


@router.callback_query(WithdrawState.method, F.data.startswith("wd:m:"))
async def wd_method(query: CallbackQuery, session, state: FSMContext):
    m = query.data.split(":")[2]
    await state.update_data(method=m)
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(t(lang, "wd_details"))
    await state.set_state(WithdrawState.details)
    await query.answer()


@router.message(WithdrawState.details)
async def wd_details(message: Message, session, state: FSMContext, bot: Bot):
    details = safe_text(message)
    if not details:
        await message.answer("❌ Send details.")
        return
    data = await state.get_data()
    await state.clear()
    amount = Decimal(data["amount"])
    method = data["method"]
    uid = message.from_user.id

    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = r.scalar_one_or_none()
        if not user or user.balance < amount:
            await message.answer("❌ Insufficient.")
            return
        user.balance -= amount
        user.total_withdrawn += amount
        session.add(WithdrawalRequest(
            user_id=uid, amount=amount,
            payment_method=method, payment_details=details[:500],
        ))
        session.add(Transaction(
            user_id=uid, amount=-amount, type=TransactionType.WITHDRAWAL,
            description=f"Withdrawal: {method}", balance_after=user.balance,
        ))
    await session.commit()
    await message.answer(t(user.language, "wd_submitted", amount=amount), parse_mode=ParseMode.HTML)
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid,
                f"💸 <b>Withdrawal</b>\n\n👤 <code>{uid}</code> ({esc(user.first_name)})\n💰 <code>{amount:,.0f}</code>\n💳 {esc(method)}\n📝 <code>{esc(details[:200])}</code>",
                parse_mode=ParseMode.HTML)
        except Exception:
            pass


@router.callback_query(F.data == "cab:replenish")
async def cab_top(query: CallbackQuery, session, state: FSMContext):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.answer(
        f"⭐ <b>Top-up with Stars</b>\n\n1 Star = <code>{BusinessRules.STAR_TO_COIN_RATE:,.0f}</code> GRAM\n\nSend stars amount:",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(TopUpState.stars)
    await query.answer()


@router.message(TopUpState.stars)
async def topup(message: Message, state: FSMContext):
    raw = safe_text(message)
    if not raw:
        await message.answer("❌ Send number.")
        return
    try:
        n = int(raw)
        if n <= 0 or n > 10000: raise ValueError()
    except Exception:
        await message.answer("❌ 1-10000.")
        return
    await state.clear()
    await message.bot.send_invoice(
        chat_id=message.chat.id, title="GRAM Top-Up",
        description=f"{n * int(BusinessRules.STAR_TO_COIN_RATE):,} GRAM",
        payload=f"topup_{n}", currency="XTR",
        prices=[LabeledPrice(label=f"{n * int(BusinessRules.STAR_TO_COIN_RATE):,} GRAM", amount=n)],
    )


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def paid(message: Message, session):
    p = message.successful_payment
    try:
        stars = int(p.invoice_payload.split("_")[1])
    except Exception:
        return
    coins = Decimal(stars) * BusinessRules.STAR_TO_COIN_RATE
    uid = message.from_user.id
    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        u = r.scalar_one_or_none()
        if u:
            u.balance += coins
            session.add(Transaction(
                user_id=uid, amount=coins, type=TransactionType.STAR_TOPUP,
                description=f"{stars} Stars", balance_after=u.balance,
            ))
    await session.commit()
    await message.answer(f"✅ +<code>{coins:,.0f}</code> GRAM", parse_mode=ParseMode.HTML)

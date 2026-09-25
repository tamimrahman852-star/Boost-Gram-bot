import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram import Bot

from config import BOT_USERNAME, BusinessRules
from database import User, Transaction, TransactionType
from localization import t
from helpers import esc

router = Router()


async def get_or_create_user(session, tg_user, referrer_id=None):
    r = await session.execute(select(User).where(User.id == tg_user.id))
    u = r.scalar_one_or_none()
    if u:
        u.username = tg_user.username
        u.first_name = tg_user.first_name
        u.last_name = tg_user.last_name
        u.is_premium = tg_user.is_premium or False
        u.last_active_at = datetime.now(timezone.utc)
        return u, False
    u = User(
        id=tg_user.id, username=tg_user.username,
        first_name=tg_user.first_name, last_name=tg_user.last_name,
        is_premium=tg_user.is_premium or False,
        referral_code=uuid.uuid4().hex[:8],
        referred_by=referrer_id,
    )
    session.add(u)
    await session.flush()
    return u, True


@router.message(CommandStart())
async def cmd_start(message: Message, session, bot: Bot, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    args = message.text.split(maxsplit=1)
    referrer_id = None
    check_code = None

    if len(args) > 1:
        payload = args[1].strip()
        if payload.startswith("ref_"):
            rc = payload[4:]
            r = await session.execute(select(User).where(User.referral_code == rc))
            ref_user = r.scalar_one_or_none()
            if ref_user and ref_user.id != uid:
                referrer_id = ref_user.id
        elif payload.startswith("check_"):
            check_code = payload[6:].upper()

    user, is_new = await get_or_create_user(session, message.from_user, referrer_id)

    if is_new and referrer_id:
        rr = await session.execute(select(User).where(User.id == referrer_id).with_for_update())
        ref = rr.scalar_one_or_none()
        if ref:
            ref.balance += BusinessRules.REFERRAL_BASE_REWARD
            ref.total_earned += BusinessRules.REFERRAL_BASE_REWARD
            ref.referral_earnings += BusinessRules.REFERRAL_BASE_REWARD
            ref.add_xp(BusinessRules.REFERRAL_XP_BASE)
            session.add(Transaction(
                user_id=ref.id,
                amount=BusinessRules.REFERRAL_BASE_REWARD,
                type=TransactionType.REFERRAL_REWARD,
                description="Referral bonus",
                balance_after=ref.balance,
            ))

    await session.commit()

    if check_code:
        from handlers.checks import _redeem_check_deeplink
        await _redeem_check_deeplink(message, session, check_code)
        return

    # Base menu-r moto same reply keyboard ekhaneo use kora holo jate start korlei menu button gulo chole ashe
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 আয়"), KeyboardButton(text="📢 প্রচার করুন")],
            [KeyboardButton(text="🎫 চেক"), KeyboardButton(text="👤 আমার কেবিনেট")],
            [KeyboardButton(text="🛡️ সাবস্ক্রিপশন চেক"), KeyboardButton(text="📊 আমাদের বট ও প...")],
            [KeyboardButton(text="🔗 দরকারি লিংক"), KeyboardButton(text="ℹ️ নির্দেশিকা")]
        ],
        resize_keyboard=True
    ]

    welcome_text = t(user.language, "welcome", bot_name=esc(BOT_USERNAME), first_name=esc(message.from_user.first_name))
    
    await message.answer(
        welcome_text,
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )

import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from aiogram import Router, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from config import BOT_USERNAME, WELCOME_MEDIA_URL, logger
from database import User
from localization import L10N, t
from keyboards import main_menu
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
    code = (tg_user.language_code or "en").split("-")[0].lower()
    lang = code if code in L10N.LANGS else "en"
    u = User(
        id=tg_user.id, username=tg_user.username,
        first_name=tg_user.first_name, last_name=tg_user.last_name,
        is_premium=tg_user.is_premium or False,
        language=lang,
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
        payload = args[1].strip()[:64]
        if payload.startswith("ref_"):
            rc = payload[4:]
            r = await session.execute(select(User).where(User.referral_code == rc))
            ref_user = r.scalar_one_or_none()
            if ref_user and ref_user.id != uid and not ref_user.is_blocked:
                referrer_id = ref_user.id
        elif payload.startswith("check_"):
            check_code = payload[6:].upper()

    user, is_new = await get_or_create_user(session, message.from_user, referrer_id)

    # NOTE: referral rewards are NOT paid here any more. They are paid once the
    # invited user completes a task and stays for the retention period
    # (see services.settle_referrals). Paying on /start was trivially farmable.
    await session.commit()

    welcome = t(user.language, "welcome", bot_name=esc(BOT_USERNAME), first_name=esc(message.from_user.first_name))
    kb = main_menu(user.language)
    sent = False
    if WELCOME_MEDIA_URL and len(welcome) <= 1024:
        # Banner is optional; any failure falls back to plain text.
        for method in (message.answer_animation, message.answer_photo):
            try:
                kw = {"animation" if method == message.answer_animation else "photo": WELCOME_MEDIA_URL}
                await method(caption=welcome, reply_markup=kb, parse_mode=ParseMode.HTML, **kw)
                sent = True
                break
            except Exception as e:
                logger.debug(f"welcome media via {method.__name__} failed: {e}")
    if not sent:
        await message.answer(welcome, reply_markup=kb, parse_mode=ParseMode.HTML)

    if check_code:
        from handlers.checks import _redeem_check_deeplink
        await _redeem_check_deeplink(message, session, check_code, state)

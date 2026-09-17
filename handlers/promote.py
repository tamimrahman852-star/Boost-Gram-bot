import re
from decimal import Decimal
from sqlalchemy import select
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.fsm.context import FSMContext

from config import BOT_USERNAME, BusinessRules, TaskPricing
from database import User, Campaign, Transaction, CampaignStatus, TaskType, TransactionType, VERIFIABLE_TASKS
from localization import L10N, t
from keyboards import promote_type_kb, premium_kb
from helpers import esc, safe_text, PromoState, hash_sensitive

router = Router()


async def get_user(session, uid):
    r = await session.execute(select(User).where(User.id == uid))
    return r.scalar_one_or_none()


@router.message(F.text.in_([L10N.T["en"]["menu_promote"], L10N.T["bn"]["menu_promote"]]))
async def promote_start(message: Message, session, state: FSMContext):
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer(t("en", "not_started"))
        return
    await message.answer(
        t(user.language, "promote_title"),
        reply_markup=promote_type_kb(user.language),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(PromoState.task_type)


@router.callback_query(PromoState.task_type, F.data.startswith("promo:type:"))
async def promo_type(query: CallbackQuery, state: FSMContext, session):
    tt = query.data.split(":")[2]
    await state.update_data(task_type=tt)
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(t(lang, "promote_name"))
    await state.set_state(PromoState.title)
    await query.answer()


@router.message(PromoState.title)
async def promo_title(message: Message, session, state: FSMContext):
    txt = safe_text(message)
    if not txt or len(txt) > 200:
        await message.answer("❌ Title 1-200 chars.")
        return
    await state.update_data(title=txt)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    data = await state.get_data()
    tt = data["task_type"]
    if tt in ("channel_sub", "group_join", "boost_7day"):
        await message.answer(t(lang, "promote_target"), parse_mode=ParseMode.HTML)
    elif tt == "bot_start":
        await message.answer(t(lang, "promote_bot_token"))
    else:
        await message.answer(t(lang, "promote_link"))
    await state.set_state(PromoState.target)


@router.message(PromoState.target)
async def promo_target(message: Message, session, state: FSMContext, bot: Bot):
    raw = safe_text(message)
    if not raw:
        await message.answer(t("en", "text_only"))
        return
    data = await state.get_data()
    tt = data["task_type"]
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"

    if tt in ("channel_sub", "group_join", "boost_7day"):
        status = await message.answer("🔎 Verifying...")
        try:
            identifier = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", raw, flags=re.I)
            identifier = identifier.lstrip("@").split("/")[0].split("?")[0]
            if identifier.lstrip("-").isdigit():
                identifier = int(identifier)
            else:
                identifier = f"@{identifier}"
            chat = await bot.get_chat(identifier)
            me = await bot.get_me()
            m = await bot.get_chat_member(chat.id, me.id)
            if m.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
                await status.edit_text(f"❌ Add @{esc(BOT_USERNAME)} as <b>Admin</b>, then send again.", parse_mode=ParseMode.HTML)
                return
            invite = None
            if not chat.username:
                try: invite = await bot.export_chat_invite_link(chat.id)
                except Exception: pass
            await state.update_data(
                target_chat_id=chat.id, target_username=chat.username,
                target_link=invite or (f"https://t.me/{chat.username}" if chat.username else None),
                target_title=chat.title or str(chat.id),
            )
            await status.edit_text(f"✅ Verified: <b>{esc(chat.title)}</b>", parse_mode=ParseMode.HTML)
        except Exception as e:
            await status.edit_text(f"❌ Chat error: {esc(str(e))[:200]}")
            return
    elif tt == "bot_start":
        try:
            from aiogram import Bot as TBot
            tb = TBot(token=raw)
            me = await tb.get_me()
            await tb.session.close()
            await state.update_data(
                target_bot_token_hash=hash_sensitive(raw),
                target_bot_id=me.id, target_username=me.username,
                target_link=f"https://t.me/{me.username}",
                target_title=me.first_name,
            )
            await message.answer(f"✅ Bot: @{esc(me.username)}")
        except Exception:
            await message.answer("❌ Invalid token. Send again.")
            return
    else:
        await state.update_data(target_link=raw)

    await message.answer(t(lang, "promote_premium"), reply_markup=premium_kb(lang), parse_mode=ParseMode.HTML)
    await state.set_state(PromoState.premium)


@router.callback_query(PromoState.premium, F.data.startswith("promo:premium:"))
async def promo_premium(query: CallbackQuery, state: FSMContext, session):
    is_prem = query.data.endswith(":yes")
    await state.update_data(premium_only=is_prem)
    data = await state.get_data()
    rules = TaskPricing.get(data["task_type"], is_prem)
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(
        t(lang, "promote_reward", min=rules["min"], max=rules["max"], suggested=rules["suggested"]),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(PromoState.reward)
    await query.answer()


@router.message(PromoState.reward)
async def promo_reward(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    if not raw:
        await message.answer(t(lang, "text_only"))
        return
    try:
        v = Decimal(raw)
    except Exception:
        await message.answer("❌ Invalid number.")
        return
    data = await state.get_data()
    ok, err = TaskPricing.validate(data["task_type"], v, data.get("premium_only", False))
    if not ok:
        await message.answer(f"❌ {err}")
        return
    await state.update_data(reward=str(v))
    await message.answer(t(lang, "promote_slots"))
    await state.set_state(PromoState.slots)


@router.message(PromoState.slots)
async def promo_slots(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    if not raw:
        await message.answer(t(lang, "text_only"))
        return
    try:
        n = int(raw)
        if n <= 0 or n > 100000:
            raise ValueError()
    except Exception:
        await message.answer("❌ 1-100000.")
        return

    data = await state.get_data()
    await state.clear()

    reward = Decimal(data["reward"])
    cost = reward * n
    commission = cost * BusinessRules.PLATFORM_COMMISSION
    total = cost + commission

    uid = message.from_user.id
    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = r.scalar_one_or_none()
        if not user:
            await message.answer(t("en", "not_started"))
            return
        if user.balance < total:
            await message.answer(
                t(lang, "promote_insufficient", required=total, balance=user.balance),
                parse_mode=ParseMode.HTML,
            )
            return

        # Duplicate check
        if data.get("target_chat_id"):
            d = await session.execute(select(Campaign).where(
                Campaign.advertiser_id == uid,
                Campaign.target_chat_id == data["target_chat_id"],
                Campaign.task_type == TaskType(data["task_type"]),
                Campaign.status == CampaignStatus.ACTIVE,
            ))
            if d.scalar_one_or_none():
                await message.answer(t(lang, "promote_duplicate"))
                return

        user.balance -= total
        camp = Campaign(
            advertiser_id=uid,
            title=data["title"],
            task_type=TaskType(data["task_type"]),
            target_chat_id=data.get("target_chat_id"),
            target_username=data.get("target_username"),
            target_link=data.get("target_link"),
            target_title=data.get("target_title"),
            target_bot_token_hash=data.get("target_bot_token_hash"),
            target_bot_id=data.get("target_bot_id"),
            premium_only=data.get("premium_only", False),
            reward_per_user=reward,
            base_reward=reward / (TaskPricing.PREMIUM_MULTIPLIER if data.get("premium_only") else Decimal("1")),
            max_completions=n,
            total_budget=total,
            requires_retention_check=data["task_type"] in {x.value for x in VERIFIABLE_TASKS},
        )
        session.add(camp)
        session.add(Transaction(
            user_id=uid, amount=-total,
            type=TransactionType.CAMPAIGN_PAYMENT,
            description=f"Campaign: {data['title']}",
            reference_id=camp.id, reference_type="campaign",
            balance_after=user.balance,
        ))
    await session.commit()

    await message.answer(
        t(lang, "promote_created", title=esc(data["title"]), reward=reward, slots=n, cost=total),
        parse_mode=ParseMode.HTML,
    )

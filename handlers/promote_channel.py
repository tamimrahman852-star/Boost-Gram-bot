"""Campaign creation for channels and groups.

Flow: pick chat -> audience -> reward per user -> number of users -> confirm & pay.
The bot verifies with Telegram that (a) the user really is an admin of the chat and
(b) the bot is an admin there, otherwise nobody could be verified later.
"""
import math
from decimal import Decimal, InvalidOperation

from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, KeyboardButtonRequestChat,
)
from aiogram.types.chat_administrator_rights import ChatAdministratorRights
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select, func

from config import BusinessRules, TaskPricing, logger
from database import (
    User, Campaign, Transaction, CampaignStatus, TaskType, TransactionType, redis_client,
)
from keyboards import main_menu
from helpers import esc, safe_text, L, CampaignCreateState

router = Router()

CANCEL_TEXTS = {"❌ বাতিল", "❌ Cancel"}
REQ_CHANNEL, REQ_GROUP = 1, 2


def _no_rights(**on) -> ChatAdministratorRights:
    base = dict(
        is_anonymous=False, can_manage_chat=False, can_delete_messages=False,
        can_manage_video_chats=False, can_restrict_members=False, can_promote_members=False,
        can_change_info=False, can_invite_users=False, can_post_stories=False,
        can_edit_stories=False, can_delete_stories=False,
    )
    base.update(on)
    return ChatAdministratorRights(**base)


async def _get_user(session, uid):
    return (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()


async def _start_picker(query: CallbackQuery, state: FSMContext, session, is_channel: bool):
    await state.clear()
    user = await _get_user(session, query.from_user.id)
    lang = user.language if user else "bn"
    active = (await session.execute(select(func.count(Campaign.id)).where(
        Campaign.advertiser_id == query.from_user.id,
        Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).scalar() or 0
    if active >= BusinessRules.MAX_ACTIVE_CAMPAIGNS:
        await query.answer(L(lang, f"সর্বোচ্চ {BusinessRules.MAX_ACTIVE_CAMPAIGNS}টি সক্রিয় ক্যাম্পেইন চালানো যায়।",
                               f"Maximum {BusinessRules.MAX_ACTIVE_CAMPAIGNS} active campaigns allowed."), show_alert=True)
        return
    kind_bn, kind_en = ("চ্যানেল", "channel") if is_channel else ("গ্রুপ", "group")
    text = L(
        lang,
        f"📢 <b>প্রচারের জন্য {kind_bn} বেছে নিন</b>\n\n"
        f"১) নিচের বাটনে চাপুন এবং আপনার {kind_bn} সিলেক্ট করুন\n"
        "২) বটকে সেই চ্যাটে <b>অ্যাডমিন</b> বানান (ইনভাইট পারমিশনসহ) — নাহলে সদস্য যাচাই করা যাবে না",
        f"📢 <b>Choose the {kind_en} to promote</b>\n\n"
        f"1) Tap the button below and pick your {kind_en}\n"
        "2) Make the bot an <b>admin</b> there (with invite permission) — otherwise members cannot be verified",
    )
    pick = KeyboardButton(
        text=L(lang, f"🏠 আমার {kind_bn} বেছে নিন", f"🏠 Pick my {kind_en}"),
        request_chat=KeyboardButtonRequestChat(
            request_id=REQ_CHANNEL if is_channel else REQ_GROUP,
            chat_is_channel=is_channel,
            # Telegram requires bot rights to be a subset of the user's rights
            user_administrator_rights=_no_rights(can_manage_chat=True, can_invite_users=True),
            bot_administrator_rights=_no_rights(can_manage_chat=True, can_invite_users=True),
            bot_is_member=True,
        ),
    )
    kb = ReplyKeyboardMarkup(
        keyboard=[[pick], [KeyboardButton(text=L(lang, "❌ বাতিল", "❌ Cancel"))]],
        resize_keyboard=True,
    )
    await query.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "promote:cat:channel")
async def promote_channel_start(query: CallbackQuery, state: FSMContext, session):
    await _start_picker(query, state, session, True)


@router.callback_query(F.data == "promote:cat:group")
async def promote_group_start(query: CallbackQuery, state: FSMContext, session):
    await _start_picker(query, state, session, False)


@router.message(F.text.in_(CANCEL_TEXTS))
async def cancel_flow(message: Message, state: FSMContext, session):
    await state.clear()
    user = await _get_user(session, message.from_user.id)
    lang = user.language if user else "bn"
    await message.answer(L(lang, "🏠 প্রধান মেনু", "🏠 Main menu"), reply_markup=main_menu(lang))


@router.message(F.chat_shared)
async def handle_shared_chat(message: Message, state: FSMContext, session, bot: Bot):
    shared = message.chat_shared
    if shared.request_id not in (REQ_CHANNEL, REQ_GROUP):
        return
    uid = message.from_user.id
    user = await _get_user(session, uid)
    if not user or user.is_blocked:
        return
    lang = user.language
    chat_id = shared.chat_id
    task_type = TaskType.CHANNEL_SUB if shared.request_id == REQ_CHANNEL else TaskType.GROUP_JOIN

    # --- verify with Telegram, never trust the client ---
    try:
        chat = await bot.get_chat(chat_id)
        me = await bot.get_chat_member(chat_id, bot.id)
        who = await bot.get_chat_member(chat_id, uid)
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(L(lang, "❌ বট এই চ্যাটে অ্যাডমিন নয়। বটকে অ্যাডমিন বানিয়ে আবার চেষ্টা করুন।",
                               "❌ The bot is not an admin in that chat. Add it as admin and try again."))
        return
    if me.status != ChatMemberStatus.ADMINISTRATOR:
        await message.answer(L(lang, "❌ বটকে চ্যাটে অ্যাডমিন বানান, তারপর আবার চেষ্টা করুন।",
                               "❌ Please make the bot an admin of that chat, then try again."))
        return
    if who.status not in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        await message.answer(L(lang, "❌ শুধুমাত্র চ্যাটের অ্যাডমিন প্রচার করতে পারেন।",
                               "❌ Only chat admins can promote it."))
        return
    dup = (await session.execute(select(Campaign.id).where(
        Campaign.target_chat_id == chat_id,
        Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).first()
    if dup:
        await message.answer(L(lang, "❌ এই চ্যাটের জন্য ইতিমধ্যে একটি সক্রিয় ক্যাম্পেইন আছে।",
                               "❌ This chat already has an active campaign."))
        return

    link = f"https://t.me/{chat.username}" if chat.username else chat.invite_link
    if not link:
        try:
            link = (await bot.create_chat_invite_link(chat_id, name="BoostGram")).invite_link
        except (TelegramBadRequest, TelegramForbiddenError):
            await message.answer(L(lang, "❌ বটের ইনভাইট লিংক তৈরির পারমিশন নেই।",
                                   "❌ The bot needs the 'invite users' permission."))
            return

    await state.update_data(
        chat_id=chat_id, title=(chat.title or "Chat")[:200], username=chat.username,
        link=link, task_type=task_type.value, premium=False,
    )
    await message.answer("✅", reply_markup=main_menu(lang))
    text = L(
        lang,
        f"✅ <b>{esc(chat.title)}</b> সফলভাবে যোগ হয়েছে।\n\n🎯 <b>কারা এই টাস্ক করতে পারবে?</b>",
        f"✅ <b>{esc(chat.title)}</b> added.\n\n🎯 <b>Who can complete this task?</b>",
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=L(lang, "🌍 সব ব্যবহারকারী", "🌍 All users"), callback_data="promote:aud:all")],
        [InlineKeyboardButton(text=L(lang, "⭐ শুধু Telegram Premium (২x মূল্য)", "⭐ Telegram Premium only (2x price)"), callback_data="promote:aud:premium")],
        [InlineKeyboardButton(text=L(lang, "◀️ বাতিল", "◀️ Cancel"), callback_data="menu:promote")],
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.in_({"promote:aud:all", "promote:aud:premium"}))
async def audience_chosen(query: CallbackQuery, state: FSMContext, session):
    data = await state.get_data()
    if "chat_id" not in data:
        await query.answer("Session expired.", show_alert=True)
        return
    user = await _get_user(session, query.from_user.id)
    lang = user.language if user else "bn"
    premium = query.data.endswith("premium")
    await state.update_data(premium=premium)
    rules = TaskPricing.get(data["task_type"], premium)
    await state.set_state(CampaignCreateState.reward)
    text = L(
        lang,
        f"💲 <b>প্রতিটি সাবস্ক্রাইবারের জন্য পুরস্কার (GRAM) লিখুন</b>\n\n"
        f"ন্যূনতম: <b>{rules['min']:,}</b>\nপ্রস্তাবিত: <b>{rules['suggested']:,}</b>\nসর্বোচ্চ: <b>{rules['max']:,}</b>\n\n"
        "💡 পুরস্কার যত বেশি, টাস্ক তত দ্রুত সম্পন্ন হবে।",
        f"💲 <b>Enter the reward (GRAM) per subscriber</b>\n\n"
        f"Minimum: <b>{rules['min']:,}</b>\nSuggested: <b>{rules['suggested']:,}</b>\nMaximum: <b>{rules['max']:,}</b>\n\n"
        "💡 Higher rewards get completed faster.",
    )
    await query.message.edit_text(text, parse_mode=ParseMode.HTML)
    await query.answer()


@router.message(CampaignCreateState.reward)
async def got_reward(message: Message, state: FSMContext, session):
    user = await _get_user(session, message.from_user.id)
    lang = user.language if user else "bn"
    raw = safe_text(message)
    data = await state.get_data()
    try:
        reward = Decimal((raw or "").replace(",", ""))
        if not reward.is_finite() or reward != reward.to_integral_value():
            raise InvalidOperation
    except InvalidOperation:
        await message.answer(L(lang, "❌ একটি পূর্ণ সংখ্যা লিখুন।", "❌ Send a whole number."))
        return
    ok, msg = TaskPricing.validate(data["task_type"], reward, data.get("premium", False))
    if not ok:
        await message.answer(f"❌ {msg}")
        return
    await state.update_data(reward=str(reward))
    await state.set_state(CampaignCreateState.slots)
    await message.answer(L(
        lang,
        f"👥 <b>কতজন ব্যবহারকারী চান?</b>\n\n{BusinessRules.CAMPAIGN_MIN_SLOTS} থেকে {BusinessRules.CAMPAIGN_MAX_SLOTS:,} এর মধ্যে লিখুন।",
        f"👥 <b>How many users do you want?</b>\n\nSend a number between {BusinessRules.CAMPAIGN_MIN_SLOTS} and {BusinessRules.CAMPAIGN_MAX_SLOTS:,}.",
    ), parse_mode=ParseMode.HTML)


def _cost(reward: Decimal, slots: int):
    budget = reward * slots
    fee = (budget * BusinessRules.PLATFORM_COMMISSION).to_integral_value(rounding="ROUND_UP")
    return budget, fee, budget + fee


@router.message(CampaignCreateState.slots)
async def got_slots(message: Message, state: FSMContext, session):
    user = await _get_user(session, message.from_user.id)
    lang = user.language if user else "bn"
    raw = safe_text(message)
    try:
        slots = int(raw or "")
        if not BusinessRules.CAMPAIGN_MIN_SLOTS <= slots <= BusinessRules.CAMPAIGN_MAX_SLOTS:
            raise ValueError
    except ValueError:
        await message.answer(L(lang, f"❌ {BusinessRules.CAMPAIGN_MIN_SLOTS}–{BusinessRules.CAMPAIGN_MAX_SLOTS:,} এর মধ্যে একটি সংখ্যা দিন।",
                               f"❌ Send a number between {BusinessRules.CAMPAIGN_MIN_SLOTS} and {BusinessRules.CAMPAIGN_MAX_SLOTS:,}."))
        return
    data = await state.get_data()
    reward = Decimal(data["reward"])
    budget, fee, total = _cost(reward, slots)
    await state.update_data(slots=slots)
    await state.set_state(None)
    text = L(
        lang,
        f"🧾 <b>ক্যাম্পেইন সারসংক্ষেপ</b>\n\n📢 {esc(data['title'])}\n"
        f"💲 প্রতি ইউজার: <b>{reward:,.0f}</b> GRAM\n👥 ইউজার: <b>{slots:,}</b>\n"
        f"💰 বাজেট: <b>{budget:,.0f}</b>\n🏷 প্ল্যাটফর্ম ফি ({BusinessRules.PLATFORM_COMMISSION * 100:.0f}%): <b>{fee:,.0f}</b>\n"
        f"➖ <b>মোট কাটা হবে: {total:,.0f} GRAM</b>\n\n"
        f"আপনার ব্যালেন্স: {(user.balance if user else 0):,.0f} GRAM",
        f"🧾 <b>Campaign summary</b>\n\n📢 {esc(data['title'])}\n"
        f"💲 Per user: <b>{reward:,.0f}</b> GRAM\n👥 Users: <b>{slots:,}</b>\n"
        f"💰 Budget: <b>{budget:,.0f}</b>\n🏷 Platform fee ({BusinessRules.PLATFORM_COMMISSION * 100:.0f}%): <b>{fee:,.0f}</b>\n"
        f"➖ <b>Total charge: {total:,.0f} GRAM</b>\n\n"
        f"Your balance: {(user.balance if user else 0):,.0f} GRAM",
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=L(lang, "✅ নিশ্চিত করুন ও পেমেন্ট", "✅ Confirm & pay"), callback_data="promote:confirm")],
        [InlineKeyboardButton(text=L(lang, "❌ বাতিল", "❌ Cancel"), callback_data="menu:promote")],
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "promote:confirm")
async def confirm_campaign(query: CallbackQuery, state: FSMContext, session):
    uid = query.from_user.id
    lock = f"lock:campaign:{uid}"
    if not await redis_client.set(lock, "1", nx=True, ex=20):
        await query.answer("⏳", show_alert=True)
        return
    try:
        data = await state.get_data()
        if not {"chat_id", "reward", "slots", "task_type"} <= data.keys():
            await query.answer("Session expired.", show_alert=True)
            return
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = r.scalar_one_or_none()
        if not user or user.is_blocked:
            await session.rollback()
            await query.answer("❌", show_alert=True)
            return
        lang = user.language
        reward, slots, premium = Decimal(data["reward"]), int(data["slots"]), bool(data.get("premium"))
        ok, msg = TaskPricing.validate(data["task_type"], reward, premium)
        if not ok or not BusinessRules.CAMPAIGN_MIN_SLOTS <= slots <= BusinessRules.CAMPAIGN_MAX_SLOTS:
            await session.rollback()
            await query.answer("❌ Invalid.", show_alert=True)
            return
        budget, fee, total = _cost(reward, slots)
        if user.balance < total:
            await session.rollback()
            await query.answer(L(lang, f"❌ ব্যালেন্স কম। দরকার {total:,.0f} GRAM।", f"❌ Insufficient balance. Need {total:,.0f} GRAM."), show_alert=True)
            return
        if (await session.execute(select(Campaign.id).where(
                Campaign.target_chat_id == data["chat_id"],
                Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).first():
            await session.rollback()
            await query.answer(L(lang, "❌ এই চ্যাটের ক্যাম্পেইন আগে থেকেই আছে।", "❌ A campaign for this chat already exists."), show_alert=True)
            return

        c = Campaign(
            advertiser_id=uid, title=data["title"], task_type=TaskType(data["task_type"]),
            target_chat_id=data["chat_id"], target_username=data.get("username"),
            target_link=data.get("link"), target_title=data["title"],
            premium_only=premium, reward_per_user=reward, base_reward=reward,
            max_completions=slots, total_budget=budget,
            retention_days=BusinessRules.RETENTION_DAYS, requires_retention_check=True,
        )
        session.add(c)
        await session.flush()
        user.balance -= total
        session.add(Transaction(
            user_id=uid, amount=-total, type=TransactionType.CAMPAIGN_PAYMENT,
            description=f"Campaign: {data['title']} (fee {fee:,.0f})"[:255],
            reference_id=c.id, reference_type="campaign", balance_after=user.balance,
        ))
        await session.commit()
        await state.clear()
        await query.message.edit_text(L(
            lang,
            f"🚀 <b>ক্যাম্পেইন চালু হয়েছে!</b>\n\n📢 {esc(data['title'])}\n👥 {slots:,} জন · 💲 {reward:,.0f} GRAM\n\n"
            "‘📋 আমার ক্যাম্পেইন’ থেকে অগ্রগতি দেখুন ও ম্যানেজ করুন।",
            f"🚀 <b>Campaign is live!</b>\n\n📢 {esc(data['title'])}\n👥 {slots:,} users · 💲 {reward:,.0f} GRAM\n\n"
            "Track and manage it under ‘📋 My campaigns’.",
        ), parse_mode=ParseMode.HTML)
        await query.answer("✅")
    except Exception as e:
        await session.rollback()
        logger.exception(f"Campaign create failed: {e}")
        await query.answer("❌ Error", show_alert=True)
    finally:
        await redis_client.delete(lock)

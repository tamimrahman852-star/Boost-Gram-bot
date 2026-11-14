"""Campaign creation for channels and groups.

Flow: pick chat -> audience -> reward per user -> number of users -> confirm & pay.
The bot verifies with Telegram that (a) the user really is an admin of the chat and
(b) the bot is an admin there, otherwise nobody could be verified later.
"""
import math
import re
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
REQ_CHANNEL, REQ_GROUP, REQ_CHANNEL_ANY, REQ_GROUP_ANY = 1, 2, 3, 4
_USERNAME_RE = re.compile(r"^(?:https?://)?(?:t(?:elegram)?\.me/)?@?([A-Za-z][A-Za-z0-9_]{3,31})/?$")


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
    # While this step is open, a typed @username / t.me link is also accepted.
    await state.set_state(CampaignCreateState.target)
    await state.update_data(is_channel=is_channel)

    kind_bn, kind_en = ("চ্যানেল", "channel") if is_channel else ("গ্রুপ", "group")
    text = L(
        lang,
        f"📢 <b>প্রমোশনের জন্য {kind_bn} বেছে নিন</b> (বটকে অ্যাডমিন হতে হবে)\n\n"
        f"🏠 <b>আমি অ্যাডমিন</b> — আপনি যে {kind_bn}-এর অ্যাডমিন সেটি বেছে নিন\n"
        f"👤 <b>আমি অ্যাডমিন নই</b> — যেকোনো পাবলিক {kind_bn} বেছে নিন, অথবা সরাসরি <code>@username</code> / লিংক পাঠান\n\n"
        "⚠️ দুই ক্ষেত্রেই বটকে সেই চ্যাটে <b>অ্যাডমিন</b> (ইনভাইট পারমিশনসহ) বানাতে হবে, নাহলে সদস্য যাচাই করা যাবে না।",
        f"📢 <b>Choose a {kind_en} to promote</b> (the bot must be an admin there)\n\n"
        f"🏠 <b>I'm an admin</b> — pick a {kind_en} you administer\n"
        f"👤 <b>I'm not an admin</b> — pick any public {kind_en}, or just send its <code>@username</code> / link\n\n"
        "⚠️ In both cases the bot must be an <b>admin</b> there (with invite permission), otherwise members cannot be verified.",
    )
    cancel = [KeyboardButton(text=L(lang, "❌ বাতিল", "❌ Cancel"))]
    admin_text = L(lang, "🏠 আমি অ্যাডমিন", "🏠 I'm an admin")
    any_text = L(lang, "👤 আমি অ্যাডমিন নই", "👤 I'm not an admin")
    admin_id = REQ_CHANNEL if is_channel else REQ_GROUP
    any_id = REQ_CHANNEL_ANY if is_channel else REQ_GROUP_ANY

    def keyboards():
        # Telegram is strict about the combinations of right-requirements (bot rights must be a
        # subset of the user's rights, etc). Try the strictest layout first and fall back to
        # simpler ones instead of crashing: admin status is verified through the API anyway.
        rights = _no_rights(can_manage_chat=True, can_invite_users=True)
        yield [
            [KeyboardButton(text=admin_text, request_chat=KeyboardButtonRequestChat(
                request_id=admin_id, chat_is_channel=is_channel,
                user_administrator_rights=rights, bot_administrator_rights=rights, bot_is_member=True))],
            [KeyboardButton(text=any_text, request_chat=KeyboardButtonRequestChat(
                request_id=any_id, chat_is_channel=is_channel, chat_has_username=True, bot_is_member=True))],
            cancel,
        ]
        yield [
            [KeyboardButton(text=admin_text, request_chat=KeyboardButtonRequestChat(
                request_id=admin_id, chat_is_channel=is_channel))],
            [KeyboardButton(text=any_text, request_chat=KeyboardButtonRequestChat(
                request_id=any_id, chat_is_channel=is_channel))],
            cancel,
        ]
        yield [cancel]   # last resort: user types @username / link

    sent = False
    for rows in keyboards():
        try:
            await query.message.answer(
                text, reply_markup=ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True),
                parse_mode=ParseMode.HTML)
            sent = True
            break
        except TelegramBadRequest as e:
            logger.warning(f"Chat picker keyboard rejected by Telegram ({e}); trying a simpler one")
    if not sent:
        await query.answer("❌ Error", show_alert=True)
        return
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


async def _process_target(message: Message, state: FSMContext, session, bot: Bot,
                          chat_id: int, task_type: TaskType, require_user_admin: bool):
    """Common pipeline for a chosen chat. The BOT must always be admin there (otherwise
    nobody can be verified later). Anyone may promote any such chat: the advertiser pays,
    and the chat owner has implicitly agreed by making the bot an admin."""
    uid = message.from_user.id
    user = await _get_user(session, uid)
    if not user or user.is_blocked:
        return
    lang = user.language

    try:
        chat = await bot.get_chat(chat_id)
        me = await bot.get_chat_member(chat_id, bot.id)
        who = await bot.get_chat_member(chat_id, uid) if require_user_admin else None
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(L(lang, "❌ বট এই চ্যাটে অ্যাডমিন নয়। বটকে অ্যাডমিন বানিয়ে আবার চেষ্টা করুন।",
                               "❌ The bot is not an admin in that chat. Add it as admin and try again."))
        return
    if me.status != ChatMemberStatus.ADMINISTRATOR:
        await message.answer(L(lang, "❌ বটকে চ্যাটে অ্যাডমিন বানান, তারপর আবার চেষ্টা করুন।",
                               "❌ Please make the bot an admin of that chat, then try again."))
        return
    if require_user_admin and who.status not in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        await message.answer(L(lang, "❌ আপনি এই চ্যাটের অ্যাডমিন নন। “আমি অ্যাডমিন নই” অপশন ব্যবহার করুন।",
                               "❌ You are not an admin of that chat. Use the “I'm not an admin” option."))
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

    await state.set_state(None)
    await state.update_data(
        chat_id=chat_id, title=(chat.title or "Chat")[:200], username=chat.username,
        link=link, task_type=task_type.value, premium=False,
    )
    await message.answer("✅", reply_markup=main_menu(lang))
    rl = TaskPricing.get(task_type.value, False)
    rp = TaskPricing.get(task_type.value, True)
    text = L(
        lang,
        f"🎯 <b>টাস্কের অডিয়েন্স</b> — {esc(chat.title)}\n\n"
        f"<b>1️⃣ সব ব্যবহারকারী</b>\nসকল ব্যবহারকারীর মধ্যে বিস্তৃত পৌঁছানো।\n💡 সর্বনিম্ন মূল্য: {rl['min']:,} GRAM/ইউনিট।\n\n"
        f"<b>2️⃣ শুধু Telegram Premium</b>\nশুধু Premium ব্যবহারকারীদের দেখানো হবে — আরও মানসম্মত অডিয়েন্স।\n💡 সর্বনিম্ন মূল্য: {rp['min']:,} GRAM/ইউনিট।",
        f"🎯 <b>Task audience</b> — {esc(chat.title)}\n\n"
        f"<b>1️⃣ All users</b>\nWide reach across all users.\n💡 Minimum price: {rl['min']:,} GRAM/unit.\n\n"
        f"<b>2️⃣ Telegram Premium only</b>\nShown only to Premium users — a higher-quality audience.\n💡 Minimum price: {rp['min']:,} GRAM/unit.",
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=L(lang, "1️⃣ সব ব্যবহারকারী", "1️⃣ All users"), callback_data="promote:aud:all")],
        [InlineKeyboardButton(text=L(lang, "2️⃣ শুধুমাত্র Telegram Premium", "2️⃣ Telegram Premium only"), callback_data="promote:aud:premium")],
        [InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="menu:promote")],
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.message(F.chat_shared)
async def handle_shared_chat(message: Message, state: FSMContext, session, bot: Bot):
    shared = message.chat_shared
    rid = shared.request_id
    if rid not in (REQ_CHANNEL, REQ_GROUP, REQ_CHANNEL_ANY, REQ_GROUP_ANY):
        return
    task_type = TaskType.CHANNEL_SUB if rid in (REQ_CHANNEL, REQ_CHANNEL_ANY) else TaskType.GROUP_JOIN
    await _process_target(message, state, session, bot, shared.chat_id, task_type,
                          require_user_admin=rid in (REQ_CHANNEL, REQ_GROUP))


@router.message(CampaignCreateState.target, F.text)
async def typed_target(message: Message, state: FSMContext, session, bot: Bot):
    """Let users promote any public channel/group by sending its @username or t.me link."""
    user = await _get_user(session, message.from_user.id)
    lang = user.language if user else "bn"
    data = await state.get_data()
    is_channel = data.get("is_channel", True)
    m = _USERNAME_RE.match((message.text or "").strip())
    if not m:
        await message.answer(L(lang, "❌ সঠিক <code>@username</code> বা <code>t.me/username</code> লিংক পাঠান (শুধু পাবলিক চ্যাট)।",
                               "❌ Send a valid <code>@username</code> or <code>t.me/username</code> link (public chats only)."),
                             parse_mode=ParseMode.HTML)
        return
    try:
        chat = await bot.get_chat("@" + m.group(1))
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(L(lang, "❌ এই নামে কোনো পাবলিক চ্যানেল/গ্রুপ পাওয়া যায়নি।",
                               "❌ No public channel/group was found with that name."))
        return
    ok_type = chat.type == "channel" if is_channel else chat.type in ("group", "supergroup")
    if not ok_type:
        await message.answer(L(lang, f"❌ এটি {'চ্যানেল' if is_channel else 'গ্রুপ'} নয়। সঠিক ধরন বেছে নিন।",
                               f"❌ That is not a {'channel' if is_channel else 'group'}."))
        return
    await _process_target(message, state, session, bot, chat.id,
                          TaskType.CHANNEL_SUB if is_channel else TaskType.GROUP_JOIN,
                          require_user_admin=False)


async def _reply(ev, text: str, kb=None):
    """Edit the message for callbacks, send a new one for text input."""
    if isinstance(ev, CallbackQuery):
        await ev.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await ev.answer()
    else:
        await ev.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


def _back_kb(lang: str, extra=None):
    rows = list(extra or [])
    rows.append([InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="menu:promote")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        f"ন্যূনতম: <b>{rules['min']:,}</b> · প্রস্তাবিত: <b>{rules['suggested']:,}</b> · সর্বোচ্চ: <b>{rules['max']:,}</b>\n\n"
        "💡 পুরস্কার যত বেশি, টাস্ক তত দ্রুত সম্পন্ন হবে। নিচের বাটন চাপুন অথবা সংখ্যা লিখুন।",
        f"💲 <b>Enter the reward (GRAM) per subscriber</b>\n\n"
        f"Min: <b>{rules['min']:,}</b> · Suggested: <b>{rules['suggested']:,}</b> · Max: <b>{rules['max']:,}</b>\n\n"
        "💡 Higher rewards get completed faster. Tap a button or type a number.",
    )
    kb = _back_kb(lang, [[
        InlineKeyboardButton(text=f"{rules['min']:,}", callback_data=f"promote:price:{rules['min']}"),
        InlineKeyboardButton(text=f"⭐ {rules['suggested']:,}", callback_data=f"promote:price:{rules['suggested']}"),
    ]])
    await _reply(query, text, kb)


def _max_slots(balance: Decimal, reward: Decimal) -> int:
    per_slot = reward * (1 + BusinessRules.PLATFORM_COMMISSION)
    if per_slot <= 0:
        return 0
    return int(min(BusinessRules.CAMPAIGN_MAX_SLOTS, math.floor(balance / per_slot)))


async def _ask_slots(ev, state: FSMContext, user, reward: Decimal):
    lang = user.language if user else "bn"
    await state.update_data(reward=str(reward))
    await state.set_state(CampaignCreateState.slots)
    bal = user.balance if user else Decimal(0)
    mx = _max_slots(bal, reward)
    pct = BusinessRules.PLATFORM_COMMISSION * 100
    text = L(
        lang,
        f"ℹ️ টাস্ক তৈরির কমিশন — <b>{pct:.0f}%</b>\n\n"
        f"<blockquote>💲 সাবস্ক্রিপশনের মূল্য — {reward:,.0f} GRAM\n💰 আপনার ব্যালেন্স — {bal:,.1f} GRAM</blockquote>\n"
        "✏️ <b>সংখ্যা লিখুন: কতজন সাবস্ক্রাইবার চান</b>",
        f"ℹ️ Task creation commission — <b>{pct:.0f}%</b>\n\n"
        f"<blockquote>💲 Price per subscriber — {reward:,.0f} GRAM\n💰 Your balance — {bal:,.1f} GRAM</blockquote>\n"
        "✏️ <b>Enter how many subscribers you want</b>",
    )
    extra = []
    if mx >= BusinessRules.CAMPAIGN_MIN_SLOTS:
        extra.append([InlineKeyboardButton(
            text=L(lang, f"{mx:,} (আপনার ব্যালেন্সের সর্বোচ্চ সীমা)", f"{mx:,} (max for your balance)"),
            callback_data=f"promote:qty:{mx}")])
    else:
        text += L(lang, "\n\n⚠️ ব্যালেন্স কম। প্রথমে ব্যালেন্স টপ-আপ করুন।", "\n\n⚠️ Not enough balance. Please top up first.")
    await _reply(ev, text, _back_kb(lang, extra))


@router.callback_query(F.data.startswith("promote:price:"))
async def price_button(query: CallbackQuery, state: FSMContext, session):
    data = await state.get_data()
    if "task_type" not in data:
        await query.answer("Session expired.", show_alert=True)
        return
    reward = Decimal(query.data.split(":")[2])
    ok, msg = TaskPricing.validate(data["task_type"], reward, data.get("premium", False))
    if not ok:
        await query.answer(f"❌ {msg}", show_alert=True)
        return
    await _ask_slots(query, state, await _get_user(session, query.from_user.id), reward)


@router.message(CampaignCreateState.reward)
async def got_reward(message: Message, state: FSMContext, session):
    user = await _get_user(session, message.from_user.id)
    lang = user.language if user else "bn"
    data = await state.get_data()
    try:
        reward = Decimal((safe_text(message) or "").replace(",", ""))
        if not reward.is_finite() or reward != reward.to_integral_value():
            raise InvalidOperation
    except InvalidOperation:
        await message.answer(L(lang, "❌ একটি পূর্ণ সংখ্যা লিখুন।", "❌ Send a whole number."))
        return
    ok, msg = TaskPricing.validate(data["task_type"], reward, data.get("premium", False))
    if not ok:
        await message.answer(f"❌ {msg}")
        return
    await _ask_slots(message, state, user, reward)


def _cost(reward: Decimal, slots: int):
    budget = reward * slots
    fee = (budget * BusinessRules.PLATFORM_COMMISSION).to_integral_value(rounding="ROUND_UP")
    return budget, fee, budget + fee


async def _show_ready(ev, state: FSMContext, user, slots: int):
    lang = user.language if user else "bn"
    data = await state.get_data()
    reward = Decimal(data["reward"])
    budget, fee, total = _cost(reward, slots)
    await state.update_data(slots=slots)
    await state.set_state(None)
    text = L(
        lang,
        f"✅ <b>প্রকাশের জন্য সব প্রস্তুত</b>\n\n📢 {esc(data['title'])}\n"
        f"👥 সাবস্ক্রাইবার: <b>{slots:,}</b> × <b>{reward:,.0f}</b> GRAM = {budget:,.0f}\n"
        f"🏷 কমিশন ({BusinessRules.PLATFORM_COMMISSION * 100:.0f}%): {fee:,.0f}\n"
        f"💲 <b>মোট কাটা হবে: {total:,.0f} GRAM</b>\n\nপোস্ট করতে «কাজ প্রকাশ করুন» চাপুন।",
        f"✅ <b>Ready to publish</b>\n\n📢 {esc(data['title'])}\n"
        f"👥 Subscribers: <b>{slots:,}</b> × <b>{reward:,.0f}</b> GRAM = {budget:,.0f}\n"
        f"🏷 Commission ({BusinessRules.PLATFORM_COMMISSION * 100:.0f}%): {fee:,.0f}\n"
        f"💲 <b>Total charge: {total:,.0f} GRAM</b>\n\nTap “Publish task” to post it.",
    )
    kb = _back_kb(lang, [[InlineKeyboardButton(text=L(lang, "কাজ প্রকাশ করুন", "Publish task"), callback_data="promote:confirm")]])
    await _reply(ev, text, kb)


@router.callback_query(F.data.startswith("promote:qty:"))
async def qty_button(query: CallbackQuery, state: FSMContext, session):
    data = await state.get_data()
    if "reward" not in data:
        await query.answer("Session expired.", show_alert=True)
        return
    slots = int(query.data.split(":")[2])
    if not BusinessRules.CAMPAIGN_MIN_SLOTS <= slots <= BusinessRules.CAMPAIGN_MAX_SLOTS:
        await query.answer("❌", show_alert=True)
        return
    await _show_ready(query, state, await _get_user(session, query.from_user.id), slots)


@router.message(CampaignCreateState.slots)
async def got_slots(message: Message, state: FSMContext, session):
    user = await _get_user(session, message.from_user.id)
    lang = user.language if user else "bn"
    try:
        slots = int(safe_text(message) or "")
        if not BusinessRules.CAMPAIGN_MIN_SLOTS <= slots <= BusinessRules.CAMPAIGN_MAX_SLOTS:
            raise ValueError
    except ValueError:
        await message.answer(L(lang, f"❌ {BusinessRules.CAMPAIGN_MIN_SLOTS}–{BusinessRules.CAMPAIGN_MAX_SLOTS:,} এর মধ্যে একটি সংখ্যা দিন।",
                               f"❌ Send a number between {BusinessRules.CAMPAIGN_MIN_SLOTS} and {BusinessRules.CAMPAIGN_MAX_SLOTS:,}."))
        return
    await _show_ready(message, state, user, slots)


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

        await query.message.edit_text(L(lang, "⏳ কাজ তৈরি হচ্ছে, দয়া করে অপেক্ষা করুন...", "⏳ Creating your task, please wait..."))
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
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=L(lang, "📋 আমার কাজ", "📋 My tasks"), callback_data="promote:my_tasks")],
            [InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="menu:promote")],
        ])
        await query.message.edit_text(L(
            lang,
            f"✅ <b>টাস্ক №{c.id[:8].upper()} প্রকাশিত হয়েছে</b>\n💲 ব্যালেন্স থেকে কাটা হয়েছে: {total:,.1f} GRAM",
            f"✅ <b>Task №{c.id[:8].upper()} published</b>\n💲 Deducted from balance: {total:,.1f} GRAM",
        ), reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception as e:
        await session.rollback()
        logger.exception(f"Campaign create failed: {e}")
        await query.answer("❌ Error", show_alert=True)
    finally:
        await redis_client.delete(lock)

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

from config import BusinessRules, TaskPricing, BOT_USERNAME, logger
from database import (
    User, Campaign, Transaction, CampaignStatus, TaskType, TransactionType, CampaignPost, redis_client,
)
from i18n_filters import TextVariant
from keyboards import main_menu
from helpers import esc, safe_text, L, CampaignCreateState

router = Router()

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


async def _over_limit(session, uid) -> bool:
    n = (await session.execute(select(func.count(Campaign.id)).where(
        Campaign.advertiser_id == uid,
        Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).scalar() or 0
    return n >= BusinessRules.MAX_ACTIVE_CAMPAIGNS


async def _start_picker(query: CallbackQuery, state: FSMContext, session, is_channel: bool, promo_type=None):
    await state.clear()
    user = await _get_user(session, query.from_user.id)
    lang = user.language if user else "bn"
    active = (await session.execute(select(func.count(Campaign.id)).where(
        Campaign.advertiser_id == query.from_user.id,
        Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).scalar() or 0
    if active >= BusinessRules.MAX_ACTIVE_CAMPAIGNS:
        await query.answer(L(lang, f"সর্বোচ্চ {BusinessRules.MAX_ACTIVE_CAMPAIGNS}টি সক্রিয় ক্যাম্পেইন চালানো যায়।",
                               f"Maximum {BusinessRules.MAX_ACTIVE_CAMPAIGNS} active campaigns allowed.", _tpl='Maximum {v0} active campaigns allowed.', _kw={"v0": BusinessRules.MAX_ACTIVE_CAMPAIGNS}), show_alert=True)
        return
    # While this step is open, a typed @username / t.me link is also accepted.
    await state.set_state(CampaignCreateState.target)
    await state.update_data(is_channel=is_channel, promo_type=promo_type)

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
        "⚠️ In both cases the bot must be an <b>admin</b> there (with invite permission), otherwise members cannot be verified."
    , _tpl="📢 <b>Choose a {v0} to promote</b> (the bot must be an admin there)\n\n🏠 <b>I'm an admin</b> — pick a {v0} you administer\n👤 <b>I'm not an admin</b> — pick any public {v0}, or just send its <code>@username</code> / link\n\n⚠️ In both cases the bot must be an <b>admin</b> there (with invite permission), otherwise members cannot be verified.", _kw={"v0": kind_en})
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


@router.callback_query(F.data == "promote:cat:boost")
async def promote_boost_start(query: CallbackQuery, state: FSMContext, session):
    await _start_picker(query, state, session, True, "boost_7day")


@router.callback_query(F.data == "promote:cat:group")
async def promote_group_start(query: CallbackQuery, state: FSMContext, session):
    await _start_picker(query, state, session, False)


@router.message(TextVariant("❌ Cancel", "❌ বাতিল"))
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
    if (await state.get_data()).get("promo_type") == "boost_7day":
        task_type = TaskType.BOOST_7DAY

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
    if task_type == TaskType.BOOST_7DAY and not chat.username:
        await message.answer(L(lang, "❌ বুস্ট টাস্কের জন্য চ্যানেল/গ্রুপটি পাবলিক (username সহ) হতে হবে।",
                               "❌ Boost tasks need a public channel/group (with a username)."))
        return
    dup = (await session.execute(select(Campaign.id).where(
        Campaign.target_chat_id == chat_id, Campaign.task_type == task_type,
        Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).first()
    if dup:
        await message.answer(L(lang, "❌ এই চ্যাটের জন্য ইতিমধ্যে একটি সক্রিয় ক্যাম্পেইন আছে।",
                               "❌ This chat already has an active campaign of this type."))
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
    await _after_target(message, state, lang, chat.title, task_type)


async def _after_target(message: Message, state: FSMContext, lang: str, title: str, task_type: TaskType):
    """Boost tasks are Premium-only by nature -> straight to price. Others ask for the audience first."""
    if task_type == TaskType.BOOST_7DAY:
        await state.update_data(premium=True)
        text, kb = _reward_prompt(lang, task_type.value, True)
        await state.set_state(CampaignCreateState.reward)
        await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return
    rl = TaskPricing.get(task_type.value, False)
    rp = TaskPricing.get(task_type.value, True)
    text = L(
        lang,
        f"🎯 <b>টাস্কের অডিয়েন্স</b> — {esc(title)}\n\n"
        f"<b>1️⃣ সব ব্যবহারকারী</b>\nসকল ব্যবহারকারীর মধ্যে বিস্তৃত পৌঁছানো।\n💡 সর্বনিম্ন মূল্য: {rl['min']:,} GRAM/ইউনিট।\n\n"
        f"<b>2️⃣ শুধু Telegram Premium</b>\nশুধু Premium ব্যবহারকারীদের দেখানো হবে — আরও মানসম্মত অডিয়েন্স।\n💡 সর্বনিম্ন মূল্য: {rp['min']:,} GRAM/ইউনিট।",
        f"🎯 <b>Task audience</b> — {esc(title)}\n\n"
        f"<b>1️⃣ All users</b>\nWide reach across all users.\n💡 Minimum price: {rl['min']:,} GRAM/unit.\n\n"
        f"<b>2️⃣ Telegram Premium only</b>\nShown only to Premium users — a higher-quality audience.\n💡 Minimum price: {rp['min']:,} GRAM/unit."
    , _tpl='🎯 <b>Task audience</b> — {v0}\n\n<b>1️⃣ All users</b>\nWide reach across all users.\n💡 Minimum price: {v1:,} GRAM/unit.\n\n<b>2️⃣ Telegram Premium only</b>\nShown only to Premium users — a higher-quality audience.\n💡 Minimum price: {v2:,} GRAM/unit.', _kw={"v0": esc(title), "v1": rl['min'], "v2": rp['min']})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=L(lang, "1️⃣ সব ব্যবহারকারী", "1️⃣ All users"), callback_data="promote:aud:all")],
        [InlineKeyboardButton(text=L(lang, "2️⃣ শুধুমাত্র Telegram Premium", "2️⃣ Telegram Premium only"), callback_data="promote:aud:premium")],
        [InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="menu:promote")],
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


POST_RE = re.compile(r"^(?:https?://)?(?:www\.)?t\.me/([A-Za-z][A-Za-z0-9_]{3,31})/(\d{1,12})(?:[/?#].*)?$")
BOT_RE = re.compile(r"^(?:(?:https?://)?(?:www\.)?t\.me/|@)([A-Za-z][A-Za-z0-9_]{1,30}[Bb][Oo][Tt])(?:\?start=([A-Za-z0-9_-]{1,64}))?/?$")
LINK_KINDS = {"post": "post_view", "reaction": "reaction", "bot": "bot_start"}


async def _start_link(query: CallbackQuery, state: FSMContext, session, kind: str):
    await state.clear()
    user = await _get_user(session, query.from_user.id)
    lang = user.language if user else "bn"
    if await _over_limit(session, query.from_user.id):
        await query.answer(L(lang, f"সর্বোচ্চ {BusinessRules.MAX_ACTIVE_CAMPAIGNS}টি সক্রিয় ক্যাম্পেইন চালানো যায়।",
                               f"Maximum {BusinessRules.MAX_ACTIVE_CAMPAIGNS} active campaigns allowed.", _tpl='Maximum {v0} active campaigns allowed.', _kw={"v0": BusinessRules.MAX_ACTIVE_CAMPAIGNS}), show_alert=True)
        return
    await state.set_state(CampaignCreateState.link)
    await state.update_data(link_kind=kind)
    texts = {
        "post": ("👁 <b>পোস্ট ভিউ</b>\n\n<b>যে পোস্টটি প্রচার করতে চান সেটি ফরওয়ার্ড করুন।</b>\n"
                 "<blockquote>চ্যানেল খুলুন → পোস্ট বেছে নিন → ফরওয়ার্ড → @{bot}</blockquote>\n"
                 "অথবা পাবলিক চ্যানেলের পোস্টের লিংক পাঠান (<code>https://t.me/channel/123</code>)।\n\n"
                 "✅ কর্মীরা পোস্টটি সরাসরি বটের ভিতরে দেখবে (এটি আসল পোস্টের ভিউ কাউন্টারেও যোগ হয়), কোনো স্ক্রিনশট বা অনুমোদন লাগে না।",
                 "👁 <b>Post views</b>\n\n<b>Forward the post you want to promote.</b>\n"
                 "<blockquote>Open the channel → pick the post → Forward → @{bot}</blockquote>\n"
                 "Or send the link of a public channel post (<code>https://t.me/channel/123</code>).\n\n"
                 "✅ Workers view the post right inside the bot (it also counts on the original post's view counter) — no screenshots or approvals."),
        "reaction": ("❤️ <b>পোস্ট রিঅ্যাকশন</b>\n\nপাবলিক চ্যানেলের পোস্টের লিংক পাঠান।\nউদাহরণ: <code>https://t.me/channel/123</code>\n\n"
                     "ℹ️ কর্মী রিঅ্যাকশনসহ স্ক্রিনশট পাঠায়, আপনি অনুমোদন দেন।",
                     "❤️ <b>Post reactions</b>\n\nSend the link of a post in a public channel.\nExample: <code>https://t.me/channel/123</code>\n\n"
                     "ℹ️ Workers send a screenshot showing their reaction and you approve it."),
        "bot": ("🤖 <b>বট স্টার্ট</b>\n\nআপনার বটের লিংক বা <code>@username</code> পাঠান (রেফারেল লিংকও চলবে)।\nউদাহরণ: <code>https://t.me/mybot?start=promo</code>\n\n"
                "ℹ️ কর্মী স্টার্ট দেওয়ার স্ক্রিনশট পাঠায়, আপনি অনুমোদন দেন।",
                "🤖 <b>Bot starts</b>\n\nSend your bot's link or <code>@username</code> (referral links work too).\nExample: <code>https://t.me/mybot?start=promo</code>\n\n"
                "ℹ️ Workers send a screenshot of starting the bot and you approve it."),
    }
    bn, en = texts[kind]
    await query.message.answer(
        L(lang, bn.format(bot=BOT_USERNAME), en.format(bot=BOT_USERNAME), _tpl=en, _kw={"bot": BOT_USERNAME}),
        parse_mode=ParseMode.HTML, reply_markup=_back_kb(lang))
    await query.answer()


@router.callback_query(F.data.in_({"promote:cat:post", "promote:cat:reaction", "promote:cat:bot"}))
async def promote_link_start(query: CallbackQuery, state: FSMContext, session):
    await _start_link(query, state, session, query.data.split(":")[2])


def _post_link(chat, chat_id: int, mid: int) -> str:
    if getattr(chat, "username", None):
        return f"https://t.me/{chat.username}/{mid}"
    return f"https://t.me/c/{str(abs(chat_id))[3:] if str(abs(chat_id)).startswith('100') else abs(chat_id)}/{mid}"


async def _register_post(message: Message, state: FSMContext, session, bot: Bot, lang: str, chat, chat_id: int, mid: int):
    """Common path for a forwarded post or a post link: prove the bot can forward it, then continue."""
    try:
        await bot.forward_message(message.chat.id, chat_id, mid)        # also acts as a preview for the advertiser
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(L(
            lang, "❌ বট এই পোস্টটি ফরওয়ার্ড করতে পারছে না। চ্যানেলটি পাবলিক হতে হবে এবং ফরওয়ার্ড/সেভ করা অনুমোদিত থাকতে হবে, অথবা বটকে চ্যানেলের অ্যাডমিন বানান।",
            "❌ The bot can't forward this post. The channel must be public with forwarding allowed, or make the bot an admin of the channel."))
        return
    link = _post_link(chat, chat_id, mid)
    dup = (await session.execute(select(Campaign.id).where(
        Campaign.target_link == link, Campaign.task_type == TaskType.POST_VIEW,
        Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).first()
    if dup:
        await message.answer(L(lang, "❌ এই পোস্টের জন্য ইতিমধ্যে একটি সক্রিয় ক্যাম্পেইন আছে।", "❌ This post already has an active campaign."))
        return
    title = f"👁 {(getattr(chat, 'title', None) or 'Post')[:60]} #{mid}"
    await state.set_state(None)
    await state.update_data(chat_id=None, post_chat_id=chat_id, post_message_id=mid, title=title, username=None,
                            link=link, task_type="post_view", premium=False)
    await message.answer(L(lang, "👆 কর্মীরা পোস্টটি ঠিক এভাবেই দেখবে।", "👆 This is exactly how workers will see your post."))
    await _after_target(message, state, lang, title, TaskType.POST_VIEW)


@router.message(CampaignCreateState.link, F.forward_origin)
async def forwarded_post(message: Message, state: FSMContext, session, bot: Bot):
    user = await _get_user(session, message.from_user.id)
    if not user or user.is_blocked:
        return
    lang = user.language
    if (await state.get_data()).get("link_kind") != "post":
        await message.answer(L(lang, "❌ এখানে লিংক পাঠান।", "❌ Please send a link here."))
        return
    origin = message.forward_origin
    if getattr(origin, "type", None) != "channel":
        await message.answer(L(lang, "❌ একটি চ্যানেলের পোস্ট ফরওয়ার্ড করুন।", "❌ Forward a post from a channel."))
        return
    await _register_post(message, state, session, bot, lang, origin.chat, origin.chat.id, origin.message_id)


@router.message(CampaignCreateState.link, F.text)
async def typed_link(message: Message, state: FSMContext, session, bot: Bot):
    user = await _get_user(session, message.from_user.id)
    if not user or user.is_blocked:
        return
    lang = user.language
    kind = (await state.get_data()).get("link_kind")
    ttype = LINK_KINDS.get(kind)
    raw = (message.text or "").strip()
    if not ttype:
        await state.clear()
        return
    if kind == "bot":
        m = BOT_RE.match(raw)
        if not m:
            await message.answer(L(lang, "❌ সঠিক বটের লিংক বা @username পাঠান (নাম <code>bot</code> দিয়ে শেষ হতে হবে)।",
                                   "❌ Send a valid bot link or @username (the name must end with <code>bot</code>)."), parse_mode=ParseMode.HTML)
            return
        name, param = m.group(1), m.group(2)
        try:
            await bot.get_chat("@" + name)
        except (TelegramBadRequest, TelegramForbiddenError):
            await message.answer(L(lang, "❌ এই নামে কোনো বট পাওয়া যায়নি।", "❌ No bot was found with that name."))
            return
        link = f"https://t.me/{name}" + (f"?start={param}" if param else "")
        title, username = f"🤖 @{name}", None
    else:
        m = POST_RE.match(raw)
        if not m:
            await message.answer(L(lang, "❌ পাবলিক চ্যানেলের পোস্টের লিংক পাঠান, যেমন <code>https://t.me/channel/123</code>।",
                                   "❌ Send a public channel post link like <code>https://t.me/channel/123</code>."), parse_mode=ParseMode.HTML)
            return
        name, num = m.group(1), m.group(2)
        try:
            chat = await bot.get_chat("@" + name)
        except (TelegramBadRequest, TelegramForbiddenError):
            await message.answer(L(lang, "❌ এই পাবলিক চ্যানেল পাওয়া যায়নি।", "❌ That public channel was not found."))
            return
        if chat.type not in ("channel", "supergroup", "group"):
            await message.answer(L(lang, "❌ এটি চ্যানেল বা গ্রুপের পোস্ট নয়।", "❌ That is not a channel/group post."))
            return
        if kind == "post":
            await _register_post(message, state, session, bot, lang, chat, chat.id, int(num))
            return
        link = f"https://t.me/{name}/{num}"
        icon = "👁" if kind == "post" else "❤️"
        title, username = f"{icon} {(chat.title or name)[:60]} #{num}", name

    dup = (await session.execute(select(Campaign.id).where(
        Campaign.target_link == link, Campaign.task_type == TaskType(ttype),
        Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).first()
    if dup:
        await message.answer(L(lang, "❌ এই লিংকের জন্য ইতিমধ্যে একটি সক্রিয় ক্যাম্পেইন আছে।",
                               "❌ This link already has an active campaign."))
        return
    await state.set_state(None)
    await state.update_data(chat_id=None, title=title[:200], username=username, link=link, task_type=ttype, premium=False)
    await _after_target(message, state, lang, title, TaskType(ttype))


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
                               f"❌ That is not a {'channel' if is_channel else 'group'}.", _tpl='❌ That is not a {v0}.', _kw={"v0": 'channel' if is_channel else 'group'}))
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


def _reward_prompt(lang: str, task_type: str, premium: bool):
    rules = TaskPricing.get(task_type, premium)
    unit_bn, unit_en = {
        "channel_sub": ("সাবস্ক্রাইবার", "subscriber"), "group_join": ("সদস্য", "member"),
        "post_view": ("ভিউ", "view"), "bot_start": ("বট স্টার্ট", "bot start"),
        "reaction": ("রিঅ্যাকশন", "reaction"), "boost_7day": ("বুস্ট", "boost"),
    }.get(task_type, ("কাজ", "task"))
    text = L(
        lang,
        f"💲 <b>প্রতিটি {unit_bn}-এর জন্য পুরস্কার (GRAM) লিখুন</b>\n\n"
        f"ন্যূনতম: <b>{rules['min']:,}</b> · প্রস্তাবিত: <b>{rules['suggested']:,}</b> · সর্বোচ্চ: <b>{rules['max']:,}</b>\n\n"
        "💡 পুরস্কার যত বেশি, টাস্ক তত দ্রুত সম্পন্ন হবে। নিচের বাটন চাপুন অথবা সংখ্যা লিখুন।",
        f"💲 <b>Enter the reward (GRAM) per {unit_en}</b>\n\n"
        f"Min: <b>{rules['min']:,}</b> · Suggested: <b>{rules['suggested']:,}</b> · Max: <b>{rules['max']:,}</b>\n\n"
        "💡 Higher rewards get completed faster. Tap a button or type a number."
    , _tpl='💲 <b>Enter the reward (GRAM) per {v0}</b>\n\nMin: <b>{v1:,}</b> · Suggested: <b>{v2:,}</b> · Max: <b>{v3:,}</b>\n\n💡 Higher rewards get completed faster. Tap a button or type a number.', _kw={"v0": unit_en, "v1": rules['min'], "v2": rules['suggested'], "v3": rules['max']})
    kb = _back_kb(lang, [[
        InlineKeyboardButton(text=f"{rules['min']:,}", callback_data=f"promote:price:{rules['min']}"),
        InlineKeyboardButton(text=f"⭐ {rules['suggested']:,}", callback_data=f"promote:price:{rules['suggested']}"),
    ]])
    return text, kb


@router.callback_query(F.data.in_({"promote:aud:all", "promote:aud:premium"}))
async def audience_chosen(query: CallbackQuery, state: FSMContext, session):
    data = await state.get_data()
    if "task_type" not in data or "title" not in data:
        await query.answer("Session expired.", show_alert=True)
        return
    user = await _get_user(session, query.from_user.id)
    lang = user.language if user else "bn"
    premium = query.data.endswith("premium")
    await state.update_data(premium=premium)
    await state.set_state(CampaignCreateState.reward)
    text, kb = _reward_prompt(lang, data["task_type"], premium)
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
        "✏️ <b>Enter how many subscribers you want</b>"
    , _tpl='ℹ️ Task creation commission — <b>{v0:.0f}%</b>\n\n<blockquote>💲 Price per subscriber — {v1:,.0f} GRAM\n💰 Your balance — {v2:,.1f} GRAM</blockquote>\n✏️ <b>Enter how many subscribers you want</b>', _kw={"v0": pct, "v1": reward, "v2": bal})
    extra = []
    if mx >= BusinessRules.CAMPAIGN_MIN_SLOTS:
        extra.append([InlineKeyboardButton(
            text=L(lang, f"{mx:,} (আপনার ব্যালেন্সের সর্বোচ্চ সীমা)", f"{mx:,} (max for your balance)", _tpl='{v0:,} (max for your balance)', _kw={"v0": mx}),
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
        f"💲 <b>Total charge: {total:,.0f} GRAM</b>\n\nTap “Publish task” to post it."
    , _tpl='✅ <b>Ready to publish</b>\n\n📢 {v0}\n👥 Subscribers: <b>{v1:,}</b> × <b>{v2:,.0f}</b> GRAM = {v3:,.0f}\n🏷 Commission ({v4:.0f}%): {v5:,.0f}\n💲 <b>Total charge: {v6:,.0f} GRAM</b>\n\nTap “Publish task” to post it.', _kw={"v0": esc(data['title']), "v1": slots, "v2": reward, "v3": budget, "v4": BusinessRules.PLATFORM_COMMISSION * 100, "v5": fee, "v6": total})
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
                               f"❌ Send a number between {BusinessRules.CAMPAIGN_MIN_SLOTS} and {BusinessRules.CAMPAIGN_MAX_SLOTS:,}.", _tpl='❌ Send a number between {v0} and {v1:,}.', _kw={"v0": BusinessRules.CAMPAIGN_MIN_SLOTS, "v1": BusinessRules.CAMPAIGN_MAX_SLOTS}))
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
        if not {"title", "reward", "slots", "task_type"} <= data.keys():
            await query.answer("Session expired.", show_alert=True)
            return
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = r.scalar_one_or_none()
        if not user or user.is_blocked:
            await session.rollback()
            await query.answer("❌", show_alert=True)
            return
        lang = user.language
        reward, slots = Decimal(data["reward"]), int(data["slots"])
        premium = bool(data.get("premium")) or data["task_type"] == "boost_7day"
        ok, msg = TaskPricing.validate(data["task_type"], reward, premium)
        if not ok or not BusinessRules.CAMPAIGN_MIN_SLOTS <= slots <= BusinessRules.CAMPAIGN_MAX_SLOTS:
            await session.rollback()
            await query.answer("❌ Invalid.", show_alert=True)
            return
        budget, fee, total = _cost(reward, slots)
        if user.balance < total:
            await session.rollback()
            await query.answer(L(lang, f"❌ ব্যালেন্স কম। দরকার {total:,.0f} GRAM।", f"❌ Insufficient balance. Need {total:,.0f} GRAM.", _tpl='❌ Insufficient balance. Need {v0:,.0f} GRAM.', _kw={"v0": total}), show_alert=True)
            return
        dup_cond = (Campaign.target_chat_id == data["chat_id"]) if data.get("chat_id") is not None else (Campaign.target_link == data.get("link"))
        if (await session.execute(select(Campaign.id).where(
                dup_cond, Campaign.task_type == TaskType(data["task_type"]),
                Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PAUSED])))).first():
            await session.rollback()
            await query.answer(L(lang, "❌ এই চ্যাটের ক্যাম্পেইন আগে থেকেই আছে।", "❌ A campaign for this chat already exists."), show_alert=True)
            return

        await query.message.edit_text(L(lang, "⏳ কাজ তৈরি হচ্ছে, দয়া করে অপেক্ষা করুন...", "⏳ Creating your task, please wait..."))
        c = Campaign(
            advertiser_id=uid, title=data["title"], task_type=TaskType(data["task_type"]),
            target_chat_id=data.get("chat_id"), target_username=data.get("username"),
            target_link=data.get("link"), target_title=data["title"],
            premium_only=premium, reward_per_user=reward, base_reward=reward,
            max_completions=slots, total_budget=budget,
            retention_days=BusinessRules.RETENTION_DAYS,
            requires_retention_check=data["task_type"] in BusinessRules.API_VERIFIED_TASK_TYPES,
        )
        session.add(c)
        await session.flush()
        if data.get("post_message_id"):
            session.add(CampaignPost(campaign_id=c.id, source_chat_id=int(data["post_chat_id"]),
                                     source_message_id=int(data["post_message_id"])))
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
            f"✅ <b>Task №{c.id[:8].upper()} published</b>\n💲 Deducted from balance: {total:,.1f} GRAM"
        , _tpl='✅ <b>Task №{v0} published</b>\n💲 Deducted from balance: {v1:,.1f} GRAM', _kw={"v0": c.id[:8].upper(), "v1": total}), reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception as e:
        await session.rollback()
        logger.exception(f"Campaign create failed: {e}")
        await query.answer("❌ Error", show_alert=True)
    finally:
        await redis_client.delete(lock)


import sys
import os
import re
import html
import uuid
import logging
import asyncio
from typing import Optional, Union
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

# Third-party Imports
from dotenv import load_dotenv
import redis.asyncio as aioredis

from sqlalchemy import (
    BigInteger, String, Numeric, Integer, Boolean, DateTime,
    ForeignKey, UniqueConstraint, select,
    func, Enum as SQLEnum
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker
)

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, PreCheckoutQuery, LabeledPrice
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ErrorEvent

import uvicorn  # noqa: F401  (kept for deployment parity, used if serving fastapi_app)
from fastapi import FastAPI


# ==============================================================================
# SECTION 1: CONFIGURATION & ENVIRONMENT
# ==============================================================================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger("BoostGramBot")

def esc(value) -> str:
    """Escape dynamic text (names, titles) so it can't break HTML parse_mode."""
    return html.escape(str(value), quote=False)

def safe_text(message: "Message") -> Optional[str]:
    """Returns stripped message text, or None if the user sent something non-text
    (photo, sticker, voice note, etc.) where a text reply was expected."""
    return message.text.strip() if message.text else None

NON_TEXT_INPUT_MSG = "⚠️ Please send this as a text message (not a photo/sticker/file)."

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "BoostGramBot")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")

if not BOT_TOKEN or not DATABASE_URL or not REDIS_URL:
    logger.critical("CRITICAL ERROR: Missing required environment variables (BOT_TOKEN, DATABASE_URL, REDIS_URL).")
    sys.exit(1)

try:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()]
except ValueError:
    ADMIN_IDS = []

# Business Logic Constants
REFERRAL_COIN_REWARD = Decimal("5000")
REFERRAL_XP_REWARD = 500
STAR_TO_COIN_RATE = 5000  # 1 Telegram Star = 5000 Coins
PLATFORM_COMMISSION_PERCENT = Decimal("0.15")  # 15% Commission on Tasks
MIN_WITHDRAWAL = Decimal("50000")
MAX_WITHDRAWAL = Decimal("1000000")
TASKS_PAGE_SIZE = 5


# ==============================================================================
# SECTION 2: ENUMS & LOCALIZATION DICTIONARY
# ==============================================================================

class TaskType(str, Enum):
    CHANNEL_SUB = "channel_sub"
    GROUP_JOIN = "group_join"
    BOT_START = "bot_start"
    POST_VIEW = "post_view"
    WEB_APP = "web_app"
    CUSTOM = "custom"

class CampaignStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class TransactionType(str, Enum):
    TASK_REWARD = "task_reward"
    REFERRAL_REWARD = "referral_reward"
    STAR_TOPUP = "star_topup"
    WITHDRAWAL = "withdrawal"
    CAMPAIGN_PAYMENT = "campaign_payment"
    CHECK_REDEEM = "check_redeem"

# Task types that require live verification against the Telegram Bot API.
# (BOT_START / POST_VIEW / WEB_APP / CUSTOM cannot be verified remotely by
# Telegram's API, so they are marked complete on user confirmation instead.)
VERIFIABLE_TASK_TYPES = {TaskType.CHANNEL_SUB, TaskType.GROUP_JOIN}

LANGUAGES = {
    "en": {
        "welcome": "👋 Welcome to <b>{bot_name}</b>, {first_name}!\n\n🚀 Complete tasks, earn GRAM coins, and promote your channels instantly!",
        "cabinet": "👤 <b>Your Cabinet:</b>\n\n🆔 My ID: <code>{user_id}</code>\n📈 Level: 🌱 {rank} {xp}/1500 XP\n💰 Balance: <code>{balance:,.0f}</code> GRAM",
        "menu_earnings": "💰 Earnings",
        "menu_promote": "📢 Promote",
        "menu_checks": "📋 Checks",
        "menu_cabinet": "👤 My Cabinet",
        "menu_sub_check": "🛡 Subscription Check",
        "menu_stats": "📊 Our Bots and Statistics",
        "menu_links": "🔗 Useful Links",
        "menu_instruction": "ℹ️ Instruction",
        "btn_replenish": "🪙 Replenish Balance",
        "btn_referral": "👥 Referral System",
        "btn_level": "📊 Level System",
        "btn_tasks": "📋 My Tasks",
        "btn_lang": "🌐 Change Language",
        "btn_notif_on": "🔕 Disable notifications",
        "btn_notif_off": "🔔 Enable notifications",
        "btn_back": "◀️ Back",
        "lang_changed": "✅ Language changed to English successfully!"
    },
    "bn": {
        "welcome": "👋 <b>{bot_name}</b>-এ আপনাকে স্বাগতম, {first_name}!\n\n🚀 টাস্ক সম্পন্ন করুন, GRAM কয়েন আয় করুন এবং আপনার চ্যানেল প্রমোট করুন!",
        "cabinet": "👤 <b>আপনার ক্যাবিনেট:</b>\n\n🆔 আমার আইডি: <code>{user_id}</code>\n📈 লেভেল: 🌱 {rank} {xp}/1500 XP\n💰 ব্যালেন্স: <code>{balance:,.0f}</code> GRAM",
        "menu_earnings": "💰 আর্নিংস",
        "menu_promote": "📢 প্রমোট",
        "menu_checks": "📋 চেক্স",
        "menu_cabinet": "👤 মাই ক্যাবিনেট",
        "menu_sub_check": "🛡 সাবস্ক্রিপশন চেক",
        "menu_stats": "📊 আমাদের বট ও পরিসংখ্যান",
        "menu_links": "🔗 দরকারী লিংক",
        "menu_instruction": "ℹ️ নির্দেশিকা",
        "btn_replenish": "🪙 ব্যালেন্স টপ-আপ",
        "btn_referral": "👥 রেফারেল সিস্টেম",
        "btn_level": "📊 লেভেল সিস্টেম",
        "btn_tasks": "📋 আমার টাস্কসমূহ",
        "btn_lang": "🌐 ভাষা পরিবর্তন",
        "btn_notif_on": "🔕 নোটিফিকেশন বন্ধ করুন",
        "btn_notif_off": "🔔 নোটিফিকেশন চালু করুন",
        "btn_back": "◀️ ফিরে যান",
        "lang_changed": "✅ সফলভাবে ভাষা বাংলায় পরিবর্তন করা হয়েছে!"
    }
}

def get_text(lang: str, key: str, **kwargs) -> str:
    lang_dict = LANGUAGES.get(lang, LANGUAGES["en"])
    text = lang_dict.get(key, LANGUAGES["en"].get(key, ""))
    return text.format(**kwargs)


# ==============================================================================
# SECTION 3: DATABASE MODELS
# ==============================================================================

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    language: Mapped[str] = mapped_column(String(5), default="en")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    total_earned: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    completed_tasks_count: Mapped[int] = mapped_column(default=0)

    referral_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    referred_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    completions = relationship("TaskCompletion", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")
    campaigns = relationship("Campaign", back_populates="advertiser")

class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    advertiser_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255))
    task_type: Mapped[TaskType] = mapped_column(SQLEnum(TaskType))
    target_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    target_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_link: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    reward_per_user: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    max_completions: Mapped[int] = mapped_column()
    completed_count: Mapped[int] = mapped_column(default=0)
    total_budget: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    spent_budget: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))

    status: Mapped[CampaignStatus] = mapped_column(SQLEnum(CampaignStatus), default=CampaignStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    advertiser = relationship("User", back_populates="campaigns")
    completions = relationship("TaskCompletion", back_populates="campaign")

class TaskCompletion(Base):
    __tablename__ = "task_completions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"))
    reward: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="completions")
    campaign = relationship("Campaign", back_populates="completions")

    __table_args__ = (UniqueConstraint('user_id', 'campaign_id', name='uq_user_campaign_completion'),)

class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    type: Mapped[TransactionType] = mapped_column(SQLEnum(TransactionType))
    description: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="transactions")

class Check(Base):
    """A pre-funded voucher code that can be redeemed by other users for GRAM."""
    __tablename__ = "checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount_per_activation: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    max_activations: Mapped[int] = mapped_column(Integer)
    activations_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class CheckActivation(Base):
    __tablename__ = "check_activations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    check_id: Mapped[str] = mapped_column(String(36), ForeignKey("checks.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint('check_id', 'user_id', name='uq_check_user_activation'),)


# ==============================================================================
# SECTION 4: ENGINE & REDIS SETUP
# ==============================================================================

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ==============================================================================
# SECTION 5: FSM STATES
# ==============================================================================

class CampaignCreationState(StatesGroup):
    task_type = State()
    title = State()
    target = State()
    reward = State()
    max_completions = State()

class TopUpState(StatesGroup):
    amount_stars = State()

class CheckCreateState(StatesGroup):
    amount = State()
    activations = State()

class CheckRedeemState(StatesGroup):
    code = State()

class AdminState(StatesGroup):
    target_user_id = State()
    coin_amount = State()
    ban_user_id = State()


# ==============================================================================
# SECTION 6: KEYBOARDS
# ==============================================================================

def get_main_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=get_text(lang, "menu_earnings")), KeyboardButton(text=get_text(lang, "menu_promote"))],
            [KeyboardButton(text=get_text(lang, "menu_checks")), KeyboardButton(text=get_text(lang, "menu_cabinet"))],
            [KeyboardButton(text=get_text(lang, "menu_sub_check")), KeyboardButton(text=get_text(lang, "menu_stats"))],
            [KeyboardButton(text=get_text(lang, "menu_links")), KeyboardButton(text=get_text(lang, "menu_instruction"))]
        ],
        resize_keyboard=True
    )

def get_cabinet_keyboard(lang: str = "en", notifications_enabled: bool = True) -> InlineKeyboardMarkup:
    notif_key = "btn_notif_on" if notifications_enabled else "btn_notif_off"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, "btn_replenish"), callback_data="cab_replenish")],
        [InlineKeyboardButton(text=get_text(lang, "btn_referral"), callback_data="cab_referral")],
        [InlineKeyboardButton(text=get_text(lang, "btn_level"), callback_data="cab_level")],
        [InlineKeyboardButton(text=get_text(lang, "btn_tasks"), callback_data="cab_tasks")],
        [InlineKeyboardButton(text=get_text(lang, "btn_lang"), callback_data="cab_lang")],
        [InlineKeyboardButton(text=get_text(lang, notif_key), callback_data="cab_notif")]
    ])

def get_back_to_cabinet_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="cab_back")]
    ])


# ==============================================================================
# SECTION 7: TARGET RESOLUTION & VERIFICATION ENGINE
# ==============================================================================

def normalize_target_identifier(raw: str) -> Union[int, str]:
    """
    Turns whatever the advertiser typed (a numeric chat id, an @username,
    a t.me link, or a bare username) into something aiogram's get_chat /
    get_chat_member calls accept directly.
    """
    raw = raw.strip()
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    raw = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", raw, flags=re.IGNORECASE)
    raw = raw.lstrip("@").split("/")[0].split("?")[0]
    return f"@{raw}"


class TargetResolutionError(str, Enum):
    NOT_FOUND = "not_found"
    BOT_NOT_MEMBER = "bot_not_member"
    BOT_NOT_ADMIN = "bot_not_admin"
    UNKNOWN = "unknown"


_bot_id_cache: dict = {}

async def get_bot_id(bot: Bot) -> int:
    """Resolves and caches the bot's own numeric user id via getMe (more
    reliable across aiogram versions than relying on Bot.id parsing the token)."""
    if "id" not in _bot_id_cache:
        me = await bot.get_me()
        _bot_id_cache["id"] = me.id
    return _bot_id_cache["id"]


async def resolve_and_verify_target_chat(bot: Bot, raw_target: str):
    """
    Resolves a channel/group from user input and confirms this bot is an
    *administrator* there — required so the bot can call getChatMember and
    verify any user's subscription automatically, including for private
    channels/groups that have no public username.
    Returns (chat_info_dict | None, error | None).
    """
    identifier = normalize_target_identifier(raw_target)
    try:
        chat = await bot.get_chat(identifier)
    except TelegramBadRequest:
        return None, TargetResolutionError.NOT_FOUND
    except TelegramForbiddenError:
        return None, TargetResolutionError.BOT_NOT_MEMBER
    except Exception as e:
        logger.error(f"get_chat failed for {identifier}: {e}")
        return None, TargetResolutionError.UNKNOWN

    try:
        bot_id = await get_bot_id(bot)
        bot_member = await bot.get_chat_member(chat.id, bot_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        return None, TargetResolutionError.BOT_NOT_MEMBER
    except Exception as e:
        logger.error(f"get_chat_member (self) failed for {chat.id}: {e}")
        return None, TargetResolutionError.UNKNOWN

    if bot_member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        return None, TargetResolutionError.BOT_NOT_ADMIN

    invite_link = None
    if not chat.username:
        try:
            invite_link = await bot.export_chat_invite_link(chat.id)
        except Exception:
            invite_link = None

    return {
        "chat_id": chat.id,
        "username": chat.username,
        "title": chat.title or (chat.username or str(chat.id)),
        "invite_link": invite_link,
    }, None


class SubscriptionVerifier:
    @staticmethod
    async def verify(bot: Bot, user_id: int, campaign: "Campaign") -> bool:
        target = campaign.target_chat_id or (
            f"@{campaign.target_username}" if campaign.target_username else None
        )
        if target is None:
            return False
        try:
            member = await bot.get_chat_member(chat_id=target, user_id=user_id)
            valid_statuses = (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR,
                ChatMemberStatus.RESTRICTED,  # still a member, just limited
            )
            return member.status in valid_statuses
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning(f"Membership check warning for chat {target}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in getChatMember: {str(e)}")
            return False


class TaskEngine:
    @staticmethod
    async def process_task_completion(
        session: AsyncSession,
        bot: Bot,
        user_id: int,
        campaign_id: str
    ) -> tuple[bool, str]:

        lock_key = f"lock:task:{user_id}:{campaign_id}"
        acquired = await redis_client.set(lock_key, "1", nx=True, ex=10)
        if not acquired:
            return False, "⏳ Processing, please wait..."

        try:
            async with session.begin_nested():
                res_c = await session.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
                campaign = res_c.scalar_one_or_none()

                if not campaign or campaign.status != CampaignStatus.ACTIVE:
                    return False, "❌ This task is no longer active."

                if campaign.completed_count >= campaign.max_completions:
                    campaign.status = CampaignStatus.COMPLETED
                    return False, "❌ Task limit has been reached."

                res_u = await session.execute(select(User).where(User.id == user_id).with_for_update())
                user = res_u.scalar_one_or_none()
                if not user:
                    return False, "❌ Please /start the bot first."
                if user.is_blocked:
                    return False, "❌ Your account has been suspended."

                res_comp = await session.execute(select(TaskCompletion).where(
                    TaskCompletion.user_id == user_id, TaskCompletion.campaign_id == campaign_id
                ))
                if res_comp.scalar_one_or_none():
                    return False, "❌ You have already completed this task!"

                if TaskType(campaign.task_type) in VERIFIABLE_TASK_TYPES:
                    if not (campaign.target_chat_id or campaign.target_username):
                        return False, "❌ This task is misconfigured (no target set). Contact support."
                    is_valid = await SubscriptionVerifier.verify(bot, user_id, campaign)
                    if not is_valid:
                        return False, "❌ Verification failed! Please make sure you've joined and try again."

                reward = campaign.reward_per_user

                completion = TaskCompletion(user_id=user_id, campaign_id=campaign_id, reward=reward)
                session.add(completion)

                user.balance += reward
                user.total_earned += reward
                user.completed_tasks_count += 1
                user.xp += 50
                user.level = 1 + (user.xp // 1500)

                tx = Transaction(
                    user_id=user_id, amount=reward,
                    type=TransactionType.TASK_REWARD,
                    description=f"Reward for task: {campaign.title}"
                )
                session.add(tx)

                campaign.completed_count += 1
                campaign.spent_budget += reward

                if campaign.completed_count >= campaign.max_completions:
                    campaign.status = CampaignStatus.COMPLETED

            await session.commit()
            return True, f"🎉 Task Verified Successfully!\n💰 You earned: +<code>{reward:,.0f}</code> GRAM\n⚡ XP gained: +50"

        except Exception as e:
            await session.rollback()
            logger.error(f"Task verification error: {str(e)}")
            return False, "❌ An error occurred during verification."
        finally:
            try:
                await redis_client.delete(lock_key)
            except Exception:
                pass


# ==============================================================================
# SECTION 8: BOT HANDLERS & ROUTING
# ==============================================================================

router = Router()


async def get_or_none(session: AsyncSession, user_id: int) -> Optional[User]:
    res = await session.execute(select(User).where(User.id == user_id))
    return res.scalar_one_or_none()


NOT_STARTED_TEXT = {
    "en": "⚠️ Please tap /start first to activate your account.",
    "bn": "⚠️ প্রথমে /start চাপুন আপনার অ্যাকাউন্ট চালু করতে।",
}


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    referrer_id = None

    if len(args) > 1 and args[1].startswith("ref_"):
        ref_code = args[1].replace("ref_", "").strip()
        res_ref = await session.execute(select(User).where(User.referral_code == ref_code))
        ref_user = res_ref.scalar_one_or_none()
        if ref_user and ref_user.id != user_id:
            referrer_id = ref_user.id

    user = await get_or_none(session, user_id)
    referrer_notify = None

    if not user:
        ref_code = str(uuid.uuid4())[:8]
        user = User(
            id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            referral_code=ref_code,
            referred_by=referrer_id,
            language="en"
        )
        session.add(user)

        if referrer_id:
            res_referrer = await session.execute(select(User).where(User.id == referrer_id))
            referrer = res_referrer.scalar_one_or_none()
            if referrer:
                referrer.balance += REFERRAL_COIN_REWARD
                referrer.xp += REFERRAL_XP_REWARD
                referrer.level = 1 + (referrer.xp // 1500)

                tx = Transaction(
                    user_id=referrer.id, amount=REFERRAL_COIN_REWARD,
                    type=TransactionType.REFERRAL_REWARD,
                    description=f"Referral bonus for {message.from_user.first_name}"
                )
                session.add(tx)
                if referrer.notifications_enabled:
                    referrer_notify = referrer.id

        await session.commit()

        if referrer_notify:
            try:
                await message.bot.send_message(
                    referrer_notify,
                    f"🎉 {message.from_user.first_name} joined using your referral link!\n"
                    f"💰 +{REFERRAL_COIN_REWARD:,.0f} GRAM, ⚡ +{REFERRAL_XP_REWARD} XP"
                )
            except (TelegramForbiddenError, TelegramBadRequest):
                pass

    welcome_msg = get_text(user.language, "welcome", bot_name=esc(BOT_USERNAME), first_name=esc(message.from_user.first_name))
    await message.answer(welcome_msg, reply_markup=get_main_keyboard(user.language), parse_mode=ParseMode.HTML)


# --- MY CABINET ---

@router.message(F.text.in_(["👤 My Cabinet", "👤 মাই ক্যাবিনেট"]))
async def show_cabinet(message: Message, session: AsyncSession):
    user = await get_or_none(session, message.from_user.id)
    if not user:
        await message.answer(NOT_STARTED_TEXT["en"])
        return

    rank_name = "Activist" if user.level >= 1 else "Newbie"
    current_xp_mod = user.xp % 1500

    cabinet_text = get_text(
        user.language, "cabinet",
        user_id=user.id, rank=rank_name, xp=current_xp_mod, balance=user.balance
    )
    await message.answer(
        cabinet_text,
        reply_markup=get_cabinet_keyboard(user.language, user.notifications_enabled),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "cab_back")
async def cb_back_to_cabinet(query: CallbackQuery, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    await query.answer()
    if not user:
        await query.message.edit_text(NOT_STARTED_TEXT["en"])
        return
    rank_name = "Activist" if user.level >= 1 else "Newbie"
    cabinet_text = get_text(
        user.language, "cabinet",
        user_id=user.id, rank=rank_name, xp=user.xp % 1500, balance=user.balance
    )
    await query.message.edit_text(
        cabinet_text,
        reply_markup=get_cabinet_keyboard(user.language, user.notifications_enabled),
        parse_mode=ParseMode.HTML
    )


# --- INLINE ACTIONS FROM CABINET ---

@router.callback_query(F.data == "cab_replenish")
async def cb_replenish(query: CallbackQuery, state: FSMContext, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    lang = user.language if user else "en"

    msg = (
        "⭐ <b>Replenish Balance via Telegram Stars</b>\n\nRate: <code>1 Star = 5000 GRAM</code>\nEnter amount of Telegram Stars:"
        if lang == "en" else
        "⭐ <b>টেলিগ্রাম স্টারের মাধ্যমে ব্যালেন্স টপ-আপ</b>\n\nরেট: <code>১ স্টার = ৫০০০ GRAM</code>\nকতগুলো স্টার পেমেন্ট করতে চান সংখ্যাটি লিখুন:"
    )
    await query.message.answer(msg, parse_mode=ParseMode.HTML)
    await state.set_state(TopUpState.amount_stars)
    await query.answer()

@router.message(TopUpState.amount_stars)
async def process_star_invoice(message: Message, state: FSMContext):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    try:
        stars = int(raw)
        if stars <= 0:
            raise ValueError()
    except Exception:
        await message.answer("❌ Invalid input. Please enter a positive whole number.")
        return

    await state.clear()
    total_coins = stars * STAR_TO_COIN_RATE
    prices = [LabeledPrice(label=f"{total_coins:,} GRAM", amount=stars)]

    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title="GRAM Top-Up",
        description=f"Top up {total_coins:,} GRAM coins",
        payload=f"topup_coins_{stars}",
        currency="XTR",
        prices=prices
    )

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, session: AsyncSession):
    payment = message.successful_payment
    payload = payment.invoice_payload

    if payload.startswith("topup_coins_"):
        try:
            stars = int(payload.split("_")[2])
        except (IndexError, ValueError):
            logger.error(f"Malformed top-up payload: {payload}")
            return

        coins_to_add = Decimal(stars * STAR_TO_COIN_RATE)
        user_id = message.from_user.id

        async with session.begin_nested():
            res = await session.execute(select(User).where(User.id == user_id).with_for_update())
            user = res.scalar_one_or_none()
            if user:
                user.balance += coins_to_add
                tx = Transaction(
                    user_id=user_id, amount=coins_to_add,
                    type=TransactionType.STAR_TOPUP,
                    description=f"Top-up {coins_to_add:,.0f} GRAM via {stars} Stars"
                )
                session.add(tx)

        await session.commit()
        await message.answer(f"✅ Top-up successful! Added <code>+{coins_to_add:,.0f}</code> GRAM.", parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "cab_referral")
async def cb_referral(query: CallbackQuery, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    if not user:
        await query.answer(NOT_STARTED_TEXT["en"], show_alert=True)
        return
    res_count = await session.execute(select(func.count(User.id)).where(User.referred_by == query.from_user.id))
    invited = res_count.scalar()

    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.referral_code}"
    text = (
        f"👥 <b>Referral System</b>\n\nInvite friends &amp; earn <code>{REFERRAL_COIN_REWARD:,.0f} GRAM</code> + "
        f"<code>{REFERRAL_XP_REWARD} XP</code> per referral!\n\n📊 Total Invited: <code>{invited}</code>\n🔗 Your Link:\n<code>{esc(ref_link)}</code>"
    )
    await query.message.edit_text(text, reply_markup=get_back_to_cabinet_keyboard(user.language), parse_mode=ParseMode.HTML)
    await query.answer()

@router.callback_query(F.data == "cab_level")
async def cb_level(query: CallbackQuery, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    if not user:
        await query.answer(NOT_STARTED_TEXT["en"], show_alert=True)
        return
    text = f"📊 <b>Level System</b>\n\nCurrent Level: <code>{user.level}</code>\nTotal XP: <code>{user.xp}</code>\nNext rank progress: <code>{user.xp % 1500}/1500 XP</code>"
    await query.message.edit_text(text, reply_markup=get_back_to_cabinet_keyboard(user.language), parse_mode=ParseMode.HTML)
    await query.answer()

@router.callback_query(F.data == "cab_tasks")
async def cb_tasks(query: CallbackQuery, session: AsyncSession):
    await query.answer()
    await render_tasks_page(query.from_user.id, query.message, session, page=0, edit=True)

@router.callback_query(F.data == "cab_lang")
async def cb_lang(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id).with_for_update())
    user = res.scalar_one_or_none()
    if not user:
        await query.answer(NOT_STARTED_TEXT["en"], show_alert=True)
        return

    user.language = "bn" if user.language == "en" else "en"
    await session.commit()
    lang = user.language

    rank_name = "Activist" if user.level >= 1 else "Newbie"
    cabinet_text = get_text(lang, "cabinet", user_id=user.id, rank=rank_name, xp=user.xp % 1500, balance=user.balance)
    await query.message.edit_text(
        f"{get_text(lang, 'lang_changed')}\n\n{cabinet_text}",
        reply_markup=get_cabinet_keyboard(lang, user.notifications_enabled),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@router.callback_query(F.data == "cab_notif")
async def cb_notif(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id).with_for_update())
    user = res.scalar_one_or_none()
    if not user:
        await query.answer(NOT_STARTED_TEXT["en"], show_alert=True)
        return

    user.notifications_enabled = not user.notifications_enabled
    await session.commit()

    status_msg = "🔔 Notifications enabled." if user.notifications_enabled else "🔕 Notifications disabled."
    await query.answer(status_msg, show_alert=True)
    try:
        await query.message.edit_reply_markup(reply_markup=get_cabinet_keyboard(user.language, user.notifications_enabled))
    except TelegramBadRequest:
        pass


# --- EARNINGS & TASKS MENU ---

@router.message(F.text.in_(["💰 Earnings", "💰 আর্নিংস"]))
async def show_earnings_menu(message: Message, session: AsyncSession):
    user = await get_or_none(session, message.from_user.id)
    lang = user.language if user else "en"

    text = (
        "🎯 <b>Available Tasks</b>\nComplete any task below to earn GRAM coins:"
        if lang == "en" else
        "🎯 <b>উপলব্ধ টাস্কসমূহ</b>\nGRAM কয়েন অর্জনের জন্য নিচের টাস্কগুলো সম্পন্ন করুন:"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)
    await render_tasks_page(message.from_user.id, message, session, page=0, edit=False)

async def render_tasks_page(user_id: int, message: Message, session: AsyncSession, page: int = 0, edit: bool = False):
    limit = TASKS_PAGE_SIZE
    offset = page * limit

    completed_sub = select(TaskCompletion.campaign_id).where(TaskCompletion.user_id == user_id)
    base_filter = (Campaign.status == CampaignStatus.ACTIVE) & (Campaign.id.not_in(completed_sub))

    total = (await session.execute(select(func.count(Campaign.id)).where(base_filter))).scalar() or 0

    stmt = select(Campaign).where(base_filter).order_by(Campaign.created_at.desc()).offset(offset).limit(limit)
    res = await session.execute(stmt)
    campaigns = res.scalars().all()

    if not campaigns:
        text = "🎯 No active tasks right now. Check back soon!"
        if edit:
            try:
                await message.edit_text(text)
            except TelegramBadRequest:
                pass
        else:
            await message.answer(text)
        return

    buttons = []
    for c in campaigns:
        buttons.append([InlineKeyboardButton(
            text=f"📢 {c.title} (+{c.reward_per_user:,.0f} GRAM)",
            callback_data=f"task_view_{c.id}"
        )])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"tasks_page_{page - 1}"))
    if offset + limit < total:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"tasks_page_{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = f"🎯 <b>Active Tasks List</b> ({total} available):"

    if edit:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith("tasks_page_"))
async def tasks_page_nav(query: CallbackQuery, session: AsyncSession):
    page = int(query.data.replace("tasks_page_", ""))
    await query.answer()
    await render_tasks_page(query.from_user.id, query.message, session, page=page, edit=True)

@router.callback_query(F.data.startswith("task_view_"))
async def view_task_detail(query: CallbackQuery, session: AsyncSession):
    c_id = query.data.replace("task_view_", "")
    res = await session.execute(select(Campaign).where(Campaign.id == c_id))
    campaign = res.scalar_one_or_none()

    if not campaign or campaign.status != CampaignStatus.ACTIVE:
        await query.answer("Task not available.", show_alert=True)
        return

    if campaign.target_link:
        target_url = campaign.target_link
    elif campaign.target_username:
        target_url = f"https://t.me/{campaign.target_username}"
    else:
        target_url = f"https://t.me/{BOT_USERNAME}"

    type_labels = {
        TaskType.CHANNEL_SUB: "📢 Join Channel",
        TaskType.GROUP_JOIN: "👥 Join Group",
        TaskType.BOT_START: "🤖 Start Bot",
        TaskType.POST_VIEW: "👀 View Post",
        TaskType.WEB_APP: "🌐 Open Link",
        TaskType.CUSTOM: "✅ Complete Task",
    }
    action_label = type_labels.get(TaskType(campaign.task_type), "🚀 Open Link")

    text = (
        f"📢 <b>{esc(campaign.title)}</b>\n\n"
        f"💰 Reward: <code>{campaign.reward_per_user:,.0f}</code> GRAM\n"
        f"Slots remaining: <code>{campaign.max_completions - campaign.completed_count}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=action_label, url=target_url)],
        [InlineKeyboardButton(text="✅ Verify Task", callback_data=f"task_ver_{campaign.id}")],
        [InlineKeyboardButton(text="◀️ Back to list", callback_data="tasks_page_0")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

@router.callback_query(F.data.startswith("task_ver_"))
async def verify_task_callback(query: CallbackQuery, session: AsyncSession, bot: Bot):
    c_id = query.data.replace("task_ver_", "")
    success, message = await TaskEngine.process_task_completion(
        session=session, bot=bot, user_id=query.from_user.id, campaign_id=c_id
    )
    await query.answer(message, show_alert=True)
    if success:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Back to task list", callback_data="tasks_page_0")]
        ])
        await query.message.edit_text(message, reply_markup=kb, parse_mode=ParseMode.HTML)


# --- PROMOTE / CAMPAIGN CREATION ---

@router.message(F.text.in_(["📢 Promote", "📢 প্রমোট"]))
async def start_campaign_creation(message: Message, state: FSMContext, session: AsyncSession):
    user = await get_or_none(session, message.from_user.id)
    if not user:
        await message.answer(NOT_STARTED_TEXT["en"])
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Channel Sub", callback_data="ctype:channel_sub"),
         InlineKeyboardButton(text="👥 Group Join", callback_data="ctype:group_join")],
        [InlineKeyboardButton(text="👀 Post View", callback_data="ctype:post_view"),
         InlineKeyboardButton(text="🤖 Bot Start", callback_data="ctype:bot_start")],
        [InlineKeyboardButton(text="🌐 Web App / Custom", callback_data="ctype:web_app")]
    ])
    await message.answer(
        "📢 <b>Create Promotion Campaign</b>\n\nSelect the task type advertisers/subscribers must complete:",
        reply_markup=kb, parse_mode=ParseMode.HTML
    )
    await state.set_state(CampaignCreationState.task_type)

@router.callback_query(CampaignCreationState.task_type, F.data.startswith("ctype:"))
async def campaign_type_selected(query: CallbackQuery, state: FSMContext):
    task_type = query.data.split(":")[1]
    await state.update_data(task_type=task_type)
    await query.message.edit_text("📝 Enter Campaign Title:")
    await state.set_state(CampaignCreationState.title)
    await query.answer()

@router.message(CampaignCreationState.title)
async def campaign_title_entered(message: Message, state: FSMContext):
    title = safe_text(message)
    if not title or len(title) > 200:
        await message.answer("❌ Please enter a title between 1 and 200 characters (text only).")
        return
    await state.update_data(title=title)
    data = await state.get_data()
    task_type = data.get("task_type")

    if task_type in (TaskType.CHANNEL_SUB.value, TaskType.GROUP_JOIN.value):
        await message.answer(
            "🔗 Send the channel/group username (e.g. <code>@mychannel</code>), invite link, or numeric chat ID:",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer("🔗 Enter the link users should open (or any short instruction text):")
    await state.set_state(CampaignCreationState.target)

@router.message(CampaignCreationState.target)
async def campaign_target_entered(message: Message, state: FSMContext, bot: Bot):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    data = await state.get_data()
    task_type = data.get("task_type")

    if task_type in (TaskType.CHANNEL_SUB.value, TaskType.GROUP_JOIN.value):
        status_msg = await message.answer("🔎 Verifying access to that channel/group, please wait...")
        try:
            chat_info, error = await asyncio.wait_for(
                resolve_and_verify_target_chat(bot, raw), timeout=15
            )
        except asyncio.TimeoutError:
            await status_msg.edit_text("❌ Verification timed out. Please try again in a moment.")
            return

        if error == TargetResolutionError.NOT_FOUND:
            await status_msg.edit_text(
                "❌ Couldn't find that channel/group. Double-check the username/link and send it again."
            )
            return
        if error == TargetResolutionError.BOT_NOT_MEMBER:
            await status_msg.edit_text(
                f"❌ I'm not a member of that chat yet.\n\n"
                f"👉 Please add <b>@{esc(BOT_USERNAME)}</b> to your channel/group as an <b>Administrator</b>, "
                f"then send the username/link again.",
                parse_mode=ParseMode.HTML
            )
            return
        if error == TargetResolutionError.BOT_NOT_ADMIN:
            await status_msg.edit_text(
                f"❌ I'm in the chat, but not as an <b>Administrator</b>.\n\n"
                f"👉 Please promote <b>@{esc(BOT_USERNAME)}</b> to Admin (any permissions are fine) so I can "
                f"automatically verify subscriptions, then send the username/link again.",
                parse_mode=ParseMode.HTML
            )
            return
        if error:
            await status_msg.edit_text("❌ Something went wrong while checking that chat. Please try again.")
            return

        await state.update_data(
            target=raw,
            target_chat_id=chat_info["chat_id"],
            target_username=chat_info["username"],
            target_invite_link=chat_info["invite_link"],
        )
        await status_msg.edit_text(
            f"✅ Verified! I'm an admin in <b>{esc(chat_info['title'])}</b> — subscriptions will be checked automatically.\n\n"
            f"💰 Now enter the reward per user (GRAM coins):",
            parse_mode=ParseMode.HTML
        )
    else:
        await state.update_data(target=raw, target_chat_id=None, target_username=None, target_invite_link=None)
        await message.answer("💰 Enter reward per user (GRAM coins):")

    await state.set_state(CampaignCreationState.reward)

@router.message(CampaignCreationState.reward)
async def campaign_reward_entered(message: Message, state: FSMContext):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    try:
        reward = Decimal(raw)
        if reward <= 0:
            raise ValueError()
    except Exception:
        await message.answer("❌ Invalid reward. Please enter a positive number.")
        return
    await state.update_data(reward=str(reward))
    await message.answer("👥 Enter total target completions count (how many users can complete this):")
    await state.set_state(CampaignCreationState.max_completions)

@router.message(CampaignCreationState.max_completions)
async def campaign_finalize(message: Message, state: FSMContext, session: AsyncSession):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    try:
        max_comp = int(raw)
        if max_comp <= 0:
            raise ValueError()
    except Exception:
        await message.answer("❌ Invalid number. Please enter a positive whole number.")
        return

    data = await state.get_data()
    reward_per_user = Decimal(data["reward"])
    base_budget = reward_per_user * max_comp
    commission = base_budget * PLATFORM_COMMISSION_PERCENT
    total_cost = base_budget + commission

    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id).with_for_update())
    user = res.scalar_one_or_none()

    if not user:
        await message.answer(NOT_STARTED_TEXT["en"])
        await state.clear()
        return

    if user.balance < total_cost:
        await message.answer(
            f"❌ Insufficient balance!\nRequired: <code>{total_cost:,.0f}</code> GRAM (includes 15% platform fee)\n"
            f"Your balance: <code>{user.balance:,.0f}</code> GRAM",
            parse_mode=ParseMode.HTML
        )
        await state.clear()
        return

    target_username = data.get("target_username")
    target_chat_id = data.get("target_chat_id")
    target_link = data.get("target_invite_link") if not target_username else None
    if target_chat_id is None:
        # Non-verifiable task types: store the raw link/instruction the user provided.
        target_link = data.get("target")

    async with session.begin_nested():
        user.balance -= total_cost
        campaign = Campaign(
            advertiser_id=user_id,
            title=data["title"],
            task_type=TaskType(data["task_type"]),
            target_chat_id=target_chat_id,
            target_username=target_username,
            target_link=target_link,
            reward_per_user=reward_per_user,
            max_completions=max_comp,
            total_budget=total_cost,
            status=CampaignStatus.ACTIVE
        )
        session.add(campaign)
        session.add(Transaction(
            user_id=user_id, amount=-total_cost,
            type=TransactionType.CAMPAIGN_PAYMENT,
            description=f"Campaign created: {data['title']}"
        ))

    await session.commit()
    await state.clear()
    await message.answer(
        "✅ Campaign created successfully and is now active!\n\n"
        "Users can now find and complete it under 💰 <b>Earnings</b>.",
        parse_mode=ParseMode.HTML
    )


# --- CHECKS (VOUCHER CODES) ---

def get_checks_menu_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    if lang == "bn":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎫 চেক তৈরি করুন", callback_data="chk_create")],
            [InlineKeyboardButton(text="💳 চেক একটিভেট করুন", callback_data="chk_activate")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 Create a Check", callback_data="chk_create")],
        [InlineKeyboardButton(text="💳 Activate a Check", callback_data="chk_activate")],
    ])

@router.message(F.text.in_(["📋 Checks", "📋 চেক্স"]))
async def checks_menu(message: Message, session: AsyncSession):
    user = await get_or_none(session, message.from_user.id)
    lang = user.language if user else "en"
    text = (
        "📋 <b>Checks System</b>\n\nA Check is a shareable code pre-funded with GRAM coins — "
        "create one to gift coins to others, or activate a code someone shared with you."
        if lang == "en" else
        "📋 <b>চেক সিস্টেম</b>\n\nচেক হলো GRAM কয়েন দিয়ে ফান্ড করা একটি শেয়ারযোগ্য কোড — "
        "অন্যদের কয়েন গিফট করতে একটি তৈরি করুন, অথবা কারো শেয়ার করা কোড একটিভেট করুন।"
    )
    await message.answer(text, reply_markup=get_checks_menu_keyboard(lang), parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "chk_create")
async def chk_create_start(query: CallbackQuery, state: FSMContext):
    await query.message.edit_text("💰 Enter the GRAM amount <b>per activation</b> (e.g. <code>1000</code>):", parse_mode=ParseMode.HTML)
    await state.set_state(CheckCreateState.amount)
    await query.answer()

@router.message(CheckCreateState.amount)
async def chk_create_amount(message: Message, state: FSMContext):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise ValueError()
    except Exception:
        await message.answer("❌ Invalid amount. Please enter a positive number.")
        return
    await state.update_data(amount=str(amount))
    await message.answer("👥 Enter number of activations (how many people can redeem this check):")
    await state.set_state(CheckCreateState.activations)

@router.message(CheckCreateState.activations)
async def chk_create_finalize(message: Message, state: FSMContext, session: AsyncSession):
    raw_input = safe_text(message)
    if raw_input is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    try:
        activations = int(raw_input)
        if activations <= 0:
            raise ValueError()
    except Exception:
        await message.answer("❌ Invalid number. Please enter a positive whole number.")
        return

    data = await state.get_data()
    amount = Decimal(data["amount"])
    total_cost = amount * activations
    await state.clear()

    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id).with_for_update())
    user = res.scalar_one_or_none()

    if not user:
        await message.answer(NOT_STARTED_TEXT["en"])
        return
    if user.balance < total_cost:
        await message.answer(
            f"❌ Insufficient balance. Required: <code>{total_cost:,.0f}</code> GRAM, you have <code>{user.balance:,.0f}</code> GRAM.",
            parse_mode=ParseMode.HTML
        )
        return

    code = uuid.uuid4().hex[:10].upper()
    async with session.begin_nested():
        user.balance -= total_cost
        chk = Check(code=code, created_by=user_id, amount_per_activation=amount, max_activations=activations)
        session.add(chk)
        session.add(Transaction(
            user_id=user_id, amount=-total_cost,
            type=TransactionType.WITHDRAWAL,
            description=f"Created check {code}"
        ))

    await session.commit()
    await message.answer(
        f"✅ <b>Check Created!</b>\n\n"
        f"🎫 Code: <code>{code}</code>\n"
        f"💰 Amount: <code>{amount:,.0f}</code> GRAM x {activations} activation(s)\n\n"
        f"Share this code — each person can activate it once.",
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "chk_activate")
async def chk_activate_start(query: CallbackQuery, state: FSMContext):
    await query.message.edit_text("🎫 Enter the check code to activate:")
    await state.set_state(CheckRedeemState.code)
    await query.answer()

@router.message(CheckRedeemState.code)
async def chk_activate_finalize(message: Message, state: FSMContext, session: AsyncSession):
    raw_code = safe_text(message)
    if raw_code is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    code = raw_code.upper()
    await state.clear()
    user_id = message.from_user.id

    async with session.begin_nested():
        res = await session.execute(select(Check).where(Check.code == code).with_for_update())
        chk = res.scalar_one_or_none()

        if not chk or not chk.is_active:
            await message.answer("❌ Invalid or expired check code.")
            return
        if chk.activations_count >= chk.max_activations:
            chk.is_active = False
            await message.answer("❌ This check has already been fully redeemed.")
            return
        if chk.created_by == user_id:
            await message.answer("❌ You can't activate your own check.")
            return

        res_act = await session.execute(select(CheckActivation).where(
            CheckActivation.check_id == chk.id, CheckActivation.user_id == user_id
        ))
        if res_act.scalar_one_or_none():
            await message.answer("❌ You've already activated this check.")
            return

        res_u = await session.execute(select(User).where(User.id == user_id).with_for_update())
        user = res_u.scalar_one_or_none()
        if not user:
            await message.answer(NOT_STARTED_TEXT["en"])
            return

        user.balance += chk.amount_per_activation
        user.total_earned += chk.amount_per_activation
        chk.activations_count += 1
        if chk.activations_count >= chk.max_activations:
            chk.is_active = False

        session.add(CheckActivation(check_id=chk.id, user_id=user_id))
        session.add(Transaction(
            user_id=user_id, amount=chk.amount_per_activation,
            type=TransactionType.CHECK_REDEEM,
            description=f"Redeemed check {code}"
        ))
        reward_amount = chk.amount_per_activation

    await session.commit()
    await message.answer(f"✅ Check redeemed! +<code>{reward_amount:,.0f}</code> GRAM added to your balance.", parse_mode=ParseMode.HTML)


# --- OTHER MENU BUTTONS ---

@router.message(F.text.in_(["🛡 Subscription Check", "🛡 সাবস্ক্রিপশন চেক"]))
async def sub_check_menu(message: Message):
    await message.answer(
        "🛡 <b>Subscription Verification Engine</b>\n\n"
        "All channel/group subscriptions are verified live through the Telegram Bot API.\n\n"
        "📌 For this to work, the channel/group owner must add this bot as an <b>Administrator</b> — "
        "once that's done, the bot checks membership automatically every time a user taps 'Verify Task'.",
        parse_mode=ParseMode.HTML
    )

@router.message(F.text.in_(["📊 Our Bots and Statistics", "📊 আমাদের বট ও পরিসংখ্যান"]))
async def stats_menu(message: Message, session: AsyncSession):
    res_users = await session.execute(select(func.count(User.id)))
    total_users = res_users.scalar()
    res_camp = await session.execute(select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.ACTIVE))
    active_campaigns = res_camp.scalar()
    res_camp_all = await session.execute(select(func.count(Campaign.id)))
    total_campaigns = res_camp_all.scalar()

    await message.answer(
        f"📊 <b>Platform Statistics</b>\n\n"
        f"👥 Total Users: <code>{total_users:,}</code>\n"
        f"📢 Active Campaigns: <code>{active_campaigns:,}</code>\n"
        f"🗂 Total Campaigns Ever: <code>{total_campaigns:,}</code>",
        parse_mode=ParseMode.HTML
    )

@router.message(F.text.in_(["🔗 Useful Links", "🔗 দরকারী লিংক"]))
async def links_menu(message: Message):
    await message.answer(
        f"🔗 <b>Useful Links</b>\n• Bot: @{esc(BOT_USERNAME)}\n• Official Channel: Update soon\n• Support: Contact admin",
        parse_mode=ParseMode.HTML
    )

@router.message(F.text.in_(["ℹ️ Instruction", "ℹ️ নির্দেশিকা"]))
async def instruction_menu(message: Message):
    await message.answer(
        "ℹ️ <b>Instruction</b>\n\n"
        "1️⃣ Earn GRAM coins by completing tasks under 💰 Earnings.\n"
        "2️⃣ Promote your own channel/group under 📢 Promote — add this bot as <b>Admin</b> "
        "to your channel/group first so subscriptions can be verified automatically.\n"
        "3️⃣ Use 📋 Checks to gift or redeem pre-funded GRAM codes.\n"
        "4️⃣ Use 👤 My Cabinet to manage your profile, referrals, and language.",
        parse_mode=ParseMode.HTML
    )


# ==============================================================================
# SECTION 9: PROTECTED ADMIN PANEL & COMMAND (/admin)
# ==============================================================================

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ You are not authorized.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Add/Deduct Coins", callback_data="adm_coins")],
        [InlineKeyboardButton(text="🚫 Ban User", callback_data="adm_ban")],
        [InlineKeyboardButton(text="📢 Moderate Tasks", callback_data="adm_tasks")]
    ])
    await message.answer("👑 <b>Admin Panel</b>\nChoose an action:", reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "adm_coins")
async def adm_coins(query: CallbackQuery, state: FSMContext):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    await query.message.edit_text("Send target User ID:")
    await state.set_state(AdminState.target_user_id)
    await query.answer()

@router.message(AdminState.target_user_id)
async def adm_get_uid(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    try:
        uid = int(raw)
        await state.update_data(target_uid=uid)
        await message.answer("Enter GRAM coin amount to add/deduct (e.g. <code>5000</code> or <code>-1000</code>):", parse_mode=ParseMode.HTML)
        await state.set_state(AdminState.coin_amount)
    except ValueError:
        await message.answer("❌ Invalid ID.")

@router.message(AdminState.coin_amount)
async def adm_apply_coins(message: Message, state: FSMContext, session: AsyncSession):
    if message.from_user.id not in ADMIN_IDS:
        return
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    try:
        amount = Decimal(raw)
    except Exception:
        await message.answer("❌ Invalid amount.")
        return

    data = await state.get_data()
    uid = data["target_uid"]
    await state.clear()

    async with session.begin_nested():
        res = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = res.scalar_one_or_none()
        if not user:
            await message.answer("❌ User not found.")
            return
        user.balance += amount
        session.add(Transaction(user_id=uid, amount=amount, type=TransactionType.TASK_REWARD, description="Admin adjustment"))

    await session.commit()
    await message.answer(f"✅ Balance updated for user <code>{uid}</code> by <code>{amount:,.0f}</code> GRAM.", parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "adm_ban")
async def adm_ban(query: CallbackQuery, state: FSMContext):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    await query.message.edit_text("Send User ID to ban:")
    await state.set_state(AdminState.ban_user_id)
    await query.answer()

@router.message(AdminState.ban_user_id)
async def adm_apply_ban(message: Message, state: FSMContext, session: AsyncSession):
    if message.from_user.id not in ADMIN_IDS:
        return
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    try:
        uid = int(raw)
    except ValueError:
        await message.answer("❌ Invalid ID.")
        return
    await state.clear()
    async with session.begin_nested():
        res = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = res.scalar_one_or_none()
        if not user:
            await message.answer("❌ User not found.")
            return
        user.is_blocked = True
    await session.commit()
    await message.answer(f"🚫 User <code>{uid}</code> banned successfully.", parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "adm_tasks")
async def adm_tasks(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    res = await session.execute(select(Campaign).where(Campaign.status == CampaignStatus.ACTIVE).limit(10))
    campaigns = res.scalars().all()
    if not campaigns:
        await query.message.edit_text("No active campaigns.")
        await query.answer()
        return
    buttons = [[InlineKeyboardButton(text=f"❌ Cancel: {c.title[:20]}", callback_data=f"adm_del_{c.id}")] for c in campaigns]
    await query.message.edit_text("Select campaign to cancel:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await query.answer()

@router.callback_query(F.data.startswith("adm_del_"))
async def adm_cancel_camp(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    cid = query.data.replace("adm_del_", "")
    async with session.begin_nested():
        res = await session.execute(select(Campaign).where(Campaign.id == cid).with_for_update())
        c = res.scalar_one_or_none()
        if c:
            c.status = CampaignStatus.CANCELLED
    await session.commit()
    await query.answer("Campaign cancelled.", show_alert=True)
    await query.message.edit_text("✅ Campaign has been deactivated.")


# ==============================================================================
# SECTION 10: FASTAPI & APP INITIALIZATION
# ==============================================================================

fastapi_app = FastAPI(title="BoostGram Health Check")

@fastapi_app.get("/health")
async def health_check():
    return {"status": "active", "timestamp": datetime.now(timezone.utc).isoformat()}

async def start_bot():
    bot = Bot(token=BOT_TOKEN)
    storage = RedisStorage(redis=redis_client)
    dp = Dispatcher(storage=storage)

    class DatabaseMiddleware(BaseMiddleware):
        async def __call__(self, handler, event, data):
            async with AsyncSessionLocal() as session:
                data["session"] = session
                return await handler(event, data)

    dp.message.outer_middleware(DatabaseMiddleware())
    dp.callback_query.outer_middleware(DatabaseMiddleware())
    dp.include_router(router)

    @dp.error()
    async def global_error_handler(event: ErrorEvent):
        """
        Safety net: if any handler above raises an unhandled exception (bad
        input, a Telegram API quirk, a network hiccup, etc.) this makes sure
        the user gets a reply instead of the bot silently doing nothing.
        """
        logger.exception(f"Unhandled exception while processing update: {event.exception}")
        update = event.update
        chat_id = None
        if update.message:
            chat_id = update.message.chat.id
        elif update.callback_query and update.callback_query.message:
            chat_id = update.callback_query.message.chat.id

        if chat_id:
            try:
                await bot.send_message(
                    chat_id,
                    "⚠️ Something went wrong processing that. Please try again, or send /start to reset."
                )
            except Exception:
                pass
        return True

    await init_db()
    logger.info("Database initialized successfully.")
    logger.info("Starting bot in polling mode...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")

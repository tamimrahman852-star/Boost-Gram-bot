
import sys
import os
import re
import math
import uuid
import logging
import asyncio
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

# Third-party Imports
from dotenv import load_dotenv
import redis.asyncio as aioredis

from sqlalchemy import (
    BigInteger, String, Numeric, Integer, Boolean, DateTime,
    ForeignKey, UniqueConstraint, Index, select, update,
    func, or_, and_, Enum as SQLEnum
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
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import uvicorn
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

LANGUAGES = {
    "en": {
        "welcome": "👋 Welcome to *{bot_name}*, {first_name}!\n\n🚀 Complete tasks, earn GRAM coins, and promote your channels instantly!",
        "cabinet": "👤 *Your Cabinet:*\n\n🆔 My ID: `{user_id}`\n📈 Level: 🌱 {rank} {xp}/1500 XP\n💰 Balance: `{balance:,.0f}` GRAM",
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
        "btn_notif": "🔕 Disable notifications",
        "lang_changed": "✅ Language changed to English successfully!"
    },
    "bn": {
        "welcome": "👋 *{bot_name}*-এ আপনাকে স্বাগতম, {first_name}!\n\n🚀 টাস্ক সম্পন্ন করুন, GRAM কয়েন আয় করুন এবং আপনার চ্যানেল প্রমোট করুন!",
        "cabinet": "👤 *আপনার ক্যাবিনেট:*\n\n🆔 আমার আইডি: `{user_id}`\n📈 লেভেল: 🌱 {rank} {xp}/1500 XP\n💰 ব্যালেন্স: `{balance:,.0f}` GRAM",
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
        "btn_notif": "🔕 নোটিফিকেশন বন্ধ করুন",
        "lang_changed": "✅ সফলভাবে ভাষা বাংলায় পরিবর্তন করা হয়েছে!"
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

class AdminState(StatesGroup):
    target_user_id = State()
    coin_amount = State()
    ban_user_id = State()


# ==============================================================================
# SECTION 6: KEYBOARDS (AS SEEN IN SCREENSHOT)
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

def get_cabinet_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, "btn_replenish"), callback_data="cab_replenish")],
        [InlineKeyboardButton(text=get_text(lang, "btn_referral"), callback_data="cab_referral")],
        [InlineKeyboardButton(text=get_text(lang, "btn_level"), callback_data="cab_level")],
        [InlineKeyboardButton(text=get_text(lang, "btn_tasks"), callback_data="cab_tasks")],
        [InlineKeyboardButton(text=get_text(lang, "btn_lang"), callback_data="cab_lang")],
        [InlineKeyboardButton(text=get_text(lang, "btn_notif"), callback_data="cab_notif")]
    ])


# ==============================================================================
# SECTION 7: CORE BUSINESS LOGIC & VERIFICATION ENGINE
# ==============================================================================

class SubscriptionVerifier:
    @staticmethod
    async def verify(bot: Bot, user_id: int, target_chat: Union[int, str]) -> bool:
        try:
            member = await bot.get_chat_member(chat_id=target_chat, user_id=user_id)
            valid_statuses = [
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR
            ]
            return member.status in valid_statuses
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning(f"Membership check warning for chat {target_chat}: {str(e)}")
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

                res_comp = await session.execute(select(TaskCompletion).where(
                    TaskCompletion.user_id == user_id, TaskCompletion.campaign_id == campaign_id
                ))
                if res_comp.scalar_one_or_none():
                    return False, "❌ You have already completed this task!"

                target = campaign.target_chat_id or campaign.target_username
                if campaign.task_type in [TaskType.CHANNEL_SUB, TaskType.GROUP_JOIN]:
                    if target:
                        is_valid = await SubscriptionVerifier.verify(bot, user_id, target)
                        if not is_valid:
                            return False, "❌ Verification failed! Please make sure you joined."

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
            return True, f"🎉 Task Verified Successfully!\n💰 You earned: +`{reward:,.0f}` GRAM\n⚡ XP gained: +50"

        except Exception as e:
            await session.rollback()
            logger.error(f"Task verification error: {str(e)}")
            return False, "❌ An error occurred during verification."


# ==============================================================================
# SECTION 8: BOT HANDLERS & ROUTING
# ==============================================================================

router = Router()

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

    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

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

        await session.commit()

    welcome_msg = get_text(user.language, "welcome", bot_name=BOT_USERNAME, first_name=message.from_user.first_name)
    await message.answer(welcome_msg, reply_markup=get_main_keyboard(user.language), parse_mode=ParseMode.MARKDOWN)


# --- MY CABINET (AS SEEN IN SCREENSHOT) ---

@router.message(F.text.in_(["👤 My Cabinet", "👤 মাই ক্যাবিনেট"]))
async def show_cabinet(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    rank_name = "Activist" if user.level >= 1 else "Newbie"
    current_xp_mod = user.xp % 1500

    cabinet_text = get_text(
        user.language, "cabinet",
        user_id=user.id, rank=rank_name, xp=current_xp_mod, balance=user.balance
    )
    await message.answer(cabinet_text, reply_markup=get_cabinet_keyboard(user.language), parse_mode=ParseMode.MARKDOWN)


# --- INLINE ACTIONS FROM CABINET ---

@router.callback_query(F.data == "cab_replenish")
async def cb_replenish(query: CallbackQuery, state: FSMContext, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id))
    user = res.scalar_one_or_none()
    lang = user.language if user else "en"

    msg = "⭐ *Replenish Balance via Telegram Stars*\n\nRate: `1 Star = 5000 GRAM`\nEnter amount of Telegram Stars:" if lang == "en" else "⭐ *টেলিগ্রাম স্টারের মাধ্যমে ব্যালেন্স টপ-আপ*\n\nরেট: `১ স্টার = ৫০০০ GRAM`\nকতগুলো স্টার পেমেন্ট করতে চান সংখ্যাটি লিখুন:"
    await query.message.answer(msg, parse_mode=ParseMode.MARKDOWN)
    await state.set_state(TopUpState.amount_stars)
    await query.answer()

@router.message(TopUpState.amount_stars)
async def process_star_invoice(message: Message, state: FSMContext):
    try:
        stars = int(message.text.strip())
        if stars <= 0: raise ValueError()
    except Exception:
        await message.answer("❌ Invalid input.")
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
        stars = int(payload.split("_")[2])
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
        await message.answer(f"✅ Top-up successful! Added `+{coins_to_add:,.0f}` GRAM.", parse_mode=ParseMode.MARKDOWN)

@router.callback_query(F.data == "cab_referral")
async def cb_referral(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id))
    user = res.scalar_one_or_none()
    res_count = await session.execute(select(func.count(User.id)).where(User.referred_by == query.from_user.id))
    invited = res_count.scalar()

    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.referral_code}"
    text = f"👥 *Referral System*\n\nInvite friends & earn `5,000 GRAM` + `500 XP` per referral!\n\n📊 Total Invited: `{invited}`\n🔗 Link:\n`{ref_link}`"
    await query.message.answer(text, parse_mode=ParseMode.MARKDOWN)
    await query.answer()

@router.callback_query(F.data == "cab_level")
async def cb_level(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id))
    user = res.scalar_one_or_none()
    text = f"📊 *Level System*\n\nCurrent Level: `{user.level}`\nTotal XP: `{user.xp}`\nNext rank progress: `{user.xp % 1500}/1500 XP`"
    await query.message.answer(text, parse_mode=ParseMode.MARKDOWN)
    await query.answer()

@router.callback_query(F.data == "cab_tasks")
async def cb_tasks(query: CallbackQuery, session: AsyncSession):
    await query.answer()
    await render_tasks_page(query.from_user.id, query.message, session, page=0)

@router.callback_query(F.data == "cab_lang")
async def cb_lang(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id).with_for_update())
    user = res.scalar_one_or_none()
    if user:
        user.language = "bn" if user.language == "en" else "en"
        await session.commit()
        lang = user.language
        kb = get_cabinet_keyboard(lang)
        await query.message.edit_text(get_text(lang, "lang_changed"), reply_markup=kb)
    await query.answer()

@router.callback_query(F.data == "cab_notif")
async def cb_notif(query: CallbackQuery):
    await query.answer("🔕 Notifications toggled.", show_alert=True)


# --- EARNINGS & TASKS MENU ---

@router.message(F.text.in_(["💰 Earnings", "💰 আর্নিংস"]))
async def show_earnings_menu(message: Message, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == message.from_user.id))
    user = res.scalar_one_or_none()
    lang = user.language if user else "en"
    
    text = "🎯 *Available Tasks*\nSelect tasks below to earn GRAM coins:" if lang == "en" else "🎯 *উপলব্ধ টাস্কসমূহ*\nGRAM কয়েন অর্জনের জন্য টাস্ক নির্বাচন করুন:"
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)
    await render_tasks_page(message.from_user.id, message, session, page=0)

async def render_tasks_page(user_id: int, event: Union[Message, CallbackQuery], session: AsyncSession, page: int = 0):
    limit = 5
    offset = page * limit

    completed_sub = select(TaskCompletion.campaign_id).where(TaskCompletion.user_id == user_id)
    stmt = select(Campaign).where(
        Campaign.status == CampaignStatus.ACTIVE,
        Campaign.id.not_in(completed_sub)
    ).offset(offset).limit(limit)
    
    res = await session.execute(stmt)
    campaigns = res.scalars().all()

    if not campaigns:
        text = "🎯 No active tasks right now. Check back soon!"
        if isinstance(event, Message):
            await event.answer(text)
        else:
            await event.message.edit_text(text)
        return

    buttons = []
    for c in campaigns:
        buttons.append([InlineKeyboardButton(
            text=f"📢 {c.title} (+{c.reward_per_user:,.0f} GRAM)",
            callback_data=f"task_view_{c.id}"
        )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = "🎯 *Active Tasks List:*"
    if isinstance(event, Message):
        await event.answer(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        try:
            await event.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await event.answer(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.callback_query(F.data.startswith("task_view_"))
async def view_task_detail(query: CallbackQuery, session: AsyncSession):
    c_id = query.data.replace("task_view_", "")
    res = await session.execute(select(Campaign).where(Campaign.id == c_id))
    campaign = res.scalar_one_or_none()

    if not campaign or campaign.status != CampaignStatus.ACTIVE:
        await query.answer("Task not available.", show_alert=True)
        return

    target_url = campaign.target_link or (f"https://t.me/{campaign.target_username}" if campaign.target_username else f"https://t.me/{BOT_USERNAME}")

    text = f"📢 *{campaign.title}*\n\n💰 Reward: `{campaign.reward_per_user:,.0f}` GRAM\nSlots remaining: `{campaign.max_completions - campaign.completed_count}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Open Link", url=target_url)],
        [InlineKeyboardButton(text="✅ Verify Task", callback_data=f"task_ver_{campaign.id}")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.callback_query(F.data.startswith("task_ver_"))
async def verify_task_callback(query: CallbackQuery, session: AsyncSession, bot: Bot):
    c_id = query.data.replace("task_ver_", "")
    success, message = await TaskEngine.process_task_completion(
        session=session, bot=bot, user_id=query.from_user.id, campaign_id=c_id
    )
    await query.answer(message, show_alert=True)
    if success:
        await query.message.edit_text(message, parse_mode=ParseMode.MARKDOWN)


# --- OTHER MENU BUTTONS HANDLERS ---

@router.message(F.text.in_(["📢 Promote", "📢 প্রমোট"]))
async def start_campaign_creation(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Channel Sub", callback_data="ctype:channel_sub"), InlineKeyboardButton(text="👥 Group Join", callback_data="ctype:group_join")],
        [InlineKeyboardButton(text="👀 Post View", callback_data="ctype:post_view"), InlineKeyboardButton(text="🤖 Bot Start", callback_data="ctype:bot_start")],
        [InlineKeyboardButton(text="🌐 Web App / Custom", callback_data="ctype:web_app")]
    ])
    await message.answer("📢 *Create Promotion Campaign*\nSelect type:", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    await state.set_state(CampaignCreationState.task_type)

@router.callback_query(CampaignCreationState.task_type, F.data.startswith("ctype:"))
async def campaign_type_selected(query: CallbackQuery, state: FSMContext):
    await state.update_data(task_type=query.data.split(":")[1])
    await query.message.edit_text("📝 Enter Campaign Title:")
    await state.set_state(CampaignCreationState.title)

@router.message(CampaignCreationState.title)
async def campaign_title_entered(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("🔗 Enter target link or username (@channel):")
    await state.set_state(CampaignCreationState.target)

@router.message(CampaignCreationState.target)
async def campaign_target_entered(message: Message, state: FSMContext):
    await state.update_data(target=message.text.strip())
    await message.answer("💰 Enter reward per user (GRAM coins):")
    await state.set_state(CampaignCreationState.reward)

@router.message(CampaignCreationState.reward)
async def campaign_reward_entered(message: Message, state: FSMContext):
    try:
        reward = Decimal(message.text.strip())
        if reward <= 0: raise ValueError()
    except Exception:
        await message.answer("❌ Invalid reward.")
        return
    await state.update_data(reward=str(reward))
    await message.answer("👥 Enter total target completions count:")
    await state.set_state(CampaignCreationState.max_completions)

@router.message(CampaignCreationState.max_completions)
async def campaign_finalize(message: Message, state: FSMContext, session: AsyncSession):
    try:
        max_comp = int(message.text.strip())
        if max_comp <= 0: raise ValueError()
    except Exception:
        await message.answer("❌ Invalid number.")
        return

    data = await state.get_data()
    reward_per_user = Decimal(data["reward"])
    base_budget = reward_per_user * max_comp
    commission = base_budget * PLATFORM_COMMISSION_PERCENT
    total_cost = base_budget + commission

    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    if user.balance < total_cost:
        await message.answer(f"❌ Insufficient balance! Required: `{total_cost:,.0f}` GRAM (incl. 15% fee)", parse_mode=ParseMode.MARKDOWN)
        await state.clear()
        return

    target = data["target"]
    target_username = target.replace("@", "").split("t.me/")[-1] if "@" in target or "t.me/" in target else None

    async with session.begin_nested():
        user.balance -= total_cost
        campaign = Campaign(
            advertiser_id=user_id,
            title=data["title"],
            task_type=TaskType(data["task_type"]),
            target_username=target_username,
            target_link=target if not target_username else None,
            reward_per_user=reward_per_user,
            max_completions=max_comp,
            total_budget=total_cost,
            status=CampaignStatus.ACTIVE
        )
        session.add(campaign)

    await session.commit()
    await state.clear()
    await message.answer("✅ Campaign created successfully and is now active!", parse_mode=ParseMode.MARKDOWN)


@router.message(F.text.in_(["📋 Checks", "📋 চেক্স"]))
async def checks_menu(message: Message):
    await message.answer("📋 *Checks System*\nYou can create and redeem crypto/coin checks here.", parse_mode=ParseMode.MARKDOWN)

@router.message(F.text.in_(["🛡 Subscription Check", "🛡 সাবস্ক্রিপশন চেক"]))
async def sub_check_menu(message: Message):
    await message.answer("🛡 *Subscription Verification Engine*\nAll channel and group memberships are verified automatically by bot administration status.", parse_mode=ParseMode.MARKDOWN)

@router.message(F.text.in_(["📊 Our Bots and Statistics", "📊 আমাদের বট ও পরিসংখ্যান"]))
async def stats_menu(message: Message, session: AsyncSession):
    res_users = await session.execute(select(func.count(User.id)))
    total_users = res_users.scalar()
    res_camp = await session.execute(select(func.count(Campaign.id)))
    total_campaigns = res_camp.scalar()

    await message.answer(f"📊 *Platform Statistics*\n\n👥 Total Users: `{total_users:,}`\n📢 Total Campaigns: `{total_campaigns:,}`", parse_mode=ParseMode.MARKDOWN)

@router.message(F.text.in_(["🔗 Useful Links", "🔗 দরকারী লিংক"]))
async def links_menu(message: Message):
    await message.answer("🔗 *Useful Links*\n• Official Channel: Update soon\n• Support: Contact admin", parse_mode=ParseMode.MARKDOWN)

@router.message(F.text.in_(["ℹ️ Instruction", "ℹ️ নির্দেশিকা"]))
async def instruction_menu(message: Message):
    await message.answer("ℹ️ *Instruction*\n1. Earn GRAM coins by completing tasks.\n2. Promote your channel or group using 'Promote'.\n3. Use 'My Cabinet' to manage profile & language.", parse_mode=ParseMode.MARKDOWN)


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
    await message.answer("👑 *Admin Panel*\nChoose an action:", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.callback_query(F.data == "adm_coins")
async def adm_coins(query: CallbackQuery, state: FSMContext):
    if query.from_user.id not in ADMIN_IDS: return
    await query.message.edit_text("Send target User ID:")
    await state.set_state(AdminState.target_user_id)
    await query.answer()

@router.message(AdminState.target_user_id)
async def adm_get_uid(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
        await state.update_data(target_uid=uid)
        await message.answer("Enter GRAM coin amount to add/deduct (e.g. `5000` or `-1000`):", parse_mode=ParseMode.MARKDOWN)
        await state.set_state(AdminState.coin_amount)
    except ValueError:
        await message.answer("❌ Invalid ID.")

@router.message(AdminState.coin_amount)
async def adm_apply_coins(message: Message, state: FSMContext, session: AsyncSession):
    try:
        amount = Decimal(message.text.strip())
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
    await message.answer(f"✅ Balance updated for user `{uid}` by `{amount:,.0f}` GRAM.", parse_mode=ParseMode.MARKDOWN)

@router.callback_query(F.data == "adm_ban")
async def adm_ban(query: CallbackQuery, state: FSMContext):
    if query.from_user.id not in ADMIN_IDS: return
    await query.message.edit_text("Send User ID to ban:")
    await state.set_state(AdminState.ban_user_id)
    await query.answer()

@router.message(AdminState.ban_user_id)
async def adm_apply_ban(message: Message, state: FSMContext, session: AsyncSession):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID.")
        return
    await state.clear()
    async with session.begin_nested():
        res = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = res.scalar_one_or_none()
        if user:
            user.is_blocked = True
    await session.commit()
    await message.answer(f"🚫 User `{uid}` banned successfully.", parse_mode=ParseMode.MARKDOWN)

@router.callback_query(F.data == "adm_tasks")
async def adm_tasks(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS: return
    res = await session.execute(select(Campaign).where(Campaign.status == CampaignStatus.ACTIVE).limit(5))
    campaigns = res.scalars().all()
    if not campaigns:
        await query.message.edit_text("No active campaigns.")
        return
    buttons = [[InlineKeyboardButton(text=f"❌ Cancel: {c.title[:15]}", callback_data=f"adm_del_{c.id}")] for c in campaigns]
    await query.message.edit_text("Select campaign to cancel:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await query.answer()

@router.callback_query(F.data.startswith("adm_del_"))
async def adm_cancel_camp(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS: return
    cid = query.data.replace("adm_del_", "")
    async with session.begin_nested():
        res = await session.execute(select(Campaign).where(Campaign.id == cid).with_for_update())
        c = res.scalar_one_or_none()
        if c: c.status = CampaignStatus.CANCELLED
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

    await init_db()
    logger.info("Database initialized successfully.")
    logger.info("Starting bot in polling mode...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")

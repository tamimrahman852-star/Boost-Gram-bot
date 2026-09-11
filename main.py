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
    reward = State()
    max_completions = State()
    link = State()

class TopUpState(StatesGroup):
    amount_stars = State()

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


# --- ADMIN COMMAND ---

@router.message(Command("admin"))
async def cmd_admin(message: Message, session: AsyncSession):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ You are not authorized to use this command.")
        return
    
    res_users = await session.execute(select(func.count(User.id)))
    total_users = res_users.scalar() or 0

    res_camp = await session.execute(select(func.count(Campaign.id)))
    total_camps = res_camp.scalar() or 0

    await message.answer(
        f"👑 *Admin Panel*\n\n👥 Total Users: `{total_users}`\n📢 Total Campaigns: `{total_camps}`",
        parse_mode=ParseMode.MARKDOWN
    )


# --- MY CABINET ---

@router.message(F.text.in_(["👤 My Cabinet", "👤 মাই ক্যাবিনেট"]))
async def show_cabinet(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        return

    rank_name = "Activist" if user.level >= 1 else "Newbie"
    current_xp_mod = user.xp % 1500

    cabinet_text = get_text(
        user.language, "cabinet",
        user_id=user.id, rank=rank_name, xp=current_xp_mod, balance=user.balance
    )
    await message.answer(cabinet_text, reply_markup=get_cabinet_keyboard(user.language), parse_mode=ParseMode.MARKDOWN)


# --- PROMOTION CREATION FLOW (WITH ADMIN & BOT CHECK) ---

@router.message(F.text.in_(["📢 Promote", "📢 প্রমোট"]))
async def start_promotion(message: Message, state: FSMContext, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == message.from_user.id))
    user = res.scalar_one_or_none()
    lang = user.language if user else "en"

    msg = "💰 Enter reward per user (GRAM coins):" if lang == "en" else "💰 প্রতি ইউজারের জন্য রিওয়ার্ড (GRAM কয়েন) লিখুন:"
    await message.answer(msg)
    await state.set_state(CampaignCreationState.reward)

@router.message(CampaignCreationState.reward)
async def process_campaign_reward(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text.isdigit():
        await message.answer("❌ Please enter a valid number.")
        return
    await state.update_data(reward=Decimal(message.text))
    
    res = await session.execute(select(User).where(User.id == message.from_user.id))
    user = res.scalar_one_or_none()
    lang = user.language if user else "en"

    msg = "👥 Enter total target completions count:" if lang == "en" else "👥 মোট টার্গেট কমপ্লিশন সংখ্যা লিখুন:"
    await message.answer(msg)
    await state.set_state(CampaignCreationState.max_completions)

@router.message(CampaignCreationState.max_completions)
async def process_campaign_max(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text.isdigit():
        await message.answer("❌ Please enter a valid number.")
        return
    await state.update_data(max_completions=int(message.text))

    res = await session.execute(select(User).where(User.id == message.from_user.id))
    user = res.scalar_one_or_none()
    lang = user.language if user else "en"

    msg = "🔗 Send your channel or group username/link (e.g., @mychannel or https://t.me/...):" if lang == "en" else "🔗 আপনার চ্যানেল বা গ্রুপের লিংক বা ইউজারনেম দিন (যেমন: @mychannel বা https://t.me/...):"
    await message.answer(msg)
    await state.set_state(CampaignCreationState.link)

@router.message(CampaignCreationState.link)
async def process_campaign_link(message: Message, state: FSMContext, session: AsyncSession):
    channel_link = message.text.strip()
    data = await state.get_data()
    
    reward = data.get("reward")
    max_completions = data.get("max_completions")
    user_id = message.from_user.id

    chat_id = channel_link if channel_link.startswith("@") else channel_link.split("/")[-1]
    if not chat_id.startswith("@") and not chat_id.lstrip("-").isdigit():
        chat_id = "@" + chat_id

    bot = message.bot
    try:
        bot_member = await bot.get_chat_member(chat_id=chat_id, user_id=bot.id)
        if bot_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await message.answer("❌ Bot is not an admin in this channel/group! Please add the bot as an administrator first.")
            await state.clear()
            return

        user_member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        if user_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR] and user_id not in ADMIN_IDS:
            await message.answer("❌ You are not an administrator of this channel/group!")
            await state.clear()
            return

    except TelegramBadRequest:
        await message.answer("❌ Channel or group not found, or bot lacks permission to check. Make sure the link is correct and the bot is added.")
        await state.clear()
        return
    except Exception as e:
        logger.error(f"Promotion validation error: {str(e)}")
        await message.answer(f"❌ An error occurred: {str(e)}")
        await state.clear()
        return

    total_budget = reward * max_completions

    res_u = await session.execute(select(User).where(User.id == user_id).with_for_update())
    user = res_u.scalar_one_or_none()

    if user.balance < total_budget:
        await message.answer(f"❌ Insufficient balance! You need `{total_budget:,.0f}` GRAM coins, but you have `{user.balance:,.0f}` GRAM.", parse_mode=ParseMode.MARKDOWN)
        await state.clear()
        return

    user.balance -= total_budget

    campaign = Campaign(
        advertiser_id=user_id,
        title=f"Channel Promotion: {chat_id}",
        task_type=TaskType.CHANNEL_SUB,
        target_chat_id=None if chat_id.startswith("@") else int(chat_id) if chat_id.lstrip("-").isdigit() else None,
        target_username=chat_id if chat_id.startswith("@") else None,
        target_link=channel_link,
        reward_per_user=reward,
        max_completions=max_completions,
        total_budget=total_budget,
        status=CampaignStatus.ACTIVE
    )
    session.add(campaign)

    tx = Transaction(
        user_id=user_id, amount=-total_budget,
        type=TransactionType.CAMPAIGN_PAYMENT,
        description=f"Created promotion campaign for {chat_id}"
    )
    session.add(tx)

    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ *Campaign Created Successfully!*\n\n📌 Target: `{chat_id}`\n💎 Reward/User: `{reward:,.0f}` GRAM\n🎯 Total Target: `{max_completions}`\n💰 Total Budget Deducted: `{total_budget:,.0f}` GRAM",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(user.language)
    )


# --- CABINET CALLBACK ACTIONS ---

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
async def process_topup_stars(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text.isdigit():
        await message.answer("❌ Please enter a valid number.")
        return
    
    stars_count = int(message.text)
    coins_to_add = Decimal(stars_count * STAR_TO_COIN_RATE)
    
    prices = [LabeledPrice(label=f"{stars_count} Telegram Stars", amount=stars_count)]
    await message.bot.send_invoice(
        chat_id=message.from_user.id,
        title="Top-up GRAM Balance",
        description=f"Top-up balance with {coins_to_add:,.0f} GRAM coins using Telegram Stars.",
        payload=f"topup_{stars_count}",
        currency="XTR",
        prices=prices
    )
    await state.clear()


@router.callback_query(F.data == "cab_referral")
async def cb_referral(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id))
    user = res.scalar_one_or_none()
    lang = user.language if user else "en"
    bot_user = BOT_USERNAME

    ref_link = f"https://t.me/{bot_user}?start=ref_{user.referral_code}"
    msg = f"👥 *Referral System*\n\nShare your link and earn `{REFERRAL_COIN_REWARD:,.0f}` GRAM + `{REFERRAL_XP_REWARD}` XP for every active referral!\n\n🔗 Your Link:\n`{ref_link}`" if lang == "en" else f"👥 *রেফারেল সিস্টেম*\n\nআপনার লিংক শেয়ার করুন এবং প্রতি রেফারে পান `{REFERRAL_COIN_REWARD:,.0f}` GRAM + `{REFERRAL_XP_REWARD}` XP!\n\n🔗 আপনার লিংক:\n`{ref_link}`"
    await query.message.answer(msg, parse_mode=ParseMode.MARKDOWN)
    await query.answer()


@router.callback_query(F.data == "cab_level")
async def cb_level(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id))
    user = res.scalar_one_or_none()
    lang = user.language if user else "en"

    msg = f"📊 *Level System*\n\nCurrent Level: `{user.level}`\nCurrent XP: `{user.xp}`" if lang == "en" else f"📊 *লেভেল সিস্টেম*\n\nবর্তমান লেভেল: `{user.level}`\nবর্তমান XP: `{user.xp}`"
    await query.message.answer(msg, parse_mode=ParseMode.MARKDOWN)
    await query.answer()


@router.callback_query(F.data == "cab_tasks")
async def cb_tasks(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id))
    user = res.scalar_one_or_none()
    lang = user.language if user else "en"

    msg = f"📋 *My Tasks Status*\n\nCompleted Tasks: `{user.completed_tasks_count}`" if lang == "en" else f"📋 *আমার টাস্ক স্ট্যাটাস*\n\nসম্পন্ন টাস্ক সংখ্যা: `{user.completed_tasks_count}`"
    await query.message.answer(msg, parse_mode=ParseMode.MARKDOWN)
    await query.answer()


@router.callback_query(F.data == "cab_lang")
async def cb_change_lang(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id))
    user = res.scalar_one_or_none()
    if not user:
        return
    
    new_lang = "bn" if user.language == "en" else "en"
    user.language = new_lang
    await session.commit()

    await query.message.answer(get_text(new_lang, "lang_changed"), reply_markup=get_main_keyboard(new_lang))
    await query.answer()


@router.callback_query(F.data == "cab_notif")
async def cb_notif(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == query.from_user.id))
    user = res.scalar_one_or_none()
    lang = user.language if user else "en"

    msg = "🔕 Notifications setting updated." if lang == "en" else "🔕 নোটিফিকেশন সেটিংস আপডেট করা হয়েছে।"
    await query.answer(msg, show_alert=True)


# --- TEXT MENU ROUTING ---

@router.message(F.text.in_(["💰 Earnings", "💰 আর্নিংস"]))
async def menu_earnings(message: Message, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == message.from_user.id))
    user = res.scalar_one_or_none()
    await message.answer(f"💰 Total Earned: `{user.total_earned:,.0f}` GRAM\n✨ Completed Tasks: `{user.completed_tasks_count}`", parse_mode=ParseMode.MARKDOWN)

@router.message(F.text.in_(["🛡 Subscription Check", "🛡 সাবস্ক্রিপশন চেক"]))
async def menu_sub_check(message: Message):
    await message.answer("🛡 Ensure the bot is added as an administrator to target channels/groups to properly verify subscriptions.")

@router.message(F.text.in_(["📊 Our Bots and Statistics", "📊 আমাদের বট ও পরিসংখ্যান"]))
async def menu_stats(message: Message, session: AsyncSession):
    res_users = await session.execute(select(func.count(User.id)))
    total_users = res_users.scalar() or 0
    await message.answer(f"📊 Live Platform Statistics:\n\n👥 Registered Users: `{total_users}`", parse_mode=ParseMode.MARKDOWN)

@router.message(F.text.in_(["🔗 Useful Links", "🔗 দরকারী লিংক"]))
async def menu_links(message: Message):
    await message.answer("🔗 Official channels and community support links will appear here.")

@router.message(F.text.in_(["ℹ️ Instruction", "ℹ️ নির্দেশিকা"]))
async def menu_instruction(message: Message):
    await message.answer("ℹ️ Use this bot to earn GRAM coins by completing tasks or create your own promotion campaigns easily.")

@router.message(F.text.in_(["📋 Checks", "📋 চেক্স"]))
async def menu_checks(message: Message):
    await message.answer("📋 No pending task checks at the moment.")


# ==============================================================================
# SECTION 9: FASTAPI & MAIN ENTRYPOINT
# ==============================================================================

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    await init_db()
    logger.info("Database initialized successfully.")

@app.get("/")
async def index():
    return {"status": "running", "bot": BOT_USERNAME}


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=RedisStorage(redis=redis_client))
    dp.include_router(router)

    @dp.update.outer_middleware()
    async def db_session_middleware(handler, event, data):
        async with AsyncSessionLocal() as session:
            data["session"] = session
            return await handler(event, data)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Starting bot polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")

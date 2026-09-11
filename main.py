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

# Business Logic Constants (User Requirements)
REFERRAL_COIN_REWARD = Decimal("5000")
REFERRAL_XP_REWARD = 500
STAR_TO_COIN_RATE = 5000  # 1 Telegram Star = 5000 Coins
PLATFORM_COMMISSION_PERCENT = Decimal("0.15")  # 15% Commission on Tasks
MIN_WITHDRAWAL = Decimal("50000")
MAX_WITHDRAWAL = Decimal("1000000")


# ==============================================================================
# SECTION 2: ENUMS
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
    WITHDRAWAL_REFUND = "withdrawal_refund"
    CAMPAIGN_PAYMENT = "campaign_payment"


# ==============================================================================
# SECTION 3: DATABASE MODELS
# ==============================================================================

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram ID
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    
    # Financial & Level System
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0")) # Coins
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    
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


# ==============================================================================
# SECTION 6: CALLBACK DATA & KEYBOARDS
# ==============================================================================

class TaskCallback(CallbackData, prefix="task"):
    action: str
    campaign_id: str

class PaginationCallback(CallbackData, prefix="page"):
    menu: str
    page: int

def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="🎯 Available Tasks"), KeyboardButton(text="💰 My Wallet")],
        [KeyboardButton(text="⭐ Top-up Coins (Stars)"), KeyboardButton(text="👥 Referral Program")],
        [KeyboardButton(text="📢 Create Promotion"), KeyboardButton(text="🏆 Leaderboard")]
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="👑 Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# ==============================================================================
# SECTION 7: CORE BUSINESS LOGIC & VERIFICATION ENGINE
# ==============================================================================

class SubscriptionVerifier:
    @staticmethod
    async def verify(bot: Bot, user_id: int, target_chat: Union[int, str]) -> bool:
        """
        যাচাই করে ইউজার চ্যানেল বা গ্রুপে সাবস্ক্রাইব করেছে কিনা।
        ইউজার নিজে অ্যাডমিন না হলেও, যদি বট ওই চ্যানেল/গ্রুপের অ্যাডমিন হয়, 
        তবে বট API দিয়ে চেক করতে পারবে ইউজার মেম্বার কি না।
        """
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
                # 1. Fetch Campaign
                res_c = await session.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
                campaign = res_c.scalar_one_or_none()

                if not campaign or campaign.status != CampaignStatus.ACTIVE:
                    return False, "❌ This task is no longer active."

                if campaign.completed_count >= campaign.max_completions:
                    campaign.status = CampaignStatus.COMPLETED
                    return False, "❌ Task limit has been reached."

                # 2. Fetch User
                res_u = await session.execute(select(User).where(User.id == user_id).with_for_update())
                user = res_u.scalar_one_or_none()

                # 3. Check Duplicate
                res_comp = await session.execute(select(TaskCompletion).where(
                    TaskCompletion.user_id == user_id, TaskCompletion.campaign_id == campaign_id
                ))
                if res_comp.scalar_one_or_none():
                    return False, "❌ You have already completed this task!"

                # 4. Task Specific Verification
                target = campaign.target_chat_id or campaign.target_username
                if campaign.task_type in [TaskType.CHANNEL_SUB, TaskType.GROUP_JOIN]:
                    if target:
                        is_valid = await SubscriptionVerifier.verify(bot, user_id, target)
                        if not is_valid:
                            return False, "❌ Verification failed! Please make sure you joined the channel/group."

                # 5. Reward Calculation & Distribution
                reward = campaign.reward_per_user
                
                completion = TaskCompletion(user_id=user_id, campaign_id=campaign_id, reward=reward)
                session.add(completion)

                user.balance += reward
                user.total_earned += reward
                user.completed_tasks_count += 1
                
                # Update XP & Level (Level up logic: every 10,000 XP = +1 Level)
                user.xp += 50
                user.level = 1 + (user.xp // 10000)

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
            return True, f"🎉 Task Verified Successfully!\n💰 You earned: +`{reward:,.0f}` Coins\n⚡ XP gained: +50"

        except Exception as e:
            await session.rollback()
            logger.error(f"Task verification error: {str(e)}")
            return False, "❌ An error occurred during verification."


# ==============================================================================
# SECTION 8: BOT HANDLERS (USER FLOWS)
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
            referred_by=referrer_id
        )
        session.add(user)

        # Referral reward: 5000 Coins + 500 XP
        if referrer_id:
            res_referrer = await session.execute(select(User).where(User.id == referrer_id))
            referrer = res_referrer.scalar_one_or_none()
            if referrer:
                referrer.balance += REFERRAL_COIN_REWARD
                referrer.xp += REFERRAL_XP_REWARD
                referrer.level = 1 + (referrer.xp // 10000)
                
                tx = Transaction(
                    user_id=referrer.id, amount=REFERRAL_COIN_REWARD,
                    type=TransactionType.REFERRAL_REWARD,
                    description=f"Referral bonus for inviting {message.from_user.first_name}"
                )
                session.add(tx)

        await session.commit()

    is_admin = user_id in ADMIN_IDS
    welcome_text = (
        f"👋 Welcome to *{BOT_USERNAME}*, {message.from_user.first_name}!\n\n"
        f"🚀 Complete tasks to earn coins, level up, and promote your own channels/groups instantly!\n\n"
        f"📌 Use the menu below to navigate:"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(is_admin), parse_mode=ParseMode.MARKDOWN)


# --- WALLET & STARS TOPUP ---

@router.message(F.text == "💰 My Wallet")
async def show_wallet(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    msg = (
        f"💰 *Your Wallet & Profile*\n\n"
        f"🪙 *Balance:* `{user.balance:,.0f}` Coins\n"
        f"⚡ *XP:* `{user.xp:,}` | 🏆 *Level:* `{user.level}`\n"
        f"📈 *Total Earned:* `{user.total_earned:,.0f}` Coins\n"
        f"✅ *Completed Tasks:* `{user.completed_tasks_count}`"
    )
    await message.answer(msg, parse_mode=ParseMode.MARKDOWN)

@router.message(F.text == "⭐ Top-up Coins (Stars)")
async def prompt_star_topup(message: Message, state: FSMContext):
    msg = (
        f"⭐ *Telegram Stars Top-Up*\n\n"
        f"Rate: `1 Star = {STAR_TO_COIN_RATE:,} Coins`\n\n"
        f"Please enter the number of Telegram Stars you want to pay (e.g., 10, 50, 100):"
    )
    await message.answer(msg, parse_mode=ParseMode.MARKDOWN)
    await state.set_state(TopUpState.amount_stars)

@router.message(TopUpState.amount_stars)
async def process_star_invoice(message: Message, state: FSMContext):
    try:
        stars = int(message.text.strip())
        if stars <= 0: raise ValueError()
    except Exception:
        await message.answer("❌ Invalid input. Please enter a valid number of stars.")
        return

    await state.clear()
    total_coins = stars * STAR_TO_COIN_RATE

    prices = [LabeledPrice(label=f"{total_coins:,} Coins", amount=stars)] # 1 Star = 1 XTR (Telegram Stars currency is XTR)
    
    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title="Coin Top-Up",
        description=f"Top up {total_coins:,} Coins using Telegram Stars",
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
                    description=f"Purchased {coins_to_add:,.0f} coins via {stars} Telegram Stars"
                )
                session.add(tx)

        await session.commit()
        await message.answer(f"✅ *Top-up Successful!*\n\nAdded `+{coins_to_add:,.0f}` Coins to your balance.", parse_mode=ParseMode.MARKDOWN)


# --- REFERRAL MENU ---

@router.message(F.text == "👥 Referral Program")
async def show_referral(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    res_count = await session.execute(select(func.count(User.id)).where(User.referred_by == user_id))
    invited_count = res_count.scalar()

    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.referral_code}"

    msg = (
        f"👥 *Referral Program*\n\n"
        f"Invite your friends and earn:\n"
        f"💰 *{REFERRAL_COIN_REWARD:,.0f} Coins* + *{REFERRAL_XP_REWARD} XP* per referral!\n\n"
        f"📊 *Total Friends Invited:* `{invited_count}`\n\n"
        f"🔗 *Your Referral Link:*\n`{ref_link}`"
    )
    await message.answer(msg, parse_mode=ParseMode.MARKDOWN)


# --- TASK LISTING & EXECUTION ---

@router.message(F.text == "🎯 Available Tasks")
async def show_tasks_menu(message: Message, session: AsyncSession):
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
        text = "🎯 *Available Tasks*\n\nNo active tasks found right now. Check back later or create your own!"
        if isinstance(event, Message):
            await event.answer(text, parse_mode=ParseMode.MARKDOWN)
        else:
            await event.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    buttons = []
    for c in campaigns:
        buttons.append([InlineKeyboardButton(
            text=f"📢 {c.title} (+{c.reward_per_user:,.0f} Coins)",
            callback_data=TaskCallback(action="view", campaign_id=c.id).pack()
        )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = "🎯 *Available Tasks*\nSelect a task below to start earning:"
    
    if isinstance(event, Message):
        await event.answer(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await event.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.callback_query(TaskCallback.filter(F.action == "view"))
async def view_task_detail(query: CallbackQuery, callback_data: TaskCallback, session: AsyncSession):
    res = await session.execute(select(Campaign).where(Campaign.id == callback_data.campaign_id))
    campaign = res.scalar_one_or_none()

    if not campaign or campaign.status != CampaignStatus.ACTIVE:
        await query.answer("Task is no longer available.", show_alert=True)
        return

    target_url = campaign.target_link or (f"https://t.me/{campaign.target_username}" if campaign.target_username else f"https://t.me/{BOT_USERNAME}")

    text = (
        f"📢 *Task: {campaign.title}*\n\n"
        f"🔹 *Type:* `{campaign.task_type.value.upper()}`\n"
        f"💰 *Reward:* `{campaign.reward_per_user:,.0f}` Coins\n"
        f"👥 *Slots Remaining:* `{campaign.max_completions - campaign.completed_count}`"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Open / Start Task", url=target_url)],
        [InlineKeyboardButton(text="✅ Verify Task", callback_data=TaskCallback(action="verify", campaign_id=campaign.id).pack())],
        [InlineKeyboardButton(text="⬅️ Back to Tasks", callback_data="tasks_back")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.callback_query(TaskCallback.filter(F.action == "verify"))
async def verify_task_callback(query: CallbackQuery, callback_data: TaskCallback, session: AsyncSession, bot: Bot):
    success, message = await TaskEngine.process_task_completion(
        session=session, bot=bot, user_id=query.from_user.id, campaign_id=callback_data.campaign_id
    )
    if success:
        await query.answer("Task completed successfully!", show_alert=True)
        await query.message.edit_text(message, parse_mode=ParseMode.MARKDOWN)
    else:
        await query.answer(message, show_alert=True)

@router.callback_query(F.data == "tasks_back")
async def tasks_back_handler(query: CallbackQuery, session: AsyncSession):
    await render_tasks_page(query.from_user.id, query, session, page=0)


# --- ADVERTISER CAMPAIGN CREATION (15% Commission & Non-Admin Support) ---

@router.message(F.text == "📢 Create Promotion")
async def start_campaign_creation(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Channel Sub", callback_data="ctype:channel_sub"), InlineKeyboardButton(text="👥 Group Join", callback_data="ctype:group_join")],
        [InlineKeyboardButton(text="👀 Post View", callback_data="ctype:post_view"), InlineKeyboardButton(text="🤖 Bot Start", callback_data="ctype:bot_start")],
        [InlineKeyboardButton(text="🌐 Web App / Custom", callback_data="ctype:web_app")]
    ])
    await message.answer("📢 *Create Promotion Campaign*\n\nSelect campaign type:\n*(Note: ইউজার গ্রুপের অ্যাডমিন না হলেও, যদি বট গ্রুপ/চ্যানেলে অ্যাডমিন থাকে তবেই ক্যাম্পেইন সফলভাবে কাজ করবে)*", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    await state.set_state(CampaignCreationState.task_type)

@router.callback_query(CampaignCreationState.task_type, F.data.startswith("ctype:"))
async def campaign_type_selected(query: CallbackQuery, state: FSMContext):
    t_type = query.data.split(":")[1]
    await state.update_data(task_type=t_type)
    await query.message.edit_text("📝 Enter Campaign Title (e.g., 'Join My Awesome Channel'):")
    await state.set_state(CampaignCreationState.title)

@router.message(CampaignCreationState.title)
async def campaign_title_entered(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("🔗 Enter Target Link or Username (e.g., `@mychannel` or `https://t.me/...`):")
    await state.set_state(CampaignCreationState.target)

@router.message(CampaignCreationState.target)
async def campaign_target_entered(message: Message, state: FSMContext):
    await state.update_data(target=message.text.strip())
    await message.answer("💰 Enter reward per user (in Coins):")
    await state.set_state(CampaignCreationState.reward)

@router.message(CampaignCreationState.reward)
async def campaign_reward_entered(message: Message, state: FSMContext):
    try:
        reward = Decimal(message.text.strip())
        if reward <= 0: raise ValueError()
    except Exception:
        await message.answer("❌ Invalid reward. Must be greater than 0.")
        return
    await state.update_data(reward=str(reward))
    await message.answer("👥 Enter total target completions count (e.g., 100):")
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
    
    # Calculation with 15% Platform Commission
    base_budget = reward_per_user * max_comp
    commission = base_budget * PLATFORM_COMMISSION_PERCENT
    total_cost = base_budget + commission

    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    if user.balance < total_cost:
        await message.answer(
            f"❌ *Insufficient Balance*\n\n"
            f"Base Cost: `{base_budget:,.0f}` Coins\n"
            f"Platform Commission (15%): `{commission:,.0f}` Coins\n"
            f"Total Required: `{total_cost:,.0f}` Coins\n"
            f"Your Balance: `{user.balance:,.0f}` Coins",
            parse_mode=ParseMode.MARKDOWN
        )
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

        tx = Transaction(
            user_id=user_id, amount=-total_cost,
            type=TransactionType.CAMPAIGN_PAYMENT,
            description=f"Created campaign: {data['title']} (includes 15% fee)"
        )
        session.add(tx)

    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ *Campaign Launched Successfully!*\n\n"
        f"📢 *Title:* {data['title']}\n"
        f"💰 *Total Deducted (incl. 15% fee):* `{total_cost:,.0f}` Coins\n"
        f"🚀 Status: Active",
        parse_mode=ParseMode.MARKDOWN
    )


# --- GENERAL LEADERBOARD ---

@router.message(F.text == "🏆 Leaderboard")
async def show_leaderboard(message: Message, session: AsyncSession):
    stmt = select(User).order_by(User.total_earned.desc()).limit(10)
    res = await session.execute(stmt)
    top_users = res.scalars().all()

    text = "🏆 *Top Earners Leaderboard*\n\n"
    for i, u in enumerate(top_users, 1):
        name = u.first_name or "User"
        text += f"{i}. *{name}* — `🪙 {u.total_earned:,.0f}` (Lvl {u.level})\n"

    await message.answer(text, parse_mode=ParseMode.MARKDOWN)


# ==============================================================================
# SECTION 9: FASTAPI & APP INITIALIZATION (POLLING / WEBHOOK)
# ==============================================================================

fastapi_app = FastAPI(title="BoostGram Health Check")

@fastapi_app.get("/health")
async def health_check():
    return {"status": "active", "timestamp": datetime.now(timezone.utc).isoformat()}

async def start_bot():
    bot = Bot(token=BOT_TOKEN)
    storage = RedisStorage(redis=redis_client)
    dp = Dispatcher(storage=storage)

    # Middleware Registration
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

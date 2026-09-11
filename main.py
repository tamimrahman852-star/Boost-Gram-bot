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
from contextlib import asynccontextmanager

# Third-party Imports
from dotenv import load_dotenv
import redis.asyncio as aioredis

from sqlalchemy import (
    BigInteger, String, Numeric, Boolean, DateTime,
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
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, TelegramObject
)
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError

import uvicorn
from fastapi import FastAPI


# ==============================================================================
# SECTION 1: CONFIGURATION
# ==============================================================================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger("TelegramEarningBot")

# Environment Variables Validation
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")

missing_vars = []
if not BOT_TOKEN: missing_vars.append("BOT_TOKEN")
if not BOT_USERNAME: missing_vars.append("BOT_USERNAME")
if not DATABASE_URL: missing_vars.append("DATABASE_URL")
if not REDIS_URL: missing_vars.append("REDIS_URL")

if missing_vars:
    logger.critical(f"CRITICAL ERROR: Missing required environment variables: {', '.join(missing_vars)}")
    sys.exit(1)

try:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()]
except ValueError:
    logger.critical("CRITICAL ERROR: ADMIN_IDS must be a comma-separated list of Telegram user IDs.")
    sys.exit(1)

MIN_WITHDRAWAL = Decimal(os.getenv("MIN_WITHDRAWAL", "1000"))
MAX_WITHDRAWAL = Decimal(os.getenv("MAX_WITHDRAWAL", "100000"))
REFERRAL_REWARD = Decimal(os.getenv("REFERRAL_REWARD", "100"))
PLATFORM_FEE = Decimal(os.getenv("PLATFORM_FEE", "0"))
MAINTENANCE_MODE = os.getenv("MAINTENANCE_MODE", "false").lower() == "true"
BOT_MODE = os.getenv("BOT_MODE", "polling").lower()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


# ==============================================================================
# SECTION 2: ENUMS
# ==============================================================================

class TaskType(str, Enum):
    CHANNEL_SUB = "channel_sub"
    GROUP_JOIN = "group_join"
    BOT_START = "bot_start"
    POST_VIEW = "post_view"
    CUSTOM = "custom"

class CampaignStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

class TransactionType(str, Enum):
    TASK_REWARD = "task_reward"
    REFERRAL_REWARD = "referral_reward"
    BONUS = "bonus"
    WITHDRAWAL = "withdrawal"
    WITHDRAWAL_REFUND = "withdrawal_refund"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    CAMPAIGN_PAYMENT = "campaign_payment"
    CAMPAIGN_REFUND = "campaign_refund"

class WithdrawalStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

class PaymentStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


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
    language: Mapped[str] = mapped_column(String(10), default="en")
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    total_earned: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    total_withdrawn: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    completed_tasks_count: Mapped[int] = mapped_column(default=0)
    referral_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    referred_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_activity: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    completions = relationship("TaskCompletion", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")
    withdrawals = relationship("Withdrawal", back_populates="user")
    campaigns = relationship("Campaign", back_populates="advertiser")

class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    advertiser_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    task_type: Mapped[TaskType] = mapped_column(SQLEnum(TaskType))
    target_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    target_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_link: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reward_per_user: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    max_completions: Mapped[int] = mapped_column()
    completed_count: Mapped[int] = mapped_column(default=0)
    total_budget: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    spent_budget: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    status: Mapped[CampaignStatus] = mapped_column(SQLEnum(CampaignStatus), default=CampaignStatus.DRAFT)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    advertiser = relationship("User", back_populates="campaigns")
    completions = relationship("TaskCompletion", back_populates="campaign")

class TaskCompletion(Base):
    __tablename__ = "task_completions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"))
    reward: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", back_populates="completions")
    campaign = relationship("Campaign", back_populates="completions")

    __table_args__ = (
        UniqueConstraint('user_id', 'campaign_id', name='uq_user_campaign_completion'),
    )

class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    type: Mapped[TransactionType] = mapped_column(SQLEnum(TransactionType))
    description: Mapped[str] = mapped_column(String(255))
    reference_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", back_populates="transactions")

class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    method: Mapped[str] = mapped_column(String(64))
    payout_details: Mapped[str] = mapped_column(String(255))
    status: Mapped[WithdrawalStatus] = mapped_column(SQLEnum(WithdrawalStatus), default=WithdrawalStatus.PENDING)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", back_populates="withdrawals")

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    admin_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(128))
    target_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    details: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ==============================================================================
# SECTION 4: DATABASE & REDIS SETUP
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

class CreateCampaignState(StatesGroup):
    title = State()
    task_type = State()
    target = State()
    reward = State()
    max_completions = State()
    confirm = State()

class WithdrawState(StatesGroup):
    method = State()
    amount = State()
    details = State()
    confirm = State()

class AdminBroadcastState(StatesGroup):
    message = State()
    confirm = State()

class AdminUserSearchState(StatesGroup):
    query = State()
    adjust_balance = State()


# ==============================================================================
# SECTION 6: CALLBACK DATA STRUCTS
# ==============================================================================

class TaskCallback(CallbackData, prefix="task"):
    action: str
    campaign_id: str

class PaginationCallback(CallbackData, prefix="page"):
    menu: str
    page: int

class AdminActionCallback(CallbackData, prefix="adm"):
    action: str
    target_id: Optional[str] = None


# ==============================================================================
# SECTION 7: MIDDLEWARE
# ==============================================================================

class DatabaseMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with AsyncSessionLocal() as session:
            data["session"] = session
            return await handler(event, data)

class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = None
        if isinstance(event, (Message, CallbackQuery)):
            user_id = event.from_user.id

        if MAINTENANCE_MODE and user_id not in ADMIN_IDS:
            msg = "🛠 *System Maintenance*\n\nThe bot is currently undergoing scheduled maintenance. Please check back later!"
            if isinstance(event, Message):
                await event.answer(msg, parse_mode=ParseMode.MARKDOWN)
            elif isinstance(event, CallbackQuery):
                await event.answer("Bot is under maintenance.", show_alert=True)
            return

        return await handler(event, data)

class UserActivityMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        session: AsyncSession = data.get("session")
        user = None
        if isinstance(event, (Message, CallbackQuery)):
            u = event.from_user
            if session:
                res = await session.execute(select(User).where(User.id == u.id))
                user = res.scalar_one_or_none()
                if user:
                    if user.is_blocked:
                        if isinstance(event, CallbackQuery):
                            await event.answer("Your account is blocked.", show_alert=True)
                        return
                    user.last_activity = datetime.now(timezone.utc)
                    await session.commit()
        return await handler(event, data)


# ==============================================================================
# SECTION 8: KEYBOARDS
# ==============================================================================

def get_main_menu_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="🎯 Tasks"), KeyboardButton(text="💰 Wallet")],
        [KeyboardButton(text="👥 Referral"), KeyboardButton(text="📢 Advertiser Panel")],
        [KeyboardButton(text="📊 Statistics"), KeyboardButton(text="🏆 Leaderboard")],
        [KeyboardButton(text="💸 Withdraw"), KeyboardButton(text="ℹ️ Help")]
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="👑 Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_advertiser_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Create Campaign", callback_data="adv:create")],
        [InlineKeyboardButton(text="📋 My Campaigns", callback_data="adv:list:0")],
        [InlineKeyboardButton(text="📊 Campaign Stats", callback_data="adv:stats")]
    ])

def get_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Users", callback_data="adm:users"), InlineKeyboardButton(text="💸 Withdrawals", callback_data="adm:withdrawals")],
        [InlineKeyboardButton(text="📢 Campaigns", callback_data="adm:campaigns"), InlineKeyboardButton(text="📣 Broadcast", callback_data="adm:broadcast")],
        [InlineKeyboardButton(text="📊 Platform Stats", callback_data="adm:stats")]
    ])


# ==============================================================================
# SECTION 9: SUBSCRIPTION & TASK ENGINE
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
            logger.warning(f"Failed to check membership for user {user_id} in {target_chat}: {str(e)}")
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
        
        # 1. Check Rate Limiting in Redis
        lock_key = f"lock:task:{user_id}:{campaign_id}"
        acquired = await redis_client.set(lock_key, "1", nx=True, ex=10)
        if not acquired:
            return False, "⏳ Processing request, please wait..."

        try:
            # Begin Database Atomic Transaction Block
            async with session.begin_nested():
                # Fetch Campaign with Row Locking
                res = await session.execute(
                    select(Campaign).where(Campaign.id == campaign_id).with_for_update()
                )
                campaign = res.scalar_one_or_none()

                if not campaign or campaign.status != CampaignStatus.ACTIVE:
                    return False, "❌ Task is no longer active."

                if campaign.completed_count >= campaign.max_completions:
                    campaign.status = CampaignStatus.COMPLETED
                    return False, "❌ Task limit has been reached."

                # Fetch User with Row Locking
                res_u = await session.execute(
                    select(User).where(User.id == user_id).with_for_update()
                )
                user = res_u.scalar_one_or_none()
                if not user:
                    return False, "❌ User not found."

                # Check Duplicate Completion
                res_comp = await session.execute(
                    select(TaskCompletion).where(
                        TaskCompletion.user_id == user_id,
                        TaskCompletion.campaign_id == campaign_id
                    )
                )
                if res_comp.scalar_one_or_none():
                    return False, "❌ You have already completed this task and received your reward!"

                # Perform Verification based on Task Type
                target = campaign.target_chat_id or campaign.target_username
                if campaign.task_type in [TaskType.CHANNEL_SUB, TaskType.GROUP_JOIN]:
                    if not target:
                        return False, "❌ Campaign target is misconfigured."
                    is_valid = await SubscriptionVerifier.verify(bot, user_id, target)
                    if not is_valid:
                        return False, "❌ Verification failed. Please ensure you have joined the channel/group!"
                else:
                    # Generic fallback verification
                    is_valid = True

                # Atomic Rewards & Budget Allocation
                reward = campaign.reward_per_user

                # Record Completion
                completion = TaskCompletion(
                    user_id=user_id,
                    campaign_id=campaign_id,
                    reward=reward
                )
                session.add(completion)

                # Credit Wallet
                user.balance += reward
                user.total_earned += reward
                user.completed_tasks_count += 1

                # Create Transaction
                tx = Transaction(
                    user_id=user_id,
                    amount=reward,
                    type=TransactionType.TASK_REWARD,
                    description=f"Reward for completing: {campaign.title}",
                    reference_id=campaign_id
                )
                session.add(tx)

                # Update Campaign
                campaign.completed_count += 1
                campaign.spent_budget += reward

                if campaign.completed_count >= campaign.max_completions or (campaign.total_budget - campaign.spent_budget) < reward:
                    campaign.status = CampaignStatus.COMPLETED

            await session.commit()
            return True, f"🎉 Task Verified! You earned 💰 {reward:,.2f} points!"

        except Exception as e:
            await session.rollback()
            logger.error(f"Error during task completion processing: {str(e)}")
            return False, "❌ An error occurred during verification. Duplicate reward prevented."


# ==============================================================================
# SECTION 10: USER HANDLERS
# ==============================================================================

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    referrer_id = None

    if len(args) > 1 and args[1].startswith("ref_"):
        ref_code = args[1].replace("ref_", "").strip()
        res = await session.execute(select(User).where(User.referral_code == ref_code))
        ref_user = res.scalar_one_or_none()
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

        # Handle Referral Bonus if applicable
        if referrer_id:
            res_ref = await session.execute(select(User).where(User.id == referrer_id))
            referrer = res_ref.scalar_one_or_none()
            if referrer:
                referrer.balance += REFERRAL_REWARD
                referrer.total_earned += REFERRAL_REWARD
                tx = Transaction(
                    user_id=referrer.id,
                    amount=REFERRAL_REWARD,
                    type=TransactionType.REFERRAL_REWARD,
                    description=f"Referral reward for inviting {message.from_user.first_name}",
                    reference_id=str(user_id)
                )
                session.add(tx)

        await session.commit()

    is_admin = user_id in ADMIN_IDS
    welcome_text = (
        f"👋 Welcome {message.from_user.first_name} to *{BOT_USERNAME}*!\n\n"
        "🎯 Complete simple tasks to earn rewards.\n"
        "📢 Promote your own channels/groups instantly!"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu_keyboard(is_admin), parse_mode=ParseMode.MARKDOWN)

@router.message(F.text == "🎯 Tasks")
async def show_tasks(message: Message, session: AsyncSession):
    await display_tasks_page(message.from_user.id, message, session, page=0)

async def display_tasks_page(user_id: int, message_or_query: Union[Message, CallbackQuery], session: AsyncSession, page: int = 0):
    limit = 5
    offset = page * limit

    # Query Active Campaigns not yet completed by User
    completed_sub = select(TaskCompletion.campaign_id).where(TaskCompletion.user_id == user_id)
    
    stmt = (
        select(Campaign)
        .where(
            Campaign.status == CampaignStatus.ACTIVE,
            Campaign.id.not_in(completed_sub)
        )
        .offset(offset)
        .limit(limit)
    )
    res = await session.execute(stmt)
    campaigns = res.scalars().all()

    if not campaigns:
        msg = "🎯 *Available Tasks*\n\nThere are currently no active tasks available. Check back soon!"
        if isinstance(message_or_query, Message):
            await message_or_query.answer(msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await message_or_query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    buttons = []
    for c in campaigns:
        title = f"📢 {c.title} (+{c.reward_per_user:,.0f} pts)"
        buttons.append([InlineKeyboardButton(text=title, callback_data=TaskCallback(action="view", campaign_id=c.id).pack())])

    # Navigation
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=PaginationCallback(menu="tasks", page=page-1).pack()))
    nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=PaginationCallback(menu="tasks", page=page+1).pack()))
    buttons.append(nav)

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    msg = "🎯 *Available Tasks*\nSelect a task below to complete:"
    if isinstance(message_or_query, Message):
        await message_or_query.answer(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await message_or_query.message.edit_text(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.callback_query(TaskCallback.filter(F.action == "view"))
async def view_task(query: CallbackQuery, callback_data: TaskCallback, session: AsyncSession):
    res = await session.execute(select(Campaign).where(Campaign.id == callback_data.campaign_id))
    campaign = res.scalar_one_or_none()

    if not campaign or campaign.status != CampaignStatus.ACTIVE:
        await query.answer("Task is no longer available.", show_alert=True)
        return

    if campaign.target_username and not campaign.target_username.startswith("@"):
        target_url = f"https://t.me/{campaign.target_username}"
    elif campaign.target_link:
        target_url = campaign.target_link
    else:
        target_url = f"https://t.me/{BOT_USERNAME}"

    msg = (
        f"📢 *Task Details: {campaign.title}*\n\n"
        f"📜 *Description:* {campaign.description or 'No description'}\n"
        f"💰 *Reward:* {campaign.reward_per_user:,.2f} points\n"
        f"👥 *Remaining:* {campaign.max_completions - campaign.completed_count:,}\n"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Open Link / Join", url=target_url)],
        [InlineKeyboardButton(text="✅ Check Subscription", callback_data=TaskCallback(action="check", campaign_id=campaign.id).pack())],
        [InlineKeyboardButton(text="⬅️ Back", callback_data=PaginationCallback(menu="tasks", page=0).pack())]
    ])

    await query.message.edit_text(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.callback_query(TaskCallback.filter(F.action == "check"))
async def check_task(query: CallbackQuery, callback_data: TaskCallback, session: AsyncSession, bot: Bot):
    user_id = query.from_user.id
    success, message = await TaskEngine.process_task_completion(
        session=session,
        bot=bot,
        user_id=user_id,
        campaign_id=callback_data.campaign_id
    )

    if success:
        await query.answer("Verified!", show_alert=False)
        await query.message.edit_text(message, parse_mode=ParseMode.MARKDOWN)
    else:
        await query.answer(message, show_alert=True)

@router.message(F.text == "💰 Wallet")
async def show_wallet(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    msg = (
        f"💰 *Your Balance & Wallet*\n\n"
        f"👤 *User:* {user.first_name}\n"
        f"💳 *Current Balance:* `{user.balance:,.2f}` points\n"
        f"📈 *Total Earned:* `{user.total_earned:,.2f}` points\n"
        f"💸 *Total Withdrawn:* `{user.total_withdrawn:,.2f}` points\n"
        f"✅ *Completed Tasks:* `{user.completed_tasks_count}`"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Withdraw Funds", callback_data="wallet:withdraw")],
        [InlineKeyboardButton(text="📜 Transaction History", callback_data="wallet:history:0")]
    ])

    await message.answer(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.message(F.text == "👥 Referral")
async def show_referral(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    res_count = await session.execute(select(func.count(User.id)).where(User.referred_by == user_id))
    invited_count = res_count.scalar()

    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.referral_code}"

    msg = (
        f"👥 *Referral Program*\n\n"
        f"Invite friends and earn *{REFERRAL_REWARD:,.0f} points* for every active user that joins!\n\n"
        f"📊 *Your Invites:* `{invited_count}` users\n"
        f"🔗 *Your Referral Link:*\n`{ref_link}`"
    )

    await message.answer(msg, parse_mode=ParseMode.MARKDOWN)

@router.message(F.text == "📊 Statistics")
async def show_statistics(message: Message, session: AsyncSession):
    res_u = await session.execute(select(func.count(User.id)))
    total_users = res_u.scalar()

    res_c = await session.execute(select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.ACTIVE))
    active_campaigns = res_c.scalar()

    res_comp = await session.execute(select(func.count(TaskCompletion.id)))
    total_completions = res_comp.scalar()

    msg = (
        f"📊 *Platform Statistics*\n\n"
        f"👥 *Total Registered Users:* `{total_users:,}`\n"
        f"📢 *Active Campaigns:* `{active_campaigns:,}`\n"
        f"✅ *Tasks Completed:* `{total_completions:,}`"
    )

    await message.answer(msg, parse_mode=ParseMode.MARKDOWN)

@router.message(F.text == "🏆 Leaderboard")
async def show_leaderboard(message: Message, session: AsyncSession):
    stmt = select(User).order_by(User.total_earned.desc()).limit(10)
    res = await session.execute(stmt)
    top_users = res.scalars().all()

    msg = "🏆 *Top Earners Leaderboard*\n\n"
    for idx, u in enumerate(top_users, 1):
        name = u.first_name or u.username or "Anonymous"
        msg += f"{idx}. *{name}* — `{u.total_earned:,.2f}` points\n"

    await message.answer(msg, parse_mode=ParseMode.MARKDOWN)


# ==============================================================================
# SECTION 11: WITHDRAWAL SYSTEM
# ==============================================================================

@router.message(F.text == "💸 Withdraw")
@router.callback_query(F.data == "wallet:withdraw")
async def init_withdrawal(event: Union[Message, CallbackQuery], state: FSMContext, session: AsyncSession):
    user_id = event.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    if user.balance < MIN_WITHDRAWAL:
        msg = f"❌ *Insufficient Balance*\n\nMinimum withdrawal is `{MIN_WITHDRAWAL:,.0f}` points. Your balance: `{user.balance:,.2f}` points."
        if isinstance(event, Message):
            await event.answer(msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await event.answer(msg, show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="TON Wallet", callback_data="method:TON"), InlineKeyboardButton(text="USDT (TRC20)", callback_data="method:USDT")],
        [InlineKeyboardButton(text="Payeer", callback_data="method:Payeer")]
    ])

    msg = "💸 *Withdrawal Request*\nSelect your preferred payment method:"
    if isinstance(event, Message):
        await event.answer(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await event.message.edit_text(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    await state.set_state(WithdrawState.method)

@router.callback_query(WithdrawState.method, F.data.startswith("method:"))
async def select_withdraw_method(query: CallbackQuery, state: FSMContext):
    method = query.data.split(":")[1]
    await state.update_data(method=method)

    await query.message.edit_text(
        f"💳 Method Selected: *{method}*\n\nPlease enter the amount you wish to withdraw (Min: `{MIN_WITHDRAWAL:,.0f}`):",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(WithdrawState.amount)

@router.message(WithdrawState.amount)
async def process_withdraw_amount(message: Message, state: FSMContext, session: AsyncSession):
    try:
        amount = Decimal(message.text.strip())
    except Exception:
        await message.answer("❌ Invalid amount. Please enter a numerical value.")
        return

    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    if amount < MIN_WITHDRAWAL or amount > MAX_WITHDRAWAL or amount > user.balance:
        await message.answer(f"❌ Invalid amount. Ensure it is within bounds and doesn't exceed your balance (`{user.balance:,.2f}`).")
        return

    await state.update_data(amount=str(amount))
    await message.answer("📝 Enter your payment destination address / account details:")
    await state.set_state(WithdrawState.details)

@router.message(WithdrawState.details)
async def process_withdraw_details(message: Message, state: FSMContext, session: AsyncSession):
    details = message.text.strip()
    data = await state.get_data()
    amount = Decimal(data["amount"])
    method = data["method"]
    user_id = message.from_user.id

    async with session.begin_nested():
        res = await session.execute(select(User).where(User.id == user_id).with_for_update())
        user = res.scalar_one_or_none()

        if user.balance < amount:
            await message.answer("❌ Balance error. Transaction cancelled.")
            await state.clear()
            return

        user.balance -= amount
        withdrawal = Withdrawal(
            user_id=user_id,
            amount=amount,
            method=method,
            payout_details=details,
            status=WithdrawalStatus.PENDING
        )
        session.add(withdrawal)

        tx = Transaction(
            user_id=user_id,
            amount=-amount,
            type=TransactionType.WITHDRAWAL,
            description=f"Withdrawal request via {method}",
            reference_id=withdrawal.id
        )
        session.add(tx)

    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ *Withdrawal Request Submitted!*\n\n"
        f"💰 *Amount:* `{amount:,.2f}`\n"
        f"💳 *Method:* `{method}`\n"
        f"📋 *Details:* `{details}`\n\n"
        "Your request is now pending manual admin approval.",
        parse_mode=ParseMode.MARKDOWN
    )


# ==============================================================================
# SECTION 12: ADVERTISER PANEL & CAMPAIGN CREATION
# ==============================================================================

@router.message(F.text == "📢 Advertiser Panel")
async def show_advertiser_panel(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    msg = (
        f"📢 *Advertiser Dashboard*\n\n"
        f"Create campaigns to promote channels, groups, or links.\n"
        f"💰 *Available Account Balance:* `{user.balance:,.2f}` points"
    )

    await message.answer(msg, reply_markup=get_advertiser_keyboard(), parse_mode=ParseMode.MARKDOWN)

@router.callback_query(F.data == "adv:create")
async def start_campaign_creation(query: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Channel Sub", callback_data="type:channel_sub"), InlineKeyboardButton(text="👥 Group Join", callback_data="type:group_join")],
        [InlineKeyboardButton(text="🔗 Custom Link", callback_data="type:custom")]
    ])
    await query.message.edit_text("🎯 Select Task Type for Campaign:", reply_markup=kb)
    await state.set_state(CreateCampaignState.task_type)

@router.callback_query(CreateCampaignState.task_type, F.data.startswith("type:"))
async def process_campaign_type(query: CallbackQuery, state: FSMContext):
    task_type = query.data.split(":")[1]
    await state.update_data(task_type=task_type)

    await query.message.edit_text("📝 Enter Campaign Title:")
    await state.set_state(CreateCampaignState.title)

@router.message(CreateCampaignState.title)
async def process_campaign_title(message: Message, state: FSMContext):
    title = message.text.strip()
    await state.update_data(title=title)

    await message.answer("🔗 Enter Channel/Group Username (e.g. `@mychannel`) or full URL link:")
    await state.set_state(CreateCampaignState.target)

@router.message(CreateCampaignState.target)
async def process_campaign_target(message: Message, state: FSMContext):
    target = message.text.strip()
    await state.update_data(target=target)

    await message.answer("💰 Enter reward per user (in points):")
    await state.set_state(CreateCampaignState.reward)

@router.message(CreateCampaignState.reward)
async def process_campaign_reward(message: Message, state: FSMContext):
    try:
        reward = Decimal(message.text.strip())
        if reward <= 0: raise ValueError()
    except Exception:
        await message.answer("❌ Invalid reward value. Must be greater than 0.")
        return

    await state.update_data(reward=str(reward))
    await message.answer("👥 Enter maximum number of user completions target:")
    await state.set_state(CreateCampaignState.max_completions)

@router.message(CreateCampaignState.max_completions)
async def process_campaign_max_completions(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    try:
        max_comp = int(message.text.strip())
        if max_comp <= 0: raise ValueError()
    except Exception:
        await message.answer("❌ Invalid integer value.")
        return

    data = await state.get_data()
    reward = Decimal(data["reward"])
    total_budget = reward * max_comp
    user_id = message.from_user.id

    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()

    if user.balance < total_budget:
        await message.answer(
            f"❌ *Insufficient Balance*\n\nRequired Budget: `{total_budget:,.2f}` points\nYour Balance: `{user.balance:,.2f}` points\n\nPlease earn or top-up balance first.",
            parse_mode=ParseMode.MARKDOWN
        )
        await state.clear()
        return

    target = data["target"]
    target_username = None

    if target.startswith("@"):
        target_username = target.replace("@", "")
    elif "t.me/" in target:
        target_username = target.split("t.me/")[1].replace("/", "")

    async with session.begin_nested():
        user.balance -= total_budget

        campaign = Campaign(
            advertiser_id=user_id,
            title=data["title"],
            task_type=TaskType(data["task_type"]),
            target_username=target_username,
            target_link=target if not target_username else None,
            reward_per_user=reward,
            max_completions=max_comp,
            total_budget=total_budget,
            status=CampaignStatus.ACTIVE
        )
        session.add(campaign)

        tx = Transaction(
            user_id=user_id,
            amount=-total_budget,
            type=TransactionType.CAMPAIGN_PAYMENT,
            description=f"Created campaign: {data['title']}",
            reference_id=campaign.id
        )
        session.add(tx)

    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ *Campaign Successfully Launched!*\n\n"
        f"📢 *Title:* {data['title']}\n"
        f"💰 *Budget Allocated:* `{total_budget:,.2f}` points\n"
        f"🚀 Status: Active",
        parse_mode=ParseMode.MARKDOWN
    )


# ==============================================================================
# SECTION 13: ADMIN PANEL
# ==============================================================================

@router.message(F.text == "👑 Admin Panel")
async def show_admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    msg = "👑 *Admin Control Panel*\n\nSelect a management option below:"
    await message.answer(msg, reply_markup=get_admin_keyboard(), parse_mode=ParseMode.MARKDOWN)

@router.callback_query(AdminActionCallback.filter(F.action == "withdrawals"))
async def admin_list_withdrawals(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        return

    res = await session.execute(
        select(Withdrawal).where(Withdrawal.status == WithdrawalStatus.PENDING).limit(10)
    )
    withdrawals = res.scalars().all()

    if not withdrawals:
        await query.message.edit_text("✅ No pending withdrawals found.", reply_markup=get_admin_keyboard())
        return

    msg = "💸 *Pending Withdrawals:*\n\n"
    buttons = []
    for w in withdrawals:
        msg += f"🆔 `{w.id[:8]}` | User: `{w.user_id}` | `{w.amount:,.2f}` via {w.method}\n"
        buttons.append([
            InlineKeyboardButton(text=f"✅ Approve {w.id[:8]}", callback_data=AdminActionCallback(action="app_w", target_id=w.id).pack()),
            InlineKeyboardButton(text=f"❌ Reject {w.id[:8]}", callback_data=AdminActionCallback(action="rej_w", target_id=w.id).pack())
        ])

    buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="adm:back")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await query.message.edit_text(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.callback_query(AdminActionCallback.filter(F.action == "app_w"))
async def admin_approve_withdrawal(query: CallbackQuery, callback_data: AdminActionCallback, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS: return

    async with session.begin_nested():
        res = await session.execute(select(Withdrawal).where(Withdrawal.id == callback_data.target_id).with_for_update())
        w = res.scalar_one_or_none()

        if not w or w.status != WithdrawalStatus.PENDING:
            await query.answer("Withdrawal no longer pending.", show_alert=True)
            return

        w.status = WithdrawalStatus.PAID

        audit = AuditLog(
            admin_id=query.from_user.id,
            action="APPROVE_WITHDRAWAL",
            target_user_id=w.user_id,
            details=f"Approved withdrawal {w.id} of amount {w.amount}"
        )
        session.add(audit)

    await session.commit()
    await query.answer("Withdrawal Approved!")
    await admin_list_withdrawals(query, session)

@router.callback_query(AdminActionCallback.filter(F.action == "rej_w"))
async def admin_reject_withdrawal(query: CallbackQuery, callback_data: AdminActionCallback, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS: return

    async with session.begin_nested():
        res = await session.execute(select(Withdrawal).where(Withdrawal.id == callback_data.target_id).with_for_update())
        w = res.scalar_one_or_none()

        if not w or w.status != WithdrawalStatus.PENDING:
            await query.answer("Withdrawal no longer pending.", show_alert=True)
            return

        w.status = WithdrawalStatus.REJECTED

        res_u = await session.execute(select(User).where(User.id == w.user_id).with_for_update())
        user = res_u.scalar_one_or_none()
        if user:
            user.balance += w.amount
            tx = Transaction(
                user_id=user.id,
                amount=w.amount,
                type=TransactionType.WITHDRAWAL_REFUND,
                description="Withdrawal rejected & refunded",
                reference_id=w.id
            )
            session.add(tx)

        audit = AuditLog(
            admin_id=query.from_user.id,
            action="REJECT_WITHDRAWAL",
            target_user_id=w.user_id,
            details=f"Rejected withdrawal {w.id} and refunded {w.amount}"
        )
        session.add(audit)

    await session.commit()
    await query.answer("Withdrawal Rejected and Refunded!")
    await admin_list_withdrawals(query, session)


# ==============================================================================
# SECTION 14: FASTAPI & APP INITIALIZATION
# ==============================================================================

fastapi_app = FastAPI(title="Telegram Bot Health Checker")

@fastapi_app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


async def start_bot():
    bot = Bot(token=BOT_TOKEN)
    storage = RedisStorage(redis=redis_client)
    dp = Dispatcher(storage=storage)

    # Register Middlewares
    dp.message.outer_middleware(DatabaseMiddleware())
    dp.callback_query.outer_middleware(DatabaseMiddleware())
    dp.message.outer_middleware(MaintenanceMiddleware())
    dp.callback_query.outer_middleware(MaintenanceMiddleware())
    dp.message.outer_middleware(UserActivityMiddleware())
    dp.callback_query.outer_middleware(UserActivityMiddleware())

    # Include Router
    dp.include_router(router)

    # Initialize Database Tables
    await init_db()

    logger.info("Database initialized successfully.")

    if BOT_MODE == "polling":
        logger.info("Starting Telegram Bot in POLLING mode...")
        await dp.start_polling(bot)
    else:
        logger.info(f"Setting Webhook to {WEBHOOK_URL}...")
        await bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
        
        config = uvicorn.Config(app=fastapi_app, host="0.0.0.0", port=8000, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped successfully.")

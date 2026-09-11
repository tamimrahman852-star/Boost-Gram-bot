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

                # 1. Record Co

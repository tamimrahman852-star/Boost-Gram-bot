import uuid
import hmac
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List
from enum import Enum

from sqlalchemy import (
    BigInteger, String, Numeric, Integer, Boolean, DateTime, Text,
    ForeignKey, UniqueConstraint, Index, Enum as SQLEnum
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import redis.asyncio as aioredis

from config import DATABASE_URL, REDIS_URL, BusinessRules
from helpers import hash_sensitive


class Base(DeclarativeBase):
    pass


# Helper to force lowercase values for PostgreSQL Enum types
def enum_values(x):
    return [e.value for e in x]


class TaskType(str, Enum):
    CHANNEL_SUB = "channel_sub"
    GROUP_JOIN = "group_join"
    BOT_START = "bot_start"
    POST_VIEW = "post_view"
    WEB_APP = "web_app"
    CUSTOM = "custom"
    REACTION = "reaction"
    BOOST_7DAY = "boost_7day"


class CampaignStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TransactionType(str, Enum):
    TASK_REWARD = "task_reward"
    REFERRAL_REWARD = "referral_reward"
    REFERRAL_TIER_BONUS = "referral_tier_bonus"
    STAR_TOPUP = "star_topup"
    WITHDRAWAL = "withdrawal"
    CAMPAIGN_PAYMENT = "campaign_payment"
    CAMPAIGN_REFUND = "campaign_refund"
    CHECK_REDEEM = "check_redeem"
    CHECK_CREATE = "check_create"
    PENALTY_REVOKE = "penalty_revoke"
    ADMIN_ADJUSTMENT = "admin_adjustment"


class CheckType(str, Enum):
    SINGLE_USE = "single_use"
    MULTI_USE = "multi_use"


class RetentionStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    PENALIZED = "penalized"


class NotificationType(str, Enum):
    TASK_COMPLETE = "task_complete"
    REFERRAL_JOIN = "referral_join"
    RETENTION_WARNING = "retention_warning"
    PENALTY_APPLIED = "penalty_applied"
    WITHDRAWAL_APPROVED = "withdrawal_approved"
    CAMPAIGN_STATUS = "campaign_status"


VERIFIABLE_TASKS = {TaskType.CHANNEL_SUB, TaskType.GROUP_JOIN, TaskType.BOOST_7DAY}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128))
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)

    balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    language: Mapped[str] = mapped_column(String(5), default="en")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    total_earned: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    total_withdrawn: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    completed_tasks_count: Mapped[int] = mapped_column(Integer, default=0)
    referral_earnings: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))

    referral_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    referred_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"))
    referral_tier: Mapped[int] = mapped_column(Integer, default=1)

    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    completions = relationship("TaskCompletion", back_populates="user", lazy="selectin")
    transactions = relationship("Transaction", back_populates="user", lazy="selectin")
    campaigns = relationship("Campaign", back_populates="advertiser", lazy="selectin")
    withdrawals = relationship("WithdrawalRequest", back_populates="user", lazy="selectin")

    @property
    def next_level_xp(self) -> int:
        return self.level * BusinessRules.XP_PER_LEVEL

    @property
    def current_level_xp(self) -> int:
        return (self.level - 1) * BusinessRules.XP_PER_LEVEL

    @property
    def can_withdraw(self) -> bool:
        return self.level >= BusinessRules.WITHDRAWAL_MIN_LEVEL

    def add_xp(self, amount: int) -> bool:
        old = self.level
        self.xp += amount
        self.level = max(1, self.xp // BusinessRules.XP_PER_LEVEL + 1)
        return self.level > old


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    advertiser_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))

    title: Mapped[str] = mapped_column(String(255))
    task_type: Mapped[TaskType] = mapped_column(
        SQLEnum(TaskType, name="tasktype", values_callable=enum_values, create_type=False)
    )

    target_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    target_username: Mapped[Optional[str]] = mapped_column(String(64))
    target_link: Mapped[Optional[str]] = mapped_column(String(255))
    target_title: Mapped[Optional[str]] = mapped_column(String(255))
    target_bot_token_hash: Mapped[Optional[str]] = mapped_column(String(128))
    target_bot_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    premium_only: Mapped[bool] = mapped_column(Boolean, default=False)

    reward_per_user: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    base_reward: Mapped[Decimal] = mapped_column(Numeric(18, 4))

    max_completions: Mapped[int] = mapped_column(Integer)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)

    total_budget: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    spent_budget: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    refunded_budget: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))

    status: Mapped[CampaignStatus] = mapped_column(
        SQLEnum(CampaignStatus, name="campaignstatus", values_callable=enum_values, create_type=False),
        default=CampaignStatus.ACTIVE
    )

    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    requires_retention_check: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    advertiser = relationship("User", back_populates="campaigns")
    completions = relationship("TaskCompletion", back_populates="campaign", lazy="selectin")

    __table_args__ = (
        Index('ix_campaign_advertiser_status', 'advertiser_id', 'status'),
        Index('ix_campaign_target', 'target_chat_id', 'target_username'),
    )

    @property
    def slots_remaining(self) -> int:
        return self.max_completions - self.completed_count

    @property
    def is_full(self) -> bool:
        return self.completed_count >= self.max_completions


class TaskCompletion(Base):
    __tablename__ = "task_completions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"))

    reward: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    xp_earned: Mapped[int] = mapped_column(Integer, default=0)

    retention_status: Mapped[RetentionStatus] = mapped_column(
        SQLEnum(RetentionStatus, name="retentionstatus", values_callable=enum_values, create_type=False),
        default=RetentionStatus.PENDING
    )
    retention_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retention_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    penalty_applied: Mapped[bool] = mapped_column(Boolean, default=False)

    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="completions")
    campaign = relationship("Campaign", back_populates="completions")

    __table_args__ = (
        UniqueConstraint('user_id', 'campaign_id', name='uq_user_campaign_completion'),
        Index('ix_completion_retention', 'retention_status', 'retention_deadline'),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))

    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    type: Mapped[TransactionType] = mapped_column(
        SQLEnum(TransactionType, name="transactiontype", values_callable=enum_values, create_type=False)
    )
    description: Mapped[str] = mapped_column(String(255))

    reference_id: Mapped[Optional[str]] = mapped_column(String(36))
    reference_type: Mapped[Optional[str]] = mapped_column(String(50))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="transactions")

    __table_args__ = (
        Index('ix_transaction_user_created', 'user_id', 'created_at'),
    )


class Check(Base):
    __tablename__ = "checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    
    check_type: Mapped[CheckType] = mapped_column(
        SQLEnum(CheckType, name="check_type_enum", values_callable=enum_values, create_type=False),
        default=CheckType.MULTI_USE
    )
    
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount_per_activation: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    max_activations: Mapped[int] = mapped_column(Integer)
    activations_count: Mapped[int] = mapped_column(Integer, default=0)
    password_hash: Mapped[Optional[str]] = mapped_column(String(128))
    requires_password: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    total_funded: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    @property
    def remaining(self) -> int:
        return self.max_activations - self.activations_count

    @property
    def is_exhausted(self) -> bool:
        return self.activations_count >= self.max_activations

    def verify_password(self, pw: str) -> bool:
        if not self.requires_password:
            return True
        return hmac.compare_digest(hash_sensitive(pw), self.password_hash or "")


class CheckActivation(Base):
    __tablename__ = "check_activations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    check_id: Mapped[str] = mapped_column(String(36), ForeignKey("checks.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint('check_id', 'user_id', name='uq_check_user_activation'),)


class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    payment_method: Mapped[Optional[str]] = mapped_column(String(50))
    payment_details: Mapped[Optional[str]] = mapped_column(Text)
    admin_notes: Mapped[Optional[str]] = mapped_column(Text)
    processed_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user = relationship("User", back_populates="withdrawals")


class ReferralEarning(Base):
    __tablename__ = "referral_earnings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    referrer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    referred_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    tier: Mapped[int] = mapped_column(Integer)
    source_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    bonus_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    xp_bonus: Mapped[int] = mapped_column(Integer, default=0)
    transaction_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FraudReport(Base):
    __tablename__ = "fraud_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    campaign_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("campaigns.id"))
    completion_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("task_completions.id"))
    report_type: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    action_taken: Mapped[Optional[str]] = mapped_column(String(50))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    type: Mapped[NotificationType] = mapped_column(
        SQLEnum(NotificationType, name="notificationtype", values_callable=enum_values, create_type=False)
    )
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[Optional[str]] = mapped_column(Text)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index('ix_notification_unsent', 'is_sent', 'created_at'),
    )


# Database setup
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

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
from helpers import hash_sensitive, hash_password


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
        stored = self.password_hash or ""
        if stored.startswith("h2$"):
            return hmac.compare_digest(hash_password(pw, self.code), stored)
        # legacy unsalted hashes created before the security update
        return hmac.compare_digest(hash_sensitive(pw), stored)


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



class DepositRequest(Base):
    """Manual (bKash/Nagad/Rocket) deposit waiting for an admin to verify the payment."""
    __tablename__ = "deposit_requests"
    __table_args__ = (
        UniqueConstraint("method", "trx_id", name="uq_deposit_method_trx"),   # a TrxID can only be used once
        Index("ix_deposit_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    method: Mapped[str] = mapped_column(String(20))
    amount_bdt: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    coins: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    trx_id: Mapped[str] = mapped_column(String(40))
    proof_file_id: Mapped[Optional[str]] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(20), default="pending")      # pending / approved / rejected
    processed_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class StarsPayment(Base):
    """One Telegram Stars payment. telegram_payment_charge_id is unique, so a payment is credited exactly once (survives restarts)."""
    __tablename__ = "stars_payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    charge_id: Mapped[str] = mapped_column(String(128), unique=True)
    stars: Mapped[int] = mapped_column(Integer)
    coins: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PortalLoginToken(Base):
    """One-time, short-lived link from the Mini App to the web portal. Only the SHA-256 of the token is stored."""
    __tablename__ = "portal_login_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TonDeposit(Base):
    """One incoming TON transfer that carried a user's memo (BG<user id>). tx_hash is unique, so a transfer is credited once."""
    __tablename__ = "ton_deposits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    tx_hash: Mapped[str] = mapped_column(String(80), unique=True)
    ton_amount: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    usd: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    coins: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(12), default="credited")            # credited / below_min
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Voucher(Base):
    """Promo code created by an admin."""
    __tablename__ = "vouchers"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class VoucherRedemption(Base):
    __tablename__ = "voucher_redemptions"
    __table_args__ = (UniqueConstraint("code", "user_id", name="uq_voucher_user"),)   # once per user

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(32), ForeignKey("vouchers.code"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TaskSubmission(Base):
    """Screenshot proof for tasks Telegram cannot verify (post view / bot start / reaction).
    Lifecycle: pending -> approved | rejected -> (disputed ->) approved | final_rejected."""
    __tablename__ = "task_submissions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "user_id", name="uq_submission_user_campaign"),
        Index("ix_submission_status", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    advertiser_id: Mapped[int] = mapped_column(BigInteger)
    proof_file_id: Mapped[str] = mapped_column(String(256))
    proof_unique_id: Mapped[str] = mapped_column(String(64), unique=True)   # same screenshot can never be reused
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decided_by: Mapped[Optional[int]] = mapped_column(BigInteger)           # 0 = automatic
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AutoRenew(Base):
    """Opt-in: when this campaign completes, publish an identical one (if the owner can afford it)."""
    __tablename__ = "auto_renew"

    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"), primary_key=True)
    renewals_left: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CampaignPost(Base):
    """The post a view-campaign promotes (forwarded to workers inside the bot)."""
    __tablename__ = "campaign_posts"

    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"), primary_key=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger)
    source_message_id: Mapped[int] = mapped_column(BigInteger)


class Translation(Base):
    """Cached machine translation of one English text into one language (see i18n.py)."""
    __tablename__ = "translations"

    lang: Mapped[str] = mapped_column(String(8), primary_key=True)
    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    text: Mapped[str] = mapped_column(Text)


class EarlyLeave(Base):
    """A user left a channel/group (or removed a boost) before the retention period ended.
    The reward is HELD (deducted) and the user must re-subscribe within 24h or pay the penalty."""
    __tablename__ = "early_leaves"
    __table_args__ = (
        Index("ix_early_leave_user_status", "user_id", "status"),
        Index("ix_early_leave_completion", "completion_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    completion_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_completions.id"))   # one row per leave (re-joining and leaving again creates a new one)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    status: Mapped[str] = mapped_column(String(20), default="held")     # held / restored / penalized
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AdChannel(Base):
    """A channel its owner monetizes: the bot posts advertisers' ads there inside the owner's schedule."""
    __tablename__ = "ad_channels"
    __table_args__ = (Index("ix_adch_owner", "owner_id"), Index("ix_adch_status", "status"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    members: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="active")           # active / paused / removed
    price_per_post: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))   # legacy (fixed-price model), unused
    tz_offset_min: Mapped[int] = mapped_column(Integer, default=0)              # owner's UTC offset in minutes
    window_start: Mapped[int] = mapped_column(Integer, default=8)               # local hour the ads may start
    window_end: Mapped[int] = mapped_column(Integer, default=23)                # local hour they must stop (exclusive)
    slots: Mapped[str] = mapped_column(String(200), default="")                 # owner's local posting times, "09:00,13:30,21:00"
    max_posts_per_day: Mapped[int] = mapped_column(Integer, default=2)
    min_gap_hours: Mapped[int] = mapped_column(Integer, default=6)
    post_lifetime_hours: Mapped[int] = mapped_column(Integer, default=24)
    auto_delete: Mapped[bool] = mapped_column(Boolean, default=True)
    strikes: Mapped[int] = mapped_column(Integer, default=0)
    total_posts: Mapped[int] = mapped_column(Integer, default=0)
    total_earned: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    pause_reason: Mapped[Optional[str]] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AdCampaign(Base):
    """An advertiser's budget-limited ad. Runs between start_at and end_at, stops when the budget is used up."""
    __tablename__ = "ad_campaigns"
    __table_args__ = (Index("ix_adcamp_adv", "advertiser_id"), Index("ix_adcamp_status", "status"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    advertiser_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    media_file_id: Mapped[Optional[str]] = mapped_column(String(256))
    media_type: Mapped[Optional[str]] = mapped_column(String(8))                # photo / video
    pricing: Mapped[str] = mapped_column(String(4), default="cpm")              # cpm = per 1,000 views, cpc = per click
    bid: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))  # GRAM per 1,000 views or per click (gross, fee included)
    button_text: Mapped[str] = mapped_column(String(40))
    button_url: Mapped[str] = mapped_column(String(512))
    # pending_review / active / paused / stopped / finished / exhausted / rejected
    status: Mapped[str] = mapped_column(String(16), default="pending_review")
    budget: Mapped[Decimal] = mapped_column(Numeric(18, 4))                     # escrow taken from the balance
    spent: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))      # reserved/paid for placements (price + fee)
    refunded: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))   # returned to the advertiser
    max_price_per_post: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))   # legacy, unused
    posts_per_day: Mapped[int] = mapped_column(Integer, default=3)
    min_members: Mapped[int] = mapped_column(Integer, default=0)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    total_posts: Mapped[int] = mapped_column(Integer, default=0)
    reject_reason: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AdPlacement(Base):
    """One ad post in one channel (reserved -> posted -> completed / failed / removed_early)."""
    __tablename__ = "ad_placements"
    __table_args__ = (Index("ix_adpl_status", "status", "scheduled_for"), Index("ix_adpl_campaign", "campaign_id"), Index("ix_adpl_channel", "channel_id"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("ad_campaigns.id"))
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("ad_channels.id"))
    owner_id: Mapped[int] = mapped_column(BigInteger)                           # publisher
    advertiser_id: Mapped[int] = mapped_column(BigInteger)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))    # publisher's share earned so far (paid when the ad ends)
    fee: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))      # platform commission so far
    slot: Mapped[Optional[str]] = mapped_column(String(24))                     # "2026-11-20 09:00" owner-local posting time it fills
    billed_views: Mapped[int] = mapped_column(Integer, default=0)
    billed_clicks: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    members_at_post: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    bot_starts: Mapped[int] = mapped_column(Integer, default=0)
    views_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failure: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


NEW_TABLES = [StarsPayment.__table__, PortalLoginToken.__table__, TonDeposit.__table__, DepositRequest.__table__, Voucher.__table__, VoucherRedemption.__table__,
              TaskSubmission.__table__, AutoRenew.__table__, CampaignPost.__table__, Translation.__table__, EarlyLeave.__table__,
              AdChannel.__table__, AdCampaign.__table__, AdPlacement.__table__]


_AD_COLUMNS = [
    ("ad_channels", "slots", "VARCHAR(200) DEFAULT ''"),
    ("ad_campaigns", "media_type", "VARCHAR(8)"),
    ("ad_campaigns", "pricing", "VARCHAR(4) DEFAULT 'cpm'"),
    ("ad_campaigns", "bid", "NUMERIC(18,4) DEFAULT 0"),
    ("ad_placements", "slot", "VARCHAR(24)"),
    ("ad_placements", "billed_views", "INTEGER DEFAULT 0"),
    ("ad_placements", "billed_clicks", "INTEGER DEFAULT 0"),
]
_AD_RELAX = [("ad_channels", "price_per_post"), ("ad_campaigns", "max_price_per_post"), ("ad_placements", "price"), ("ad_placements", "fee")]


async def ensure_new_tables():
    """Create the tables added after the first release (idempotent, no migration step needed)."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=NEW_TABLES, checkfirst=True))
    # columns added to the ad tables after they first shipped (create_all never alters existing tables)
    from sqlalchemy import text as _t
    for table, col, ddl in _AD_COLUMNS:
        try:
            async with engine.begin() as conn:
                if engine.dialect.name == "postgresql":
                    await conn.execute(_t(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}"))
                else:
                    await conn.execute(_t(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        except Exception:
            pass                                                              # already there
    if engine.dialect.name == "postgresql":
        for table, col in _AD_RELAX:
            try:
                async with engine.begin() as conn:
                    await conn.execute(_t(f"ALTER TABLE {table} ALTER COLUMN {col} SET DEFAULT 0"))
            except Exception:
                pass

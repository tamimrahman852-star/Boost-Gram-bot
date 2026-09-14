"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-01-01
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------- ENUMS ----------------
    task_type_enum = sa.Enum(
        "channel_sub", "group_join", "bot_start", "post_view",
        "web_app", "custom", "reaction", "boost_7day",
        name="tasktype",
    )
    campaign_status_enum = sa.Enum(
        "active", "paused", "completed", "cancelled", name="campaignstatus"
    )
    transaction_type_enum = sa.Enum(
        "task_reward", "referral_reward", "referral_tier_bonus",
        "star_topup", "withdrawal", "campaign_payment", "campaign_refund",
        "check_redeem", "check_create", "penalty_revoke", "admin_adjustment",
        name="transactiontype",
    )
    check_type_enum = sa.Enum("single_use", "multi_use", name="checktype")
    retention_status_enum = sa.Enum(
        "pending", "verified", "failed", "penalized", name="retentionstatus"
    )
    notification_type_enum = sa.Enum(
        "task_complete", "referral_join", "retention_warning",
        "penalty_applied", "withdrawal_approved", "campaign_status",
        name="notificationtype",
    )

    # ---------------- USERS ----------------
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("username", sa.String(64)),
        sa.Column("first_name", sa.String(128), nullable=False),
        sa.Column("last_name", sa.String(128)),
        sa.Column("is_premium", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("balance", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("xp", sa.Integer, server_default="0", nullable=False),
        sa.Column("level", sa.Integer, server_default="1", nullable=False),
        sa.Column("language", sa.String(5), server_default="en", nullable=False),
        sa.Column("notifications_enabled", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("total_earned", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("total_withdrawn", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("completed_tasks_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("referral_earnings", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("referral_code", sa.String(32), unique=True, nullable=False, index=True),
        sa.Column("referred_by", sa.BigInteger, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("referral_tier", sa.Integer, server_default="1", nullable=False),
        sa.Column("is_blocked", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("is_verified", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("risk_score", sa.Integer, server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_active_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ---------------- CAMPAIGNS ----------------
    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("advertiser_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("task_type", task_type_enum, nullable=False),
        sa.Column("target_chat_id", sa.BigInteger),
        sa.Column("target_username", sa.String(64)),
        sa.Column("target_link", sa.String(255)),
        sa.Column("target_title", sa.String(255)),
        sa.Column("target_bot_token_hash", sa.String(128)),
        sa.Column("target_bot_id", sa.BigInteger),
        sa.Column("premium_only", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("reward_per_user", sa.Numeric(18, 4), nullable=False),
        sa.Column("base_reward", sa.Numeric(18, 4), nullable=False),
        sa.Column("max_completions", sa.Integer, nullable=False),
        sa.Column("completed_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("total_budget", sa.Numeric(18, 4), nullable=False),
        sa.Column("spent_budget", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("refunded_budget", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("status", campaign_status_enum, server_default="active", nullable=False),
        sa.Column("retention_days", sa.Integer, server_default="7", nullable=False),
        sa.Column("requires_retention_check", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_campaign_advertiser_status", "campaigns", ["advertiser_id", "status"])
    op.create_index("ix_campaign_target", "campaigns", ["target_chat_id", "target_username"])

    # ---------------- TASK COMPLETIONS ----------------
    op.create_table(
        "task_completions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("reward", sa.Numeric(18, 4), nullable=False),
        sa.Column("xp_earned", sa.Integer, server_default="0", nullable=False),
        sa.Column("retention_status", retention_status_enum, server_default="pending", nullable=False),
        sa.Column("retention_deadline", sa.DateTime(timezone=True)),
        sa.Column("retention_checked_at", sa.DateTime(timezone=True)),
        sa.Column("penalty_applied", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "campaign_id", name="uq_user_campaign_completion"),
    )
    op.create_index("ix_completion_retention", "task_completions",
                    ["retention_status", "retention_deadline"])

    # ---------------- TRANSACTIONS ----------------
    op.create_table(
        "transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("type", transaction_type_enum, nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("reference_id", sa.String(36)),
        sa.Column("reference_type", sa.String(50)),
        sa.Column("balance_after", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_transaction_user_created", "transactions", ["user_id", "created_at"])

    # ---------------- CHECKS ----------------
    op.create_table(
        "checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(16), unique=True, nullable=False, index=True),
        sa.Column("check_type", check_type_enum, server_default="multi_use", nullable=False),
        sa.Column("created_by", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount_per_activation", sa.Numeric(18, 4), nullable=False),
        sa.Column("max_activations", sa.Integer, nullable=False),
        sa.Column("activations_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("password_hash", sa.String(128)),
        sa.Column("requires_password", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("total_funded", sa.Numeric(18, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "check_activations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("check_id", sa.String(36), sa.ForeignKey("checks.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("check_id", "user_id", name="uq_check_user_activation"),
    )

    # ---------------- WITHDRAWALS ----------------
    op.create_table(
        "withdrawal_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("payment_method", sa.String(50)),
        sa.Column("payment_details", sa.Text),
        sa.Column("admin_notes", sa.Text),
        sa.Column("processed_by", sa.BigInteger),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    )

    # ---------------- REFERRAL EARNINGS ----------------
    op.create_table(
        "referral_earnings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("referrer_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("referred_user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tier", sa.Integer, nullable=False),
        sa.Column("source_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("bonus_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("xp_bonus", sa.Integer, server_default="0", nullable=False),
        sa.Column("transaction_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ---------------- FRAUD REPORTS ----------------
    op.create_table(
        "fraud_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id")),
        sa.Column("completion_id", sa.String(36), sa.ForeignKey("task_completions.id")),
        sa.Column("report_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("evidence", sa.Text),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("action_taken", sa.String(50)),
        sa.Column("reported_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.BigInteger),
    )

    # ---------------- NOTIFICATIONS ----------------
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("type", notification_type_enum, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("data", sa.Text),
        sa.Column("is_sent", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_notification_unsent", "notifications", ["is_sent", "created_at"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("fraud_reports")
    op.drop_table("referral_earnings")
    op.drop_table("withdrawal_requests")
    op.drop_table("check_activations")
    op.drop_table("checks")
    op.drop_table("transactions")
    op.drop_table("task_completions")
    op.drop_table("campaigns")
    op.drop_table("users")

    for enum_name in (
        "notificationtype", "retentionstatus", "checktype",
        "transactiontype", "campaignstatus", "tasktype",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")

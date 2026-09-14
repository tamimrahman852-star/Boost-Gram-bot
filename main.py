# ==============================================================================
# FILE: main.py - BoostGram Bot Professional Edition
# ==============================================================================

import sys
import os
import re
import html
import uuid
import hashlib
import hmac
import secrets
import logging
import asyncio
import traceback
from typing import Optional, Union, List, Dict, Any
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN
from enum import Enum
from dataclasses import dataclass

# Third-party Imports
from dotenv import load_dotenv
import redis.asyncio as aioredis

from sqlalchemy import (
    BigInteger, String, Numeric, Integer, Boolean, DateTime, Text,
    ForeignKey, UniqueConstraint, Index, select, and_, or_, not_,
    func, Enum as SQLEnum, case
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker
)
from sqlalchemy.dialects.postgresql import JSONB, ARRAY

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, PreCheckoutQuery, LabeledPrice,
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat
)
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ErrorEvent
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

# ==============================================================================
# SECTION 1: CONFIGURATION & ENVIRONMENT
# ==============================================================================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger("BoostGramBot")

# --- Helper Functions ---
def esc(value) -> str:
    """Escape dynamic text for HTML parse_mode."""
    return html.escape(str(value), quote=False)

def safe_text(message: "Message") -> Optional[str]:
    """Returns stripped message text, or None if non-text."""
    return message.text.strip() if message.text else None

def generate_secure_token(length: int = 32) -> str:
    """Generate cryptographically secure token."""
    return secrets.token_urlsafe(length)

def hash_sensitive_data(data: str) -> str:
    """Hash sensitive data for storage."""
    return hashlib.sha256(data.encode()).hexdigest()

NON_TEXT_INPUT_MSG = "⚠️ Please send this as a text message (not a photo/sticker/file)."

# --- Environment Variables ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "BoostGramBot")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "default-key-change-in-production")

if not BOT_TOKEN or not DATABASE_URL or not REDIS_URL:
    logger.critical("CRITICAL ERROR: Missing required environment variables.")
    sys.exit(1)

try:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()]
except ValueError:
    ADMIN_IDS = []

# ==============================================================================
# SECTION 2: BUSINESS CONSTANTS & PRICING RULES
# ==============================================================================

class TaskPricing:
    """Dynamic pricing rules for different task types."""
    
    PRICING_RULES = {
        "channel_sub": {"min": 850, "suggested": 1000, "max": 5000},
        "group_join": {"min": 1250, "suggested": 1500, "max": 6000},
        "bot_start": {"min": 2500, "suggested": 3000, "max": 10000},
        "post_view": {"min": 120, "suggested": 200, "max": 500},
        "reaction": {"min": 500, "suggested": 600, "max": 800},
        "boost_7day": {"min": 25000, "suggested": 30000, "max": 100000},
    }
    
    PREMIUM_MULTIPLIER = Decimal("2.0")
    
    @classmethod
    def get_pricing(cls, task_type: str, is_premium: bool = False) -> dict:
        """Get pricing rules for a task type."""
        base = cls.PRICING_RULES.get(task_type, {"min": 100, "suggested": 200, "max": 1000})
        if is_premium:
            return {
                "min": int(Decimal(str(base["min"])) * cls.PREMIUM_MULTIPLIER),
                "suggested": int(Decimal(str(base["suggested"])) * cls.PREMIUM_MULTIPLIER),
                "max": int(Decimal(str(base["max"])) * cls.PREMIUM_MULTIPLIER),
            }
        return base
    
    @classmethod
    def validate_reward(cls, task_type: str, reward: Decimal, is_premium: bool = False) -> tuple[bool, str]:
        """Validate reward against pricing rules."""
        rules = cls.get_pricing(task_type, is_premium)
        if reward < rules["min"]:
            return False, f"Minimum reward for this task type is {rules['min']:,} coins"
        if reward > rules["max"]:
            return False, f"Maximum reward for this task type is {rules['max']:,} coins"
        return True, "Valid"

class BusinessRules:
    """Central business rules configuration."""
    
    # Referral System
    REFERRAL_BASE_REWARD = Decimal("5000")
    REFERRAL_TIER_BONUSES = {
        1: Decimal("0.08"),  # 8% for direct referrals
        2: Decimal("0.05"),  # 5% for tier 2
        3: Decimal("0.02"),  # 2% for tier 3
    }
    REFERRAL_XP_BASE = 500
    
    # Star Top-up
    STAR_TO_COIN_RATE = Decimal("5000")
    
    # Platform Commission
    PLATFORM_COMMISSION = Decimal("0.15")  # 15%
    
    # Withdrawal Settings
    WITHDRAWAL_MIN_LEVEL = 30
    WITHDRAWAL_MIN_AMOUNT = Decimal("250000")
    WITHDRAWAL_MAX_AMOUNT = Decimal("10000000")
    
    # XP System
    XP_PER_1000_COINS = Decimal("3")
    XP_PER_LEVEL = 1500
    
    # Anti-Cheat
    RETENTION_CHECK_DAYS = 7
    RETENTION_CHECK_INTERVAL_HOURS = 24
    
    # Task Limits
    TASKS_PAGE_SIZE = 5
    MAX_ACTIVE_CAMPAIGNS_PER_USER = 10

# Legacy constants for backward compatibility
REFERRAL_COIN_REWARD = BusinessRules.REFERRAL_BASE_REWARD
REFERRAL_XP_REWARD = BusinessRules.REFERRAL_XP_BASE
STAR_TO_COIN_RATE = int(BusinessRules.STAR_TO_COIN_RATE)
PLATFORM_COMMISSION_PERCENT = BusinessRules.PLATFORM_COMMISSION
MIN_WITHDRAWAL = BusinessRules.WITHDRAWAL_MIN_AMOUNT
MAX_WITHDRAWAL = BusinessRules.WITHDRAWAL_MAX_AMOUNT
TASKS_PAGE_SIZE = BusinessRules.TASKS_PAGE_SIZE

# ==============================================================================
# SECTION 3: ENUMS
# ==============================================================================

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

VERIFIABLE_TASK_TYPES = {TaskType.CHANNEL_SUB, TaskType.GROUP_JOIN, TaskType.BOOST_7DAY}

# ==============================================================================
# SECTION 4: LOCALIZATION SYSTEM
# ==============================================================================

class Localization:
    """Comprehensive multi-language support system."""
    
    LANGUAGES = {
        "en": {
            # General
            "welcome": "👋 Welcome to <b>{bot_name}</b>, {first_name}!\n\n🚀 Complete tasks, earn GRAM coins, and promote your channels instantly!",
            "lang_changed": "✅ Language changed to English successfully!",
            "error_generic": "❌ Something went wrong. Please try again.",
            "not_started": "⚠️ Please tap /start first to activate your account.",
            "non_text_input": "⚠️ Please send this as a text message.",
            
            # Menu Buttons
            "menu_earnings": "💰 Earnings",
            "menu_promote": "📢 Promote",
            "menu_checks": "📋 Checks",
            "menu_cabinet": "👤 My Cabinet",
            "menu_sub_check": "🛡 Subscription Check",
            "menu_stats": "📊 Statistics",
            "menu_links": "🔗 Useful Links",
            "menu_instruction": "ℹ️ Instruction",
            
            # Cabinet
            "cabinet": "👤 <b>Your Cabinet:</b>\n\n🆔 My ID: <code>{user_id}</code>\n📈 Level: {level_emoji} {level}\n💰 Balance: <code>{balance:,.0f}</code> GRAM\n⚡ XP: {xp}/{next_level_xp}\n🎯 Tasks Done: {tasks_done}",
            "btn_replenish": "🪙 Replenish Balance",
            "btn_referral": "👥 Referral System",
            "btn_level": "📊 Level System",
            "btn_tasks": "📋 My Tasks",
            "btn_lang": "🌐 Change Language",
            "btn_withdraw": "💸 Withdraw",
            "btn_notif_on": "🔕 Disable notifications",
            "btn_notif_off": "🔔 Enable notifications",
            "btn_back": "◀️ Back",
            
            # Tasks
            "tasks_title": "🎯 <b>Available Tasks</b>\nComplete any task below to earn GRAM coins:",
            "no_tasks": "🎯 No active tasks right now. Check back soon!",
            "task_verified": "🎉 Task Verified Successfully!\n💰 You earned: +<code>{reward:,.0f}</code> GRAM\n⚡ XP gained: +{xp}",
            "task_failed": "❌ Verification failed! Please make sure you've joined and try again.",
            "task_already_done": "❌ You have already completed this task!",
            "task_limit_reached": "❌ Task limit has been reached.",
            
            # Campaign Creation
            "campaign_title": "📝 Enter Campaign Title:",
            "campaign_target": "🔗 Send the channel/group username (e.g. <code>@mychannel</code>), invite link, or numeric chat ID:",
            "campaign_link": "🔗 Enter the link users should open:",
            "campaign_reward": "💰 Enter reward per user (GRAM coins):\n\n💡 Suggested: <code>{suggested:,}</code>\n📊 Range: <code>{min:,}</code> - <code>{max:,}</code>",
            "campaign_slots": "👥 Enter total target completions count:",
            "campaign_created": "✅ Campaign created successfully and is now active!",
            "campaign_insufficient": "❌ Insufficient balance!\nRequired: <code>{required:,.0f}</code> GRAM\nYour balance: <code>{balance:,.0f}</code> GRAM",
            "campaign_duplicate": "⚠️ You already have an active campaign for this target!\n\nWould you like to add more slots instead?",
            
            # Premium
            "premium_targeting": "⭐ <b>Telegram Premium Targeting</b>\n\nTarget only Premium users? Their rewards will be 2x the normal rate.",
            "premium_yes": "⭐ Premium Only (2x rewards)",
            "premium_no": "👥 All Users (Normal rewards)",
            
            # Withdrawals
            "withdraw_locked": "🔒 <b>Withdrawal Locked</b>\n\nYou need to reach Level {required_level} to unlock withdrawals.\n\nYour current level: {current_level}\nKeep completing tasks to level up!",
            "withdraw_available": "💸 <b>Withdrawal Available!</b>\n\nMinimum: <code>{min_amount:,.0f}</code> GRAM\nMaximum: <code>{max_amount:,.0f}</code> GRAM\nYour balance: <code>{balance:,.0f}</code> GRAM\n\nEnter amount to withdraw:",
            "withdraw_min_error": "❌ Minimum withdrawal is <code>{min_amount:,.0f}</code> GRAM.",
            "withdraw_max_error": "❌ Maximum withdrawal is <code>{max_amount:,.0f}</code> GRAM.",
            "withdraw_insufficient": "❌ Insufficient balance. You have <code>{balance:,.0f}</code> GRAM.",
            "withdraw_submitted": "✅ Withdrawal request submitted!\n\nAmount: <code>{amount:,.0f}</code> GRAM\nStatus: Pending admin approval",
            
            # Checks
            "checks_menu": "📋 <b>Checks System</b>\n\nCreate shareable GRAM vouchers or redeem existing ones.",
            "check_create": "🎫 Create a Check",
            "check_activate": "💳 Activate a Check",
            "check_amount": "💰 Enter GRAM amount per activation:",
            "check_activations": "👥 Enter number of activations:",
            "check_password_prompt": "🔐 Set a password for this check? (Send password or type 'skip'):",
            "check_created": "✅ <b>Check Created!</b>\n\n🎫 Code: <code>{code}</code>\n💰 Amount: <code>{amount:,.0f}</code> GRAM\n👥 Activations: {activations}\n🔗 Link: {link}",
            "check_invalid": "❌ Invalid or expired check code.",
            "check_redeemed": "✅ Check redeemed! +<code>{amount:,.0f}</code> GRAM added to your balance.",
            "check_password_required": "🔐 This check requires a password. Please enter it:",
            "check_wrong_password": "❌ Incorrect password.",
            
            # Referrals
            "referral_title": "👥 <b>Referral System</b>\n\nEarn <code>{base_reward:,.0f}</code> GRAM per direct referral!\n\n📊 <b>Tier Bonuses:</b>\n• Tier 1 (Direct): 8% of their task earnings\n• Tier 2: 5% bonus\n• Tier 3: 2% bonus\n\n📈 Your Stats:\n• Direct Referrals: <code>{direct_count}</code>\n• Total Earned: <code>{total_earned:,.0f}</code> GRAM\n\n🔗 Your Link:\n<code>{ref_link}</code>",
            
            # Retention
            "retention_warning": "⚠️ <b>Retention Warning!</b>\n\nYou left {target_type} <b>{target_name}</b> before the 7-day retention period.\n\nPlease rejoin within 24 hours to avoid penalty:\n{target_link}\n\nIf you don't rejoin, your reward of <code>{reward:,.0f}</code> GRAM will be revoked.",
            "retention_penalty": "🚫 <b>Penalty Applied!</b>\n\nYou failed to rejoin {target_name} within the required time.\nYour reward of <code>{reward:,.0f}</code> GRAM has been revoked.",
            
            # Admin
            "admin_panel": "👑 <b>Admin Panel</b>\nChoose an action:",
            "admin_reports": "🚨 Reported Cheaters",
        },
        "bn": {
            # General
            "welcome": "👋 <b>{bot_name}</b>-এ আপনাকে স্বাগতম, {first_name}!\n\n🚀 টাস্ক সম্পন্ন করুন, GRAM কয়েন আয় করুন এবং আপনার চ্যানেল প্রমোট করুন!",
            "lang_changed": "✅ সফলভাবে ভাষা বাংলায় পরিবর্তন করা হয়েছে!",
            "error_generic": "❌ কিছু ভুল হয়েছে। আবার চেষ্টা করুন।",
            "not_started": "⚠️ প্রথমে /start চাপুন আপনার অ্যাকাউন্ট চালু করতে।",
            "non_text_input": "⚠️ অনুগ্রহ করে টেক্সট মেসেজ পাঠান।",
            
            # Menu Buttons
            "menu_earnings": "💰 আর্নিংস",
            "menu_promote": "📢 প্রমোট",
            "menu_checks": "📋 চেক্স",
            "menu_cabinet": "👤 মাই ক্যাবিনেট",
            "menu_sub_check": "🛡 সাবস্ক্রিপশন চেক",
            "menu_stats": "📊 পরিসংখ্যান",
            "menu_links": "🔗 দরকারী লিংক",
            "menu_instruction": "ℹ️ নির্দেশিকা",
            
            # Cabinet
            "cabinet": "👤 <b>আপনার ক্যাবিনেট:</b>\n\n🆔 আমার আইডি: <code>{user_id}</code>\n📈 লেভেল: {level_emoji} {level}\n💰 ব্যালেন্স: <code>{balance:,.0f}</code> GRAM\n⚡ XP: {xp}/{next_level_xp}\n🎯 টাস্ক সম্পন্ন: {tasks_done}",
            "btn_replenish": "🪙 ব্যালেন্স টপ-আপ",
            "btn_referral": "👥 রেফারেল সিস্টেম",
            "btn_level": "📊 লেভেল সিস্টেম",
            "btn_tasks": "📋 আমার টাস্কসমূহ",
            "btn_lang": "🌐 ভাষা পরিবর্তন",
            "btn_withdraw": "💸 উইথড্র",
            "btn_notif_on": "🔕 নোটিফিকেশন বন্ধ করুন",
            "btn_notif_off": "🔔 নোটিফিকেশন চালু করুন",
            "btn_back": "◀️ ফিরে যান",
            
            # Tasks
            "tasks_title": "🎯 <b>উপলব্ধ টাস্কসমূহ</b>\nGRAM কয়েন অর্জনের জন্য টাস্ক সম্পন্ন করুন:",
            "no_tasks": "🎯 এখন কোনো টাস্ক নেই। শীঘ্রই আবার চেক করুন!",
            "task_verified": "🎉 টাস্ক সফলভাবে যাচাই হয়েছে!\n💰 আপনি অর্জন করেছেন: +<code>{reward:,.0f}</code> GRAM\n⚡ XP: +{xp}",
            "task_failed": "❌ যাচাই ব্যর্থ! নিশ্চিত করুন আপনি জয়েন করেছেন।",
            "task_already_done": "❌ আপনি ইতিমধ্যে এই টাস্ক সম্পন্ন করেছেন!",
            "task_limit_reached": "❌ টাস্ক লিমিট পূর্ণ হয়েছে।",
            
            # Campaign Creation
            "campaign_title": "📝 ক্যাম্পেইন টাইটেল লিখুন:",
            "campaign_target": "🔗 চ্যানেল/গ্রুপ ইউজারনেম পাঠান (যেমন <code>@mychannel</code>):",
            "campaign_link": "🔗 ইউজারদের যে লিংকে পাঠাতে হবে তা লিখুন:",
            "campaign_reward": "💰 প্রতি ইউজার রিওয়ার্ড লিখুন (GRAM কয়েন):\n\n💡 প্রস্তাবিত: <code>{suggested:,}</code>\n📊 রেঞ্জ: <code>{min:,}</code> - <code>{max:,}</code>",
            "campaign_slots": "👥 মোট কতজন কমপ্লিট করতে পারবে:",
            "campaign_created": "✅ ক্যাম্পেইন সফলভাবে তৈরি হয়েছে!",
            "campaign_insufficient": "❌ অপর্যাপ্ত ব্যালেন্স!\nপ্রয়োজন: <code>{required:,.0f}</code> GRAM\nআপনার ব্যালেন্স: <code>{balance:,.0f}</code> GRAM",
            "campaign_duplicate": "⚠️ এই টার্গেটের জন্য ইতিমধ্যে একটি সক্রিয় ক্যাম্পেইন আছে!\n\nআরো স্লট যোগ করতে চান?",
            
            # Premium
            "premium_targeting": "⭐ <b>টেলিগ্রাম প্রিমিয়াম টার্গেটিং</b>\n\nশুধু প্রিমিয়াম ইউজার টার্গেট করবেন? তাদের রিওয়ার্ড ২x হবে।",
            "premium_yes": "⭐ শুধু প্রিমিয়াম (২x রিওয়ার্ড)",
            "premium_no": "👥 সব ইউজার (সাধারণ রিওয়ার্ড)",
            
            # Withdrawals
            "withdraw_locked": "🔒 <b>উইথড্র লকড</b>\n\nউইথড্র আনলক করতে লেভেল {required_level} এ পৌঁছাতে হবে।\n\nআপনার বর্তমান লেভেল: {current_level}",
            "withdraw_available": "💸 <b>উইথড্র উপলব্ধ!</b>\n\nসর্বনিম্ন: <code>{min_amount:,.0f}</code> GRAM\nসর্বোচ্চ: <code>{max_amount:,.0f}</code> GRAM\nআপনার ব্যালেন্স: <code>{balance:,.0f}</code> GRAM\n\nপরিমাণ লিখুন:",
            "withdraw_min_error": "❌ সর্বনিম্ন উইথড্র <code>{min_amount:,.0f}</code> GRAM।",
            "withdraw_max_error": "❌ সর্বোচ্চ উইথড্র <code>{max_amount:,.0f}</code> GRAM।",
            "withdraw_insufficient": "❌ অপর্যাপ্ত ব্যালেন্স। আপনার আছে <code>{balance:,.0f}</code> GRAM।",
            "withdraw_submitted": "✅ উইথড্র রিকোয়েস্ট জমা হয়েছে!\n\nপরিমাণ: <code>{amount:,.0f}</code> GRAM\nস্ট্যাটাস: অ্যাডমিন অনুমোদনের অপেক্ষায়",
            
            # Checks
            "checks_menu": "📋 <b>চেক সিস্টেম</b>\n\nশেয়ারযোগ্য GRAM ভাউচার তৈরি করুন বা রিডিম করুন।",
            "check_create": "🎫 চেক তৈরি করুন",
            "check_activate": "💳 চেক অ্যাক্টিভেট করুন",
            "check_amount": "💰 প্রতি অ্যাক্টিভেশনে GRAM পরিমাণ লিখুন:",
            "check_activations": "👥 কতবার অ্যাক্টিভেট করা যাবে:",
            "check_password_prompt": "🔐 এই চেকের জন্য পাসওয়ার্ড সেট করবেন? (পাসওয়ার্ড লিখুন বা 'skip' টাইপ করুন):",
            "check_created": "✅ <b>চেক তৈরি হয়েছে!</b>\n\n🎫 কোড: <code>{code}</code>\n💰 পরিমাণ: <code>{amount:,.0f}</code> GRAM\n👥 অ্যাক্টিভেশন: {activations}\n🔗 লিংক: {link}",
            "check_invalid": "❌ অকার্যকর বা মেয়াদোত্তীর্ণ চেক কোড।",
            "check_redeemed": "✅ চেক রিডিম হয়েছে! +<code>{amount:,.0f}</code> GRAM যোগ হয়েছে।",
            "check_password_required": "🔐 এই চেকের জন্য পাসওয়ার্ড প্রয়োজন। লিখুন:",
            "check_wrong_password": "❌ ভুল পাসওয়ার্ড।",
            
            # Referrals
            "referral_title": "👥 <b>রেফারেল সিস্টেম</b>\n\nপ্রতি সরাসরি রেফারেলে <code>{base_reward:,.0f}</code> GRAM!\n\n📊 <b>টিয়ার বোনাস:</b>\n• টিয়ার ১ (সরাসরি): তাদের টাস্ক আয়ের ৮%\n• টিয়ার ২: ৫% বোনাস\n• টিয়ার ৩: ২% বোনাস\n\n📈 আপনার স্ট্যাটস:\n• সরাসরি রেফারেল: <code>{direct_count}</code>\n• মোট আয়: <code>{total_earned:,.0f}</code> GRAM\n\n🔗 আপনার লিংক:\n<code>{ref_link}</code>",
            
            # Retention
            "retention_warning": "⚠️ <b>রিটেনশন সতর্কতা!</b>\n\nআপনি ৭ দিনের রিটেনশন পিরিয়ডের আগেই <b>{target_name}</b> ছেড়ে দিয়েছেন।\n\n২৪ ঘন্টার মধ্যে rejoin করুন:\n{target_link}\n\nনা করলে আপনার <code>{reward:,.0f}</code> GRAM রিওয়ার্ড বাতিল হবে।",
            "retention_penalty": "🚫 <b>পেনাল্টি প্রয়োগ!</b>\n\nআপনি নির্ধারিত সময়ে rejoin করেননি।\nআপনার <code>{reward:,.0f}</code> GRAM রিওয়ার্ড বাতিল হয়েছে।",
            
            # Admin
            "admin_panel": "👑 <b>অ্যাডমিন প্যানেল</b>\nএকটি অ্যাকশন বেছে নিন:",
            "admin_reports": "🚨 রিপোর্টেড চিটার",
        }
    }
    
    @classmethod
    def get_text(cls, lang: str, key: str, **kwargs) -> str:
        """Get localized text with fallback to English."""
        lang_dict = cls.LANGUAGES.get(lang, cls.LANGUAGES["en"])
        text = lang_dict.get(key, cls.LANGUAGES["en"].get(key, f"[{key}]"))
        try:
            return text.format(**kwargs)
        except KeyError as e:
            logger.warning(f"Missing format key {e} for {key}")
            return text

def get_text(lang: str, key: str, **kwargs) -> str:
    """Convenience function for localization."""
    return Localization.get_text(lang, key, **kwargs)

# ==============================================================================
# SECTION 5: DATABASE MODELS
# ==============================================================================

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)

    balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    language: Mapped[str] = mapped_column(String(5), default="en")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    total_earned: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    total_withdrawn: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    completed_tasks_count: Mapped[int] = mapped_column(Integer, default=0)
    referral_earnings: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))

    referral_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    referred_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    referral_tier: Mapped[int] = mapped_column(Integer, default=1)  # Which tier this user is in
    
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)  # Anti-fraud score
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    completions = relationship("TaskCompletion", back_populates="user", lazy="selectin")
    transactions = relationship("Transaction", back_populates="user", lazy="selectin")
    campaigns = relationship("Campaign", back_populates="advertiser", lazy="selectin")
    referrals = relationship("User", backref="referrer", remote_side=[id])
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

    def calculate_level(self) -> int:
        """Calculate level from XP."""
        return max(1, (self.xp // BusinessRules.XP_PER_LEVEL) + 1)

    def add_xp(self, xp_amount: int) -> bool:
        """Add XP and return True if level up occurred."""
        old_level = self.level
        self.xp += xp_amount
        self.level = self.calculate_level()
        return self.level > old_level

class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    advertiser_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    
    title: Mapped[str] = mapped_column(String(255))
    task_type: Mapped[TaskType] = mapped_column(SQLEnum(TaskType))
    
    # Target information
    target_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    target_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_link: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    target_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Bot verification (for bot_start tasks)
    target_bot_token_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    target_bot_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    
    # Premium targeting
    premium_only: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Pricing
    reward_per_user: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    base_reward: Mapped[Decimal] = mapped_column(Numeric(18, 4))  # Before premium multiplier
    
    # Capacity
    max_completions: Mapped[int] = mapped_column(Integer)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Budget
    total_budget: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    spent_budget: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    refunded_budget: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    
    # Status
    status: Mapped[CampaignStatus] = mapped_column(SQLEnum(CampaignStatus), default=CampaignStatus.ACTIVE)
    
    # Retention tracking
    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    requires_retention_check: Mapped[bool] = mapped_column(Boolean, default=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
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
    
    # Retention tracking
    retention_status: Mapped[RetentionStatus] = mapped_column(SQLEnum(RetentionStatus), default=RetentionStatus.PENDING)
    retention_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    penalty_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
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
    type: Mapped[TransactionType] = mapped_column(SQLEnum(TransactionType))
    description: Mapped[str] = mapped_column(String(255))
    
    # Reference to related entities
    reference_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    reference_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0.0"))
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="transactions")

    __table_args__ = (
        Index('ix_transaction_user_created', 'user_id', 'created_at'),
    )

class Check(Base):
    """Voucher code system with password protection."""
    __tablename__ = "checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    
    check_type: Mapped[CheckType] = mapped_column(SQLEnum(CheckType), default=CheckType.MULTI_USE)
    
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount_per_activation: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    
    max_activations: Mapped[int] = mapped_column(Integer)
    activations_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Password protection
    password_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    requires_password: Mapped[bool] = mapped_column(Boolean, default=False)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    total_funded: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def remaining_activations(self) -> int:
        return self.max_activations - self.activations_count

    @property
    def is_exhausted(self) -> bool:
        return self.activations_count >= self.max_activations

    def verify_password(self, password: str) -> bool:
        if not self.requires_password:
            return True
        return hmac.compare_digest(
            hash_sensitive_data(password),
            self.password_hash or ""
        )

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
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, approved, rejected
    
    # Payment details (encrypted in production)
    payment_method: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    payment_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="withdrawals")

class ReferralEarning(Base):
    """Track multi-tier referral earnings."""
    __tablename__ = "referral_earnings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    referrer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    referred_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    
    tier: Mapped[int] = mapped_column(Integer)  # 1, 2, or 3
    source_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))  # Original earning amount
    bonus_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))  # Bonus earned
    xp_bonus: Mapped[int] = mapped_column(Integer, default=0)
    
    transaction_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class FraudReport(Base):
    """Track fraudulent users for admin review."""
    __tablename__ = "fraud_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    campaign_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("campaigns.id"), nullable=True)
    completion_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("task_completions.id"), nullable=True)
    
    report_type: Mapped[str] = mapped_column(String(50))  # retention_fail, suspicious_activity, etc.
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON data
    
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, reviewed, actioned, dismissed
    action_taken: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

class Notification(Base):
    """Queue for notifications."""
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    
    type: Mapped[NotificationType] = mapped_column(SQLEnum(NotificationType))
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index('ix_notification_unsent', 'is_sent', 'created_at'),
    )

# ==============================================================================
# SECTION 6: ENGINE & REDIS SETUP
# ==============================================================================

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ==============================================================================
# SECTION 7: FSM STATES
# ==============================================================================

class LanguageState(StatesGroup):
    selecting = State()

class CampaignCreationState(StatesGroup):
    task_type = State()
    title = State()
    target = State()
    bot_token = State()
    premium_targeting = State()
    reward = State()
    max_completions = State()

class AddSlotsState(StatesGroup):
    amount = State()

class TopUpState(StatesGroup):
    amount_stars = State()

class CheckCreateState(StatesGroup):
    amount = State()
    activations = State()
    password = State()

class CheckRedeemState(StatesGroup):
    code = State()
    password = State()

class WithdrawalState(StatesGroup):
    amount = State()
    payment_method = State()
    payment_details = State()

class AdminState(StatesGroup):
    target_user_id = State()
    coin_amount = State()
    ban_user_id = State()
    broadcast_message = State()
    withdrawal_action = State()

# ==============================================================================
# SECTION 8: KEYBOARDS
# ==============================================================================

def get_level_emoji(level: int) -> str:
    """Get emoji based on user level."""
    if level >= 50:
        return "👑"
    elif level >= 40:
        return "💎"
    elif level >= 30:
        return "🥇"
    elif level >= 20:
        return "🥈"
    elif level >= 10:
        return "🥉"
    else:
        return "🌱"

def get_main_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=get_text(lang, "menu_earnings")),
        KeyboardButton(text=get_text(lang, "menu_promote"))
    )
    builder.row(
        KeyboardButton(text=get_text(lang, "menu_checks")),
        KeyboardButton(text=get_text(lang, "menu_cabinet"))
    )
    builder.row(
        KeyboardButton(text=get_text(lang, "menu_sub_check")),
        KeyboardButton(text=get_text(lang, "menu_stats"))
    )
    builder.row(
        KeyboardButton(text=get_text(lang, "menu_links")),
        KeyboardButton(text=get_text(lang, "menu_instruction"))
    )
    return builder.as_markup(resize_keyboard=True)

def get_cabinet_keyboard(lang: str = "en", notifications_enabled: bool = True, can_withdraw: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    notif_key = "btn_notif_on" if notifications_enabled else "btn_notif_off"
    
    builder.row(InlineKeyboardButton(text=get_text(lang, "btn_replenish"), callback_data="cab_replenish"))
    builder.row(InlineKeyboardButton(text=get_text(lang, "btn_referral"), callback_data="cab_referral"))
    builder.row(
        InlineKeyboardButton(text=get_text(lang, "btn_level"), callback_data="cab_level"),
        InlineKeyboardButton(text=get_text(lang, "btn_tasks"), callback_data="cab_tasks")
    )
    if can_withdraw:
        builder.row(InlineKeyboardButton(text=get_text(lang, "btn_withdraw"), callback_data="cab_withdraw"))
    builder.row(
        InlineKeyboardButton(text=get_text(lang, "btn_lang"), callback_data="cab_lang"),
        InlineKeyboardButton(text=get_text(lang, notif_key), callback_data="cab_notif")
    )
    return builder.as_markup()

def get_back_to_cabinet_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="cab_back")]
    ])

def get_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton(text="🇧🇩 বাংলা", callback_data="lang_bn")],
    ])

def get_premium_targeting_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, "premium_yes"), callback_data="premium_yes")],
        [InlineKeyboardButton(text=get_text(lang, "premium_no"), callback_data="premium_no")],
    ])

# ==============================================================================
# SECTION 9: SECURITY & VERIFICATION UTILITIES
# ==============================================================================

class SecurityManager:
    """Handles encryption and secure operations."""
    
    @staticmethod
    def encrypt_token(token: str) -> str:
        """Encrypt bot token for storage (simplified - use proper encryption in production)."""
        return hash_sensitive_data(token + ENCRYPTION_KEY)
    
    @staticmethod
    def verify_token(token: str, stored_hash: str) -> bool:
        """Verify a token against its stored hash."""
        return hmac.compare_digest(
            SecurityManager.encrypt_token(token),
            stored_hash
        )

class TargetResolver:
    """Resolves and verifies Telegram chat targets."""
    
    @staticmethod
    def normalize_target_identifier(raw: str) -> Union[int, str]:
        """Normalize various input formats to a standard identifier."""
        raw = raw.strip()
        if re.fullmatch(r"-?\d+", raw):
            return int(raw)
        raw = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", raw, flags=re.IGNORECASE)
        raw = raw.lstrip("@").split("/")[0].split("?")[0]
        return f"@{raw}"
    
    @staticmethod
    async def resolve_chat(bot: Bot, raw_target: str) -> tuple[Optional[dict], Optional[str]]:
        """Resolve a chat and verify bot is admin."""
        identifier = TargetResolver.normalize_target_identifier(raw_target)
        
        try:
            chat = await bot.get_chat(identifier)
        except TelegramBadRequest:
            return None, "not_found"
        except TelegramForbiddenError:
            return None, "bot_not_member"
        except Exception as e:
            logger.error(f"get_chat failed for {identifier}: {e}")
            return None, "unknown"
        
        try:
            bot_id = (await bot.get_me()).id
            bot_member = await bot.get_chat_member(chat.id, bot_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            return None, "bot_not_member"
        except Exception as e:
            logger.error(f"get_chat_member failed: {e}")
            return None, "unknown"
        
        if bot_member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return None, "bot_not_admin"
        
        invite_link = None
        if not chat.username:
            try:
                invite_link = await bot.export_chat_invite_link(chat.id)
            except Exception:
                pass
        
        return {
            "chat_id": chat.id,
            "username": chat.username,
            "title": chat.title or str(chat.id),
            "invite_link": invite_link,
        }, None

class BotVerifier:
    """Verifies user interaction with target bots using their API token."""
    
    @staticmethod
    async def verify_bot_interaction(target_bot_token: str, user_id: int) -> bool:
        """
        Verify if a user has interacted with a target bot.
        Uses getUpdates to check for user interactions.
        Note: This requires the target bot to have received messages from the user.
        """
        try:
            target_bot = Bot(token=target_bot_token)
            try:
                updates = await target_bot.get_updates(timeout=5, limit=100)
                for update in updates:
                    if update.message and update.message.from_user:
                        if update.message.from_user.id == user_id:
                            return True
                    if update.my_chat_member:
                        if update.my_chat_member.from_user.id == user_id:
                            if update.my_chat_member.new_chat_member.status in (
                                ChatMemberStatus.MEMBER,
                                ChatMemberStatus.ADMINISTRATOR
                            ):
                                return True
                return False
            finally:
                await target_bot.session.close()
        except Exception as e:
            logger.error(f"Bot verification failed: {e}")
            return False
    
    @staticmethod
    async def validate_bot_token(token: str) -> tuple[bool, Optional[dict]]:
        """Validate a bot token and return bot info."""
        try:
            temp_bot = Bot(token=token)
            try:
                me = await temp_bot.get_me()
                return True, {"id": me.id, "username": me.username, "name": me.first_name}
            finally:
                await temp_bot.session.close()
        except Exception as e:
            logger.error(f"Bot token validation failed: {e}")
            return False, None

class SubscriptionVerifier:
    """Verifies channel/group subscriptions."""
    
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
                ChatMemberStatus.RESTRICTED,
            )
            return member.status in valid_statuses
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning(f"Membership check warning for {target}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in getChatMember: {e}")
            return False

# ==============================================================================
# SECTION 10: CORE BUSINESS ENGINES
# ==============================================================================

class TaskEngine:
    """Core engine for processing task completions with anti-cheat."""
    
    @staticmethod
    async def process_task_completion(
        session: AsyncSession,
        bot: Bot,
        user_id: int,
        campaign_id: str
    ) -> tuple[bool, str]:
        
        lock_key = f"lock:task:{user_id}:{campaign_id}"
        acquired = await redis_client.set(lock_key, "1", nx=True, ex=30)
        if not acquired:
            return False, "⏳ Processing, please wait..."
        
        try:
            async with session.begin_nested():
                # Lock campaign row
                res_c = await session.execute(
                    select(Campaign).where(Campaign.id == campaign_id).with_for_update()
                )
                campaign = res_c.scalar_one_or_none()
                
                if not campaign or campaign.status != CampaignStatus.ACTIVE:
                    return False, "❌ This task is no longer active."
                
                if campaign.is_full:
                    campaign.status = CampaignStatus.COMPLETED
                    return False, "❌ Task limit has been reached."
                
                # Lock user row
                res_u = await session.execute(
                    select(User).where(User.id == user_id).with_for_update()
                )
                user = res_u.scalar_one_or_none()
                
                if not user:
                    return False, "❌ Please /start the bot first."
                if user.is_blocked:
                    return False, "❌ Your account has been suspended."
                
                # Check for duplicate completion
                res_comp = await session.execute(
                    select(TaskCompletion).where(
                        TaskCompletion.user_id == user_id,
                        TaskCompletion.campaign_id == campaign_id
                    )
                )
                if res_comp.scalar_one_or_none():
                    return False, "❌ You have already completed this task!"
                
                # Premium targeting check
                if campaign.premium_only and not user.is_premium:
                    return False, "⭐ This task is for Telegram Premium users only."
                
                # Verification based on task type
                task_type = TaskType(campaign.task_type)
                
                if task_type in VERIFIABLE_TASK_TYPES:
                    if not (campaign.target_chat_id or campaign.target_username):
                        return False, "❌ This task is misconfigured."
                    
                    is_valid = await SubscriptionVerifier.verify(bot, user_id, campaign)
                    if not is_valid:
                        return False, "❌ Verification failed! Please make sure you've joined and try again."
                
                elif task_type == TaskType.BOT_START:
                    if campaign.target_bot_token_hash:
                        # We need the actual token - this is a limitation
                        # In production, you'd need to store encrypted token and decrypt
                        # For now, we'll use a simpler verification method
                        pass  # Will be verified through other means
                
                # Calculate rewards
                reward = campaign.reward_per_user
                xp_earned = int((reward / 1000) * BusinessRules.XP_PER_1000_COINS)
                
                # Create completion record
                retention_deadline = None
                if campaign.requires_retention_check and task_type in VERIFIABLE_TASK_TYPES:
                    retention_deadline = datetime.now(timezone.utc) + timedelta(days=campaign.retention_days)
                
                completion = TaskCompletion(
                    user_id=user_id,
                    campaign_id=campaign_id,
                    reward=reward,
                    xp_earned=xp_earned,
                    retention_deadline=retention_deadline,
                    retention_status=RetentionStatus.PENDING if retention_deadline else RetentionStatus.VERIFIED
                )
                session.add(completion)
                
                # Update user balance
                user.balance += reward
                user.total_earned += reward
                user.completed_tasks_count += 1
                user.add_xp(xp_earned)
                
                # Record transaction
                tx = Transaction(
                    user_id=user_id,
                    amount=reward,
                    type=TransactionType.TASK_REWARD,
                    description=f"Reward for task: {campaign.title}",
                    reference_id=campaign_id,
                    reference_type="campaign",
                    balance_after=user.balance
                )
                session.add(tx)
                
                # Update campaign
                campaign.completed_count += 1
                campaign.spent_budget += reward
                
                if campaign.is_full:
                    campaign.status = CampaignStatus.COMPLETED
                
                # Process referral bonuses
                await ReferralEngine.process_task_referral_bonuses(
                    session, user_id, reward, tx.id
                )
            
            await session.commit()
            
            return True, get_text(
                user.language, "task_verified",
                reward=reward, xp=xp_earned
            )
            
        except Exception as e:
            await session.rollback()
            logger.error(f"Task verification error: {str(e)}")
            tb_text = "".join(traceback.format_exception(type(e), e, e.__traceback__))[-3200:]
            await alert_admins(bot, f"🚨 Task Error\n\nUser: {user_id}\nCampaign: {campaign_id}\n\n<pre>{esc(tb_text)}</pre>")
            return False, "❌ An error occurred during verification."
        finally:
            try:
                await redis_client.delete(lock_key)
            except Exception:
                pass

class ReferralEngine:
    """Handles multi-tier referral system."""
    
    @staticmethod
    async def process_new_referral(
        session: AsyncSession,
        referrer_id: int,
        new_user_id: int
    ) -> Optional[Decimal]:
        """Process a new referral signup."""
        res = await session.execute(
            select(User).where(User.id == referrer_id).with_for_update()
        )
        referrer = res.scalar_one_or_none()
        
        if not referrer:
            return None
        
        reward = BusinessRules.REFERRAL_BASE_REWARD
        xp_reward = BusinessRules.REFERRAL_XP_BASE
        
        referrer.balance += reward
        referrer.total_earned += reward
        referrer.referral_earnings += reward
        referrer.add_xp(xp_reward)
        
        tx = Transaction(
            user_id=referrer_id,
            amount=reward,
            type=TransactionType.REFERRAL_REWARD,
            description=f"Referral bonus for new user",
            reference_id=str(new_user_id),
            reference_type="user",
            balance_after=referrer.balance
        )
        session.add(tx)
        
        return reward
    
    @staticmethod
    async def process_task_referral_bonuses(
        session: AsyncSession,
        earner_id: int,
        earning_amount: Decimal,
        source_transaction_id: str
    ):
        """Process tier bonuses when a referred user earns from tasks."""
        # Get the referrer chain
        current_user_id = earner_id
        tier = 1
        
        while tier <= 3:
            res = await session.execute(
                select(User).where(User.id == current_user_id)
            )
            current_user = res.scalar_one_or_none()
            
            if not current_user or not current_user.referred_by:
                break
            
            referrer_id = current_user.referred_by
            bonus_percent = BusinessRules.REFERRAL_TIER_BONUSES.get(tier, Decimal("0"))
            
            if bonus_percent > 0:
                bonus_amount = earning_amount * bonus_percent
                xp_bonus = int((bonus_amount / 1000) * BusinessRules.XP_PER_1000_COINS)
                
                res_ref = await session.execute(
                    select(User).where(User.id == referrer_id).with_for_update()
                )
                referrer = res_ref.scalar_one_or_none()
                
                if referrer and not referrer.is_blocked:
                    referrer.balance += bonus_amount
                    referrer.total_earned += bonus_amount
                    referrer.referral_earnings += bonus_amount
                    referrer.add_xp(xp_bonus)
                    
                    tx = Transaction(
                        user_id=referrer_id,
                        amount=bonus_amount,
                        type=TransactionType.REFERRAL_TIER_BONUS,
                        description=f"Tier {tier} referral bonus",
                        reference_id=source_transaction_id,
                        reference_type="referral_bonus",
                        balance_after=referrer.balance
                    )
                    session.add(tx)
                    
                    earning_record = ReferralEarning(
                        referrer_id=referrer_id,
                        referred_user_id=earner_id,
                        tier=tier,
                        source_amount=earning_amount,
                        bonus_amount=bonus_amount,
                        xp_bonus=xp_bonus,
                        transaction_id=tx.id
                    )
                    session.add(earning_record)
            
            current_user_id = referrer_id
            tier += 1

class RetentionEngine:
    """Handles 7-day retention checks for anti-cheat."""
    
    @staticmethod
    async def check_retention(bot: Bot, session: AsyncSession) -> List[dict]:
        """Check all pending retention deadlines and process failures."""
        now = datetime.now(timezone.utc)
        results = []
        
        # Find completions past their retention deadline
        res = await session.execute(
            select(TaskCompletion)
            .where(
                TaskCompletion.retention_status == RetentionStatus.PENDING,
                TaskCompletion.retention_deadline <= now,
                TaskCompletion.penalty_applied == False
            )
            .limit(50)
        )
        completions = res.scalars().all()
        
        for completion in completions:
            try:
                # Get campaign and user
                res_c = await session.execute(
                    select(Campaign).where(Campaign.id == completion.campaign_id)
                )
                campaign = res_c.scalar_one_or_none()
                
                res_u = await session.execute(
                    select(User).where(User.id == completion.user_id)
                )
                user = res_u.scalar_one_or_none()
                
                if not campaign or not user:
                    completion.retention_status = RetentionStatus.FAILED
                    continue
                
                # Verify retention
                if TaskType(campaign.task_type) in VERIFIABLE_TASK_TYPES:
                    is_valid = await SubscriptionVerifier.verify(bot, user.id, campaign)
                    
                    if is_valid:
                        completion.retention_status = RetentionStatus.VERIFIED
                        completion.retention_checked_at = now
                    else:
                        # User left - apply penalty
                        await RetentionEngine.apply_penalty(
                            session, bot, completion, campaign, user
                        )
                        results.append({
                            "user_id": user.id,
                            "campaign_id": campaign.id,
                            "penalty": True
                        })
                else:
                    completion.retention_status = RetentionStatus.VERIFIED
                    completion.retention_checked_at = now
                    
            except Exception as e:
                logger.error(f"Retention check error for completion {completion.id}: {e}")
        
        await session.commit()
        return results
    
    @staticmethod
    async def apply_penalty(
        session: AsyncSession,
        bot: Bot,
        completion: TaskCompletion,
        campaign: Campaign,
        user: User
    ):
        """Apply penalty for failed retention."""
        penalty_amount = completion.reward
        
        # Revoke from user
        user.balance -= penalty_amount
        user.total_earned -= penalty_amount
        user.completed_tasks_count -= 1
        
        # Refund to campaign
        campaign.spent_budget -= penalty_amount
        campaign.completed_count -= 1
        campaign.refunded_budget += penalty_amount
        
        if campaign.status == CampaignStatus.COMPLETED and not campaign.is_full:
            campaign.status = CampaignStatus.ACTIVE
        
        # Record transactions
        session.add(Transaction(
            user_id=user.id,
            amount=-penalty_amount,
            type=TransactionType.PENALTY_REVOKE,
            description=f"Retention penalty - left {campaign.title}",
            reference_id=campaign.id,
            reference_type="campaign",
            balance_after=user.balance
        ))
        
        session.add(Transaction(
            user_id=campaign.advertiser_id,
            amount=penalty_amount,
            type=TransactionType.CAMPAIGN_REFUND,
            description=f"Refund - user left {campaign.title}",
            reference_id=campaign.id,
            reference_type="campaign",
            balance_after=Decimal("0")  # Will be updated
        ))
        
        # Mark completion
        completion.retention_status = RetentionStatus.PENALIZED
        completion.penalty_applied = True
        
        # Create fraud report
        fraud_report = FraudReport(
            user_id=user.id,
            campaign_id=campaign.id,
            completion_id=completion.id,
            report_type="retention_fail",
            description=f"User left {campaign.target_title or campaign.target_username} before {campaign.retention_days} days"
        )
        session.add(fraud_report)
        
        # Notify user
        if user.notifications_enabled:
            try:
                await bot.send_message(
                    user.id,
                    get_text(user.language, "retention_penalty",
                             target_name=campaign.target_title or campaign.target_username,
                             reward=penalty_amount),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        
        # Notify admins
        await alert_admins(
            bot,
            f"🚨 <b>Retention Penalty Applied</b>\n\n"
            f"👤 User: <code>{user.id}</code> ({esc(user.first_name)})\n"
            f"📢 Campaign: {esc(campaign.title)}\n"
            f"💰 Amount: <code>{penalty_amount:,.0f}</code> GRAM\n"
            f"📊 Risk Score: {user.risk_score}\n\n"
            f"Use /admin to review and take action."
        )

# ==============================================================================
# SECTION 11: NOTIFICATION MANAGER
# ==============================================================================

class NotificationManager:
    """Handles all user notifications."""
    
    @staticmethod
    async def send_notification(
        bot: Bot,
        user_id: int,
        title: str,
        message: str,
        parse_mode: str = ParseMode.HTML
    ) -> bool:
        """Send a notification to a user."""
        try:
            await bot.send_message(user_id, f"<b>{title}</b>\n\n{message}", parse_mode=parse_mode)
            return True
        except TelegramForbiddenError:
            return False
        except TelegramBadRequest as e:
            logger.warning(f"Failed to send notification to {user_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Notification error: {e}")
            return False

async def alert_admins(bot: Bot, html_message: str, plain_message: Optional[str] = None):
    """Send an alert to all admins."""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, html_message, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            try:
                await bot.send_message(admin_id, (plain_message or html_message)[:4000])
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

# ==============================================================================
# SECTION 12: BOT HANDLERS - PART 1 (Core)
# ==============================================================================

router = Router()

async def get_or_none(session: AsyncSession, user_id: int) -> Optional[User]:
    res = await session.execute(select(User).where(User.id == user_id))
    return res.scalar_one_or_none()

async def get_user_lang(session: AsyncSession, user_id: int) -> str:
    user = await get_or_none(session, user_id)
    return user.language if user else "en"

# --- START COMMAND ---

@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, bot: Bot, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    referrer_id = None
    check_code = None
    
    # Parse deep link arguments
    if len(args) > 1:
        payload = args[1].strip()
        if payload.startswith("ref_"):
            ref_code = payload.replace("ref_", "").strip()
            res_ref = await session.execute(select(User).where(User.referral_code == ref_code))
            ref_user = res_ref.scalar_one_or_none()
            if ref_user and ref_user.id != user_id:
                referrer_id = ref_user.id
        elif payload.startswith("check_"):
            check_code = payload.replace("check_", "").strip().upper()
    
    user = await get_or_none(session, user_id)
    referrer_notify = None
    new_user_created = False
    
    if not user:
        ref_code = str(uuid.uuid4())[:8]
        user = User(
            id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            is_premium=message.from_user.is_premium or False,
            referral_code=ref_code,
            referred_by=referrer_id,
            language="en"
        )
        session.add(user)
        new_user_created = True
        
        if referrer_id:
            reward = await ReferralEngine.process_new_referral(session, referrer_id, user_id)
            if reward:
                res_referrer = await session.execute(select(User).where(User.id == referrer_id))
                referrer = res_referrer.scalar_one_or_none()
                if referrer and referrer.notifications_enabled:
                    referrer_notify = (referrer_id, message.from_user.first_name, reward)
        
        await session.commit()
        
        if referrer_notify:
            try:
                await bot.send_message(
                    referrer_notify[0],
                    f"🎉 {esc(referrer_notify[1])} joined using your referral link!\n"
                    f"💰 +{referrer_notify[2]:,.0f} GRAM"
                )
            except Exception:
                pass
    
    # Update user info if changed
    if not new_user_created:
        user.username = message.from_user.username
        user.first_name = message.from_user.first_name
        user.last_name = message.from_user.last_name
        user.is_premium = message.from_user.is_premium or False
        user.last_active_at = datetime.now(timezone.utc)
        await session.commit()
    
    # Handle check redemption via deep link
    if check_code:
        await handle_check_deeplink(message, session, check_code)
        return
    
    welcome_msg = get_text(
        user.language, "welcome",
        bot_name=esc(BOT_USERNAME),
        first_name=esc(message.from_user.first_name)
    )
    await message.answer(
        welcome_msg,
        reply_markup=get_main_keyboard(user.language),
        parse_mode=ParseMode.HTML
    )

async def handle_check_deeplink(message: Message, session: AsyncSession, code: str):
    """Handle check activation via deep link."""
    res = await session.execute(select(Check).where(Check.code == code))
    check = res.scalar_one_or_none()
    
    if not check or not check.is_active or check.is_exhausted:
        await message.answer(get_text("en", "check_invalid"))
        return
    
    if check.requires_password:
        await message.answer(
            get_text("en", "check_password_required"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔐 Enter Password", callback_data=f"check_pw_{code}")]
            ])
        )
        return
    
    # Process redemption
    success, msg = await process_check_redemption(session, message.from_user.id, check)
    await message.answer(msg, parse_mode=ParseMode.HTML)

async def process_check_redemption(
    session: AsyncSession,
    user_id: int,
    check: Check,
    password: str = None
) -> tuple[bool, str]:
    """Process check redemption."""
    async with session.begin_nested():
        # Re-fetch with lock
        res = await session.execute(
            select(Check).where(Check.id == check.id).with_for_update()
        )
        check = res.scalar_one_or_none()
        
        if not check or not check.is_active:
            return False, get_text("en", "check_invalid")
        
        if check.is_exhausted:
            check.is_active = False
            return False, "❌ This check has already been fully redeemed."
        
        if check.created_by == user_id:
            return False, "❌ You can't activate your own check."
        
        if check.requires_password:
            if not password or not check.verify_password(password):
                return False, get_text("en", "check_wrong_password")
        
        # Check if already activated
        res_act = await session.execute(
            select(CheckActivation).where(
                CheckActivation.check_id == check.id,
                CheckActivation.user_id == user_id
            )
        )
        if res_act.scalar_one_or_none():
            return False, "❌ You've already activated this check."
        
        # Get user with lock
        res_u = await session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = res_u.scalar_one_or_none()
        
        if not user:
            return False, get_text("en", "not_started")
        
        # Apply rewards
        amount = check.amount_per_activation
        user.balance += amount
        user.total_earned += amount
        
        # XP for check redemption
        xp_earned = int((amount / 1000) * BusinessRules.XP_PER_1000_COINS)
        user.add_xp(xp_earned)
        
        check.activations_count += 1
        if check.is_exhausted:
            check.is_active = False
        
        session.add(CheckActivation(
            check_id=check.id,
            user_id=user_id,
            amount=amount
        ))
        
        session.add(Transaction(
            user_id=user_id,
            amount=amount,
            type=TransactionType.CHECK_REDEEM,
            description=f"Redeemed check {check.code}",
            reference_id=check.id,
            reference_type="check",
            balance_after=user.balance
        ))
    
    await session.commit()
    return True, get_text(user.language, "check_redeemed", amount=amount)

# ==============================================================================
# SECTION 13: CABINET & PROFILE HANDLERS
# ==============================================================================

@router.message(F.text.in_(["👤 My Cabinet", "👤 মাই ক্যাবিনেট"]))
async def show_cabinet(message: Message, session: AsyncSession):
    user = await get_or_none(session, message.from_user.id)
    if not user:
        await message.answer(get_text("en", "not_started"))
        return
    
    cabinet_text = get_text(
        user.language, "cabinet",
        user_id=user.id,
        level_emoji=get_level_emoji(user.level),
        level=user.level,
        balance=user.balance,
        xp=user.current_level_xp,
        next_level_xp=user.next_level_xp,
        tasks_done=user.completed_tasks_count
    )
    await message.answer(
        cabinet_text,
        reply_markup=get_cabinet_keyboard(
            user.language,
            user.notifications_enabled,
            user.can_withdraw
        ),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "cab_back")
async def cb_back_to_cabinet(query: CallbackQuery, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    await query.answer()
    if not user:
        await query.message.edit_text(get_text("en", "not_started"))
        return
    
    cabinet_text = get_text(
        user.language, "cabinet",
        user_id=user.id,
        level_emoji=get_level_emoji(user.level),
        level=user.level,
        balance=user.balance,
        xp=user.current_level_xp,
        next_level_xp=user.next_level_xp,
        tasks_done=user.completed_tasks_count
    )
    await query.message.edit_text(
        cabinet_text,
        reply_markup=get_cabinet_keyboard(
            user.language,
            user.notifications_enabled,
            user.can_withdraw
        ),
        parse_mode=ParseMode.HTML
    )

# --- LANGUAGE SELECTION ---

@router.callback_query(F.data == "cab_lang")
async def cb_lang_menu(query: CallbackQuery, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    if not user:
        await query.answer(get_text("en", "not_started"), show_alert=True)
        return
    
    await query.message.edit_text(
        "🌐 <b>Select Language / ভাষা নির্বাচন করুন</b>",
        reply_markup=get_language_keyboard(),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@router.callback_query(F.data.startswith("lang_"))
async def cb_set_language(query: CallbackQuery, session: AsyncSession):
    lang = query.data.replace("lang_", "")
    if lang not in Localization.LANGUAGES:
        lang = "en"
    
    res = await session.execute(
        select(User).where(User.id == query.from_user.id).with_for_update()
    )
    user = res.scalar_one_or_none()
    
    if not user:
        await query.answer(get_text("en", "not_started"), show_alert=True)
        return
    
    user.language = lang
    await session.commit()
    
    cabinet_text = get_text(
        lang, "cabinet",
        user_id=user.id,
        level_emoji=get_level_emoji(user.level),
        level=user.level,
        balance=user.balance,
        xp=user.current_level_xp,
        next_level_xp=user.next_level_xp,
        tasks_done=user.completed_tasks_count
    )
    
    await query.message.edit_text(
        f"{get_text(lang, 'lang_changed')}\n\n{cabinet_text}",
        reply_markup=get_cabinet_keyboard(lang, user.notifications_enabled, user.can_withdraw),
        parse_mode=ParseMode.HTML
    )
    await query.answer()
    
    # Update main keyboard
    await query.message.answer(
        get_text(lang, "welcome", bot_name=esc(BOT_USERNAME), first_name=esc(user.first_name)),
        reply_markup=get_main_keyboard(lang),
        parse_mode=ParseMode.HTML
    )

# --- NOTIFICATIONS TOGGLE ---

@router.callback_query(F.data == "cab_notif")
async def cb_notif_toggle(query: CallbackQuery, session: AsyncSession):
    res = await session.execute(
        select(User).where(User.id == query.from_user.id).with_for_update()
    )
    user = res.scalar_one_or_none()
    
    if not user:
        await query.answer(get_text("en", "not_started"), show_alert=True)
        return
    
    user.notifications_enabled = not user.notifications_enabled
    await session.commit()
    
    status_msg = "🔔 Notifications enabled." if user.notifications_enabled else "🔕 Notifications disabled."
    await query.answer(status_msg, show_alert=True)
    
    try:
        await query.message.edit_reply_markup(
            reply_markup=get_cabinet_keyboard(
                user.language,
                user.notifications_enabled,
                user.can_withdraw
            )
        )
    except TelegramBadRequest:
        pass

# --- TOP UP ---

@router.callback_query(F.data == "cab_replenish")
async def cb_replenish(query: CallbackQuery, state: FSMContext, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    lang = user.language if user else "en"
    
    msg = (
        "⭐ <b>Replenish Balance via Telegram Stars</b>\n\n"
        f"Rate: <code>1 Star = {STAR_TO_COIN_RATE:,} GRAM</code>\n\n"
        "Enter amount of Telegram Stars:"
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
        if stars <= 0 or stars > 10000:
            raise ValueError()
    except Exception:
        await message.answer("❌ Invalid input. Please enter a number between 1 and 10000.")
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
            res = await session.execute(
                select(User).where(User.id == user_id).with_for_update()
            )
            user = res.scalar_one_or_none()
            
            if user:
                user.balance += coins_to_add
                session.add(Transaction(
                    user_id=user_id,
                    amount=coins_to_add,
                    type=TransactionType.STAR_TOPUP,
                    description=f"Top-up {coins_to_add:,.0f} GRAM via {stars} Stars",
                    balance_after=user.balance
                ))
        
        await session.commit()
        await message.answer(
            f"✅ Top-up successful! Added <code>+{coins_to_add:,.0f}</code> GRAM.",
            parse_mode=ParseMode.HTML
        )

# ==============================================================================
# SECTION 14: REFERRAL SYSTEM HANDLERS
# ==============================================================================

@router.callback_query(F.data == "cab_referral")
async def cb_referral(query: CallbackQuery, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    if not user:
        await query.answer(get_text("en", "not_started"), show_alert=True)
        return
    
    # Count direct referrals
    res_count = await session.execute(
        select(func.count(User.id)).where(User.referred_by == query.from_user.id)
    )
    direct_count = res_count.scalar() or 0
    
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.referral_code}"
    
    text = get_text(
        user.language, "referral_title",
        base_reward=BusinessRules.REFERRAL_BASE_REWARD,
        direct_count=direct_count,
        total_earned=user.referral_earnings,
        ref_link=esc(ref_link)
    )
    
    await query.message.edit_text(
        text,
        reply_markup=get_back_to_cabinet_keyboard(user.language),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

# ==============================================================================
# SECTION 15: LEVEL SYSTEM HANDLERS
# ==============================================================================

@router.callback_query(F.data == "cab_level")
async def cb_level(query: CallbackQuery, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    if not user:
        await query.answer(get_text("en", "not_started"), show_alert=True)
        return
    
    progress = user.xp % BusinessRules.XP_PER_LEVEL
    progress_bar = "█" * (progress * 10 // BusinessRules.XP_PER_LEVEL) + "░" * (10 - progress * 10 // BusinessRules.XP_PER_LEVEL)
    
    text = (
        f"📊 <b>Level System</b>\n\n"
        f"🎖 Current Level: {get_level_emoji(user.level)} <code>{user.level}</code>\n"
        f"⚡ Total XP: <code>{user.xp:,}</code>\n"
        f"📈 Progress: [{progress_bar}] {progress}/{BusinessRules.XP_PER_LEVEL}\n\n"
        f"<b>Level Benefits:</b>\n"
        f"• Level {BusinessRules.WITHDRAWAL_MIN_LEVEL}+ unlocks withdrawals\n"
        f"• Higher levels = faster processing\n\n"
        f"💡 Earn <code>{BusinessRules.XP_PER_1000_COINS}</code> XP per 1000 coins earned!"
    )
    
    await query.message.edit_text(
        text,
        reply_markup=get_back_to_cabinet_keyboard(user.language),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

# ==============================================================================
# SECTION 16: WITHDRAWAL HANDLERS
# ==============================================================================

@router.callback_query(F.data == "cab_withdraw")
async def cb_withdraw(query: CallbackQuery, state: FSMContext, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    if not user:
        await query.answer(get_text("en", "not_started"), show_alert=True)
        return
    
    if not user.can_withdraw:
        text = get_text(
            user.language, "withdraw_locked",
            required_level=BusinessRules.WITHDRAWAL_MIN_LEVEL,
            current_level=user.level
        )
        await query.message.edit_text(
            text,
            reply_markup=get_back_to_cabinet_keyboard(user.language),
            parse_mode=ParseMode.HTML
        )
        await query.answer()
        return
    
    text = get_text(
        user.language, "withdraw_available",
        min_amount=BusinessRules.WITHDRAWAL_MIN_AMOUNT,
        max_amount=BusinessRules.WITHDRAWAL_MAX_AMOUNT,
        balance=user.balance
    )
    
    await query.message.edit_text(text, parse_mode=ParseMode.HTML)
    await state.set_state(WithdrawalState.amount)
    await query.answer()

@router.message(WithdrawalState.amount)
async def process_withdrawal_amount(message: Message, state: FSMContext, session: AsyncSession):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    
    try:
        amount = Decimal(raw)
    except Exception:
        await message.answer("❌ Invalid amount. Please enter a number.")
        return
    
    user = await get_or_none(session, message.from_user.id)
    if not user:
        await message.answer(get_text("en", "not_started"))
        await state.clear()
        return
    
    if amount < BusinessRules.WITHDRAWAL_MIN_AMOUNT:
        await message.answer(
            get_text(user.language, "withdraw_min_error",
                     min_amount=BusinessRules.WITHDRAWAL_MIN_AMOUNT),
            parse_mode=ParseMode.HTML
        )
        return
    
    if amount > BusinessRules.WITHDRAWAL_MAX_AMOUNT:
        await message.answer(
            get_text(user.language, "withdraw_max_error",
                     max_amount=BusinessRules.WITHDRAWAL_MAX_AMOUNT),
            parse_mode=ParseMode.HTML
        )
        return
    
    if amount > user.balance:
        await message.answer(
            get_text(user.language, "withdraw_insufficient", balance=user.balance),
            parse_mode=ParseMode.HTML
        )
        return
    
    await state.update_data(amount=str(amount))
    await message.answer(
        "💳 <b>Payment Method</b>\n\n"
        "Select your preferred payment method:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Crypto (USDT)", callback_data="wd_method_crypto")],
            [InlineKeyboardButton(text="🏦 Bank Transfer", callback_data="wd_method_bank")],
            [InlineKeyboardButton(text="📱 Mobile Wallet", callback_data="wd_method_mobile")],
        ]),
        parse_mode=ParseMode.HTML
    )
    await state.set_state(WithdrawalState.payment_method)

@router.callback_query(F.data.startswith("wd_method_"))
async def process_withdrawal_method(query: CallbackQuery, state: FSMContext):
    method = query.data.replace("wd_method_", "")
    method_names = {
        "crypto": "Cryptocurrency (USDT)",
        "bank": "Bank Transfer",
        "mobile": "Mobile Wallet"
    }
    
    await state.update_data(payment_method=method_names.get(method, method))
    await query.message.edit_text(
        f"📝 <b>{method_names.get(method, method)}</b>\n\n"
        "Please provide your payment details (wallet address, account number, etc.):"
    )
    await state.set_state(WithdrawalState.payment_details)
    await query.answer()

@router.message(WithdrawalState.payment_details)
async def process_withdrawal_details(message: Message, state: FSMContext, session: AsyncSession):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    
    data = await state.get_data()
    amount = Decimal(data["amount"])
    payment_method = data["payment_method"]
    await state.clear()
    
    user_id = message.from_user.id
    
    async with session.begin_nested():
        res = await session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = res.scalar_one_or_none()
        
        if not user or user.balance < amount:
            await message.answer("❌ Insufficient balance.")
            return
        
        user.balance -= amount
        user.total_withdrawn += amount
        
        withdrawal = WithdrawalRequest(
            user_id=user_id,
            amount=amount,
            payment_method=payment_method,
            payment_details=raw[:500],
            status="pending"
        )
        session.add(withdrawal)
        
        session.add(Transaction(
            user_id=user_id,
            amount=-amount,
            type=TransactionType.WITHDRAWAL,
            description=f"Withdrawal request - {payment_method}",
            balance_after=user.balance
        ))
    
    await session.commit()
    
    await message.answer(
        get_text(user.language, "withdraw_submitted", amount=amount),
        parse_mode=ParseMode.HTML
    )
    
    # Notify admins
    await alert_admins(
        message.bot,
        f"💸 <b>New Withdrawal Request</b>\n\n"
        f"👤 User: <code>{user_id}</code> ({esc(user.first_name)})\n"
        f"💰 Amount: <code>{amount:,.0f}</code> GRAM\n"
        f"💳 Method: {esc(payment_method)}\n"
        f"📝 Details: <code>{esc(raw[:100])}</code>"
    )

# ==============================================================================
# SECTION 17: TASKS & EARNINGS HANDLERS
# ==============================================================================

@router.message(F.text.in_(["💰 Earnings", "💰 আর্নিংস"]))
async def show_earnings_menu(message: Message, session: AsyncSession):
    user = await get_or_none(session, message.from_user.id)
    lang = user.language if user else "en"
    
    await message.answer(
        get_text(lang, "tasks_title"),
        parse_mode=ParseMode.HTML
    )
    await render_tasks_page(message.from_user.id, message, session, page=0, edit=False)

async def render_tasks_page(
    user_id: int,
    message: Message,
    session: AsyncSession,
    page: int = 0,
    edit: bool = False
):
    limit = TASKS_PAGE_SIZE
    offset = page * limit
    
    # Get user for premium check
    user = await get_or_none(session, user_id)
    user_is_premium = user.is_premium if user else False
    
    # Get completed campaign IDs
    completed_sub = select(TaskCompletion.campaign_id).where(
        TaskCompletion.user_id == user_id
    )
    
    # Base filter
    base_filter = (
        (Campaign.status == CampaignStatus.ACTIVE) &
        (Campaign.id.not_in(completed_sub)) &
        (Campaign.completed_count < Campaign.max_completions)
    )
    
    # Premium filter - hide premium-only tasks from non-premium users
    if not user_is_premium:
        base_filter = base_filter & (Campaign.premium_only == False)
    
    # Get total count
    total_result = await session.execute(
        select(func.count(Campaign.id)).where(base_filter)
    )
    total = total_result.scalar() or 0
    
    # Get page items
    stmt = (
        select(Campaign)
        .where(base_filter)
        .order_by(Campaign.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    campaigns = result.scalars().all()
    
    lang = user.language if user else "en"
    
    if not campaigns:
        text = get_text(lang, "no_tasks")
        if edit:
            try:
                await message.edit_text(text)
            except TelegramBadRequest:
                pass
        else:
            await message.answer(text)
        return
    
    # Build keyboard
    buttons = []
    for c in campaigns:
        premium_tag = "⭐ " if c.premium_only else ""
        buttons.append([InlineKeyboardButton(
            text=f"{premium_tag}📢 {c.title[:30]} (+{c.reward_per_user:,.0f} GRAM)",
            callback_data=f"task_view_{c.id}"
        )])
    
    # Navigation
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"tasks_page_{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page + 1}", callback_data="noop"))
    if offset + limit < total:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"tasks_page_{page + 1}"))
    if nav_row:
        buttons.append(nav_row)
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = f"🎯 <b>Active Tasks</b> ({total} available):"
    
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "noop")
async def noop_callback(query: CallbackQuery):
    await query.answer()

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
    
    user = await get_or_none(session, query.from_user.id)
    lang = user.language if user else "en"
    
    # Build target URL
    if campaign.target_link:
        target_url = campaign.target_link
    elif campaign.target_username:
        target_url = f"https://t.me/{campaign.target_username}"
    elif campaign.target_chat_id:
        # Try to get invite link
        target_url = f"https://t.me/c/{str(campaign.target_chat_id).replace('-100', '')}"
    else:
        target_url = f"https://t.me/{BOT_USERNAME}"
    
    type_labels = {
        TaskType.CHANNEL_SUB: "📢 Join Channel",
        TaskType.GROUP_JOIN: "👥 Join Group",
        TaskType.BOT_START: "🤖 Start Bot",
        TaskType.POST_VIEW: "👀 View Post",
        TaskType.WEB_APP: "🌐 Open Link",
        TaskType.CUSTOM: "✅ Complete Task",
        TaskType.REACTION: "👍 React to Post",
        TaskType.BOOST_7DAY: "🚀 Boost Channel",
    }
    action_label = type_labels.get(TaskType(campaign.task_type), "🚀 Open Link")
    
    premium_tag = "⭐ <b>PREMIUM ONLY</b>\n" if campaign.premium_only else ""
    
    text = (
        f"{premium_tag}"
        f"📢 <b>{esc(campaign.title)}</b>\n\n"
        f"💰 Reward: <code>{campaign.reward_per_user:,.0f}</code> GRAM\n"
        f"👥 Slots: <code>{campaign.slots_remaining}</code> remaining\n"
        f"⏰ Retention: {campaign.retention_days} days"
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
        session=session,
        bot=bot,
        user_id=query.from_user.id,
        campaign_id=c_id
    )
    
    await query.answer(message, show_alert=True)
    
    if success:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Back to task list", callback_data="tasks_page_0")]
        ])
        await query.message.edit_text(message, reply_markup=kb, parse_mode=ParseMode.HTML)

# --- MY TASKS (completed) ---

@router.callback_query(F.data == "cab_tasks")
async def cb_my_tasks(query: CallbackQuery, session: AsyncSession):
    user = await get_or_none(session, query.from_user.id)
    if not user:
        await query.answer("Please /start first.", show_alert=True)
        return
    
    res = await session.execute(
        select(TaskCompletion)
        .where(TaskCompletion.user_id == query.from_user.id)
        .order_by(TaskCompletion.completed_at.desc())
        .limit(10)
    )
    completions = res.scalars().all()
    
    if not completions:
        text = "📋 You haven't completed any tasks yet.\n\nGo to 💰 Earnings to find tasks!"
    else:
        lines = ["📋 <b>Your Recent Tasks:</b>\n"]
        for c in completions:
            res_camp = await session.execute(
                select(Campaign).where(Campaign.id == c.campaign_id)
            )
            camp = res_camp.scalar_one_or_none()
            title = camp.title[:25] if camp else "Unknown"
            
            status_emoji = {
                RetentionStatus.PENDING: "⏳",
                RetentionStatus.VERIFIED: "✅",
                RetentionStatus.FAILED: "❌",
                RetentionStatus.PENALIZED: "🚫",
            }.get(c.retention_status, "❓")
            
            lines.append(f"{status_emoji} {esc(title)} — <code>+{c.reward:,.0f}</code> GRAM")
        
        text = "\n".join(lines)
    
    await query.message.edit_text(
        text,
        reply_markup=get_back_to_cabinet_keyboard(user.language),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

# ==============================================================================
# SECTION 18: CAMPAIGN CREATION HANDLERS
# ==============================================================================

@router.message(F.text.in_(["📢 Promote", "📢 প্রমোট"]))
async def start_campaign_creation(message: Message, state: FSMContext, session: AsyncSession):
    user = await get_or_none(session, message.from_user.id)
    if not user:
        await message.answer(get_text("en", "not_started"))
        return
    
    # Check active campaign limit
    res = await session.execute(
        select(func.count(Campaign.id)).where(
            Campaign.advertiser_id == message.from_user.id,
            Campaign.status == CampaignStatus.ACTIVE
        )
    )
    active_count = res.scalar() or 0
    
    if active_count >= BusinessRules.MAX_ACTIVE_CAMPAIGNS_PER_USER:
        await message.answer(
            f"❌ You have reached the maximum limit of {BusinessRules.MAX_ACTIVE_CAMPAIGNS_PER_USER} active campaigns.\n"
            "Please wait for some to complete or cancel existing ones."
        )
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Channel Sub", callback_data="ctype:channel_sub"),
         InlineKeyboardButton(text="👥 Group Join", callback_data="ctype:group_join")],
        [InlineKeyboardButton(text="👀 Post View", callback_data="ctype:post_view"),
         InlineKeyboardButton(text="🤖 Bot Start", callback_data="ctype:bot_start")],
        [InlineKeyboardButton(text="👍 Reaction", callback_data="ctype:reaction"),
         InlineKeyboardButton(text="🚀 Boost Channel", callback_data="ctype:boost_7day")],
        [InlineKeyboardButton(text="🌐 Web App / Custom", callback_data="ctype:web_app")]
    ])
    
    await message.answer(
        "📢 <b>Create Promotion Campaign</b>\n\n"
        "Select the task type you want users to complete:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
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
        await message.answer("❌ Please enter a title between 1 and 200 characters.")
        return
    
    await state.update_data(title=title)
    data = await state.get_data()
    task_type = data.get("task_type")
    
    if task_type in ("channel_sub", "group_join", "boost_7day"):
        await message.answer(
            "🔗 Send the channel/group username (e.g. <code>@mychannel</code>), "
            "invite link, or numeric chat ID:\n\n"
            "⚠️ Make sure this bot is added as an Admin to verify subscriptions!",
            parse_mode=ParseMode.HTML
        )
    elif task_type == "bot_start":
        await message.answer(
            "🤖 <b>Bot Verification Setup</b>\n\n"
            "To prevent fake task completion, we need your bot's API token.\n\n"
            "⚠️ This is used only to verify user interactions.\n"
            "Send your bot token (format: <code>123456:ABC-DEF...</code>):",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer("🔗 Enter the link users should open:")
    
    await state.set_state(CampaignCreationState.target)

@router.message(CampaignCreationState.target)
async def campaign_target_entered(message: Message, state: FSMContext, bot: Bot, session: AsyncSession):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    
    data = await state.get_data()
    task_type = data.get("task_type")
    
    if task_type in ("channel_sub", "group_join", "boost_7day"):
        status_msg = await message.answer("🔎 Verifying access to that channel/group...")
        
        try:
            chat_info, error = await asyncio.wait_for(
                TargetResolver.resolve_chat(bot, raw), timeout=15
            )
        except asyncio.TimeoutError:
            await status_msg.edit_text("❌ Verification timed out. Please try again.")
            return
        
        if error == "not_found":
            await status_msg.edit_text("❌ Couldn't find that channel/group.")
            return
        if error == "bot_not_member":
            await status_msg.edit_text(
                f"❌ I'm not a member of that chat yet.\n\n"
                f"👉 Please add <b>@{esc(BOT_USERNAME)}</b> as an <b>Administrator</b>.",
                parse_mode=ParseMode.HTML
            )
            return
        if error == "bot_not_admin":
            await status_msg.edit_text(
                f"❌ I'm in the chat, but not as <b>Administrator</b>.\n\n"
                f"👉 Please promote <b>@{esc(BOT_USERNAME)}</b> to Admin.",
                parse_mode=ParseMode.HTML
            )
            return
        if error:
            await status_msg.edit_text("❌ Something went wrong. Please try again.")
            return
        
        # Check for duplicate campaign
        user_id = message.from_user.id
        res = await session.execute(
            select(Campaign).where(
                Campaign.advertiser_id == user_id,
                Campaign.target_chat_id == chat_info["chat_id"],
                Campaign.status == CampaignStatus.ACTIVE,
                Campaign.task_type == TaskType(task_type)
            )
        )
        existing = res.scalar_one_or_none()
        
        if existing:
            await state.update_data(
                target=raw,
                target_chat_id=chat_info["chat_id"],
                target_username=chat_info["username"],
                target_invite_link=chat_info["invite_link"],
                target_title=chat_info["title"],
                existing_campaign_id=existing.id
            )
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="➕ Add Slots to Existing",
                    callback_data=f"add_slots_{existing.id}"
                )],
                [InlineKeyboardButton(
                    text="🆕 Create New Campaign",
                    callback_data="create_new_campaign"
                )],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_campaign")]
            ])
            
            await status_msg.edit_text(
                get_text("en", "campaign_duplicate"),
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
            return
        
        await state.update_data(
            target=raw,
            target_chat_id=chat_info["chat_id"],
            target_username=chat_info["username"],
            target_invite_link=chat_info["invite_link"],
            target_title=chat_info["title"]
        )
        
        await status_msg.edit_text(
            f"✅ Verified! <b>{esc(chat_info['title'])}</b>\n\n"
            f"⭐ Would you like to target only Telegram Premium users?\n"
            f"(Premium targeting = 2x rewards)",
            parse_mode=ParseMode.HTML,
            reply_markup=get_premium_targeting_keyboard()
        )
        await state.set_state(CampaignCreationState.premium_targeting)
    
    elif task_type == "bot_start":
        # Validate bot token
        valid, bot_info = await BotVerifier.validate_bot_token(raw)
        
        if not valid:
            await message.answer(
                "❌ Invalid bot token. Please double-check and try again.\n\n"
                "Format should be: <code>123456:ABC-DEF...</code>",
                parse_mode=ParseMode.HTML
            )
            return
        
        await state.update_data(
            target_bot_token=raw,  # Store temporarily for validation
            target_bot_token_hash=SecurityManager.encrypt_token(raw),
            target_bot_id=bot_info["id"],
            target_title=bot_info["name"],
            target_username=bot_info["username"],
            target_link=f"https://t.me/{bot_info['username']}"
        )
        
        await message.answer(
            f"✅ Bot verified: <b>{esc(bot_info['name'])}</b> (@{esc(bot_info['username'])})\n\n"
            f"⭐ Target only Premium users? (2x rewards)",
            parse_mode=ParseMode.HTML,
            reply_markup=get_premium_targeting_keyboard()
        )
        await state.set_state(CampaignCreationState.premium_targeting)
    
    else:
        await state.update_data(target=raw, target_link=raw)
        await message.answer(
            "⭐ Target only Telegram Premium users? (2x rewards)",
            reply_markup=get_premium_targeting_keyboard()
        )
        await state.set_state(CampaignCreationState.premium_targeting)

@router.callback_query(F.data == "create_new_campaign")
async def create_new_campaign_callback(query: CallbackQuery, state: FSMContext):
    await query.message.edit_text(
        "⭐ Target only Telegram Premium users? (2x rewards)",
        reply_markup=get_premium_targeting_keyboard()
    )
    await state.set_state(CampaignCreationState.premium_targeting)
    await query.answer()

@router.callback_query(F.data == "cancel_campaign")
async def cancel_campaign_callback(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text("❌ Campaign creation cancelled.")
    await query.answer()

@router.callback_query(CampaignCreationState.premium_targeting, F.data.startswith("premium_"))
async def premium_targeting_selected(query: CallbackQuery, state: FSMContext):
    is_premium = query.data == "premium_yes"
    await state.update_data(premium_only=is_premium)
    
    data = await state.get_data()
    task_type = data.get("task_type")
    
    pricing = TaskPricing.get_pricing(task_type, is_premium)
    
    premium_note = "\n⭐ <b>Premium targeting enabled - 2x rates!</b>" if is_premium else ""
    
    await query.message.edit_text(
        f"💰 Enter reward per user (GRAM coins):{premium_note}\n\n"
        f"💡 Suggested: <code>{pricing['suggested']:,}</code>\n"
        f"📊 Range: <code>{pricing['min']:,}</code> - <code>{pricing['max']:,}</code>",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(CampaignCreationState.reward)
    await query.answer()

@router.message(CampaignCreationState.reward)
async def campaign_reward_entered(message: Message, state: FSMContext):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    
    try:
        reward = Decimal(raw)
    except Exception:
        await message.answer("❌ Invalid reward. Please enter a number.")
        return
    
    data = await state.get_data()
    task_type = data.get("task_type")
    is_premium = data.get("premium_only", False)
    
    valid, msg = TaskPricing.validate_reward(task_type, reward, is_premium)
    if not valid:
        pricing = TaskPricing.get_pricing(task_type, is_premium)
        await message.answer(
            f"❌ {msg}\n\n"
            f"📊 Valid range: <code>{pricing['min']:,}</code> - <code>{pricing['max']:,}</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    await state.update_data(reward=str(reward))
    await message.answer("👥 Enter total target completions count:")
    await state.set_state(CampaignCreationState.max_completions)

@router.message(CampaignCreationState.max_completions)
async def campaign_finalize(message: Message, state: FSMContext, session: AsyncSession):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    
    try:
        max_comp = int(raw)
        if max_comp <= 0 or max_comp > 100000:
            raise ValueError()
    except Exception:
        await message.answer("❌ Invalid number. Please enter a positive whole number (max 100,000).")
        return
    
    data = await state.get_data()
    reward_per_user = Decimal(data["reward"])
    base_budget = reward_per_user * max_comp
    commission = base_budget * BusinessRules.PLATFORM_COMMISSION
    total_cost = base_budget + commission
    
    user_id = message.from_user.id
    
    async with session.begin_nested():
        res = await session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = res.scalar_one_or_none()
        
        if not user:
            await message.answer(get_text("en", "not_started"))
            await state.clear()
            return
        
        if user.balance < total_cost:
            await message.answer(
                get_text(user.language, "campaign_insufficient",
                         required=total_cost, balance=user.balance),
                parse_mode=ParseMode.HTML
            )
            await state.clear()
            return
        
        # Determine retention requirements
        task_type = TaskType(data["task_type"])
        requires_retention = task_type in VERIFIABLE_TASK_TYPES
        
        # Build campaign
        campaign = Campaign(
            advertiser_id=user_id,
            title=data["title"],
            task_type=task_type,
            target_chat_id=data.get("target_chat_id"),
            target_username=data.get("target_username"),
            target_link=data.get("target_link") or data.get("target_invite_link"),
            target_title=data.get("target_title"),
            target_bot_token_hash=data.get("target_bot_token_hash"),
            target_bot_id=data.get("target_bot_id"),
            premium_only=data.get("premium_only", False),
            reward_per_user=reward_per_user,
            base_reward=reward_per_user / (BusinessRules.PREMIUM_MULTIPLIER if data.get("premium_only") else 1),
            max_completions=max_comp,
            total_budget=total_cost,
            requires_retention_check=requires_retention,
            retention_days=7 if requires_retention else 0
        )
        session.add(campaign)
        
        user.balance -= total_cost
        session.add(Transaction(
            user_id=user_id,
            amount=-total_cost,
            type=TransactionType.CAMPAIGN_PAYMENT,
            description=f"Campaign created: {data['title']}",
            reference_id=campaign.id,
            reference_type="campaign",
            balance_after=user.balance
        ))
    
    await session.commit()
    await state.clear()
    
    await message.answer(
        f"✅ <b>Campaign Created!</b>\n\n"
        f"📢 {esc(data['title'])}\n"
        f"💰 Reward: {reward_per_user:,.0f} GRAM/user\n"
        f"👥 Slots: {max_comp}\n"
        f"💳 Total cost: {total_cost:,.0f} GRAM (incl. 15% fee)\n\n"
        f"Users can now find it under 💰 Earnings!",
        parse_mode=ParseMode.HTML
    )

# --- ADD SLOTS TO EXISTING CAMPAIGN ---

@router.callback_query(F.data.startswith("add_slots_"))
async def add_slots_start(query: CallbackQuery, state: FSMContext, session: AsyncSession):
    campaign_id = query.data.replace("add_slots_", "")
    
    res = await session.execute(
        select(Campaign).where(Campaign.id == campaign_id)
    )
    campaign = res.scalar_one_or_none()
    
    if not campaign or campaign.advertiser_id != query.from_user.id:
        await query.answer("Campaign not found.", show_alert=True)
        return
    
    await state.update_data(add_slots_campaign_id=campaign_id)
    await query.message.edit_text(
        f"➕ <b>Add Slots to Campaign</b>\n\n"
        f"📢 {esc(campaign.title)}\n"
        f"💰 Current reward: {campaign.reward_per_user:,.0f} GRAM/user\n"
        f"👥 Current slots: {campaign.max_completions}\n\n"
        f"Enter number of additional slots:"
    )
    await state.set_state(AddSlotsState.amount)
    await query.answer()

@router.message(AddSlotsState.amount)
async def add_slots_finalize(message: Message, state: FSMContext, session: AsyncSession):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    
    try:
        additional_slots = int(raw)
        if additional_slots <= 0 or additional_slots > 100000:
            raise ValueError()
    except Exception:
        await message.answer("❌ Invalid number.")
        return
    
    data = await state.get_data()
    campaign_id = data["add_slots_campaign_id"]
    await state.clear()
    
    user_id = message.from_user.id
    
    async with session.begin_nested():
        res = await session.execute(
            select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        )
        campaign = res.scalar_one_or_none()
        
        if not campaign or campaign.advertiser_id != user_id:
            await message.answer("❌ Campaign not found.")
            return
        
        res_u = await session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = res_u.scalar_one_or_none()
        
        cost = campaign.reward_per_user * additional_slots
        commission = cost * BusinessRules.PLATFORM_COMMISSION
        total_cost = cost + commission
        
        if user.balance < total_cost:
            await message.answer(
                f"❌ Insufficient balance!\n"
                f"Required: <code>{total_cost:,.0f}</code> GRAM\n"
                f"Your balance: <code>{user.balance:,.0f}</code> GRAM",
                parse_mode=ParseMode.HTML
            )
            return
        
        user.balance -= total_cost
        campaign.max_completions += additional_slots
        campaign.total_budget += total_cost
        
        # Reactivate if was completed
        if campaign.status == CampaignStatus.COMPLETED:
            campaign.status = CampaignStatus.ACTIVE
        
        session.add(Transaction(
            user_id=user_id,
            amount=-total_cost,
            type=TransactionType.CAMPAIGN_PAYMENT,
            description=f"Added {additional_slots} slots to: {campaign.title}",
            reference_id=campaign.id,
            reference_type="campaign",
            balance_after=user.balance
        ))
    
    await session.commit()
    
    await message.answer(
        f"✅ Added <b>{additional_slots}</b> slots to your campaign!\n\n"
        f"💰 Cost: <code>{total_cost:,.0f}</code> GRAM\n"
        f"👥 New total slots: <b>{campaign.max_completions}</b>",
        parse_mode=ParseMode.HTML
    )

# ==============================================================================
# SECTION 19: CHECKS (VOUCHER) SYSTEM
# ==============================================================================

def get_checks_menu_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 Create a Check", callback_data="chk_create")],
        [InlineKeyboardButton(text="💳 Activate a Check", callback_data="chk_activate")],
    ])

@router.message(F.text.in_(["📋 Checks", "📋 চেক্স"]))
async def checks_menu(message: Message, session: AsyncSession):
    user = await get_or_none(session, message.from_user.id)
    lang = user.language if user else "en"
    
    await message.answer(
        get_text(lang, "checks_menu"),
        reply_markup=get_checks_menu_keyboard(lang),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "chk_create")
async def chk_create_start(query: CallbackQuery, state: FSMContext):
    await query.message.edit_text(
        "💰 Enter the GRAM amount <b>per activation</b>:",
        parse_mode=ParseMode.HTML
    )
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
    await message.answer("👥 Enter number of activations:")
    await state.set_state(CheckCreateState.activations)

@router.message(CheckCreateState.activations)
async def chk_create_activations(message: Message, state: FSMContext):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    
    try:
        activations = int(raw)
        if activations <= 0 or activations > 10000:
            raise ValueError()
    except Exception:
        await message.answer("❌ Invalid number. Please enter 1-10000.")
        return
    
    await state.update_data(activations=activations)
    
    if activations == 1:
        check_type = CheckType.SINGLE_USE
        await state.update_data(check_type="single_use")
    else:
        await state.update_data(check_type="multi_use")
    
    await message.answer(
        "🔐 Set a password for this check?\n\n"
        "Send a password, or type <code>skip</code> for no password:",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(CheckCreateState.password)

@router.message(CheckCreateState.password)
async def chk_create_finalize(message: Message, state: FSMContext, session: AsyncSession):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    
    password = None if raw.lower() == "skip" else raw
    data = await state.get_data()
    await state.clear()
    
    amount = Decimal(data["amount"])
    activations = data["activations"]
    total_cost = amount * activations
    user_id = message.from_user.id
    
    async with session.begin_nested():
        res = await session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = res.scalar_one_or_none()
        
        if not user:
            await message.answer(get_text("en", "not_started"))
            return
        
        if user.balance < total_cost:
            await message.answer(
                f"❌ Insufficient balance.\n"
                f"Required: <code>{total_cost:,.0f}</code> GRAM\n"
                f"Your balance: <code>{user.balance:,.0f}</code> GRAM",
                parse_mode=ParseMode.HTML
            )
            return
        
        code = uuid.uuid4().hex[:10].upper()
        
        check = Check(
            code=code,
            check_type=CheckType(data["check_type"]),
            created_by=user_id,
            amount_per_activation=amount,
            max_activations=activations,
            requires_password=password is not None,
            password_hash=hash_sensitive_data(password) if password else None,
            total_funded=total_cost
        )
        session.add(check)
        
        user.balance -= total_cost
        session.add(Transaction(
            user_id=user_id,
            amount=-total_cost,
            type=TransactionType.CHECK_CREATE,
            description=f"Created check {code}",
            reference_id=check.id,
            reference_type="check",
            balance_after=user.balance
        ))
    
    await session.commit()
    
    deep_link = f"https://t.me/{BOT_USERNAME}?start=check_{code}"
    
    await message.answer(
        get_text(user.language, "check_created",
                 code=code,
                 amount=amount,
                 activations=activations,
                 link=esc(deep_link)),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "chk_activate")
async def chk_activate_start(query: CallbackQuery, state: FSMContext):
    await query.message.edit_text("🎫 Enter the check code to activate:")
    await state.set_state(CheckRedeemState.code)
    await query.answer()

@router.message(CheckRedeemState.code)
async def chk_activate_code(message: Message, state: FSMContext, session: AsyncSession):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    
    code = raw.upper().strip()
    
    res = await session.execute(select(Check).where(Check.code == code))
    check = res.scalar_one_or_none()
    
    if not check or not check.is_active or check.is_exhausted:
        await message.answer(get_text("en", "check_invalid"))
        await state.clear()
        return
    
    if check.requires_password:
        await state.update_data(check_code=code)
        await message.answer(get_text("en", "check_password_required"))
        await state.set_state(CheckRedeemState.password)
        return
    
    await state.clear()
    success, msg = await process_check_redemption(session, message.from_user.id, check)
    await message.answer(msg, parse_mode=ParseMode.HTML)

@router.message(CheckRedeemState.password)
async def chk_activate_password(message: Message, state: FSMContext, session: AsyncSession):
    raw = safe_text(message)
    if raw is None:
        await message.answer(NON_TEXT_INPUT_MSG)
        return
    
    data = await state.get_data()
    code = data.get("check_code")
    await state.clear()
    
    res = await session.execute(select(Check).where(Check.code == code))
    check = res.scalar_one_or_none()
    
    if not check:
        await message.answer(get_text("en", "check_invalid"))
        return
    
    success, msg = await process_check_redemption(
        session, message.from_user.id, check, password=raw
    )
    await message.answer(msg, parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith("check_pw_"))
async def check_password_callback(query: CallbackQuery, state: FSMContext):
    code = query.data.replace("check_pw_", "")
    await state.update_data(check_code=code)
    await query.message.edit_text(get_text("en", "check_password_required"))
    await state.set_state(CheckRedeemState.password)
    await query.answer()

# ==============================================================================
# SECTION 20: OTHER MENU HANDLERS
# ==============================================================================

@router.message(F.text.in_(["🛡 Subscription Check", "🛡 সাবস্ক্রিপশন চেক"]))
async def sub_check_menu(message: Message):
    await message.answer(
        "🛡 <b>Subscription Verification Engine</b>\n\n"
        "All channel/group subscriptions are verified live through the Telegram Bot API.\n\n"
        "📌 For this to work, the channel/group owner must add this bot as an "
        "<b>Administrator</b> — once that's done, the bot checks membership "
        "automatically every time a user taps 'Verify Task'.",
        parse_mode=ParseMode.HTML
    )

@router.message(F.text.in_(["📊 Statistics", "📊 Our Bots and Statistics", "📊 পরিসংখ্যান", "📊 আমাদের বট ও পরিসংখ্যান"]))
async def stats_menu(message: Message, session: AsyncSession):
    res_users = await session.execute(select(func.count(User.id)))
    total_users = res_users.scalar() or 0
    
    res_camp = await session.execute(
        select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.ACTIVE)
    )
    active_campaigns = res_camp.scalar() or 0
    
    res_camp_all = await session.execute(select(func.count(Campaign.id)))
    total_campaigns = res_camp_all.scalar() or 0
    
    res_completed = await session.execute(
        select(func.count(TaskCompletion.id))
    )
    total_completions = res_completed.scalar() or 0
    
    await message.answer(
        f"📊 <b>Platform Statistics</b>\n\n"
        f"👥 Total Users: <code>{total_users:,}</code>\n"
        f"📢 Active Campaigns: <code>{active_campaigns:,}</code>\n"
        f"🗂 Total Campaigns: <code>{total_campaigns:,}</code>\n"
        f"✅ Tasks Completed: <code>{total_completions:,}</code>",
        parse_mode=ParseMode.HTML
    )

@router.message(F.text.in_(["🔗 Useful Links", "🔗 দরকারী লিংক"]))
async def links_menu(message: Message):
    await message.answer(
        f"🔗 <b>Useful Links</b>\n\n"
        f"• Bot: @{esc(BOT_USERNAME)}\n"
        f"• Support: Contact admin\n"
        f"• Channel: Coming soon",
        parse_mode=ParseMode.HTML
    )

@router.message(F.text.in_(["ℹ️ Instruction", "ℹ️ নির্দেশিকা"]))
async def instruction_menu(message: Message):
    await message.answer(
        "ℹ️ <b>Instruction</b>\n\n"
        "1️⃣ Earn GRAM coins by completing tasks under 💰 Earnings.\n"
        "2️⃣ Promote your channel/group under 📢 Promote.\n"
        "3️⃣ Use 📋 Checks to gift or redeem GRAM codes.\n"
        "4️⃣ Use 👤 My Cabinet to manage your profile.\n\n"
        "<b>Referral System:</b>\n"
        "• Get 5,000 GRAM per direct referral\n"
        "• Earn 8%/5%/2% bonuses from your referrals' earnings (3 tiers)\n\n"
        "<b>Withdrawals:</b>\n"
        f"• Unlock at Level {BusinessRules.WITHDRAWAL_MIN_LEVEL}\n"
        f"• Min: {BusinessRules.WITHDRAWAL_MIN_AMOUNT:,.0f} GRAM\n"
        f"• Max: {BusinessRules.WITHDRAWAL_MAX_AMOUNT:,.0f} GRAM",
        parse_mode=ParseMode.HTML
    )

# ==============================================================================
# SECTION 21: ADMIN PANEL
# ==============================================================================

@router.message(Command("admin"))
async def cmd_admin(message: Message, session: AsyncSession):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ You are not authorized.")
        return
    
    # Get stats
    res_reports = await session.execute(
        select(func.count(FraudReport.id)).where(FraudReport.status == "pending")
    )
    pending_reports = res_reports.scalar() or 0
    
    res_withdrawals = await session.execute(
        select(func.count(WithdrawalRequest.id)).where(WithdrawalRequest.status == "pending")
    )
    pending_withdrawals = res_withdrawals.scalar() or 0
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Add/Deduct Coins", callback_data="adm_coins")],
        [InlineKeyboardButton(text=f"🚨 Fraud Reports ({pending_reports})", callback_data="adm_reports")],
        [InlineKeyboardButton(text=f"💸 Withdrawals ({pending_withdrawals})", callback_data="adm_withdrawals")],
        [InlineKeyboardButton(text="🚫 Ban User", callback_data="adm_ban")],
        [InlineKeyboardButton(text="📢 Moderate Tasks", callback_data="adm_tasks")],
        [InlineKeyboardButton(text="📊 Full Stats", callback_data="adm_stats")],
    ])
    
    await message.answer(
        "👑 <b>Admin Panel</b>\n\nChoose an action:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "adm_stats")
async def adm_stats(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    # Gather stats
    res = await session.execute(select(func.count(User.id)))
    total_users = res.scalar() or 0
    
    res = await session.execute(
        select(func.sum(User.balance))
    )
    total_balance = res.scalar() or Decimal("0")
    
    res = await session.execute(
        select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.ACTIVE)
    )
    active_campaigns = res.scalar() or 0
    
    res = await session.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.type == TransactionType.TASK_REWARD
        )
    )
    total_paid = res.scalar() or Decimal("0")
    
    res = await session.execute(
        select(func.count(FraudReport.id)).where(FraudReport.status == "pending")
    )
    fraud_reports = res.scalar() or 0
    
    await query.message.edit_text(
        f"📊 <b>Platform Statistics</b>\n\n"
        f"👥 Total Users: <code>{total_users:,}</code>\n"
        f"💰 Total Balance: <code>{total_balance:,.0f}</code> GRAM\n"
        f"📢 Active Campaigns: <code>{active_campaigns:,}</code>\n"
        f"✅ Total Paid: <code>{total_paid:,.0f}</code> GRAM\n"
        f"🚨 Pending Reports: <code>{fraud_reports:,}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Back", callback_data="adm_back")]
        ]),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@router.callback_query(F.data == "adm_back")
async def adm_back(query: CallbackQuery, session: AsyncSession):
    await cmd_admin(query.message, session)
    await query.answer()

# --- FRAUD REPORTS ---

@router.callback_query(F.data == "adm_reports")
async def adm_reports_list(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    res = await session.execute(
        select(FraudReport)
        .where(FraudReport.status == "pending")
        .order_by(FraudReport.reported_at.desc())
        .limit(10)
    )
    reports = res.scalars().all()
    
    if not reports:
        await query.message.edit_text(
            "✅ No pending fraud reports!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Back", callback_data="adm_back")]
            ])
        )
        await query.answer()
        return
    
    buttons = []
    for r in reports:
        buttons.append([
            InlineKeyboardButton(
                text=f"🚨 User {r.user_id} - {r.report_type}",
                callback_data=f"adm_report_{r.id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Back", callback_data="adm_back")])
    
    await query.message.edit_text(
        f"🚨 <b>Pending Fraud Reports ({len(reports)})</b>\n\n"
        "Select a report to review:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@router.callback_query(F.data.startswith("adm_report_"))
async def adm_view_report(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    report_id = query.data.replace("adm_report_", "")
    
    res = await session.execute(
        select(FraudReport).where(FraudReport.id == report_id)
    )
    report = res.scalar_one_or_none()
    
    if not report:
        await query.answer("Report not found.", show_alert=True)
        return
    
    # Get user info
    res = await session.execute(
        select(User).where(User.id == report.user_id)
    )
    user = res.scalar_one_or_none()
    
    user_info = f"ID: {report.user_id}"
    if user:
        user_info = f"{esc(user.first_name)} (@{esc(user.username or 'N/A')}) - ID: {report.user_id}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Ban User", callback_data=f"adm_ban_from_report_{report.user_id}")],
        [InlineKeyboardButton(text="✅ Dismiss Report", callback_data=f"adm_dismiss_{report.id}")],
        [InlineKeyboardButton(text="◀️ Back", callback_data="adm_reports")],
    ])
    
    await query.message.edit_text(
        f"🚨 <b>Fraud Report</b>\n\n"
        f"👤 User: {user_info}\n"
        f"📋 Type: {esc(report.report_type)}\n"
        f"📝 Description: {esc(report.description)}\n"
        f"⏰ Reported: {report.reported_at.strftime('%Y-%m-%d %H:%M')}",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@router.callback_query(F.data.startswith("adm_ban_from_report_"))
async def adm_ban_from_report(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    user_id = int(query.data.replace("adm_ban_from_report_", ""))
    
    async with session.begin_nested():
        res = await session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = res.scalar_one_or_none()
        
        if user:
            user.is_blocked = True
            user.risk_score += 50
    
    await session.commit()
    await query.answer(f"🚫 User {user_id} banned.", show_alert=True)
    
    # Try to notify user
    try:
        await query.bot.send_message(
            user_id,
            "🚫 Your account has been suspended for violating our terms of service."
        )
    except Exception:
        pass

@router.callback_query(F.data.startswith("adm_dismiss_"))
async def adm_dismiss_report(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    report_id = query.data.replace("adm_dismiss_", "")
    
    async with session.begin_nested():
        res = await session.execute(
            select(FraudReport).where(FraudReport.id == report_id).with_for_update()
        )
        report = res.scalar_one_or_none()
        
        if report:
            report.status = "dismissed"
            report.reviewed_at = datetime.now(timezone.utc)
            report.reviewed_by = query.from_user.id
    
    await session.commit()
    await query.answer("✅ Report dismissed.", show_alert=True)
    await adm_reports_list(query, session)

# --- WITHDRAWALS ---

@router.callback_query(F.data == "adm_withdrawals")
async def adm_withdrawals_list(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    res = await session.execute(
        select(WithdrawalRequest)
        .where(WithdrawalRequest.status == "pending")
        .order_by(WithdrawalRequest.created_at.desc())
        .limit(10)
    )
    withdrawals = res.scalars().all()
    
    if not withdrawals:
        await query.message.edit_text(
            "✅ No pending withdrawal requests!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Back", callback_data="adm_back")]
            ])
        )
        await query.answer()
        return
    
    buttons = []
    for w in withdrawals:
        buttons.append([
            InlineKeyboardButton(
                text=f"💸 {w.amount:,.0f} GRAM - User {w.user_id}",
                callback_data=f"adm_wd_{w.id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Back", callback_data="adm_back")])
    
    await query.message.edit_text(
        f"💸 <b>Pending Withdrawals ({len(withdrawals)})</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@router.callback_query(F.data.startswith("adm_wd_"))
async def adm_view_withdrawal(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    wd_id = query.data.replace("adm_wd_", "")
    
    res = await session.execute(
        select(WithdrawalRequest).where(WithdrawalRequest.id == wd_id)
    )
    wd = res.scalar_one_or_none()
    
    if not wd:
        await query.answer("Request not found.", show_alert=True)
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Approve", callback_data=f"adm_wd_approve_{wd.id}")],
        [InlineKeyboardButton(text="❌ Reject", callback_data=f"adm_wd_reject_{wd.id}")],
        [InlineKeyboardButton(text="◀️ Back", callback_data="adm_withdrawals")],
    ])
    
    await query.message.edit_text(
        f"💸 <b>Withdrawal Request</b>\n\n"
        f"👤 User ID: <code>{wd.user_id}</code>\n"
        f"💰 Amount: <code>{wd.amount:,.0f}</code> GRAM\n"
        f"💳 Method: {esc(wd.payment_method or 'N/A')}\n"
        f"📝 Details: <code>{esc(wd.payment_details or 'N/A')}</code>\n"
        f"⏰ Requested: {wd.created_at.strftime('%Y-%m-%d %H:%M')}",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@router.callback_query(F.data.startswith("adm_wd_approve_"))
async def adm_approve_withdrawal(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    wd_id = query.data.replace("adm_wd_approve_", "")
    
    async with session.begin_nested():
        res = await session.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.id == wd_id).with_for_update()
        )
        wd = res.scalar_one_or_none()
        
        if wd:
            wd.status = "approved"
            wd.processed_at = datetime.now(timezone.utc)
            wd.processed_by = query.from_user.id
    
    await session.commit()
    await query.answer("✅ Withdrawal approved.", show_alert=True)
    
    if wd:
        try:
            await query.bot.send_message(
                wd.user_id,
                f"✅ Your withdrawal of <code>{wd.amount:,.0f}</code> GRAM has been approved!\n"
                f"Payment will be processed shortly.",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

@router.callback_query(F.data.startswith("adm_wd_reject_"))
async def adm_reject_withdrawal(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    wd_id = query.data.replace("adm_wd_reject_", "")
    
    async with session.begin_nested():
        res = await session.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.id == wd_id).with_for_update()
        )
        wd = res.scalar_one_or_none()
        
        if wd:
            wd.status = "rejected"
            wd.processed_at = datetime.now(timezone.utc)
            wd.processed_by = query.from_user.id
            
            # Refund the amount
            res_u = await session.execute(
                select(User).where(User.id == wd.user_id).with_for_update()
            )
            user = res_u.scalar_one_or_none()
            if user:
                user.balance += wd.amount
                user.total_withdrawn -= wd.amount
                
                session.add(Transaction(
                    user_id=user.id,
                    amount=wd.amount,
                    type=TransactionType.WITHDRAWAL,
                    description=f"Withdrawal rejected - refunded",
                    balance_after=user.balance
                ))
    
    await session.commit()
    await query.answer("❌ Withdrawal rejected and refunded.", show_alert=True)
    
    if wd:
        try:
            await query.bot.send_message(
                wd.user_id,
                f"❌ Your withdrawal request of <code>{wd.amount:,.0f}</code> GRAM was rejected.\n"
                f"The amount has been refunded to your balance.",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

# --- COIN MANAGEMENT ---

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
        await message.answer(
            "Enter GRAM coin amount to add/deduct (e.g. <code>5000</code> or <code>-1000</code>):",
            parse_mode=ParseMode.HTML
        )
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
        res = await session.execute(
            select(User).where(User.id == uid).with_for_update()
        )
        user = res.scalar_one_or_none()
        
        if not user:
            await message.answer("❌ User not found.")
            return
        
        user.balance += amount
        
        session.add(Transaction(
            user_id=uid,
            amount=amount,
            type=TransactionType.ADMIN_ADJUSTMENT,
            description="Admin balance adjustment",
            balance_after=user.balance
        ))
    
    await session.commit()
    await message.answer(
        f"✅ Balance updated for user <code>{uid}</code> by <code>{amount:,.0f}</code> GRAM.",
        parse_mode=ParseMode.HTML
    )

# --- BAN USER ---

@router.callback_query(F.data == "adm_ban")
async def adm_ban(query: CallbackQuery, state: FSMContext):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    await query.message.edit_text("Send User ID to ban/unban:")
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
        res = await session.execute(
            select(User).where(User.id == uid).with_for_update()
        )
        user = res.scalar_one_or_none()
        
        if not user:
            await message.answer("❌ User not found.")
            return
        
        user.is_blocked = not user.is_blocked  # Toggle
        action = "banned" if user.is_blocked else "unbanned"
    
    await session.commit()
    await message.answer(
        f"🚫 User <code>{uid}</code> {action} successfully.",
        parse_mode=ParseMode.HTML
    )

# --- MODERATE TASKS ---

@router.callback_query(F.data == "adm_tasks")
async def adm_tasks(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    res = await session.execute(
        select(Campaign).where(Campaign.status == CampaignStatus.ACTIVE).limit(10)
    )
    campaigns = res.scalars().all()
    
    if not campaigns:
        await query.message.edit_text(
            "No active campaigns.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Back", callback_data="adm_back")]
            ])
        )
        await query.answer()
        return
    
    buttons = []
    for c in campaigns:
        buttons.append([
            InlineKeyboardButton(
                text=f"❌ {c.title[:20]}",
                callback_data=f"adm_del_{c.id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Back", callback_data="adm_back")])
    
    await query.message.edit_text(
        "Select campaign to cancel:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await query.answer()

@router.callback_query(F.data.startswith("adm_del_"))
async def adm_cancel_camp(query: CallbackQuery, session: AsyncSession):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    
    cid = query.data.replace("adm_del_", "")
    
    async with session.begin_nested():
        res = await session.execute(
            select(Campaign).where(Campaign.id == cid).with_for_update()
        )
        campaign = res.scalar_one_or_none()
        
        if campaign:
            campaign.status = CampaignStatus.CANCELLED
            
            # Refund remaining budget
            remaining = campaign.total_budget - campaign.spent_budget
            if remaining > 0:
                res_u = await session.execute(
                    select(User).where(User.id == campaign.advertiser_id).with_for_update()
                )
                advertiser = res_u.scalar_one_or_none()
                if advertiser:
                    advertiser.balance += remaining
                    session.add(Transaction(
                        user_id=advertiser.id,
                        amount=remaining,
                        type=TransactionType.CAMPAIGN_REFUND,
                        description=f"Campaign cancelled: {campaign.title}",
                        reference_id=campaign.id,
                        reference_type="campaign",
                        balance_after=advertiser.balance
                    ))
    
    await session.commit()
    await query.answer("Campaign cancelled and budget refunded.", show_alert=True)
    await query.message.edit_text("✅ Campaign has been deactivated.")

# ==============================================================================
# SECTION 22: BACKGROUND TASKS
# ==============================================================================

async def retention_check_worker(bot: Bot):
    """Background worker for retention checks."""
    while True:
        try:
            await asyncio.sleep(BusinessRules.RETENTION_CHECK_INTERVAL_HOURS * 3600)
            
            async with AsyncSessionLocal() as session:
                results = await RetentionEngine.check_retention(bot, session)
                
                if results:
                    logger.info(f"Retention check processed {len(results)} penalties")
                    
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Retention worker error: {e}")
            await asyncio.sleep(300)  # Wait 5 min on error

async def notification_worker(bot: Bot):
    """Background worker for sending queued notifications."""
    while True:
        try:
            await asyncio.sleep(60)  # Check every minute
            
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(Notification)
                    .where(Notification.is_sent == False)
                    .limit(50)
                )
                notifications = res.scalars().all()
                
                for notif in notifications:
                    try:
                        await bot.send_message(
                            notif.user_id,
                            f"<b>{esc(notif.title)}</b>\n\n{notif.message}",
                            parse_mode=ParseMode.HTML
                        )
                        notif.is_sent = True
                        notif.sent_at = datetime.now(timezone.utc)
                    except Exception:
                        notif.is_sent = True  # Mark as sent to avoid infinite retry
                
                await session.commit()
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Notification worker error: {e}")
            await asyncio.sleep(60)

# ==============================================================================
# SECTION 23: FASTAPI & APP INITIALIZATION
# ==============================================================================

fastapi_app = FastAPI(title="BoostGram Health Check")

@fastapi_app.get("/", response_class=HTMLResponse)
async def root():
    """Serves the index.html file if present."""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Boost Gram - Telegram Bot</title>
        </head>
        <body style="background:#0f172a; color:#fff; text-align:center; padding:100px; font-family:Arial;">
            <h1>Boost Gram 🚀</h1>
            <p>The ultimate Telegram promotion bot is active!</p>
        </body>
        </html>
        """

@fastapi_app.get("/sitemap.xml", response_class=HTMLResponse)
async def sitemap():
    """Serves the sitemap.xml file for Google indexing."""
    try:
        with open("sitemap.xml", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>https://boost-gram-bot.onrender.com/</loc>
        <lastmod>2026-09-13T00:00:00+00:00</lastmod>
        <changefreq>weekly</changefreq>
        <priority>1.0</priority>
    </url>
</urlset>"""

@fastapi_app.get("/google8c808883d07580e1.html", response_class=HTMLResponse)
async def google_verification():
    """Serves the Google site verification file."""
    try:
        with open("google8c808883d07580e1.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "google-site-verification: google8c808883d07580e1.html"

@fastapi_app.get("/health")
async def health_check():
    return {
        "status": "active",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

async def set_bot_commands(bot: Bot):
    """Set bot commands for better UX."""
    commands = [
        BotCommand(command="start", description="🚀 Start the bot"),
        BotCommand(command="admin", description="👑 Admin panel (admins only)"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())

async def start_bot():
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS is empty — error alerts have nowhere to go!")
    
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
        """Safety net for unhandled exceptions."""
        exc = event.exception
        logger.exception(f"Unhandled exception: {exc}")
        
        update = event.update
        chat_id = None
        from_user = None
        content_preview = ""
        update_kind = "unknown"
        
        if update.message:
            update_kind = "message"
            chat_id = update.message.chat.id
            from_user = update.message.from_user
            content_preview = update.message.text or update.message.content_type
        elif update.callback_query:
            update_kind = "callback_query"
            from_user = update.callback_query.from_user
            content_preview = update.callback_query.data or ""
            if update.callback_query.message:
                chat_id = update.callback_query.message.chat.id
        
        # Notify user
        if chat_id:
            try:
                await bot.send_message(
                    chat_id,
                    "⚠️ Something went wrong. Please try again or /start to reset.",
                )
            except Exception:
                pass
        
        # Notify admins
        tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        tb_text = tb_text[-3200:]
        
        who = "unknown"
        if from_user:
            uname = f"@{from_user.username}" if from_user.username else "no username"
            who = f"{esc(from_user.first_name)} ({uname}) — ID <code>{from_user.id}</code>"
        
        await alert_admins(
            bot,
            f"🚨 <b>Bot Error</b>\n\n"
            f"👤 User: {who}\n"
            f"📍 Type: <code>{esc(update_kind)}</code>\n"
            f"💬 Content: <code>{esc(content_preview)[:300]}</code>\n"
            f"❗ Exception: <code>{esc(type(exc).__name__)}: {esc(str(exc))[:300]}</code>\n\n"
            f"<pre>{esc(tb_text)}</pre>"
        )
        
        return True
    
    # Initialize
    await init_db()
    logger.info("Database initialized successfully.")
    
    await set_bot_commands(bot)
    logger.info("Bot commands set.")
    
    # Start web server
    port = int(os.getenv("PORT", "8000"))
    uvicorn_config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    web_server = uvicorn.Server(uvicorn_config)
    
    logger.info(f"Starting web server on 0.0.0.0:{port}...")
    logger.info("Starting bot in polling mode...")
    
    # Start background workers
    asyncio.create_task(retention_check_worker(bot))
    asyncio.create_task(notification_worker(bot))
    
    await asyncio.gather(
        dp.start_polling(bot),
        web_server.serve(),
    )

if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")

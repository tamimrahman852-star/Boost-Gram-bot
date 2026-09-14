# ==============================================================================
# FILE: main.py - Boost Gram Bot (Alembic-based, Screenshot-matched UI)
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
from decimal import Decimal, InvalidOperation
from enum import Enum

# Third-party
from dotenv import load_dotenv
import redis.asyncio as aioredis

from sqlalchemy import (
    BigInteger, String, Numeric, Integer, Boolean, DateTime, Text,
    ForeignKey, UniqueConstraint, Index, select, func, Enum as SQLEnum
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker
)

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, PreCheckoutQuery, LabeledPrice,
    BotCommand, ErrorEvent,
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ==============================================================================
# SECTION 1: CONFIG
# ==============================================================================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("BoostGram")

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "BoostGramBot")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "default-key-change-me")

if not BOT_TOKEN or not DATABASE_URL or not REDIS_URL:
    logger.critical("Missing required env: BOT_TOKEN, DATABASE_URL, REDIS_URL")
    sys.exit(1)

try:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()]
except ValueError:
    ADMIN_IDS = []


def esc(v) -> str:
    return html.escape(str(v), quote=False)


def hash_sensitive(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def safe_text(message: "Message") -> Optional[str]:
    return message.text.strip() if message.text else None


NON_TEXT_INPUT_MSG = "⚠️ Please send this as a text message."


# ==============================================================================
# SECTION 2: BUSINESS RULES
# ==============================================================================

class BusinessRules:
    REFERRAL_BASE_REWARD = Decimal("5000")
    REFERRAL_XP_BASE = 500
    REFERRAL_TIER_BONUSES = {1: Decimal("0.08"), 2: Decimal("0.05"), 3: Decimal("0.02")}

    STAR_TO_COIN_RATE = Decimal("5000")
    PLATFORM_COMMISSION = Decimal("0.15")

    WITHDRAWAL_MIN_LEVEL = 30
    WITHDRAWAL_MIN_AMOUNT = Decimal("250000")
    WITHDRAWAL_MAX_AMOUNT = Decimal("10000000")

    XP_PER_1000_COINS = Decimal("3")
    XP_PER_LEVEL = 1500

    RETENTION_DAYS = 7
    TASKS_PAGE_SIZE = 5
    MAX_ACTIVE_CAMPAIGNS = 10


class TaskPricing:
    PRICING = {
        "channel_sub": {"min": 850, "suggested": 1000, "max": 5000},
        "group_join":  {"min": 1250, "suggested": 1500, "max": 6000},
        "bot_start":   {"min": 2500, "suggested": 3000, "max": 10000},
        "post_view":   {"min": 120, "suggested": 200, "max": 500},
        "reaction":    {"min": 500, "suggested": 600, "max": 800},
        "boost_7day":  {"min": 25000, "suggested": 30000, "max": 100000},
    }
    PREMIUM_MULTIPLIER = Decimal("2.0")

    @classmethod
    def get(cls, task_type: str, premium: bool = False) -> dict:
        base = cls.PRICING.get(task_type, {"min": 100, "suggested": 200, "max": 1000})
        if premium:
            return {k: int(Decimal(str(v)) * cls.PREMIUM_MULTIPLIER) for k, v in base.items()}
        return base

    @classmethod
    def validate(cls, task_type: str, reward: Decimal, premium: bool = False):
        rules = cls.get(task_type, premium)
        if reward < rules["min"]:
            return False, f"Minimum: {rules['min']:,} coins"
        if reward > rules["max"]:
            return False, f"Maximum: {rules['max']:,} coins"
        return True, "OK"


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


VERIFIABLE_TASKS = {TaskType.CHANNEL_SUB, TaskType.GROUP_JOIN, TaskType.BOOST_7DAY}


# ==============================================================================
# SECTION 4: LOCALIZATION (21 Languages)
# ==============================================================================

class L10N:
    LANGS = {
        "en": "🇬🇧 English",
        "bn": "🇧🇩 বাংলা",
        "hi": "🇮🇳 हिन्दी",
        "ru": "🇷🇺 Русский",
        "ar": "🇸🇦 العربية",
        "es": "🇪🇸 Español",
        "de": "🇩🇪 Deutsch",
        "fr": "🇫🇷 Français",
        "pt": "🇧🇷 Português",
        "id": "🇮🇩 Bahasa",
        "tr": "🇹🇷 Türkçe",
        "fa": "🇮🇷 فارسی",
        "uz": "🇺🇿 O'zbekcha",
        "kk": "🇰🇿 Қазақша",
        "uk": "🇺🇦 Українська",
        "zh": "🇨🇳 中文",
        "vi": "🇻🇳 Tiếng Việt",
        "th": "🇹🇭 ไทย",
        "ja": "🇯🇵 日本語",
        "ko": "🇰🇷 한국어",
        "my": "🇲🇲 မြန်မာ",
    }

    T = {
        "en": {
            # Main menu
            "menu_earnings": "💰 Earnings",
            "menu_promote": "📢 Promote",
            "menu_checks": "📋 Checks",
            "menu_cabinet": "👤 My Cabinet",
            "menu_sub_check": "🛡 Subscription Check",
            "menu_stats": "📊 Our Bots & Stats",
            "menu_links": "🔗 Useful Links",
            "menu_instruction": "ℹ️ Instruction",
            "menu_withdraw": "💸 Withdraw",
            # Welcome
            "welcome": "👋 Welcome to <b>{bot_name}</b>, {first_name}!\n\n🚀 Complete tasks, earn GRAM coins, and promote your channels instantly!",
            "lang_changed": "✅ Language changed successfully!",
            "not_started": "⚠️ Please tap /start first.",
            "text_only": "⚠️ Please send this as a text message.",
            # Cabinet
            "cabinet": "👤 <b>Your Cabinet:</b>\n\n🆔 My ID: <code>{user_id}</code>\n🏅 Level: {level_emoji} {level}\n💰 Balance: <code>{balance:,.0f}</code> GRAM\n⚡ XP: {xp}/{next_xp}\n🎯 Tasks Done: {tasks}",
            "btn_replenish": "🪙 Replenish Balance",
            "btn_referral": "👥 Referral System",
            "btn_level": "📊 Level System",
            "btn_tasks": "📋 My Tasks",
            "btn_lang": "🌐 Change Language",
            "btn_notif_on": "🔕 Disable Notifications",
            "btn_notif_off": "🔔 Enable Notifications",
            "btn_back": "◀️ Back",
            # Tasks
            "tasks_title": "🎯 <b>Available Tasks</b>\n\nComplete any task below to earn GRAM:",
            "no_tasks": "🎯 No active tasks right now. Check back soon!",
            "task_verified": "🎉 <b>Task Verified!</b>\n\n💰 +<code>{reward:,.0f}</code> GRAM\n⚡ +{xp} XP",
            "task_failed": "❌ Verification failed! Please join first and try again.",
            "task_already": "❌ You already completed this task!",
            "task_limit": "❌ Task limit reached.",
            "task_premium_only": "⭐ This task is for Telegram Premium users only.",
            "task_open": "🚀 Open Link",
            "task_verify": "✅ Verify",
            "task_back": "◀️ Back to List",
            # Promote
            "promote_title": "📢 <b>Create Promotion Campaign</b>\n\nSelect the task type:",
            "promote_name": "📝 Send the campaign title:",
            "promote_target": "🔗 Send the channel/group username (e.g. <code>@mychannel</code>) or link:",
            "promote_link": "🔗 Send the link users should open:",
            "promote_bot_token": "🤖 Send your bot token for verification:",
            "promote_premium": "⭐ <b>Premium Targeting</b>\n\nTarget only Telegram Premium users? Rewards will be 2x.",
            "promote_premium_yes": "⭐ Premium Only (2x)",
            "promote_premium_no": "👥 All Users",
            "promote_reward": "💰 Send reward per user (GRAM coins):\n\n💡 Suggested: <code>{suggested:,}</code>\n📊 Range: <code>{min:,}</code> – <code>{max:,}</code>",
            "promote_slots": "👥 Send total number of users (slots):",
            "promote_created": "✅ <b>Campaign Created!</b>\n\n📢 {title}\n💰 Reward: <code>{reward:,.0f}</code> GRAM\n👥 Slots: {slots}\n💳 Cost: <code>{cost:,.0f}</code> GRAM",
            "promote_insufficient": "❌ Insufficient balance!\n\nRequired: <code>{required:,.0f}</code> GRAM\nYour balance: <code>{balance:,.0f}</code> GRAM",
            "promote_duplicate": "⚠️ You already have an active campaign for this target!",
            "add_slots_btn": "➕ Add Slots to Existing",
            "new_campaign_btn": "🆕 Create New Campaign",
            "cancel_btn": "❌ Cancel",
            "cancelled": "❌ Cancelled.",
            # Withdrawals
            "wd_locked": "🔒 <b>Withdrawal Locked</b>\n\nReach Level {required} to unlock withdrawals.\nYour current level: {current}",
            "wd_prompt": "💸 <b>Withdrawal</b>\n\nMin: <code>{min:,.0f}</code> GRAM\nMax: <code>{max:,.0f}</code> GRAM\nYour balance: <code>{balance:,.0f}</code> GRAM\n\nSend the amount:",
            "wd_invalid": "❌ Amount must be between <code>{min:,.0f}</code> and <code>{max:,.0f}</code> GRAM.",
            "wd_insufficient": "❌ Insufficient balance.",
            "wd_method": "💳 Choose your payment method:",
            "wd_details": "📝 Send your payment details (wallet/account):",
            "wd_submitted": "✅ Withdrawal request submitted!\n\nAmount: <code>{amount:,.0f}</code> GRAM\nStatus: Pending admin approval",
            # Checks
            "checks_menu": "📋 <b>Checks System</b>\n\nCreate shareable GRAM vouchers or redeem existing ones.",
            "check_create_btn": "🎫 Create a Check",
            "check_activate_btn": "💳 Activate a Check",
            "check_amount": "💰 Send GRAM amount per activation:",
            "check_activations": "👥 Send number of activations:",
            "check_password": "🔐 Set a password (or type <code>skip</code>):",
            "check_created": "✅ <b>Check Created!</b>\n\n🎫 Code: <code>{code}</code>\n💰 Amount: <code>{amount:,.0f}</code> GRAM\n👥 Activations: {activations}\n🔗 Link:\n<code>{link}</code>",
            "check_redeem_prompt": "🎫 Send the check code:",
            "check_password_prompt": "🔐 This check requires a password. Send it:",
            "check_invalid": "❌ Invalid or expired code.",
            "check_wrong_pw": "❌ Wrong password.",
            "check_redeemed": "✅ Check redeemed! +<code>{amount:,.0f}</code> GRAM",
            # Referral
            "ref_title": "👥 <b>Referral System</b>\n\nEarn <code>{base:,.0f}</code> GRAM per direct referral!\n\n📊 <b>Tier Bonuses:</b>\n• Tier 1 (Direct): 8%\n• Tier 2: 5%\n• Tier 3: 2%\n\n📈 Direct referrals: <code>{direct}</code>\n💰 Total earned: <code>{earned:,.0f}</code> GRAM\n\n🔗 Your link:\n<code>{link}</code>",
            # Level
            "level_title": "📊 <b>Level System</b>\n\n🏅 Level: {emoji} <code>{level}</code>\n⚡ Total XP: <code>{xp:,}</code>\n📈 Progress: {progress}\n\n💡 Withdraw unlocks at Level {min_level}.",
            # Stats
            "stats": "📊 <b>Boost Gram Statistics</b>\n\n👥 Users: <code>{users:,}</code>\n📢 Active campaigns: <code>{active:,}</code>\n✅ Tasks completed: <code>{completed:,}</code>\n💎 Total paid: <code>{paid:,.0f}</code> GRAM",
            # Rules
            "rules_title": "⚠️ <b>Earning Rules</b>",
            "rules_forbidden": "🚫 <b>Forbidden:</b>\n• Unsubscribing within 7 days\n• Removing reactions\n• Multiple accounts\n• Fake traffic / bots\n• Automation tools",
            "rules_penalty": "⚡ <b>Penalties:</b>\n• Task ban: 7 days\n• Repeat ban: 7 more days\n• Full revocation of task reward",
            # Misc
            "instruction": "ℹ️ <b>How to use Boost Gram</b>\n\n1️⃣ Complete tasks under 💰 Earnings\n2️⃣ Promote your channel under 📢 Promote\n3️⃣ Gift/redeem under 📋 Checks\n4️⃣ Manage in 👤 My Cabinet\n5️⃣ Invite friends for 5,000 GRAM + tier bonuses",
            "links": "🔗 <b>Useful Links</b>\n\n• Bot: @{bot}\n• Support: Contact admin",
            "sub_check": "🛡 <b>Subscription Verification</b>\n\nAll subscriptions verified live via Telegram Bot API.\n\n📌 Channel/Group owner must add @{bot} as <b>Administrator</b>.",
            "retention_warning": "⚠️ <b>Retention Warning!</b>\n\nYou left <b>{target}</b> before 7 days.\nRejoin now to avoid penalty:\n{link}",
            "retention_penalty": "🚫 <b>Penalty Applied!</b>\n\nYou left {target}. Reward of <code>{reward:,.0f}</code> GRAM has been revoked.",
            "error_generic": "❌ Something went wrong. Try again.",
        },
        "bn": {
            # Main menu
            "menu_earnings": "💰 আয়",
            "menu_promote": "📢 প্রচার করুন",
            "menu_checks": "📋 চেক",
            "menu_cabinet": "👤 আমার কেবিনেট",
            "menu_sub_check": "🛡 সাবস্ক্রিপশন চেক",
            "menu_stats": "📊 আমাদের বট ও পরিসংখ্যান",
            "menu_links": "🔗 দরকারী লিংক",
            "menu_instruction": "ℹ️ নির্দেশিকা",
            "menu_withdraw": "💸 উইথড্র",
            # Welcome
            "welcome": "👋 <b>{bot_name}</b>-এ আপনাকে স্বাগতম, {first_name}!\n\n🚀 টাস্ক সম্পন্ন করুন, GRAM কয়েন আয় করুন এবং আপনার চ্যানেল প্রমোট করুন!",
            "lang_changed": "✅ ভাষা সফলভাবে পরিবর্তন হয়েছে!",
            "not_started": "⚠️ প্রথমে /start চাপুন।",
            "text_only": "⚠️ টেক্সট মেসেজ পাঠান।",
            # Cabinet
            "cabinet": "👤 <b>আপনার কেবিনেট:</b>\n\n🆔 আমার আইডি: <code>{user_id}</code>\n🏅 লেভেল: {level_emoji} {level}\n💰 ব্যালেন্স: <code>{balance:,.0f}</code> GRAM\n⚡ XP: {xp}/{next_xp}\n🎯 সম্পন্ন টাস্ক: {tasks}",
            "btn_replenish": "🪙 ব্যালেন্স রিচার্জ",
            "btn_referral": "👥 রেফারেল সিস্টেম",
            "btn_level": "📊 লেভেল সিস্টেম",
            "btn_tasks": "📋 আমার টাস্কসমূহ",
            "btn_lang": "🌐 ভাষা পরিবর্তন",
            "btn_notif_on": "🔕 নোটিফিকেশন বন্ধ করুন",
            "btn_notif_off": "🔔 নোটিফিকেশন চালু করুন",
            "btn_back": "◀️ ফিরে যান",
            # Tasks
            "tasks_title": "🎯 <b>উপলব্ধ টাস্কসমূহ</b>\n\nনিচের যেকোনো টাস্ক সম্পন্ন করে GRAM আয় করুন:",
            "no_tasks": "🎯 এখন কোনো টাস্ক নেই। শীঘ্রই আবার চেক করুন!",
            "task_verified": "🎉 <b>টাস্ক ভেরিফাইড!</b>\n\n💰 +<code>{reward:,.0f}</code> GRAM\n⚡ +{xp} XP",
            "task_failed": "❌ যাচাই ব্যর্থ! প্রথমে জয়েন করুন তারপর চেষ্টা করুন।",
            "task_already": "❌ আপনি ইতিমধ্যে এই টাস্ক সম্পন্ন করেছেন!",
            "task_limit": "❌ টাস্ক লিমিট পূর্ণ হয়েছে।",
            "task_premium_only": "⭐ এই টাস্ক শুধুমাত্র Telegram Premium ইউজারদের জন্য।",
            "task_open": "🚀 লিংক খুলুন",
            "task_verify": "✅ যাচাই করুন",
            "task_back": "◀️ ফিরে যান",
            # Promote
            "promote_title": "📢 <b>প্রচার ক্যাম্পেইন তৈরি</b>\n\nটাস্ক টাইপ নির্বাচন করুন:",
            "promote_name": "📝 ক্যাম্পেইন টাইটেল পাঠান:",
            "promote_target": "🔗 চ্যানেল/গ্রুপ ইউজারনেম পাঠান (যেমন <code>@mychannel</code>) বা লিংক:",
            "promote_link": "🔗 ইউজাররা যে লিংকে যাবে সেটি পাঠান:",
            "promote_bot_token": "🤖 ভেরিফিকেশনের জন্য আপনার বট টোকেন পাঠান:",
            "promote_premium": "⭐ <b>প্রিমিয়াম টার্গেটিং</b>\n\nশুধু Telegram Premium ইউজার টার্গেট করবেন? রিওয়ার্ড ২x হবে।",
            "promote_premium_yes": "⭐ শুধু প্রিমিয়াম (২x)",
            "promote_premium_no": "👥 সব ইউজার",
            "promote_reward": "💰 প্রতি ইউজার রিওয়ার্ড পাঠান (GRAM কয়েন):\n\n💡 প্রস্তাবিত: <code>{suggested:,}</code>\n📊 রেঞ্জ: <code>{min:,}</code> – <code>{max:,}</code>",
            "promote_slots": "👥 মোট ইউজার সংখ্যা (স্লট) পাঠান:",
            "promote_created": "✅ <b>ক্যাম্পেইন তৈরি হয়েছে!</b>\n\n📢 {title}\n💰 রিওয়ার্ড: <code>{reward:,.0f}</code> GRAM\n👥 স্লট: {slots}\n💳 খরচ: <code>{cost:,.0f}</code> GRAM",
            "promote_insufficient": "❌ অপর্যাপ্ত ব্যালেন্স!\n\nপ্রয়োজন: <code>{required:,.0f}</code> GRAM\nআপনার: <code>{balance:,.0f}</code> GRAM",
            "promote_duplicate": "⚠️ এই টার্গেটের জন্য ইতিমধ্যে একটি সক্রিয় ক্যাম্পেইন আছে!",
            "add_slots_btn": "➕ বিদ্যমান ক্যাম্পেইনে স্লট যোগ",
            "new_campaign_btn": "🆕 নতুন ক্যাম্পেইন",
            "cancel_btn": "❌ বাতিল",
            "cancelled": "❌ বাতিল হয়েছে।",
            # Withdrawals
            "wd_locked": "🔒 <b>উইথড্র লকড</b>\n\nলেভেল {required} এ পৌঁছান।\nআপনার লেভেল: {current}",
            "wd_prompt": "💸 <b>উইথড্র</b>\n\nসর্বনিম্ন: <code>{min:,.0f}</code> GRAM\nসর্বোচ্চ: <code>{max:,.0f}</code> GRAM\nআপনার ব্যালেন্স: <code>{balance:,.0f}</code> GRAM\n\nপরিমাণ পাঠান:",
            "wd_invalid": "❌ পরিমাণ <code>{min:,.0f}</code> থেকে <code>{max:,.0f}</code> GRAM এর মধ্যে হতে হবে।",
            "wd_insufficient": "❌ অপর্যাপ্ত ব্যালেন্স।",
            "wd_method": "💳 পেমেন্ট মেথড নির্বাচন করুন:",
            "wd_details": "📝 আপনার পেমেন্ট ডিটেইলস পাঠান (ওয়ালেট/অ্যাকাউন্ট):",
            "wd_submitted": "✅ উইথড্র রিকোয়েস্ট জমা হয়েছে!\n\nপরিমাণ: <code>{amount:,.0f}</code> GRAM\nস্ট্যাটাস: অ্যাডমিন অনুমোদনের অপেক্ষায়",
            # Checks
            "checks_menu": "📋 <b>চেক সিস্টেম</b>\n\nশেয়ারযোগ্য GRAM ভাউচার তৈরি করুন বা রিডিম করুন।",
            "check_create_btn": "🎫 চেক তৈরি করুন",
            "check_activate_btn": "💳 চেক অ্যাক্টিভেট করুন",
            "check_amount": "💰 প্রতি অ্যাক্টিভেশনে GRAM পরিমাণ পাঠান:",
            "check_activations": "👥 কতবার অ্যাক্টিভেট করা যাবে:",
            "check_password": "🔐 পাসওয়ার্ড সেট করুন (বা <code>skip</code> টাইপ করুন):",
            "check_created": "✅ <b>চেক তৈরি হয়েছে!</b>\n\n🎫 কোড: <code>{code}</code>\n💰 পরিমাণ: <code>{amount:,.0f}</code> GRAM\n👥 অ্যাক্টিভেশন: {activations}\n🔗 লিংক:\n<code>{link}</code>",
            "check_redeem_prompt": "🎫 চেক কোড পাঠান:",
            "check_password_prompt": "🔐 এই চেকের জন্য পাসওয়ার্ড প্রয়োজন। পাঠান:",
            "check_invalid": "❌ অকার্যকর বা মেয়াদোত্তীর্ণ কোড।",
            "check_wrong_pw": "❌ ভুল পাসওয়ার্ড।",
            "check_redeemed": "✅ চেক রিডিম হয়েছে! +<code>{amount:,.0f}</code> GRAM",
            # Referral
            "ref_title": "👥 <b>রেফারেল সিস্টেম</b>\n\nপ্রতি সরাসরি রেফারেলে <code>{base:,.0f}</code> GRAM!\n\n📊 <b>টিয়ার বোনাস:</b>\n• টিয়ার ১ (সরাসরি): ৮%\n• টিয়ার ২: ৫%\n• টিয়ার ৩: ২%\n\n📈 সরাসরি রেফারেল: <code>{direct}</code>\n💰 মোট আয়: <code>{earned:,.0f}</code> GRAM\n\n🔗 আপনার লিংক:\n<code>{link}</code>",
            # Level
            "level_title": "📊 <b>লেভেল সিস্টেম</b>\n\n🏅 লেভেল: {emoji} <code>{level}</code>\n⚡ মোট XP: <code>{xp:,}</code>\n📈 অগ্রগতি: {progress}\n\n💡 লেভেল {min_level} এ উইথড্র আনলক হবে।",
            # Stats
            "stats": "📊 <b>Boost Gram পরিসংখ্যান</b>\n\n👥 ইউজার: <code>{users:,}</code>\n📢 সক্রিয় ক্যাম্পেইন: <code>{active:,}</code>\n✅ সম্পন্ন টাস্ক: <code>{completed:,}</code>\n💎 মোট পরিশোধ: <code>{paid:,.0f}</code> GRAM",
            # Rules
            "rules_title": "⚠️ <b>আয়ের নিয়মাবলী</b>",
            "rules_forbidden": "🚫 <b>নিষিদ্ধ:</b>\n• ৭ দিনের আগে আনসাবস্ক্রাইব\n• দেওয়া রিঅ্যাকশন তুলে নেওয়া\n• একাধিক অ্যাকাউন্ট\n• ভুয়া ট্রাফিক / বট\n• অটোমেশন টুল",
            "rules_penalty": "⚡ <b>লঙ্ঘনের পরিণতি:</b>\n• টাস্ক বান: ৭ দিন\n• পুনরায় বান: আরো ৭ দিন\n• সম্পূর্ণ রিওয়ার্ড বাতিল",
            # Misc
            "instruction": "ℹ️ <b>Boost Gram ব্যবহারের নির্দেশিকা</b>\n\n১️⃣ 💰 আয় থেকে টাস্ক সম্পন্ন করুন\n২️⃣ 📢 প্রচার করুন থেকে চ্যানেল প্রমোট করুন\n৩️⃣ 📋 চেক থেকে গিফট/রিডিম করুন\n৪️⃣ 👤 আমার কেবিনেট থেকে ম্যানেজ করুন\n৫️⃣ বন্ধু ইনভাইট করে ৫,০০০ GRAM + টিয়ার বোনাস পান",
            "links": "🔗 <b>দরকারী লিংক</b>\n\n• বট: @{bot}\n• সাপোর্ট: অ্যাডমিনের সাথে যোগাযোগ করুন",
            "sub_check": "🛡 <b>সাবস্ক্রিপশন ভেরিফিকেশন</b>\n\nসব সাবস্ক্রিপশন লাইভ Telegram Bot API দিয়ে যাচাই হয়।\n\n📌 চ্যানেল/গ্রুপ মালিককে @{bot} কে <b>অ্যাডমিন</b> বানাতে হবে।",
            "retention_warning": "⚠️ <b>রিটেনশন সতর্কতা!</b>\n\nআপনি ৭ দিনের আগে <b>{target}</b> ছেড়েছেন।\nপেনাল্টি এড়াতে এখনই rejoin করুন:\n{link}",
            "retention_penalty": "🚫 <b>পেনাল্টি প্রয়োগ!</b>\n\nআপনি {target} ছেড়েছেন। <code>{reward:,.0f}</code> GRAM বাতিল হয়েছে।",
            "error_generic": "❌ কিছু ভুল হয়েছে। আবার চেষ্টা করুন।",
        },
    }

    # Fill missing with English
    for _code in LANGS:
        if _code not in T:
            T[_code] = {}

    @classmethod
    def get(cls, lang: str, key: str, **kw) -> str:
        text = (
            cls.T.get(lang, {}).get(key)
            or cls.T["en"].get(key)
            or f"[{key}]"
        )
        try:
            return text.format(**kw)
        except (KeyError, IndexError):
            return text


def t(lang: str, key: str, **kw) -> str:
    return L10N.get(lang, key, **kw)


# ==============================================================================
# SECTION 5: DATABASE MODELS
# ==============================================================================

class Base(DeclarativeBase):
    pass


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
    referrals = relationship("User", backref="referrer", remote_side=[id])

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
    task_type: Mapped[TaskType] = mapped_column(SQLEnum(TaskType, name="tasktype"))

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

    status: Mapped[CampaignStatus] = mapped_column(SQLEnum(CampaignStatus, name="campaignstatus"), default=CampaignStatus.ACTIVE)

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

    retention_status: Mapped[RetentionStatus] = mapped_column(SQLEnum(RetentionStatus, name="retentionstatus"), default=RetentionStatus.PENDING)
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
    type: Mapped[TransactionType] = mapped_column(SQLEnum(TransactionType, name="transactiontype"))
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
    check_type: Mapped[CheckType] = mapped_column(SQLEnum(CheckType, name="checktype"), default=CheckType.MULTI_USE)
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
    type: Mapped[NotificationType] = mapped_column(SQLEnum(NotificationType, name="notificationtype"))
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[Optional[str]] = mapped_column(Text)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index('ix_notification_unsent', 'is_sent', 'created_at'),
    )


# ==============================================================================
# SECTION 6: ENGINE & REDIS
# ==============================================================================

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)


# ==============================================================================
# SECTION 7: FSM STATES
# ==============================================================================

class TopUpState(StatesGroup):
    stars = State()

class PromoState(StatesGroup):
    task_type = State()
    title = State()
    target = State()
    premium = State()
    reward = State()
    slots = State()

class AddSlotsState(StatesGroup):
    amount = State()

class CheckCreateState(StatesGroup):
    amount = State()
    activations = State()
    password = State()

class CheckRedeemState(StatesGroup):
    code = State()
    password = State()

class WithdrawState(StatesGroup):
    amount = State()
    method = State()
    details = State()

class AdminState(StatesGroup):
    coins_uid = State()
    coins_amount = State()
    ban_uid = State()
    broadcast = State()


# ==============================================================================
# SECTION 8: HELPERS
# ==============================================================================

def level_emoji(lv: int) -> str:
    if lv >= 50: return "👑"
    if lv >= 40: return "💎"
    if lv >= 30: return "🥇"
    if lv >= 20: return "🥈"
    if lv >= 10: return "🥉"
    return "🌱"


async def alert_admins(bot: Bot, text: str):
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text, parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ==============================================================================
# SECTION 9: KEYBOARDS (Screenshot-Matched)
# ==============================================================================

def main_menu(lang: str) -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.row(
        KeyboardButton(text=t(lang, "menu_earnings")),
        KeyboardButton(text=t(lang, "menu_promote")),
    )
    b.row(
        KeyboardButton(text=t(lang, "menu_checks")),
        KeyboardButton(text=t(lang, "menu_cabinet")),
    )
    b.row(
        KeyboardButton(text=t(lang, "menu_sub_check")),
        KeyboardButton(text=t(lang, "menu_stats")),
    )
    b.row(
        KeyboardButton(text=t(lang, "menu_links")),
        KeyboardButton(text=t(lang, "menu_instruction")),
    )
    return b.as_markup(resize_keyboard=True, is_persistent=True)


def cabinet_kb(lang: str, notif_on: bool, can_withdraw: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=t(lang, "btn_replenish"), callback_data="cab:replenish"))
    b.row(InlineKeyboardButton(text=t(lang, "btn_referral"), callback_data="cab:referral"))
    b.row(
        InlineKeyboardButton(text=t(lang, "btn_level"), callback_data="cab:level"),
        InlineKeyboardButton(text=t(lang, "btn_tasks"), callback_data="cab:tasks"),
    )
    if can_withdraw:
        b.row(InlineKeyboardButton(text=t(lang, "menu_withdraw"), callback_data="cab:withdraw"))
    notif_key = "btn_notif_off" if notif_on else "btn_notif_on"
    b.row(InlineKeyboardButton(text=t(lang, notif_key), callback_data="cab:notif"))
    b.row(InlineKeyboardButton(text=t(lang, "btn_lang"), callback_data="cab:lang"))
    return b.as_markup()


def back_to_cabinet_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="cab:back")]
    ])


def language_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    items = [(code, name) for code, name in L10N.LANGS.items()]
    for i in range(0, len(items), 3):
        row = items[i:i + 3]
        b.row(*[InlineKeyboardButton(text=name, callback_data=f"lang:{code}")
                for code, name in row])
    return b.as_markup()


def category_kb(lang: str, counts: dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=f"📢 Channel · {counts.get('channel', 0)}", callback_data="earn:cat:channel_sub"),
        InlineKeyboardButton(text=f"👥 Group · {counts.get('group', 0)}", callback_data="earn:cat:group_join"),
    )
    b.row(
        InlineKeyboardButton(text=f"👀 Post · {counts.get('post', 0)}", callback_data="earn:cat:post_view"),
        InlineKeyboardButton(text=f"🤖 Bot · {counts.get('bot', 0)}", callback_data="earn:cat:bot_start"),
    )
    b.row(
        InlineKeyboardButton(text=f"❤️ Reaction · {counts.get('reaction', 0)}", callback_data="earn:cat:reaction"),
        InlineKeyboardButton(text=f"⚡ Boost · {counts.get('boost', 0)}", callback_data="earn:cat:boost_7day"),
    )
    b.row(InlineKeyboardButton(text="📋 Rules", callback_data="earn:rules"))
    b.row(InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="cab:back"))
    return b.as_markup()


def promote_type_kb(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="📢 Channel", callback_data="promo:type:channel_sub"),
        InlineKeyboardButton(text="👥 Group", callback_data="promo:type:group_join"),
    )
    b.row(
        InlineKeyboardButton(text="👀 Post View", callback_data="promo:type:post_view"),
        InlineKeyboardButton(text="🤖 Bot Start", callback_data="promo:type:bot_start"),
    )
    b.row(
        InlineKeyboardButton(text="❤️ Reaction", callback_data="promo:type:reaction"),
        InlineKeyboardButton(text="⚡ Premium Boost", callback_data="promo:type:boost_7day"),
    )
    b.row(InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="cab:back"))
    return b.as_markup()


def premium_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "promote_premium_yes"), callback_data="promo:premium:yes")],
        [InlineKeyboardButton(text=t(lang, "promote_premium_no"), callback_data="promo:premium:no")],
    ])


def checks_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "check_create_btn"), callback_data="chk:create")],
        [InlineKeyboardButton(text=t(lang, "check_activate_btn"), callback_data="chk:redeem")],
        [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="cab:back")],
    ])


def task_detail_kb(lang: str, cid: str, link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "task_open"), url=link)],
        [InlineKeyboardButton(text=t(lang, "task_verify"), callback_data=f"earn:verify:{cid}")],
        [InlineKeyboardButton(text=t(lang, "task_back"), callback_data="earn:back")],
    ])


def pagination_kb(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if page > 0:
        b.button(text="⬅️", callback_data=f"{prefix}:{page - 1}")
    b.button(text=f"{page + 1}/{total_pages}", callback_data="noop")
    if page < total_pages - 1:
        b.button(text="➡️", callback_data=f"{prefix}:{page + 1}")
    return b.as_markup()


def admin_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="💰 Add/Deduct Coins", callback_data="adm:coins"))
    b.row(InlineKeyboardButton(text="🚨 Fraud Reports", callback_data="adm:reports"))
    b.row(InlineKeyboardButton(text="💸 Withdrawals", callback_data="adm:withdrawals"))
    b.row(InlineKeyboardButton(text="🚫 Ban/Unban User", callback_data="adm:ban"))
    b.row(InlineKeyboardButton(text="📢 Moderate Campaigns", callback_data="adm:tasks"))
    b.row(InlineKeyboardButton(text="📊 Full Stats", callback_data="adm:stats"))
    return b.as_markup()


def withdraw_method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 USDT (TRC20)", callback_data="wd:m:usdt_trc20")],
        [InlineKeyboardButton(text="💎 USDT (ERC20)", callback_data="wd:m:usdt_erc20")],
        [InlineKeyboardButton(text="🏦 Bank Transfer", callback_data="wd:m:bank")],
        [InlineKeyboardButton(text="📱 Mobile Wallet", callback_data="wd:m:mobile")],
    ])


# ==============================================================================
# SECTION 10: ROUTER & HANDLERS
# ==============================================================================

router = Router()


async def get_user(session, uid):
    r = await session.execute(select(User).where(User.id == uid))
    return r.scalar_one_or_none()


async def get_or_create_user(session, tg_user, referrer_id=None):
    r = await session.execute(select(User).where(User.id == tg_user.id))
    u = r.scalar_one_or_none()
    if u:
        u.username = tg_user.username
        u.first_name = tg_user.first_name
        u.last_name = tg_user.last_name
        u.is_premium = tg_user.is_premium or False
        u.last_active_at = datetime.now(timezone.utc)
        return u, False
    u = User(
        id=tg_user.id, username=tg_user.username,
        first_name=tg_user.first_name, last_name=tg_user.last_name,
        is_premium=tg_user.is_premium or False,
        referral_code=uuid.uuid4().hex[:8],
        referred_by=referrer_id,
    )
    session.add(u)
    await session.flush()
    return u, True


# ==============================================================================
# START
# ==============================================================================

@router.message(CommandStart())
async def cmd_start(message: Message, session, bot: Bot, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    args = message.text.split(maxsplit=1)
    referrer_id = None
    check_code = None

    if len(args) > 1:
        payload = args[1].strip()
        if payload.startswith("ref_"):
            rc = payload[4:]
            r = await session.execute(select(User).where(User.referral_code == rc))
            ref_user = r.scalar_one_or_none()
            if ref_user and ref_user.id != uid:
                referrer_id = ref_user.id
        elif payload.startswith("check_"):
            check_code = payload[6:].upper()

    user, is_new = await get_or_create_user(session, message.from_user, referrer_id)

    if is_new and referrer_id:
        rr = await session.execute(select(User).where(User.id == referrer_id).with_for_update())
        ref = rr.scalar_one_or_none()
        if ref:
            ref.balance += BusinessRules.REFERRAL_BASE_REWARD
            ref.total_earned += BusinessRules.REFERRAL_BASE_REWARD
            ref.referral_earnings += BusinessRules.REFERRAL_BASE_REWARD
            ref.add_xp(BusinessRules.REFERRAL_XP_BASE)
            session.add(Transaction(
                user_id=ref.id,
                amount=BusinessRules.REFERRAL_BASE_REWARD,
                type=TransactionType.REFERRAL_REWARD,
                description="Referral bonus",
                balance_after=ref.balance,
            ))

    await session.commit()

    if check_code:
        await _redeem_check_deeplink(message, session, check_code)
        return

    await message.answer(
        t(user.language, "welcome", bot_name=esc(BOT_USERNAME),
          first_name=esc(message.from_user.first_name)),
        reply_markup=main_menu(user.language),
        parse_mode=ParseMode.HTML,
    )


# ==============================================================================
# CABINET
# ==============================================================================

@router.message(F.text.in_([L10N.T["en"]["menu_cabinet"], L10N.T["bn"]["menu_cabinet"]]))
async def show_cabinet(message: Message, session):
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer(t("en", "not_started"))
        return
    await message.answer(
        t(user.language, "cabinet",
          user_id=user.id, level_emoji=level_emoji(user.level),
          level=user.level, balance=user.balance,
          xp=user.current_level_xp, next_xp=user.next_level_xp,
          tasks=user.completed_tasks_count),
        reply_markup=cabinet_kb(user.language, user.notifications_enabled, user.can_withdraw),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "cab:back")
async def cab_back(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    if not user:
        await query.answer()
        return
    await query.message.edit_text(
        t(user.language, "cabinet",
          user_id=user.id, level_emoji=level_emoji(user.level),
          level=user.level, balance=user.balance,
          xp=user.current_level_xp, next_xp=user.next_level_xp,
          tasks=user.completed_tasks_count),
        reply_markup=cabinet_kb(user.language, user.notifications_enabled, user.can_withdraw),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "cab:notif")
async def cab_notif(query: CallbackQuery, session):
    r = await session.execute(select(User).where(User.id == query.from_user.id).with_for_update())
    user = r.scalar_one_or_none()
    if not user:
        await query.answer()
        return
    user.notifications_enabled = not user.notifications_enabled
    await session.commit()
    await query.answer("✅ Updated", show_alert=True)
    try:
        await query.message.edit_reply_markup(
            reply_markup=cabinet_kb(user.language, user.notifications_enabled, user.can_withdraw)
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "cab:lang")
async def cab_lang(query: CallbackQuery):
    await query.message.edit_text(
        "🌐 <b>Select Language</b>",
        reply_markup=language_kb(),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data.startswith("lang:"))
async def set_lang(query: CallbackQuery, session):
    code = query.data.split(":")[1]
    if code not in L10N.LANGS:
        code = "en"
    r = await session.execute(select(User).where(User.id == query.from_user.id).with_for_update())
    user = r.scalar_one_or_none()
    if not user:
        await query.answer()
        return
    user.language = code
    await session.commit()
    await query.message.edit_text(t(code, "lang_changed"))
    await query.message.answer(
        t(code, "welcome", bot_name=esc(BOT_USERNAME), first_name=esc(user.first_name)),
        reply_markup=main_menu(code),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


# ==============================================================================
# EARNINGS
# ==============================================================================

@router.message(F.text.in_([L10N.T["en"]["menu_earnings"], L10N.T["bn"]["menu_earnings"]]))
async def earnings_cmd(message: Message, session):
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer(t("en", "not_started"))
        return
    await _render_categories(message, session, user, edit=False)


async def _render_categories(message, session, user, edit=False):
    counts = {}
    for key, tt in [
        ("channel", TaskType.CHANNEL_SUB),
        ("group", TaskType.GROUP_JOIN),
        ("post", TaskType.POST_VIEW),
        ("bot", TaskType.BOT_START),
        ("reaction", TaskType.REACTION),
        ("boost", TaskType.BOOST_7DAY),
    ]:
        r = await session.execute(
            select(func.count(Campaign.id)).where(
                Campaign.task_type == tt,
                Campaign.status == CampaignStatus.ACTIVE,
                Campaign.completed_count < Campaign.max_completions,
            )
        )
        counts[key] = r.scalar() or 0

    text = "💰 <b>Select Task Category</b>\n\nComplete tasks to earn GRAM coins:"
    kb = category_kb(user.language, counts)
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "earn:rules")
async def earn_rules(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(
        t(lang, "rules_title") + "\n\n" + t(lang, "rules_forbidden") + "\n\n" + t(lang, "rules_penalty"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="earn:back")]
        ]),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "earn:back")
async def earn_back(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    if not user:
        await query.answer()
        return
    await _render_categories(query.message, session, user, edit=True)
    await query.answer()


@router.callback_query(F.data.startswith("earn:cat:"))
async def earn_category(query: CallbackQuery, session):
    tt = query.data.split(":")[2]
    await _show_task_list(query, session, tt, 0)
    await query.answer()


async def _show_task_list(query, session, task_type, page):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"

    done_sub = select(TaskCompletion.campaign_id).where(TaskCompletion.user_id == query.from_user.id)
    base = [
        Campaign.task_type == TaskType(task_type),
        Campaign.status == CampaignStatus.ACTIVE,
        Campaign.completed_count < Campaign.max_completions,
        Campaign.id.not_in(done_sub),
    ]
    if not (user and user.is_premium):
        base.append(Campaign.premium_only == False)

    total_res = await session.execute(select(func.count(Campaign.id)).where(*base))
    total = total_res.scalar() or 0
    pages = max(1, (total + BusinessRules.TASKS_PAGE_SIZE - 1) // BusinessRules.TASKS_PAGE_SIZE)

    stmt = (
        select(Campaign)
        .where(*base)
        .order_by(Campaign.created_at.desc())
        .offset(page * BusinessRules.TASKS_PAGE_SIZE)
        .limit(BusinessRules.TASKS_PAGE_SIZE)
    )
    r = await session.execute(stmt)
    items = r.scalars().all()

    buttons = []
    for c in items:
        star = "⭐ " if c.premium_only else ""
        buttons.append([InlineKeyboardButton(
            text=f"{star}{c.title[:30]} · +{c.reward_per_user:,.0f}",
            callback_data=f"earn:view:{c.id}",
        )])

    if items:
        if pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"earn:page:{task_type}:{page-1}"))
            nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="noop"))
            if page < pages - 1:
                nav.append(InlineKeyboardButton(text="➡️", callback_data=f"earn:page:{task_type}:{page+1}"))
            buttons.append(nav)

    buttons.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="earn:back")])
    text = f"🎯 <b>Tasks</b> ({total})" if items else t(lang, "no_tasks")
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await query.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("earn:page:"))
async def earn_page(query: CallbackQuery, session):
    _, _, tt, p = query.data.split(":")
    await _show_task_list(query, session, tt, int(p))
    await query.answer()


@router.callback_query(F.data.startswith("earn:view:"))
async def earn_view(query: CallbackQuery, session):
    cid = query.data.split(":")[2]
    r = await session.execute(select(Campaign).where(Campaign.id == cid))
    c = r.scalar_one_or_none()
    if not c or c.status != CampaignStatus.ACTIVE:
        await query.answer("Unavailable", show_alert=True)
        return
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"

    link = c.target_link or (f"https://t.me/{c.target_username}" if c.target_username else f"https://t.me/{BOT_USERNAME}")
    tag = "⭐ <b>PREMIUM ONLY</b>\n\n" if c.premium_only else ""
    text = (
        f"{tag}📢 <b>{esc(c.title)}</b>\n\n"
        f"💰 Reward: <code>{c.reward_per_user:,.0f}</code> GRAM\n"
        f"👥 Slots: <code>{c.slots_remaining}</code>\n"
        f"⏰ Retention: {c.retention_days} days"
    )
    await query.message.edit_text(text, reply_markup=task_detail_kb(lang, c.id, link), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data.startswith("earn:verify:"))
async def earn_verify(query: CallbackQuery, session, bot: Bot):
    cid = query.data.split(":")[2]
    uid = query.from_user.id
    lock = f"lock:task:{uid}:{cid}"
    if not await redis_client.set(lock, "1", nx=True, ex=15):
        await query.answer("⏳ Processing...", show_alert=True)
        return
    try:
        ok, msg = await _process_task(session, bot, uid, cid)
        await query.answer(msg[:200], show_alert=True)
        if ok:
            await _show_task_list(query, session, "", 0)
    finally:
        await redis_client.delete(lock)


async def _process_task(session, bot, uid, cid):
    try:
        async with session.begin_nested():
            r = await session.execute(select(Campaign).where(Campaign.id == cid).with_for_update())
            c = r.scalar_one_or_none()
            if not c or c.status != CampaignStatus.ACTIVE:
                return False, "❌ Task inactive."
            if c.is_full:
                c.status = CampaignStatus.COMPLETED
                return False, t("en", "task_limit")

            ru = await session.execute(select(User).where(User.id == uid).with_for_update())
            user = ru.scalar_one_or_none()
            if not user or user.is_blocked:
                return False, "❌ Account issue."

            rc = await session.execute(select(TaskCompletion).where(
                TaskCompletion.user_id == uid, TaskCompletion.campaign_id == cid
            ))
            if rc.scalar_one_or_none():
                return False, t(user.language, "task_already")

            if c.premium_only and not user.is_premium:
                return False, t(user.language, "task_premium_only")

            if c.task_type in VERIFIABLE_TASKS:
                target = c.target_chat_id or (f"@{c.target_username}" if c.target_username else None)
                if not target:
                    return False, "❌ Misconfigured."
                try:
                    m = await bot.get_chat_member(target, uid)
                    if m.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                                        ChatMemberStatus.CREATOR, ChatMemberStatus.RESTRICTED):
                        return False, t(user.language, "task_failed")
                except (TelegramBadRequest, TelegramForbiddenError):
                    return False, t(user.language, "task_failed")

            reward = c.reward_per_user
            xp = int((reward / 1000) * BusinessRules.XP_PER_1000_COINS)

            deadline = None
            if c.requires_retention_check and c.task_type in VERIFIABLE_TASKS:
                deadline = datetime.now(timezone.utc) + timedelta(days=c.retention_days)

            session.add(TaskCompletion(
                user_id=uid, campaign_id=cid, reward=reward, xp_earned=xp,
                retention_status=RetentionStatus.PENDING if deadline else RetentionStatus.VERIFIED,
                retention_deadline=deadline,
            ))

            user.balance += reward
            user.total_earned += reward
            user.completed_tasks_count += 1
            user.add_xp(xp)

            tx = Transaction(
                user_id=uid, amount=reward, type=TransactionType.TASK_REWARD,
                description=f"Task: {c.title}", reference_id=cid,
                reference_type="campaign", balance_after=user.balance,
            )
            session.add(tx)

            c.completed_count += 1
            c.spent_budget += reward
            if c.is_full:
                c.status = CampaignStatus.COMPLETED

            await _referral_tiers(session, uid, reward, tx.id)

        await session.commit()
        return True, t(user.language, "task_verified", reward=reward, xp=xp)
    except Exception as e:
        await session.rollback()
        logger.exception(f"Task error: {e}")
        return False, t("en", "error_generic")


async def _referral_tiers(session, earner_id, amount, tx_id):
    current = earner_id
    for tier in (1, 2, 3):
        ru = await session.execute(select(User).where(User.id == current))
        u = ru.scalar_one_or_none()
        if not u or not u.referred_by:
            break
        ref_id = u.referred_by
        pct = BusinessRules.REFERRAL_TIER_BONUSES.get(tier, Decimal("0"))
        if pct > 0:
            bonus = amount * pct
            xp_bonus = int((bonus / 1000) * BusinessRules.XP_PER_1000_COINS)
            rr = await session.execute(select(User).where(User.id == ref_id).with_for_update())
            ref = rr.scalar_one_or_none()
            if ref and not ref.is_blocked:
                ref.balance += bonus
                ref.total_earned += bonus
                ref.referral_earnings += bonus
                ref.add_xp(xp_bonus)
                session.add(Transaction(
                    user_id=ref_id, amount=bonus,
                    type=TransactionType.REFERRAL_TIER_BONUS,
                    description=f"Tier {tier} bonus",
                    reference_id=tx_id, reference_type="referral_bonus",
                    balance_after=ref.balance,
                ))
                session.add(ReferralEarning(
                    referrer_id=ref_id, referred_user_id=earner_id,
                    tier=tier, source_amount=amount, bonus_amount=bonus,
                    xp_bonus=xp_bonus, transaction_id=tx_id,
                ))
        current = ref_id


# ==============================================================================
# PROMOTE
# ==============================================================================

@router.message(F.text.in_([L10N.T["en"]["menu_promote"], L10N.T["bn"]["menu_promote"]]))
async def promote_start(message: Message, session, state: FSMContext):
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer(t("en", "not_started"))
        return
    await message.answer(
        t(user.language, "promote_title"),
        reply_markup=promote_type_kb(user.language),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(PromoState.task_type)


@router.callback_query(PromoState.task_type, F.data.startswith("promo:type:"))
async def promo_type(query: CallbackQuery, state: FSMContext, session):
    tt = query.data.split(":")[2]
    await state.update_data(task_type=tt)
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(t(lang, "promote_name"))
    await state.set_state(PromoState.title)
    await query.answer()


@router.message(PromoState.title)
async def promo_title(message: Message, session, state: FSMContext):
    txt = safe_text(message)
    if not txt or len(txt) > 200:
        await message.answer("❌ Title 1-200 chars.")
        return
    await state.update_data(title=txt)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    data = await state.get_data()
    tt = data["task_type"]
    if tt in ("channel_sub", "group_join", "boost_7day"):
        await message.answer(t(lang, "promote_target"), parse_mode=ParseMode.HTML)
    elif tt == "bot_start":
        await message.answer(t(lang, "promote_bot_token"))
    else:
        await message.answer(t(lang, "promote_link"))
    await state.set_state(PromoState.target)


@router.message(PromoState.target)
async def promo_target(message: Message, session, state: FSMContext, bot: Bot):
    raw = safe_text(message)
    if not raw:
        await message.answer(t("en", "text_only"))
        return
    data = await state.get_data()
    tt = data["task_type"]
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"

    if tt in ("channel_sub", "group_join", "boost_7day"):
        status = await message.answer("🔎 Verifying...")
        try:
            identifier = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", raw, flags=re.I)
            identifier = identifier.lstrip("@").split("/")[0].split("?")[0]
            if identifier.lstrip("-").isdigit():
                identifier = int(identifier)
            else:
                identifier = f"@{identifier}"
            chat = await bot.get_chat(identifier)
            me = await bot.get_me()
            m = await bot.get_chat_member(chat.id, me.id)
            if m.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
                await status.edit_text(f"❌ Add @{esc(BOT_USERNAME)} as <b>Admin</b>, then send again.", parse_mode=ParseMode.HTML)
                return
            invite = None
            if not chat.username:
                try: invite = await bot.export_chat_invite_link(chat.id)
                except Exception: pass
            await state.update_data(
                target_chat_id=chat.id, target_username=chat.username,
                target_link=invite or (f"https://t.me/{chat.username}" if chat.username else None),
                target_title=chat.title or str(chat.id),
            )
            await status.edit_text(f"✅ Verified: <b>{esc(chat.title)}</b>", parse_mode=ParseMode.HTML)
        except Exception as e:
            await status.edit_text(f"❌ Chat error: {esc(str(e))[:200]}")
            return
    elif tt == "bot_start":
        try:
            from aiogram import Bot as TBot
            tb = TBot(token=raw)
            me = await tb.get_me()
            await tb.session.close()
            await state.update_data(
                target_bot_token_hash=hash_sensitive(raw),
                target_bot_id=me.id, target_username=me.username,
                target_link=f"https://t.me/{me.username}",
                target_title=me.first_name,
            )
            await message.answer(f"✅ Bot: @{esc(me.username)}")
        except Exception:
            await message.answer("❌ Invalid token. Send again.")
            return
    else:
        await state.update_data(target_link=raw)

    await message.answer(t(lang, "promote_premium"), reply_markup=premium_kb(lang), parse_mode=ParseMode.HTML)
    await state.set_state(PromoState.premium)


@router.callback_query(PromoState.premium, F.data.startswith("promo:premium:"))
async def promo_premium(query: CallbackQuery, state: FSMContext, session):
    is_prem = query.data.endswith(":yes")
    await state.update_data(premium_only=is_prem)
    data = await state.get_data()
    rules = TaskPricing.get(data["task_type"], is_prem)
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(
        t(lang, "promote_reward", min=rules["min"], max=rules["max"], suggested=rules["suggested"]),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(PromoState.reward)
    await query.answer()


@router.message(PromoState.reward)
async def promo_reward(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    if not raw:
        await message.answer(t(lang, "text_only"))
        return
    try:
        v = Decimal(raw)
    except Exception:
        await message.answer("❌ Invalid number.")
        return
    data = await state.get_data()
    ok, err = TaskPricing.validate(data["task_type"], v, data.get("premium_only", False))
    if not ok:
        await message.answer(f"❌ {err}")
        return
    await state.update_data(reward=str(v))
    await message.answer(t(lang, "promote_slots"))
    await state.set_state(PromoState.slots)


@router.message(PromoState.slots)
async def promo_slots(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    if not raw:
        await message.answer(t(lang, "text_only"))
        return
    try:
        n = int(raw)
        if n <= 0 or n > 100000:
            raise ValueError()
    except Exception:
        await message.answer("❌ 1-100000.")
        return

    data = await state.get_data()
    await state.clear()

    reward = Decimal(data["reward"])
    cost = reward * n
    commission = cost * BusinessRules.PLATFORM_COMMISSION
    total = cost + commission

    uid = message.from_user.id
    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = r.scalar_one_or_none()
        if not user:
            await message.answer(t("en", "not_started"))
            return
        if user.balance < total:
            await message.answer(
                t(lang, "promote_insufficient", required=total, balance=user.balance),
                parse_mode=ParseMode.HTML,
            )
            return

        # Duplicate check
        if data.get("target_chat_id"):
            d = await session.execute(select(Campaign).where(
                Campaign.advertiser_id == uid,
                Campaign.target_chat_id == data["target_chat_id"],
                Campaign.task_type == TaskType(data["task_type"]),
                Campaign.status == CampaignStatus.ACTIVE,
            ))
            if d.scalar_one_or_none():
                await message.answer(t(lang, "promote_duplicate"))
                return

        user.balance -= total
        camp = Campaign(
            advertiser_id=uid,
            title=data["title"],
            task_type=TaskType(data["task_type"]),
            target_chat_id=data.get("target_chat_id"),
            target_username=data.get("target_username"),
            target_link=data.get("target_link"),
            target_title=data.get("target_title"),
            target_bot_token_hash=data.get("target_bot_token_hash"),
            target_bot_id=data.get("target_bot_id"),
            premium_only=data.get("premium_only", False),
            reward_per_user=reward,
            base_reward=reward / (BusinessRules.PREMIUM_MULTIPLIER if data.get("premium_only") else Decimal("1")),
            max_completions=n,
            total_budget=total,
            requires_retention_check=data["task_type"] in {x.value for x in VERIFIABLE_TASKS},
        )
        session.add(camp)
        session.add(Transaction(
            user_id=uid, amount=-total,
            type=TransactionType.CAMPAIGN_PAYMENT,
            description=f"Campaign: {data['title']}",
            reference_id=camp.id, reference_type="campaign",
            balance_after=user.balance,
        ))
    await session.commit()

    await message.answer(
        t(lang, "promote_created", title=esc(data["title"]), reward=reward, slots=n, cost=total),
        parse_mode=ParseMode.HTML,
    )


# ==============================================================================
# CHECKS
# ==============================================================================

@router.message(F.text.in_([L10N.T["en"]["menu_checks"], L10N.T["bn"]["menu_checks"]]))
async def checks_menu(message: Message, session):
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer(t("en", "not_started"))
        return
    await message.answer(
        t(user.language, "checks_menu"),
        reply_markup=checks_kb(user.language),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "chk:create")
async def chk_create(query: CallbackQuery, session, state: FSMContext):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(t(lang, "check_amount"))
    await state.set_state(CheckCreateState.amount)
    await query.answer()


@router.message(CheckCreateState.amount)
async def chk_amount(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    if not raw:
        await message.answer(t(lang, "text_only"))
        return
    try:
        v = Decimal(raw)
        if v <= 0: raise ValueError()
    except Exception:
        await message.answer("❌ Invalid.")
        return
    await state.update_data(amount=str(v))
    await message.answer(t(lang, "check_activations"))
    await state.set_state(CheckCreateState.activations)


@router.message(CheckCreateState.activations)
async def chk_act(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    if not raw:
        await message.answer(t(lang, "text_only"))
        return
    try:
        n = int(raw)
        if n <= 0 or n > 10000: raise ValueError()
    except Exception:
        await message.answer("❌ 1-10000.")
        return
    await state.update_data(activations=n)
    await message.answer(t(lang, "check_password"), parse_mode=ParseMode.HTML)
    await state.set_state(CheckCreateState.password)


@router.message(CheckCreateState.password)
async def chk_final(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    pw = None if not raw or raw.lower() == "skip" else raw
    data = await state.get_data()
    await state.clear()
    amount = Decimal(data["amount"])
    n = data["activations"]
    total = amount * n
    uid = message.from_user.id

    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = r.scalar_one_or_none()
        if not user:
            await message.answer(t("en", "not_started"))
            return
        if user.balance < total:
            await message.answer(
                t(user.language, "promote_insufficient", required=total, balance=user.balance),
                parse_mode=ParseMode.HTML,
            )
            return
        code = uuid.uuid4().hex[:10].upper()
        ck = Check(
            code=code,
            check_type=CheckType.SINGLE_USE if n == 1 else CheckType.MULTI_USE,
            created_by=uid,
            amount_per_activation=amount,
            max_activations=n,
            requires_password=pw is not None,
            password_hash=hash_sensitive(pw) if pw else None,
            total_funded=total,
        )
        session.add(ck)
        user.balance -= total
        session.add(Transaction(
            user_id=uid, amount=-total, type=TransactionType.CHECK_CREATE,
            description=f"Check {code}", reference_id=ck.id,
            reference_type="check", balance_after=user.balance,
        ))
    await session.commit()
    link = f"https://t.me/{BOT_USERNAME}?start=check_{code}"
    await message.answer(
        t(user.language, "check_created", code=code, amount=amount, activations=n, link=esc(link)),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "chk:redeem")
async def chk_redeem(query: CallbackQuery, session, state: FSMContext):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(t(lang, "check_redeem_prompt"))
    await state.set_state(CheckRedeemState.code)
    await query.answer()


@router.message(CheckRedeemState.code)
async def chk_redeem_code(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    await state.clear()
    if not raw:
        await message.answer(t("en", "text_only"))
        return
    await _redeem_check_deeplink(message, session, raw.upper().strip())


async def _redeem_check_deeplink(message, session, code):
    r = await session.execute(select(Check).where(Check.code == code))
    ck = r.scalar_one_or_none()
    if not ck or not ck.is_active or ck.is_exhausted:
        await message.answer(t("en", "check_invalid"))
        return
    if ck.requires_password:
        await message.answer(t("en", "check_password_prompt"))
        await redis_client.setex(f"chk_pw:{message.from_user.id}", 300, code)
        return
    ok, msg = await _do_redeem(message, session, ck, None)
    await message.answer(msg, parse_mode=ParseMode.HTML)


async def _do_redeem(message, session, ck, pw):
    uid = message.from_user.id
    async with session.begin_nested():
        r = await session.execute(select(Check).where(Check.id == ck.id).with_for_update())
        ck = r.scalar_one_or_none()
        if not ck or not ck.is_active or ck.is_exhausted:
            return False, t("en", "check_invalid")
        if ck.created_by == uid:
            return False, "❌ Can't redeem your own check."
        if ck.requires_password and (not pw or not ck.verify_password(pw)):
            return False, t("en", "check_wrong_pw")
        ra = await session.execute(select(CheckActivation).where(
            CheckActivation.check_id == ck.id, CheckActivation.user_id == uid
        ))
        if ra.scalar_one_or_none():
            return False, "❌ Already redeemed."
        ru = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = ru.scalar_one_or_none()
        if not user:
            return False, t("en", "not_started")
        amount = ck.amount_per_activation
        user.balance += amount
        user.total_earned += amount
        user.add_xp(int((amount / 1000) * BusinessRules.XP_PER_1000_COINS))
        ck.activations_count += 1
        if ck.is_exhausted:
            ck.is_active = False
        session.add(CheckActivation(check_id=ck.id, user_id=uid, amount=amount))
        session.add(Transaction(
            user_id=uid, amount=amount, type=TransactionType.CHECK_REDEEM,
            description=f"Check {ck.code}", reference_id=ck.id,
            reference_type="check", balance_after=user.balance,
        ))
    await session.commit()
    return True, t(user.language, "check_redeemed", amount=amount)


# ==============================================================================
# CABINET ACTIONS
# ==============================================================================

@router.callback_query(F.data == "cab:referral")
async def cab_ref(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    if not user:
        await query.answer()
        return
    r = await session.execute(select(func.count(User.id)).where(User.referred_by == user.id))
    direct = r.scalar() or 0
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.referral_code}"
    await query.message.edit_text(
        t(user.language, "ref_title",
          base=BusinessRules.REFERRAL_BASE_REWARD, direct=direct,
          earned=user.referral_earnings, link=esc(link)),
        reply_markup=back_to_cabinet_kb(user.language),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "cab:level")
async def cab_lv(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    if not user:
        await query.answer()
        return
    cur = user.xp % BusinessRules.XP_PER_LEVEL
    bar = "█" * (cur * 10 // BusinessRules.XP_PER_LEVEL) + "░" * (10 - cur * 10 // BusinessRules.XP_PER_LEVEL)
    await query.message.edit_text(
        t(user.language, "level_title",
          emoji=level_emoji(user.level), level=user.level, xp=user.xp,
          progress=f"[{bar}] {cur}/{BusinessRules.XP_PER_LEVEL}",
          min_level=BusinessRules.WITHDRAWAL_MIN_LEVEL),
        reply_markup=back_to_cabinet_kb(user.language),
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "cab:tasks")
async def cab_tasks(query: CallbackQuery, session):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    r = await session.execute(
        select(TaskCompletion, Campaign)
        .join(Campaign, Campaign.id == TaskCompletion.campaign_id)
        .where(TaskCompletion.user_id == query.from_user.id)
        .order_by(TaskCompletion.completed_at.desc())
        .limit(15)
    )
    rows = r.all()
    if not rows:
        text = "📋 No tasks yet."
    else:
        lines = ["📋 <b>My Tasks</b>\n"]
        for tc, c in rows:
            ic = {"pending": "⏳", "verified": "✅", "penalized": "🚫", "failed": "❌"}.get(tc.retention_status.value, "❓")
            lines.append(f"{ic} {esc(c.title[:30])} · +{tc.reward:,.0f}")
        text = "\n".join(lines)
    await query.message.edit_text(text, reply_markup=back_to_cabinet_kb(lang), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "cab:withdraw")
async def cab_wd(query: CallbackQuery, session, state: FSMContext):
    user = await get_user(session, query.from_user.id)
    if not user:
        await query.answer()
        return
    if not user.can_withdraw:
        await query.message.edit_text(
            t(user.language, "wd_locked",
              required=BusinessRules.WITHDRAWAL_MIN_LEVEL, current=user.level),
            reply_markup=back_to_cabinet_kb(user.language),
            parse_mode=ParseMode.HTML,
        )
        await query.answer()
        return
    await query.message.edit_text(
        t(user.language, "wd_prompt",
          min=BusinessRules.WITHDRAWAL_MIN_AMOUNT,
          max=BusinessRules.WITHDRAWAL_MAX_AMOUNT,
          balance=user.balance),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(WithdrawState.amount)
    await query.answer()


@router.message(WithdrawState.amount)
async def wd_amt(message: Message, session, state: FSMContext):
    raw = safe_text(message)
    user = await get_user(session, message.from_user.id)
    lang = user.language if user else "en"
    if not raw:
        await message.answer(t(lang, "text_only"))
        return
    try:
        v = Decimal(raw)
    except Exception:
        await message.answer("❌ Invalid.")
        return
    if v < BusinessRules.WITHDRAWAL_MIN_AMOUNT or v > BusinessRules.WITHDRAWAL_MAX_AMOUNT:
        await message.answer(
            t(lang, "wd_invalid",
              min=BusinessRules.WITHDRAWAL_MIN_AMOUNT,
              max=BusinessRules.WITHDRAWAL_MAX_AMOUNT))
        return
    if v > user.balance:
        await message.answer(t(lang, "wd_insufficient"))
        return
    await state.update_data(amount=str(v))
    await message.answer(t(lang, "wd_method"), reply_markup=withdraw_method_kb())
    await state.set_state(WithdrawState.method)


@router.callback_query(WithdrawState.method, F.data.startswith("wd:m:"))
async def wd_method(query: CallbackQuery, session, state: FSMContext):
    m = query.data.split(":")[2]
    await state.update_data(method=m)
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.edit_text(t(lang, "wd_details"))
    await state.set_state(WithdrawState.details)
    await query.answer()


@router.message(WithdrawState.details)
async def wd_details(message: Message, session, state: FSMContext, bot: Bot):
    details = safe_text(message)
    if not details:
        await message.answer("❌ Send details.")
        return
    data = await state.get_data()
    await state.clear()
    amount = Decimal(data["amount"])
    method = data["method"]
    uid = message.from_user.id

    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        user = r.scalar_one_or_none()
        if not user or user.balance < amount:
            await message.answer("❌ Insufficient.")
            return
        user.balance -= amount
        user.total_withdrawn += amount
        session.add(WithdrawalRequest(
            user_id=uid, amount=amount,
            payment_method=method, payment_details=details[:500],
        ))
        session.add(Transaction(
            user_id=uid, amount=-amount, type=TransactionType.WITHDRAWAL,
            description=f"Withdrawal: {method}", balance_after=user.balance,
        ))
    await session.commit()
    await message.answer(t(user.language, "wd_submitted", amount=amount), parse_mode=ParseMode.HTML)
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid,
                f"💸 <b>Withdrawal</b>\n\n👤 <code>{uid}</code> ({esc(user.first_name)})\n💰 <code>{amount:,.0f}</code>\n💳 {esc(method)}\n📝 <code>{esc(details[:200])}</code>",
                parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ==============================================================================
# TOP-UP
# ==============================================================================

@router.callback_query(F.data == "cab:replenish")
async def cab_top(query: CallbackQuery, session, state: FSMContext):
    user = await get_user(session, query.from_user.id)
    lang = user.language if user else "en"
    await query.message.answer(
        f"⭐ <b>Top-up with Stars</b>\n\n1 Star = <code>{BusinessRules.STAR_TO_COIN_RATE:,.0f}</code> GRAM\n\nSend stars amount:",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(TopUpState.stars)
    await query.answer()


@router.message(TopUpState.stars)
async def topup(message: Message, state: FSMContext):
    raw = safe_text(message)
    if not raw:
        await message.answer("❌ Send number.")
        return
    try:
        n = int(raw)
        if n <= 0 or n > 10000: raise ValueError()
    except Exception:
        await message.answer("❌ 1-10000.")
        return
    await state.clear()
    await message.bot.send_invoice(
        chat_id=message.chat.id, title="GRAM Top-Up",
        description=f"{n * int(BusinessRules.STAR_TO_COIN_RATE):,} GRAM",
        payload=f"topup_{n}", currency="XTR",
        prices=[LabeledPrice(label=f"{n * int(BusinessRules.STAR_TO_COIN_RATE):,} GRAM", amount=n)],
    )


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def paid(message: Message, session):
    p = message.successful_payment
    try:
        stars = int(p.invoice_payload.split("_")[1])
    except Exception:
        return
    coins = Decimal(stars) * BusinessRules.STAR_TO_COIN_RATE
    uid = message.from_user.id
    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        u = r.scalar_one_or_none()
        if u:
            u.balance += coins
            session.add(Transaction(
                user_id=uid, amount=coins, type=TransactionType.STAR_TOPUP,
                description=f"{stars} Stars", balance_after=u.balance,
            ))
    await session.commit()
    await message.answer(f"✅ +<code>{coins:,.0f}</code> GRAM", parse_mode=ParseMode.HTML)


# ==============================================================================
# STATIC MENUS
# ==============================================================================

@router.message(F.text.in_([L10N.T["en"]["menu_stats"], L10N.T["bn"]["menu_stats"]]))
async def stats(message: Message, session):
    r = await session.execute(select(func.count(User.id)))
    users = r.scalar() or 0
    r = await session.execute(select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.ACTIVE))
    active = r.scalar() or 0
    r = await session.execute(select(func.count(TaskCompletion.id)))
    done = r.scalar() or 0
    r = await session.execute(select(func.sum(Transaction.amount)).where(Transaction.type == TransactionType.TASK_REWARD))
    paid = r.scalar() or Decimal("0")
    await message.answer(t("en", "stats", users=users, active=active, completed=done, paid=paid), parse_mode=ParseMode.HTML)


@router.message(F.text.in_([L10N.T["en"]["menu_links"], L10N.T["bn"]["menu_links"]]))
async def links(message: Message):
    await message.answer(t("en", "links", bot=BOT_USERNAME), parse_mode=ParseMode.HTML)


@router.message(F.text.in_([L10N.T["en"]["menu_instruction"], L10N.T["bn"]["menu_instruction"]]))
async def instr(message: Message):
    await message.answer(t("en", "instruction"), parse_mode=ParseMode.HTML)


@router.message(F.text.in_([L10N.T["en"]["menu_sub_check"], L10N.T["bn"]["menu_sub_check"]]))
async def subc(message: Message):
    await message.answer(t("en", "sub_check", bot=esc(BOT_USERNAME)), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "noop")
async def noop(query: CallbackQuery):
    await query.answer()


# ==============================================================================
# ADMIN
# ==============================================================================

@router.message(Command("admin"))
async def admin_cmd(message: Message, session):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Not authorized.")
        return
    r = await session.execute(select(func.count(FraudReport.id)).where(FraudReport.status == "pending"))
    reports = r.scalar() or 0
    r = await session.execute(select(func.count(WithdrawalRequest.id)).where(WithdrawalRequest.status == "pending"))
    wds = r.scalar() or 0
    await message.answer(
        f"👑 <b>Admin Panel</b>\n\n🚨 Reports: {reports}\n💸 Withdrawals: {wds}",
        reply_markup=admin_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "adm:stats")
async def adm_stats(query: CallbackQuery, session):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    r = await session.execute(select(func.count(User.id)))
    u = r.scalar() or 0
    r = await session.execute(select(func.sum(User.balance)))
    b = r.scalar() or Decimal("0")
    r = await session.execute(select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.ACTIVE))
    a = r.scalar() or 0
    await query.message.edit_text(
        f"📊 <b>Stats</b>\n\n👥 {u:,}\n💰 {b:,.0f}\n📢 {a:,}",
        reply_markup=admin_kb(), parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "adm:reports")
async def adm_rep(query: CallbackQuery, session):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    r = await session.execute(select(FraudReport).where(FraudReport.status == "pending").limit(10))
    reps = r.scalars().all()
    if not reps:
        await query.message.edit_text("✅ No reports.", reply_markup=admin_kb())
    else:
        lines = ["🚨 <b>Reports</b>\n"]
        for rp in reps:
            lines.append(f"• <code>{rp.user_id}</code> — {rp.report_type}")
        await query.message.edit_text("\n".join(lines), reply_markup=admin_kb(), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "adm:withdrawals")
async def adm_wds(query: CallbackQuery, session):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    r = await session.execute(select(WithdrawalRequest).where(WithdrawalRequest.status == "pending").limit(10))
    wds = r.scalars().all()
    if not wds:
        await query.message.edit_text("✅ No pending.", reply_markup=admin_kb())
    else:
        lines = ["💸 <b>Pending</b>\n"]
        for w in wds:
            lines.append(f"• <code>{w.user_id}</code> — {w.amount:,.0f}")
        await query.message.edit_text("\n".join(lines), reply_markup=admin_kb(), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "adm:coins")
async def adm_coins(query: CallbackQuery, state: FSMContext):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    await query.message.edit_text("Send user ID:")
    await state.set_state(AdminState.coins_uid)
    await query.answer()


@router.message(AdminState.coins_uid)
async def adm_uid(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        uid = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Invalid.")
        return
    await state.update_data(uid=uid)
    await message.answer("Send amount (e.g. 5000 or -1000):")
    await state.set_state(AdminState.coins_amount)


@router.message(AdminState.coins_amount)
async def adm_amt(message: Message, session, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        amt = Decimal((message.text or "").strip())
    except Exception:
        await message.answer("❌ Invalid.")
        return
    data = await state.get_data()
    await state.clear()
    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == data["uid"]).with_for_update())
        u = r.scalar_one_or_none()
        if not u:
            await message.answer("❌ Not found.")
            return
        u.balance += amt
        session.add(Transaction(
            user_id=u.id, amount=amt, type=TransactionType.ADMIN_ADJUSTMENT,
            description="Admin", balance_after=u.balance,
        ))
    await session.commit()
    await message.answer(f"✅ {amt:+,.0f} GRAM → {data['uid']}", parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "adm:ban")
async def adm_ban(query: CallbackQuery, state: FSMContext):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    await query.message.edit_text("Send user ID:")
    await state.set_state(AdminState.ban_uid)
    await query.answer()


@router.message(AdminState.ban_uid)
async def adm_ban_uid(message: Message, session, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        uid = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Invalid.")
        return
    await state.clear()
    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        u = r.scalar_one_or_none()
        if not u:
            await message.answer("❌ Not found.")
            return
        u.is_blocked = not u.is_blocked
        act = "banned" if u.is_blocked else "unbanned"
    await session.commit()
    await message.answer(f"✅ {uid} {act}.")


# ==============================================================================
# BACKGROUND WORKERS
# ==============================================================================

async def retention_worker(bot: Bot):
    while True:
        try:
            await asyncio.sleep(6 * 3600)
            async with AsyncSessionLocal() as session:
                now = datetime.now(timezone.utc)
                r = await session.execute(select(TaskCompletion).where(
                    TaskCompletion.retention_status == RetentionStatus.PENDING,
                    TaskCompletion.retention_deadline <= now,
                    TaskCompletion.penalty_applied == False,
                ).limit(50))
                rows = r.scalars().all()
                for tc in rows:
                    cr = await session.execute(select(Campaign).where(Campaign.id == tc.campaign_id))
                    c = cr.scalar_one_or_none()
                    ur = await session.execute(select(User).where(User.id == tc.user_id))
                    u = ur.scalar_one_or_none()
                    if not c or not u:
                        tc.retention_status = RetentionStatus.PENALIZED
                        continue
                    target = c.target_chat_id or (f"@{c.target_username}" if c.target_username else None)
                    if not target:
                        tc.retention_status = RetentionStatus.VERIFIED
                        continue
                    try:
                        m = await bot.get_chat_member(target, u.id)
                        ok = m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                                          ChatMemberStatus.CREATOR, ChatMemberStatus.RESTRICTED)
                    except Exception:
                        ok = False
                    if ok:
                        tc.retention_status = RetentionStatus.VERIFIED
                        tc.retention_checked_at = now
                    else:
                        penalty = tc.reward
                        u.balance -= penalty
                        u.total_earned -= penalty
                        u.completed_tasks_count = max(0, u.completed_tasks_count - 1)
                        u.risk_score += 10
                        c.spent_budget -= penalty
                        c.completed_count = max(0, c.completed_count - 1)
                        c.refunded_budget += penalty
                        if c.status == CampaignStatus.COMPLETED and not c.is_full:
                            c.status = CampaignStatus.ACTIVE
                        tc.retention_status = RetentionStatus.PENALIZED
                        tc.penalty_applied = True
                        session.add(Transaction(
                            user_id=u.id, amount=-penalty,
                            type=TransactionType.PENALTY_REVOKE,
                            description=f"Retention fail: {c.title}",
                            reference_id=c.id, reference_type="campaign",
                            balance_after=u.balance,
                        ))
                        session.add(FraudReport(
                            user_id=u.id, campaign_id=c.id, completion_id=tc.id,
                            report_type="retention_fail",
                            description=f"Left {target} early",
                        ))
                        try:
                            await bot.send_message(u.id,
                                t(u.language, "retention_penalty",
                                  target=c.target_title or target, reward=penalty),
                                parse_mode=ParseMode.HTML)
                        except Exception:
                            pass
                        await alert_admins(bot,
                            f"🚨 <b>Retention Penalty</b>\n\n"
                            f"👤 <code>{u.id}</code> ({esc(u.first_name)})\n"
                            f"📢 {esc(c.title)}\n💰 -{penalty:,.0f}")
                await session.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Retention worker: {e}")
            await asyncio.sleep(300)


async def check_pw_worker():
    """Wait for check passwords stored in redis."""
    while True:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            break


# ==============================================================================
# FASTAPI
# ==============================================================================

fastapi_app = FastAPI(title="BoostGram")


@fastapi_app.get("/", response_class=HTMLResponse)
async def index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return """<!DOCTYPE html><html><body style="background:#0f172a;color:#fff;text-align:center;padding:100px;font-family:Arial"><h1>Boost Gram 🚀</h1><p>Bot is running</p></body></html>"""


@fastapi_app.get("/sitemap.xml", response_class=HTMLResponse)
async def sitemap():
    try:
        with open("sitemap.xml", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'


@fastapi_app.get("/google8c808883d07580e1.html", response_class=HTMLResponse)
async def gverify():
    try:
        with open("google8c808883d07580e1.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "google-site-verification: google8c808883d07580e1.html"


@fastapi_app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


# ==============================================================================
# BOOT
# ==============================================================================

async def main():
    storage = RedisStorage(redis=redis_client)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=storage)

    class DBSessionMiddleware(BaseMiddleware):
        async def __call__(self, handler, event, data):
            async with AsyncSessionLocal() as session:
                data["session"] = session
                return await handler(event, data)

    dp.message.outer_middleware(DBSessionMiddleware())
    dp.callback_query.outer_middleware(DBSessionMiddleware())
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="🚀 Start"),
        BotCommand(command="admin", description="👑 Admin"),
    ])

    @dp.error()
    async def on_error(event: ErrorEvent):
        logger.exception(f"Error: {event.exception}")
        tb = "".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__))[-3000:]
        await alert_admins(bot, f"🚨 <b>Error</b>\n<pre>{esc(tb)}</pre>")
        return True

    asyncio.create_task(retention_worker(bot))

    uv_config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="warning")
    server = uvicorn.Server(uv_config)

    logger.info("✅ Boost Gram started (Alembic-based)")

    await asyncio.gather(dp.start_polling(bot), server.serve())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")

import os
import sys
import logging
from decimal import Decimal
from dotenv import load_dotenv

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
# Optional banner (GIF/MP4/PNG URL or Telegram file_id) shown with the welcome message
WELCOME_MEDIA_URL = os.getenv("WELCOME_MEDIA_URL", "")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
if not ENCRYPTION_KEY:
    # Do not crash a running deployment, but make the weakness very visible.
    logger.critical("ENCRYPTION_KEY is not set! Using an insecure fallback. Set a long random value in your environment.")
    ENCRYPTION_KEY = "insecure-fallback-change-me"
PORT = int(os.getenv("PORT", "8000"))

if not BOT_TOKEN or not DATABASE_URL or not REDIS_URL:
    logger.critical("Missing required env: BOT_TOKEN, DATABASE_URL, REDIS_URL")
    sys.exit(1)

try:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()]
except ValueError:
    ADMIN_IDS = []


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

    # --- Anti-fraud limits ---
    MAX_TASKS_PER_DAY = 100            # per user, rolling 24h
    RISK_BLOCK_SCORE = 50              # risk_score >= this => cannot do tasks / withdraw
    MAX_PENDING_WITHDRAWALS = 1
    CAMPAIGN_MIN_SLOTS = 1
    CAMPAIGN_MAX_SLOTS = 10000
    CHECK_MAX_AMOUNT = Decimal("1000000")
    CHECK_MAX_PASSWORD_ATTEMPTS = 5
    RATE_LIMIT_PER_SECOND = 5          # global per-user update throttle

    # Only task types that the bot can really verify through the Telegram API.
    # Everything else would pay out without any proof, so it is disabled.
    EARNABLE_TASK_TYPES = ("channel_sub", "group_join")


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

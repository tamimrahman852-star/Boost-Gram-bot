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
# ---- Manual deposit (bKash / Nagad / Rocket). A method is offered only if its number is set. ----
PAY_NUMBERS = {
    "bkash": os.getenv("PAY_BKASH", "").strip(),
    "nagad": os.getenv("PAY_NAGAD", "").strip(),
    "rocket": os.getenv("PAY_ROCKET", "").strip(),
}
DEPOSIT_RATE_PER_BDT = Decimal(os.getenv("DEPOSIT_RATE_PER_BDT", "3000"))   # GRAM credited per 1 BDT
DEPOSIT_MIN_BDT = Decimal(os.getenv("DEPOSIT_MIN_BDT", "50"))
DEPOSIT_MAX_BDT = Decimal(os.getenv("DEPOSIT_MAX_BDT", "20000"))

# Optional banner (GIF/MP4/PNG URL or Telegram file_id) shown with the welcome message
WELCOME_MEDIA_URL = os.getenv("WELCOME_MEDIA_URL", "")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
if not ENCRYPTION_KEY:
    # Do not crash a running deployment, but make the weakness very visible.
    logger.critical("ENCRYPTION_KEY is not set! Using an insecure fallback. Set a long random value in your environment.")
    ENCRYPTION_KEY = "insecure-fallback-change-me"
PORT = int(os.getenv("PORT", "8000"))
# ---- Telegram Mini App ----
# Public URL of the deployed webapp (Vercel), e.g. https://boost-gram-bot-webapp-113m.vercel.app/
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
# Origins allowed to call /api directly (only needed if the webapp calls Render without the Vercel rewrite).
# Comma separated. WEBAPP_URL's origin is added automatically.
_origins = [o.strip().rstrip("/") for o in os.getenv("WEBAPP_ORIGINS", "").split(",") if o.strip()]
if WEBAPP_URL:
    from urllib.parse import urlparse as _up
    _u = _up(WEBAPP_URL)
    if _u.scheme and _u.netloc and f"{_u.scheme}://{_u.netloc}" not in _origins:
        _origins.append(f"{_u.scheme}://{_u.netloc}")
WEBAPP_ORIGINS = _origins

# ---- Channel monetization / ad marketplace ----
PUBLIC_URL = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "https://boost-gram-bot.onrender.com").rstrip("/")


class AdRules:
    """Performance-based ad marketplace (CPM / CPC, like Google AdSense or Meta Audience Network).

    The advertiser funds a campaign and bids either per 1,000 views (CPM) or per click (CPC). Every GRAM actually spent is split:
    COMMISSION (20%) stays with the platform, the rest (80%) belongs to the channel owner who showed the ad.
    """
    COMMISSION = Decimal(os.getenv("AD_COMMISSION", "0.20"))        # platform share of every GRAM spent
    MIN_MEMBERS = int(os.getenv("AD_MIN_MEMBERS", "100"))            # smallest channel that can monetize
    # bids, in GRAM: CPM = per 1,000 views, CPC = per click
    CPM_MIN = Decimal(os.getenv("AD_CPM_MIN", "100000"))
    CPM_MAX = Decimal(os.getenv("AD_CPM_MAX", "5000000"))
    CPM_DEFAULT = Decimal(os.getenv("AD_CPM_DEFAULT", "300000"))
    CPC_MIN = Decimal(os.getenv("AD_CPC_MIN", "10000"))
    CPC_MAX = Decimal(os.getenv("AD_CPC_MAX", "1000000"))
    CPC_DEFAULT = Decimal(os.getenv("AD_CPC_DEFAULT", "40000"))
    DEFAULT_CTR = Decimal("0.01")                                    # used to rank CPC bids against CPM bids until a channel has data
    MIN_BUDGET = Decimal(os.getenv("AD_MIN_BUDGET", "250000"))
    MAX_BUDGET = Decimal("100000000")
    MAX_ACTIVE_CAMPAIGNS = 10
    MAX_CHANNELS_PER_USER = 10
    MAX_STRIKES = 3                                                  # early deletions before a channel is paused
    MAX_SLOTS = 12                                                   # posting times per channel per day
    MIN_SLOT_GAP_MIN = 30                                            # minutes between two posting times
    SLOT_GRACE_MIN = 15                                              # a posting time missed by more than this is skipped
    # Only t.me links by default (keeps scams/phishing out of publishers' channels)
    ALLOW_EXTERNAL_LINKS = os.getenv("AD_ALLOW_EXTERNAL_LINKS", "0") == "1"
    # Read the public view counter of OUR OWN ad posts through Telegram's official post-widget (t.me/<chan>/<id>?embed=1).
    # CPM needs views, so CPM campaigns are only offered while this is on.
    VIEW_SYNC = os.getenv("AD_VIEW_SYNC", "1") == "1"
    LIFETIMES = (1, 3, 6, 12, 24, 48, 72)                            # hours an ad stays in the channel
    DEFAULT_LIFETIME = 6
    IMAGE_MAX_BYTES = 8 * 1024 * 1024
    VIDEO_MAX_BYTES = 10 * 1024 * 1024                               # ad videos: mp4 / mov, up to 10 MB
    TICK = 30                                                        # scheduler interval, seconds
    SYNC_MINUTES = 10                                                # how often live ads are re-measured and billed


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
    EARNABLE_TASK_TYPES = ("channel_sub", "group_join", "boost_7day", "post_view", "bot_start", "reaction")
    # Telegram can verify these itself (membership / boosts):
    API_VERIFIED_TASK_TYPES = ("channel_sub", "group_join", "boost_7day")
    # Telegram cannot see these, so they use screenshot proof + advertiser review:
    PROOF_TASK_TYPES = ("bot_start", "reaction")
    # Post views happen INSIDE the bot: the post is forwarded to the worker (which also counts as a real
    # view on the original post) and the reward is paid after a short dwell time.
    VIEW_TASK_TYPES = ("post_view",)
    VIEW_DWELL_SECONDS = 6             # minimum time between forwarding the post and paying
    VIEW_COOLDOWN_SECONDS = 2          # minimum gap between two "view" taps of one user
    PROOF_REVIEW_HOURS = 48            # advertiser has this long to approve/reject, then auto-approve
    PROOF_DISPUTE_HOURS = 24           # after a rejection the worker may dispute once within this time
    PROOF_MAX_PENDING_PER_USER = 10
    EARLY_LEAVE_GRACE_HOURS = 24        # time to re-subscribe after leaving early, before the penalty becomes final
    EARLY_LEAVE_VOLUNTARY_RISK = 5      # risk score added when the user pays the penalty ("Return coins")
    EARLY_LEAVE_EXPIRED_RISK = 10       # ...or when the 24h grace period ran out
    AUTO_RENEW_DEFAULT = 3             # how many times a campaign may renew itself


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
        if premium and task_type != "boost_7day":   # boosting already requires Telegram Premium
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

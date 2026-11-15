import html
import hashlib
import hmac
from typing import Optional
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_IDS, ENCRYPTION_KEY


def esc(v) -> str:
    return html.escape(str(v), quote=False)


def hash_sensitive(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def hash_password(pw: str, salt: str) -> str:
    """Salted, keyed hash for check passwords (replaces bare SHA-256)."""
    digest = hmac.new(
        ENCRYPTION_KEY.encode(), f"{salt}:{pw}".encode(), hashlib.sha256
    ).hexdigest()
    return f"h2${digest}"


def is_expired(dt) -> bool:
    """True if `dt` is in the past. Tolerates naive datetimes (treated as UTC)."""
    from datetime import datetime, timezone
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < datetime.now(timezone.utc)


def L(lang: str, bn: str, en: str) -> str:
    """Tiny helper for strings that exist only in Bangla and English."""
    return bn if lang == "bn" else en


def fmt(n) -> str:
    return f"{n:,.0f}"


def safe_text(message: Message) -> Optional[str]:
    return message.text.strip() if message.text else None


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


# FSM State Groups
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


class CampaignCreateState(StatesGroup):
    target = State()
    link = State()
    reward = State()
    slots = State()

class WithdrawState(StatesGroup):
    amount = State()
    method = State()
    details = State()

class AdminState(StatesGroup):
    broadcast_content = State()
    coins_uid = State()
    coins_amount = State()
    ban_uid = State()
    broadcast = State()


class DepositState(StatesGroup):
    amount = State()
    trx = State()
    proof = State()


class VoucherState(StatesGroup):
    code = State()


class ProofState(StatesGroup):
    photo = State()

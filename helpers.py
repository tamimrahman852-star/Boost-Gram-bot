import html
import hashlib
from typing import Optional
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_IDS


def esc(v) -> str:
    return html.escape(str(v), quote=False)


def hash_sensitive(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


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

class WithdrawState(StatesGroup):
    amount = State()
    method = State()
    details = State()

class AdminState(StatesGroup):
    coins_uid = State()
    coins_amount = State()
    ban_uid = State()
    broadcast = State()

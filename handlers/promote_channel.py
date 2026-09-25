from decimal import Decimal
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from sqlalchemy import select
from database import User, Campaign, TaskType, CampaignStatus

router = Router()

class PromoteChannelStates(StatesGroup):
    waiting_for_reward_price = State()
    waiting_for_quantity = State()

# 1. "📢 Prochar korun" menu theke Channel select korar por Inline Keyboard asbe
@router.callback_query(F.data == "promote:cat:channel")
async def promote_channel_start(query: CallbackQuery):
    text = (
        "📢 <b>Promotion-er jonno chat ba channel beche nin</b>\n"
        "(Bot-ke admin hote hobe)"
    )
    # Reply keyboard er bodole inline keyboard use kora holo jate direct popup open hoy
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Ami admin", switch_inline_query_current_chat="")],
        [InlineKeyboardButton(text="👁️ Ami admin noi", callback_data="promote:ch:admin_no")],
        [InlineKeyboardButton(text="◀️ Fire jan", callback_data="menu:promote")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# 2. "👁️ Ami admin noi" click korle
@router.callback_query(F.data == "promote:ch:admin_no")
async def promote_ch_admin_no(query: CallbackQuery):
    text = "ℹ️ Apnar channel ba group-e bot-ke admin baniye tarpor abar chesta korun."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Fire jan", callback_data="promote:cat:channel")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

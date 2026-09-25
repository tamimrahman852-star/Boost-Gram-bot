from decimal import Decimal
from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery, Message, InlineKeyboardMarkup, 
    InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton,
    KeyboardButtonRequestChat
)
# ChatAdministratorRights import path fix kora holo
from aiogram.types.chat_administrator_rights import ChatAdministratorRights
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from sqlalchemy import select
from database import User, Campaign, TaskType, CampaignStatus

router = Router()

class PromoteChannelStates(StatesGroup):
    waiting_for_reward_price = State()
    waiting_for_quantity = State()

# ১. "📢 Prochar korun" menu theke channel select korar option
@router.callback_query(F.data == "promote:cat:channel")
async def promote_channel_start(query: CallbackQuery):
    text = (
        "📢 <b>Promotion-er jonno chat ba channel beche nin</b>\n"
        "(Nicer button theke apnar channel ba group select korun)"
    )
    
    # Sarafuri popup open korar jonno request_chat use kora holo
    reply_kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🏠 Ami admin",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=1,
                        chat_is_channel=True,
                        user_administrator_rights=ChatAdministratorRights(
                            can_manage_chat=True,
                            can_invite_users=True
                        )
                    )
                )
            ],
            [
                KeyboardButton(
                    text="👁️ Ami admin noi",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=2,
                        chat_is_channel=True
                    )
                )
            ],
            [KeyboardButton(text="◀️ Fire jan")]
        ],
        resize_keyboard=True
    )
    
    await query.message.answer(text, reply_markup=reply_kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ২. Popup theke channel select korar por receive hobe
@router.message(F.chat_shared)
async def handle_shared_chat(message: Message, state: FSMContext):
    chat_id = message.chat_shared.chat_id
    
    await state.update_data(channel_link=str(chat_id), target_chat=chat_id)
    
    text = (
        "🎯 <b>Task-er audience</b>\n"
        "Bortoman: Simaboddhota nei\n\n"
        "Task-ti kara dekhte pabe ta beche nin:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 Sabke onumoti din", callback_data="promote:ch:aud:all")],
        [InlineKeyboardButton(text="🎯 Dorshok nirbachon korun", callback_data="promote:ch:aud:select")],
        [InlineKeyboardButton(text="◀️ Fire jan", callback_data="promote:cat:channel")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# ৩. "◀️ Fire jan" reply button handle korar jonno
@router.message(F.text == "◀️ Fire jan")
async def back_to_main_menu(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Ai"), KeyboardButton(text="📢 Prochar korun")],
            [KeyboardButton(text="🎫 Check"), KeyboardButton(text="👤 Amar cabinet")],
            [KeyboardButton(text="🛡️ Subscription check"), KeyboardButton(text="📊 Amader bot o porisongkhan")],
            [KeyboardButton(text="🔗 Dorkari link"), KeyboardButton(text="ℹ️ Nirdeshika")]
        ],
        resize_keyboard=True
    )
    await message.answer("🏠 Prodan menu:", reply_markup=kb)

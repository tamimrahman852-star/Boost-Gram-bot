from decimal import Decimal
from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery, Message, InlineKeyboardMarkup, 
    InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton,
    KeyboardButtonRequestChat, ChatAdminRights
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from sqlalchemy import select
from database import User, Campaign, TaskType, CampaignStatus

router = Router()

class PromoteChannelStates(StatesGroup):
    waiting_for_reward_price = State()
    waiting_for_quantity = State()

# ১. প্রমোশন মেনু থেকে চ্যানেল সিলেক্ট অপশনে আসলে Reply Keyboard দেখানো যেখানে সরাসরি পপআপ ট্রিগার থাকবে
@router.callback_query(F.data == "promote:cat:channel")
async def promote_channel_start(query: CallbackQuery):
    text = (
        "📢 <b>প্রমোশনের জন্য চ্যাট বা চ্যানেল বেছে নিন</b>\n"
        "(নিচের বাটন থেকে আপনার চ্যানেল বা গ্রুপ সিলেক্ট করুন)"
    )
    
    # KeyboardButtonRequestChat ব্যবহার করে সরাসরি চ্যানেল/গ্রুপ সিলেক্ট করার পপআপ ওপেন করা হচ্ছে
    reply_kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🏠 আমি অ্যাডমিন",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=1,
                        chat_is_channel=True,
                        user_administrator_rights=ChatAdminRights(
                            can_manage_chat=True,
                            can_invite_users=True
                        )
                    )
                )
            ],
            [
                KeyboardButton(
                    text="👁️ আমি অ্যাডমিন নই",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=2,
                        chat_is_channel=True
                    )
                )
            ],
            [KeyboardButton(text="◀️ ফিরে যান")]
        ],
        resize_keyboard=True
    )
    
    await query.message.answer(text, reply_markup=reply_kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ২. ব্যবহারকারী যখন পপআপ থেকে কোনো চ্যানেল বা গ্রুপ সিলেক্ট করবে তখন সেটি এখানে রিসিভ হবে
@router.message(F.chat_shared)
async def handle_shared_chat(message: Message, state: FSMContext):
    chat_id = message.chat_shared.chat_id
    
    # স্টেট বা পরবর্তী ধাপে যাওয়ার জন্য চ্যাট আইডি সেভ করে রাখা
    await state.update_data(channel_link=str(chat_id), target_chat=chat_id)
    
    text = (
        "🎯 <b>টাস্কের অডিয়েন্স</b>\n"
        "বর্তমান: সীমাবদ্ধতা নেই\n\n"
        "টাস্কটি কারা দেখতে পাবে তা বেছে নিন:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 সবাইকে অনুমতি দিন", callback_data="promote:ch:aud:all")],
        [InlineKeyboardButton(text="🎯 দর্শক নির্বাচন করুন", callback_data="promote:ch:aud:select")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# ৩. "◀️ ফিরে যান" রিপ্লাই বাটন হ্যান্ডেল করার জন্য
@router.message(F.text == "◀️ ফিরে যান")
async def back_to_main_menu(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 আয়"), KeyboardButton(text="📢 প্রচার করুন")],
            [KeyboardButton(text="🎫 চেক"), KeyboardButton(text="👤 আমার কেবিনেট")],
            [KeyboardButton(text="🛡️ সাবস্ক্রিপশন চেক"), KeyboardButton(text="📊 আমাদের বট ও পরিসংখ্যান")],
            [KeyboardButton(text="🔗 দরকারি লিংক"), KeyboardButton(text="ℹ️ নির্দেশিকা")]
        ],
        resize_keyboard=True
    )
    await message.answer("🏠 প্রধান মেনু:", reply_markup=kb)

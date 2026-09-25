from decimal import Decimal
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from sqlalchemy import select
from database import User, Campaign, TaskType, CampaignStatus

router = Router()

class PromoteChannelStates(StatesGroup):
    waiting_for_reward_price = State()
    waiting_for_quantity = State()

# ১. "📢 প্রচার করুন" menu theke Channel select korar por Reply Keyboard ashbe
@router.callback_query(F.data == "promote:cat:channel")
async def promote_channel_start(query: CallbackQuery):
    text = (
        "📢 <b>প্রমোশনের জন্য চ্যাট বা চ্যানেল বেছে নিন</b>\n"
        "(বটকে অ্যাডমিন হতে হবে)"
    )
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 আমি অ্যাডমিন"), KeyboardButton(text="👁️ আমি অ্যাডমিন নই")],
            [KeyboardButton(text="◀️ ফিরে যান")]
        ],
        resize_keyboard=True
    )
    await query.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ২. "👁️ আমি অ্যাডমিন নই" Reply button click korle
@router.message(F.text == "👁️ আমি অ্যাডমিন নই")
async def promote_ch_admin_no_msg(message: Message):
    text = "ℹ️ আপনার চ্যানেল বা গ্রুপে বটকে অ্যাডমিন বানিয়ে তারপর আবার চেষ্টা করুন।"
    await message.answer(text, parse_mode=ParseMode.HTML)

# ৩. "🏠 আমি অ্যাডমিন" Reply button click korle Inline Button সহ Popup trigger dewa
@router.message(F.text == "🏠 আমি অ্যাডমিন")
async def promote_ch_admin_yes_msg(message: Message):
    text = (
        "🔍 <b>চ্যানেল বা গ্রুপ সিলেক্ট করুন:</b>\n"
        "নিচের বাটনে ক্লিক করলেই আপনার চ্যানেলগুলোর তালিকা (Popup) চলে আসবে।"
    )
    
    # switch_inline_query_current_chat use kore native "Choose a Channel" popup open kora hocche
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 চ্যানেল বা গ্রুপ বেছে নিন", switch_inline_query_current_chat="")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# ৪. "◀️ ফিরে যান" reply button handle korar jonno
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

from decimal import Decimal
from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery, Message, InlineKeyboardMarkup, 
    InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove
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

# ১. "📢 Prochar korun" মেনু থেকে চ্যানেল সিলেক্ট করার পর রিপ্লাই কী-বোর্ড আসবে
@router.callback_query(F.data == "promote:cat:channel")
async def promote_channel_start(query: CallbackQuery):
    text = (
        "📢 <b>Promotion-er jonno chat ba channel beche nin</b>\n"
        "(Bot-ke admin hote hobe)"
    )
    
    # রিপ্লাই কী-বোর্ড তৈরি (বাটনগুলো নিচে ফিক্সড থাকবে)
    reply_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 আমি অ্যাডমিন"), KeyboardButton(text="👁️ আমি অ্যাডমিন নই")],
            [KeyboardButton(text="◀️ ফিরে যান")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True # একবার ব্যবহারের পর হাইড হয়ে যাবে
    )
    
    await query.message.answer(text, reply_markup=reply_kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ২. "আমি অ্যাডমিন" বাটনে ক্লিক করলে চ্যানেল লিস্ট (Inline Query Popup) আসবে
@router.message(F.text == "🏠 আমি অ্যাডমিন")
async def promote_ch_admin_yes(message: Message):
    # Switch Inline Query ব্যবহার করে চ্যানেল লিস্ট পপআপ দেখানো
    # এখানে '@your_bot_username' এর জায়গায় আপনার বটের ইউজারনেম বসাতে হবে
    # অথবা switch_inline_query_current_chat="" দিলে বর্তমান চ্যাটেই লিস্ট আসবে
    
    # নোট: ইনলাইন বাটন দিয়ে পপআপ ওপেন করতে হলে বটের ইউজারনেম লাগবে।
    # তবে সরাসরি লিস্ট দেখানোর জন্য আমরা একটি ইনলাইন বাটন দিতে পারি যা ক্লিক করলে লিস্ট আসবে।
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📋 চ্যানেল লিস্ট দেখুন", 
            switch_inline_query_current_chat="" # এটি ক্লিক করলে বটের ইনলাইন মোড ওপেন হবে
        )]
    ])
    
    await message.answer(
        "নিচের বাটনে ক্লিক করে আপনার চ্যানেল সিলেক্ট করুন:", 
        reply_markup=inline_kb
    )
    # রিপ্লাই কী-বোর্ড রিমুভ করা (ঐচ্ছিক)
    # await message.answer("...", reply_markup=ReplyKeyboardRemove())

# ৩. "আমি অ্যাডমিন নই" বাটনে ক্লিক করলে
@router.message(F.text == "👁️ আমি অ্যাডমিন নই")
async def promote_ch_admin_no(message: Message):
    text = "ℹ️ আপনার চ্যানেল বা গ্রুপে বটকে অ্যাডমিন বানিয়ে তারপর আবার চেষ্টা করুন।"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# ৪. "◀️ ফিরে যান" বাটনে ক্লিক করলে
@router.message(F.text == "◀️ ফিরে যান")
async def promote_back(message: Message):
    # এখানে আপনার পূর্বের মেনুতে ফেরত পাঠানোর লজিক বসবে
    await message.answer("মেইন মেনুতে ফিরে যাচ্ছি...", reply_markup=ReplyKeyboardRemove())
    # await message.answer("...", reply_markup=main_menu_kb) # আপনার মেইন মেনু

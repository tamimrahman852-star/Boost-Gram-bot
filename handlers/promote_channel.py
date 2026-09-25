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

# ১. "📢 প্রচার করুন" মেনু থেকে চ্যানেল সিলেক্ট করার অপشن আসলে ইনলাইন কিবোর্ড দিয়ে সরাসরি পপআপ বা অপশন দেওয়া
@router.callback_query(F.data == "promote:cat:channel")
async def promote_channel_start(query: CallbackQuery):
    text = (
        "📢 <b>প্রমোশনের জন্য চ্যাট বা চ্যানেল বেছে নিন</b>\n"
        "(বটকে আপনার চ্যানেলে অ্যাডমিন হতে হবে)"
    )
    
    # এখানে সরাসরি ইনলাইন বাটন দেওয়া হলো যাতে ক্লিক করলেই সরাসরি চ্যানেল লিস্ট পপআপ ওপেন হয়
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🏠 আমি অ্যাডমিন", 
                switch_inline_query_current_chat=""
            )
        ],
        [
            InlineKeyboardButton(
                text="👁️ আমি অ্যাডমিন নই", 
                callback_data="promote:ch:admin_no"
            )
        ],
        [
            InlineKeyboardButton(
                text="◀️ ফিরে যান", 
                callback_data="menu:promote"
            )
        ]
    ])
    
    # পুরানো মেসেজ এডিট করে ইনলাইন বাটনগুলো দেখিয়ে দেওয়া হলো, এতে চ্যাট পরিষ্কার থাকবে
    try:
        await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        await query.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    
    await query.answer()

# ২. "👁️ আমি অ্যাডমিন নই" বাটনে ক্লিক করলে সরাসরি গাইডলাইন বা নোটিফিকেশন দেখানো (চ্যাটে মেসেজ স্প্যাম হবে না)
@router.callback_query(F.data == "promote:ch:admin_no")
async def promote_ch_admin_no(query: CallbackQuery):
    text = (
        "ℹ️ <b>আপনার চ্যানেল বা গ্রুপে বটকে অ্যাডমিন বানান:</b>\n\n"
        "১. আপনার চ্যানেলের Settings > Administrators-এ যান।\n"
        "২. বটকে অ্যাড হিসেবে যোগ করুন এবং প্রয়োজনীয় পারমিশন দিন।\n"
        "৩. এরপর আবার চেষ্টা করুন।"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৩. বটের মাধ্যমে চ্যানেল সিলেক্ট করার পর পরবর্তী স্টেপ (অডিয়েন্স ও প্রাইস সিলেকشن)
@router.callback_query(F.data.startswith("promote:ch:selected:"))
async def select_user_channel(query: CallbackQuery, state: FSMContext):
    ch_link = query.data.split(":")[3]
    await state.update_data(channel_link=ch_link, target_chat=ch_link)
    
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
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

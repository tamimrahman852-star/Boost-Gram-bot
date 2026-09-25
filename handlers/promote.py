from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from sqlalchemy import select
from database import User

router = Router()

# ১. Reply Keyboard থেকে "📢 প্রচার করুন" ক্লিক করলে এই হ্যান্ডলারটি কাজ করবে
@router.message(F.text == "📢 প্রচার করুন")
async def promote_main_menu_message(message: Message, session):
    uid = message.from_user.id
    r = await session.execute(select(User).where(User.id == uid))
    user = r.scalar_one_or_none()
    balance = user.balance if user else 0.0

    text = (
        "📢 <b>আপনি কি প্রচার করতে চান?</b>\n\n"
        f"💲 ব্যালেন্স: <b>{balance:,.0f} GRAM</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📢 চ্যানেল", callback_data="promote:cat:channel"),
            InlineKeyboardButton(text="👥 গ্রুপ", callback_data="promote:cat:group")
        ],
        [
            InlineKeyboardButton(text="👁️ পোস্ট", callback_data="promote:cat:post"),
            InlineKeyboardButton(text="🤖 বট", callback_data="promote:cat:bot")
        ],
        [
            InlineKeyboardButton(text="⚡ প্রিমিয়াম বুস্ট (চ...", callback_data="promote:cat:boost"),
            InlineKeyboardButton(text="❤️ প্রতিক্রিয়া", callback_data="promote:cat:reaction")
        ],
        [
            InlineKeyboardButton(text="⚙️ অটো-টাস্ক সেটিংস", callback_data="promote:cat:autotask")
        ],
        [
            InlineKeyboardButton(text="📋 আমার কাজ", callback_data="promote:my_tasks"),
            InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:start")
        ]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# ২. ইনলাইন বাটন বা অন্য কোনো জায়গা থেকে "menu:promote" বা ব্যাক করলে এই মেনু দেখাবে
@router.callback_query(F.data == "menu:promote")
async def promote_main_menu_callback(query: CallbackQuery, session):
    uid = query.from_user.id
    r = await session.execute(select(User).where(User.id == uid))
    user = r.scalar_one_or_none()
    balance = user.balance if user else 0.0

    text = (
        "📢 <b>আপনি কি প্রচার করতে চান?</b>\n\n"
        f"💲 ব্যালেন্স: <b>{balance:,.0f} GRAM</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📢 চ্যানেল", callback_data="promote:cat:channel"),
            InlineKeyboardButton(text="👥 গ্রুপ", callback_data="promote:cat:group")
        ],
        [
            InlineKeyboardButton(text="👁️ পোস্ট", callback_data="promote:cat:post"),
            InlineKeyboardButton(text="🤖 বট", callback_data="promote:cat:bot")
        ],
        [
            InlineKeyboardButton(text="⚡ প্রিমিয়াম বুস্ট (চ...", callback_data="promote:cat:boost"),
            InlineKeyboardButton(text="❤️ প্রতিক্রিয়া", callback_data="promote:cat:reaction")
        ],
        [
            InlineKeyboardButton(text="⚙️ অটো-টাস্ক সেটিংস", callback_data="promote:cat:autotask")
        ],
        [
            InlineKeyboardButton(text="📋 আমার কাজ", callback_data="promote:my_tasks"),
            InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:start")
        ]
    ])
    
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৩. মূল মেনুতে (Start) ফিরে যাওয়ার হ্যান্ডলার
@router.callback_query(F.data == "menu:start")
async def back_to_start_menu(query: CallbackQuery):
    text = "🏠 প্রধান মেনুতে স্বাগতম। নিচে থেকে আপনার পছন্দসই অপشن বেছে নিন:"
    # যদি আপনার স্টার্ট মেনুর আলাদা কোনো টেক্সট বা কিবোর্ড থাকে এখানে সেট করতে পারেন
    await query.message.edit_text(text, parse_mode=ParseMode.HTML)
    await query.answer()

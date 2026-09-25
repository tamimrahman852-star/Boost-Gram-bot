from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from sqlalchemy import select
from database import User

router = Router()

@router.callback_query(F.data == "menu:promote")
async def promote_main_menu(query: CallbackQuery, session):
    uid = query.from_user.id
    r = await session.execute(select(User).where(User.id == uid))
    user = r.scalar_one_or_none()
    balance = user.balance if user else 0.0

    # Screenshot er moto exact text ebong design
    text = f"ADS <b>আপনি কি প্রচার করতে চান?</b>\n\n💲 ব্যালেন্স: <b>{balance:,.1f} GRAM</b>"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📢 চ্যানেল", callback_data="promote:cat:channel"),
            InlineKeyboardButton(text="👥 গ্রুপ", callback_data="promote:cat:group")
        ],
        [
            InlineKeyboardButton(text="🔘 পোস্ট", callback_data="promote:cat:post"),
            InlineKeyboardButton(text="🤖 বট", callback_data="promote:cat:bot")
        ],
        [
            InlineKeyboardButton(text="⚡ প্রিমিয়াম বুস্ট (চার্জ)", callback_data="promote:cat:boost"),
            InlineKeyboardButton(text="❤️ প্রতিক্রিয়া", callback_data="promote:cat:reaction")
        ],
        [InlineKeyboardButton(text="⚙️ অটো-টাস্ক সেটিংস", callback_data="promote:autotask")],
        [
            InlineKeyboardButton(text="📋 আমার কাজ", callback_data="promote:my_tasks"),
            InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="main_menu")
        ]
    ])

    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

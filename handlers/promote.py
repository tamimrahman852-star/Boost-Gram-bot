from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.enums import ParseMode, ChatMemberStatus
from sqlalchemy import select
from database import User, Campaign, CampaignStatus, TaskType
from config import BOT_USERNAME

router = Router()

# ১. "প্রচার করুন" এ ক্লিক করলে প্রথম মেনু (Screenshot 1 er moto)
@router.callback_query(F.data == "menu:promote")
async def promote_main_menu(query: CallbackQuery, session):
    uid = query.from_user.id
    r = await session.execute(select(User).where(User.id == uid))
    user = r.scalar_one_or_none()
    balance = user.balance if user else 0.0

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


# ২. চ্যানেল বা গ্রুপে ক্লিক করলে "আমি অ্যাডমিন" / "আমি অ্যাডমিন নই" অপশন আসবে (Screenshot 2 er moto)
@router.callback_query(F.data.in_({"promote:cat:channel", "promote:cat:group"}))
async def promote_choose_admin_status(query: CallbackQuery):
    task_type = "চ্যানেল" if "channel" in query.data else "গ্রুপ"
    
    text = (
        f"📢 <b>প্রমোশনের জন্য চ্যাট বা {task_type} বেছে নিন</b>\n"
        f"(বটকে অ্যাডমিন হতে হবে)"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 আমি অ্যাডমিন", callback_data=f"promote:admin_yes:{query.data}")],
        [InlineKeyboardButton(text="👁️ আমি অ্যাডমিন নই", callback_data=f"promote:admin_no:{query.data}")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:promote")]
    ])

    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


# ৩. "আমি অ্যাডমিন" এ ক্লিক করলে ইউজারের যেগুলোতে বট সহ অ্যাডমিন আছে সেগুলোর লিস্ট দেখাবে
@router.callback_query(F.data.startswith("promote:admin_yes:"))
async def show_user_admin_chats(query: CallbackQuery, bot: Bot):
    # ইউজার যে চ্যানেল বা গ্রুপগুলোর অ্যাডমিন, সেগুলোর লিস্ট ফেচ করার লজিক এখানে যুক্ত করতে হবে।
    # টেলিগ্রাম বট সরাসরি ইউজারের সব চ্যানেল লিস্ট নিজে থেকে দেখতে পারে না যদি না ইউজার বটকে ফরোয়ার্ড বা ওয়েব অ্যাপের মাধ্যমে পার্সোনাল চ্যাটে যুক্ত করে। 
    # সাধারণ নিয়মে টেলিগ্রাম বট অ্যাডমিন চেক করার জন্য ইউজারের চ্যাট আইডি বা ইউজারনেপ ইনপুট নেয় অথবা WebApp ব্যবহার করে।
    
    text = "⚠️ <b>অনুগ্রহ করে আপনার চ্যানেল বা গ্রুপের ইউজারনেম দিন (যেখানে বট অ্যাডমিন রয়েছে):</b>\nউদাহরণ: @mychannel"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:promote")]
    ])
    
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


# ৪. "আমি অ্যাডমিন নই" এ ক্লিক করলে অন্য অপشن দেখাবে
@router.callback_query(F.data.startswith("promote:admin_no:"))
async def show_non_admin_chats(query: CallbackQuery):
    text = "ℹ️ আপনি যেগুলোর অ্যাডমিন নন, সেগুলোতে সরাসরি ক্যাম্পেইন রান করতে হলে বটকে আগে অ্যাডমিন বানিয়ে নিন।"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:promote")]
    ])
    
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


# ৫. টার্গেট সিলেক্ট করার পর অডিয়েন্স ফিল্টার বা সেটিংস পেজ (Screenshot 6 er moto)
async def show_audience_settings(query: CallbackQuery, campaign_id: int):
    text = (
        "🎯 <b>টাস্কের অডিয়েন্স</b>\n"
        "বর্তমান: সীমাবদ্ধতা নেই\n\n"
        "টাস্কটি কারা দেখতে পাবে তা বেছে নিন:\n"
        "💡 <i>অডিয়েন্স ফিল্টার প্রতি সম্পাদনায় সর্বনিম্ন মূল্যে +100 GRAM যোগ করে।</i>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 সবাইকে অনুমতি দিন", callback_data=f"promote:aud:all:{campaign_id}")],
        [InlineKeyboardButton(text="🎯 দর্শক নির্বাচন করুন", callback_data=f"promote:aud:select:{campaign_id}")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:promote")]
    ])

    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

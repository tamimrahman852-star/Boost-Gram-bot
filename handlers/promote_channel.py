from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode, ChatMemberStatus
from sqlalchemy import select
from database import User, Campaign, Transaction, CampaignStatus, TaskType, TransactionType

router = Router()

class PromoteChannelStates(StatesGroup):
    waiting_for_reward_price = State()
    waiting_for_quantity = State()

# ১. Channel promote select korar por menu (Screenshot 20)
@router.callback_query(F.data == "promote:cat:channel")
async def promote_channel_start(query: CallbackQuery):
    text = (
        "📢 <b>প্রমোশনের জন্য চ্যাট বা চ্যানেল বেছে নিন</b>\n"
        "(বটকে অ্যাডমিন হতে হবে)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 আমি অ্যাডমিন", callback_data="promote:ch:admin_yes")],
        [InlineKeyboardButton(text="👁️ আমি অ্যাডমিন নই", callback_data="promote:ch:admin_no")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:promote")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

@router.callback_query(F.data == "promote:ch:admin_no")
async def promote_ch_admin_no(query: CallbackQuery):
    text = "ℹ️ আপনার চ্যানেল বা গ্রুপে বটকে অ্যাডমিন বানিয়ে তারপর আবার চেষ্টা করুন।"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ২. "আমি অ্যাডমিন" ক্লিক করলে ব্যবহারকারী যে যে চ্যানেলের অ্যাডমিন তা খুঁজে বের করা
@router.callback_query(F.data == "promote:ch:admin_yes")
async def promote_ch_admin_yes(query: CallbackQuery, bot: Bot, session):
    user_id = query.from_user.id
    
    # Note: Telegram API-তে সরাসরি ব্যবহারকারীর সব অ্যাডমিন চ্যানেল একসাথে বের করার সরাসরি কোনো গেটওয়ে গেট নেই যদি না বট আগে থেকে কোনো কমন গ্রুপে থাকে। 
    # তবে ইউজার যদি পূর্বে বটকে কোনো চ্যানেলে অ্যাড করে থাকে, আমরা ডাটাবেজ থেকে অথবা ইউজারের পাঠানো চ্যাট থেকে তা ফেচ করতে পারি। 
    # অথবা ব্যবহারকারীকে একটিভ চ্যানেলের লিস্ট দিতে পারি। নিচে স্ট্যান্ডার্ড এপ্রোচ দেওয়া হলো:
    
    text = (
        "🔍 <b>আপনার চ্যানেল বা গ্রুপগুলো লোড করা হচ্ছে...</b>\n"
        "অথবা আপনার চ্যানেল সিলেক্ট করুন:"
    )
    
    # এখানে ডেমো বা ডাটাবেজ থেকে ইউজারের চ্যানেল ফেচ করার লজিক অথবা ইনলাইন বাটন দেওয়া হলো।
    # আপনি চাইলে ব্যবহারকারীকে সরাসরি তার ইউজারনেম দিতে বলতে পারেন অথবা বট যেগুলোতে অ্যাডমিন সেগুলো দেখাতে পারেন।
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 আমার চ্যানেল উদাহরণ (@channel)", callback_data="promote:ch:selected:@my_sample_channel")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৩. চ্যানেল সিলেক্ট করার পর অডিয়েন্স মেনুতে যাওয়া
@router.callback_query(F.data.startswith("promote:ch:selected:"))
async def select_user_channel(query: CallbackQuery, state: FSMContext):
    ch_link = query.data.split(":")[3]
    await state.update_data(channel_link=ch_link, target_chat=ch_link)
    
    text = (
        "🎯 <b>টাস্কের অডিয়েন্স</b>\n"
        "বর্তমান: সীমাবদ্ধতা নেই\n\n"
        "টাস্কটি কারা দেখতে পাবে তা বেছে নিন:\n"
        "💡 <i>অডিয়েন্স ফিল্টার প্রতি সম্পাদনায় সর্বনিম্ন মূল্যে +100 GRAM যোগ করে।</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 সবাইকে অনুমতি দিন", callback_data="promote:ch:aud:all")],
        [InlineKeyboardButton(text="🎯 দর্শক নির্বাচন করুন", callback_data="promote:ch:aud:select")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৪. "সবাইকে অনুমতি দিন" click korle Account Type selection
@router.callback_query(F.data == "promote:ch:aud:all")
async def select_account_type(query: CallbackQuery):
    text = (
        "<b>1️⃣ সব ব্যবহারকারী</b>\n"
        "সকল PR GRAM ব্যবহারকারীর মধ্যে বিস্তৃত পৌঁছানো।\n"
        "💡 সর্বনিম্ন মূল্য: 750 GRAM/ইউনিট।\n\n"
        "<b>2️⃣ শুধু Telegram Premium</b>\n"
        "শুধু Telegram Premium ব্যবহারকারীদের দেখানো হবে — আরও মানসম্মত অডিয়েন্স।\n"
        "💡 সর্বনিম্ন মূল্য: 1,400 GRAM/ইউনিট।"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ সব ব্যবহারকারী", callback_data="promote:ch:type:all")],
        [InlineKeyboardButton(text="2️⃣ শুধুমাত্র Telegram Premium", callback_data="promote:ch:type:premium")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:ch:selected_back")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৫. Account type select korار por price setting
@router.callback_query(F.data.in_({"promote:ch:type:all", "promote:ch:type:premium"}))
async def set_reward_price(query: CallbackQuery, state: FSMContext):
    is_premium = "premium" in query.data
    min_price = 1400 if is_premium else 750
    suggested_price = 1800 if is_premium else 900
    
    await state.update_data(is_premium=is_premium, min_price=min_price)
    
    text = (
        "💲 <b>মূল্য নির্ধারণ করুন: ১টি সাবস্ক্রিপশন — এটি সম্পাদনকারীর পুরস্কার।</b>\n\n"
        f"সর্বনিম্ন — {min_price} GRAM\n"
        f"💡 প্রস্তাবিত — {suggested_price} GRAM\n"
        "সম্পাদনের গতি আপনার মূল্যের উপরে নির্ভর করে।"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:ch:aud:all")]
    ])
    await state.set_state(PromoteChannelStates.waiting_for_reward_price)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৬. Price input neoyar por Quantity selection
@router.message(PromoteChannelStates.waiting_for_reward_price)
async def receive_reward_price(message: Message, state: FSMContext, session):
    try:
        price = float(message.text.strip())
    except ValueError:
        await message.answer("⚠️ অনুগ্রহ করে সঠিক সংখ্যা লিখুন।")
        return

    data = await state.get_data()
    min_p = data.get("min_price", 750)
    if price < min_p:
        await message.answer(f"⚠️ সর্বনিম্ন মূল্য {min_p} GRAM হতে হবে।")
        return

    await state.update_data(reward_price=price)
    
    uid = message.from_user.id
    r = await session.execute(select(User).where(User.id == uid))
    user = r.scalar_one_or_none()
    user_balance = user.balance if user else 0.0

    text = (
        "ℹ️ <b>টাস্ক তৈরির কমিশন — ১৫%।</b>\n\n"
        f"💲 সাবস্ক্রিপশনের মূল্য — {price:,.1f} GRAM\n"
        f"💰 আপনার ব্যালেন্স — {user_balance:,.1f} GRAM\n\n"
        "<b>✍️ সংখ্যা লিখুন: সাবস্ক্রিপশন অথবা বেছে নিন:</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="10", callback_data="promote:ch:qty:10"),
            InlineKeyboardButton(text="20", callback_data="promote:ch:qty:20")
        ],
        [InlineKeyboardButton(text="24 (আপনার ব্যালেন্সের সর্বোচ্চ সীমা)", callback_data="promote:ch:qty:max")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    await state.set_state(PromoteChannelStates.waiting_for_quantity)
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# ৭. Quantity selection & Payment
@router.callback_query(F.data.startswith("promote:ch:qty:"))
async def select_quantity(query: CallbackQuery, state: FSMContext):
    qty = 10 if "10" in query.data else (20 if "20" in query.data else 24)
    data = await state.get_data()
    price = data.get("reward_price", 750)
    
    total_cost = (price * qty) * 1.15
    await state.update_data(quantity=qty, total_cost=total_cost)
    
    text = "💳 <b>পেমেন্ট পদ্ধতি বেছে নিন:</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💲 {total_cost:,.1f} GRAM", callback_data="promote:ch:pay:gram")],
        [InlineKeyboardButton(text="⭐ 3 Telegram stars (-15%)", callback_data="promote:ch:pay:stars")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৮. Final Publish
@router.callback_query(F.data == "promote:ch:pay:gram")
async def publish_campaign(query: CallbackQuery, state: FSMContext, session):
    uid = query.from_user.id
    r = await session.execute(select(User).where(User.id == uid))
    user = r.scalar_one_or_none()
    
    data = await state.get_data()
    total_cost = data.get("total_cost", 0)
    
    if user.balance < total_cost:
        await query.answer("❌ আপনার পর্যাপ্ত ব্যালেন্স নেই!", show_alert=True)
        return
        
    user.balance -= total_cost
    
    new_campaign = Campaign(
        user_id=uid,
        task_type=TaskType.CHANNEL_SUB,
        target_link=data.get("channel_link"),
        target_chat_id=data.get("target_chat"),
        reward_per_user=data.get("reward_price"),
        max_completions=data.get("quantity"),
        status=CampaignStatus.ACTIVE
    )
    session.add(new_campaign)
    await session.commit()
    
    text = (
        f"✅ <b>টাস্ক №{new_campaign.id} প্রকাশিত হয়েছে</b>\n\n"
        f"💲 ব্যালেন্স থেকে কাটা হয়েছে: <b>{total_cost:,.1f} GRAM</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ টাস্ক সম্পাদনা করুন", callback_data=f"promote:ch:manage:{new_campaign.id}")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer("✅ সফল!", show_alert=False)

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from sqlalchemy import select
from database import User, Campaign, Transaction, CampaignStatus, TaskType, TransactionType

router = Router()

class PromoteChannelStates(StatesGroup):
    waiting_for_channel_username = State()
    waiting_for_reward_price = State()
    waiting_for_quantity = State()

# ১. Channel promote select korar por admin status check menu
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

@router.callback_query(F.data == "promote:ch:admin_yes")
async def promote_ch_admin_yes(query: CallbackQuery, state: FSMContext):
    text = "⚠️ <b>অনুগ্রহ করে আপনার চ্যানেলের ইউজারনেম বা লিংক দিন:</b>\nউদাহরণ: @mychannel"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    await state.set_state(PromoteChannelStates.waiting_for_channel_username)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

@router.message(PromoteChannelStates.waiting_for_channel_username)
async def receive_channel_username(message: Message, state: FSMContext):
    ch_link = message.text.strip()
    await state.update_data(channel_link=ch_link, target_chat=ch_link)
    
    # Send confirmation popup style text (Screenshot 5 er moto)
    text = f"Are you sure you want to send 📺 <b>{ch_link}</b> to PR GRAM?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Cancel", callback_data="promote:cat:channel"),
            InlineKeyboardButton(text="Send", callback_data="promote:ch:confirm_target")
        ]
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# ২. Target confirm hobar por Audience Select menu (Screenshot 6 er moto)
@router.callback_query(F.data == "promote:ch:confirm_target")
async def show_audience_options(query: CallbackQuery, state: FSMContext):
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

# ৩. "সবাইকে অনুমতি দিন" click korle Account Type selection (Screenshot 8 er moto)
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
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:ch:confirm_target")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৪. "দর্শক নির্বাচন করুন" click korle Language filter (Screenshot 9 er moto)
@router.callback_query(F.data == "promote:ch:aud:select")
async def select_language_filter(query: CallbackQuery):
    text = (
        "• দর্শক: সীমাবদ্ধতা নেই\n\n"
        "🌐 <b>এক বা একাধিক ভাষা বেছে নিন</b>\n"
        "💡 <i>অডিয়েন্স ফিল্টার প্রতি সম্পাদনায় সর্বনিম্ন মূল্যে +100 GRAM যোগ করে।</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇺🇦 Українськ...", callback_data="lang:uk"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en")
        ],
        [
            InlineKeyboardButton(text="🇩🇪 Deutsch", callback_data="lang:de"),
            InlineKeyboardButton(text="🇨🇳 中文", callback_data="lang:zh"),
            InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang:ar")
        ],
        [
            InlineKeyboardButton(text="🇮🇷 فارسی", callback_data="lang:fa"),
            InlineKeyboardButton(text="🇪🇸 Español", callback_data="lang:es"),
            InlineKeyboardButton(text="🇮🇩 Bahasa...", callback_data="lang:id")
        ],
        [
            InlineKeyboardButton(text="🇧🇷 Português", callback_data="lang:pt"),
            InlineKeyboardButton(text="🇮🇳 हिंदी", callback_data="lang:hi"),
            InlineKeyboardButton(text="🇧🇩 বাংলা", callback_data="lang:bn")
        ],
        [
            InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang:uz"),
            InlineKeyboardButton(text="🇹🇷 Türkçe", callback_data="lang:tr"),
            InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="lang:kk")
        ],
        [InlineKeyboardButton(text="🇫🇷 Français", callback_data="lang:fr")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:ch:confirm_target")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৫. Account type select korar por price setting (Screenshot 10 & 11 er moto)
@router.callback_query(F.data.in_({"promote:ch:type:all", "promote:ch:type:premium"}))
async def set_reward_price(query: CallbackQuery, state: FSMContext):
    is_premium = "premium" in query.data
    min_price = 1400 if is_premium else 750
    suggested_price = 1800 if is_premium else 900
    
    await state.update_data(is_premium=is_premium, min_price=min_price)
    
    text = (
        "💲 <b>মূল্য নির্ধারণ করুন: ১টি সাবস্ক্রিপশন — এটি সম্পাদনকারীর পুরস্কার।</b>\n\n"
        f"క్కువতম — {min_price} GRAM\n"
        f"💡 প্রস্তাবিত — {suggested_price} GRAM\n"
        "সম্পাদনের গতি আপনার মূল্যের উপরে নির্ভর করে।"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:ch:aud:all")]
    ])
    await state.set_state(PromoteChannelStates.waiting_for_reward_price)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৬. Price input neoyar por Quantity selection (Screenshot 12 er moto)
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

# ৭. Quantity selection & Payment Method (Screenshot 13 er moto)
@router.callback_query(F.data.startswith("promote:ch:qty:"))
async def select_quantity(query: CallbackQuery, state: FSMContext, session):
    qty_str = query.data.split(":")
    qty = 10 if "10" in qty_str else (20 if "20" in qty_str else 24)
    
    data = await state.get_data()
    price = data.get("reward_price", 750)
    
    # Total calculation with 15% commission
    subtotal = price * qty
    total_cost = subtotal * 1.15
    
    await state.update_data(quantity=qty, total_cost=total_cost)
    
    uid = query.from_user.id
    r = await session.execute(select(User).where(User.id == uid))
    user = r.scalar_one_or_none()
    
    text = "💳 <b>পেমেন্ট পদ্ধতি বেছে নিন:</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💲 {total_cost:,.1f} GRAM", callback_data="promote:ch:pay:gram")],
        [InlineKeyboardButton(text="⭐ 3 Telegram stars (-15%)", callback_data="promote:ch:pay:stars")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:channel")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

# ৮. Final Confirmation & Publish Task (Screenshot 14, 15, 16 er moto)
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
    
    # Create campaign in database
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
    
    session.add(Transaction(
        user_id=uid,
        amount=-total_cost,
        type=TransactionType.CAMPAIGN_CREATE,
        description=f"Campaign creation cost",
        balance_after=user.balance
    ))
    await session.commit()
    
    # Success text (Screenshot 15 er moto)
    text = (
        f"✅ <b>টাস্ক №{new_campaign.id} প্রকাশিত হয়েছে</b>\n\n"
        f"💲 ব্যালেন্স থেকে কাটা হয়েছে: <b>{total_cost:,.1f} GRAM</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ টাস্ক সম্পাদনা করুন", callback_data=f"promote:ch:manage:{new_campaign.id}")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer("✅ সফল!", show_alert=False)

# ৯. Task Management Dashboard (Screenshot 17 er moto)
@router.callback_query(F.data.startswith("promote:ch:manage:"))
async def manage_campaign(query: CallbackQuery, session):
    cid = int(query.data.split(":")[3])
    res = await session.execute(select(Campaign).where(Campaign.id == cid))
    c = res.scalar_one_or_none()
    
    if not c:
        await query.answer("❌ টাস্ক পাওয়া যায়নি।", show_alert=True)
        return

    text = (
        f"📋 <b>কাজ #{c.id}</b>\n"
        f"অবস্থা: <b>▶️ প্রক্রියාধীন</b>\n"
        f"🔍 <b>বিস্তারিত:</b>\n"
        f"• {c.max_completions} সাবস্ক্রিপশন\n"
        f"• পুরস্কার: একক প্রতি {c.reward_per_user:,.0f} GRAM\n"
        f"• সম্পন্ন: {c.completed_count}/{c.max_completions}\n"
        f"• চলমান: 0\n"
        f"• আনসাবস্ক্রাইবে ফেরত: 0\n"
        f"🔗 চ্যানেল: {c.target_link}\n\n"
        f"<b>অ্যাক্সেস ফিল্টার:</b>\n"
        f"• অ্যাকাউন্টের ধরন: সকল ব্যবহারকারী\n"
        f"• দর্শক: সকল ব্যবহারকারী"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ অতিরিক্ত এক্সিকিউশন যোগ করুন", callback_data=f"promote:ch:add:{c.id}")],
        [
            InlineKeyboardButton(text="⏸️ বিরতি দিন", callback_data=f"promote:ch:pause:{c.id}"),
            InlineKeyboardButton(text="🗑️ মুছুন", callback_data=f"promote:ch:delete:{c.id}")
        ],
        [InlineKeyboardButton(text="✏️ মূল্য পরিবর্তন", callback_data=f"promote:ch:price:{c.id}")],
        [InlineKeyboardButton(text="👤 অ্যাকাউন্টের ধরন: সব ব্যবহারকারী", callback_data=f"promote:ch:type_change:{c.id}")],
        [InlineKeyboardButton(text="🌐 দর্শক: সকল ব্যবহারকারী", callback_data=f"promote:ch:aud_change:{c.id}")],
        [InlineKeyboardButton(text="❌ নোটিফিকেশন বন্ধ করুন", callback_data=f"promote:ch:notif:{c.id}")],
        [
            InlineKeyboardButton(text="1", callback_data="page:1"),
            InlineKeyboardButton(text="<", callback_data="page:prev"),
            InlineKeyboardButton(text="1", callback_data="page:curr"),
            InlineKeyboardButton(text=">", callback_data="page:next"),
            InlineKeyboardButton(text="2", callback_data="page:2")
        ],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:promote")]
    ])
    
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

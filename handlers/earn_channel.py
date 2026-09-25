import time
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from database import User, Campaign, TaskCompletion, Transaction, CampaignStatus, TaskType, TransactionType
from config import BOT_USERNAME, BusinessRules

router = Router()

# Simple memory-based rate limiting dictionary to prevent spam/auto-bots
user_last_click = {}

@router.callback_query(F.data == "earn:cat:channel")
async def show_channel_tasks(query: CallbackQuery, session):
    uid = query.from_user.id
    r = await session.execute(select(User).where(User.id == uid))
    user = r.scalar_one_or_none()
    lang = user.language if user and user.language in ('bn', 'en') else "bn"

    done_sub = select(TaskCompletion.campaign_id).where(TaskCompletion.user_id == uid)
    stmt = select(Campaign).where(
        Campaign.status == CampaignStatus.ACTIVE,
        Campaign.task_type == TaskType.CHANNEL_SUB,
        Campaign.completed_count < Campaign.max_completions,
        Campaign.id.not_in(done_sub)
    ).limit(1) # Screenshot-er moto ekta ekta kore task dekhanor jonno limit 1 rakhte paren ba list dite paren
    
    res = await session.execute(stmt)
    campaign = res.scalar_one_or_none()

    if not campaign:
        text = (
            "❌ <b>কোনো কাজ উপলব্ধ নেই</b>\n\n"
            "⚠️ 7 দিনের আগে গ্রুপ ছাড়বেন না। নাহলে কাজ করা ব্লক হবে এবং সেগুলোতে থেকে আয় করা GRAM বাতিল হবে।"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ কোনো টাস্ক উপলব্ধ নেই।", callback_data="none")],
            [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="earn:back")]
        ])
        try:
            await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            await query.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await query.answer()
        return

    link = campaign.target_link or (f"https://t.me/{campaign.target_username}" if campaign.target_username else f"https://t.me/{BOT_USERNAME}")
    
    # Screenshot-er exact text style
    text = "⚠️ <b>7 দিনের আগে গ্রুপ ছাড়বেন না। নাহলে কাজ করা ব্লক হবে এবং সেগুলোতে থেকে আয় করা GRAM বাতিল হবে।</b>"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"💲 +{campaign.reward_per_user:,.0f} | সাবস্ক্রাইব ↗️", url=link),
            InlineKeyboardButton(text="🔄 যাচাই করুন", callback_data=f"earn:verify:channel:{campaign.id}")
        ],
        [InlineKeyboardButton(text="❌ রিপোর্ট", callback_data=f"earn:report:{campaign.id}")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="earn:back")]
    ])

    try:
        await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await query.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data.startswith("earn:verify:channel:"))
async def verify_channel_task(query: CallbackQuery, session, bot: Bot):
    uid = query.from_user.id
    
    # --- AUTO-BOT / SCRIPT PROTECTION (Rate Limiting) ---
    now = time.time()
    last_time = user_last_click.get(uid, 0)
    if now - last_time < 2.0:  # 2 second er kom somoy e bar bar click korle block korbe
        await query.answer("⚠️ খুব দ্রুত ক্লিক করছেন! একটু পরে চেষ্টা করুন।", show_alert=True)
        return
    user_last_click[uid] = now
    # ---------------------------------------------------

    parts = query.data.split(":")
    if len(parts) < 4:
        await query.answer("Invalid action!", show_alert=True)
        return
    
    campaign_id = int(parts[3])
    
    c_res = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = c_res.scalar_one_or_none()
    
    u_res = await session.execute(select(User).where(User.id == uid))
    user = u_res.scalar_one_or_none()

    if not campaign or campaign.status != CampaignStatus.ACTIVE:
        await query.answer("❌ ক্যাম্পেইনটি আর সক্রিয় নেই।", show_alert=True)
        return

    # Check already completed
    tc_res = await session.execute(
        select(TaskCompletion).where(TaskCompletion.user_id == uid, TaskCompletion.campaign_id == campaign_id)
    )
    if tc_res.scalar_one_or_none():
        await query.answer("⚠️ আপনি ইতিমধ্যে এই টাস্কটি সম্পন্ন করেছেন!", show_alert=True)
        return

    chat_id = campaign.target_chat_id or campaign.target_username
    if not chat_id:
        await query.answer("❌ টাস্কের চ্যাট আইডি পাওয়া যায়নি।", show_alert=True)
        return

    # Real Telegram API check (Script ba bot jodi join na kore fake request pathay tahole eta dhora pore jabe)
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=uid)
        if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
            # Screenshot 4 er moto error message
            await query.message.answer(
                "ℹ️ <b>আপনি চ্যানেল/চ্যাটে সাবস্ক্রাইব করেননি। সাবস্ক্রাইব করুন এবং আবার চেষ্টা করুন।</b>",
                parse_mode=ParseMode.HTML
            )
            await query.answer("❌ সাবস্ক্রাইব করা হয়নি!", show_alert=False)
            return
    except Exception:
        await query.message.answer(
            "ℹ️ <b>আপনি চ্যানেল/চ্যাটে সাবস্ক্রাইব করেননি। সাবস্ক্রাইব করুন এবং আবার চেষ্টা করুন।</b>",
            parse_mode=ParseMode.HTML
        )
    user_last_click[uid] = now

    # Reward Credit & Success Message (Screenshot 3 er moto)
    user.balance += campaign.reward_per_user
    user.total_earned += campaign.reward_per_user
    user.add_xp(BusinessRules.TASK_XP_REWARD)
    
    campaign.completed_count += 1
    if campaign.completed_count >= campaign.max_completions:
        campaign.status = CampaignStatus.COMPLETED

    session.add(TaskCompletion(user_id=uid, campaign_id=campaign_id, reward=campaign.reward_per_user))
    session.add(Transaction(
        user_id=uid, amount=campaign.reward_per_user, type=TransactionType.TASK_REWARD,
        description=f"Task #{campaign_id} completed", balance_after=user.balance
    ))
    await session.commit()

    success_text = (
        f"✅ <b>টাস্ক №{campaign_id} সম্পন্ন</b>\n\n"
        f"💲 আপনি <b>+{campaign.reward_per_user:,.0f} GRAM</b> পেয়েছেন\n"
        f"💰 ব্যాలেন্স: <b>{user.balance:,.1f} GRAM</b>"
    )
    
    await query.answer("✅ সফল!", show_alert=False)
    await query.message.answer(success_text, parse_mode=ParseMode.HTML)

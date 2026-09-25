from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from sqlalchemy import select
from database import Campaign

router = Router()

@router.callback_query(F.data == "promote:my_tasks")
async def show_my_tasks(query: CallbackQuery, session):
    uid = query.from_user.id
    result = await session.execute(select(Campaign).where(Campaign.user_id == uid))
    campaigns = result.scalars().all()

    if not campaigns:
        text = "📋 <b>আপনার কোনো চলমান বা প্রকাশিত টাস্ক নেই।</b>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:promote")]
        ])
        await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await query.answer()
        return

    text = "📋 <b>আপনার টাস্কসমূহ:</b>\nনিচের তালিকা থেকে আপনার টাস্ক ম্যানেজ করুন:"
    
    buttons = []
    for c in campaigns[:10]: # Prothom 10ti task dekhabe
        buttons.append([InlineKeyboardButton(text=f"কাজ #{c.id} ({c.task_type.value})", callback_data=f"promote:ch:manage:{c.id}")])
    
    buttons.append([InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:promote")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode

router = Router()

class PromoteGroupStates(StatesGroup):
    waiting_for_group_link = State()

@router.callback_query(F.data == "promote:cat:group")
async def promote_group_start(query: CallbackQuery):
    text = (
        "👥 <b>প্রমোশনের জন্য গ্রুপ বেছে নিন</b>\n"
        "(বকে গ্রুপের অ্যাডমিন হতে হবে)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 আমি অ্যাডমিন", callback_data="promote:grp:admin_yes")],
        [InlineKeyboardButton(text="👁️ আমি অ্যাডমিন নই", callback_data="promote:grp:admin_no")],
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:promote")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

@router.callback_query(F.data == "promote:grp:admin_no")
async def promote_grp_admin_no(query: CallbackQuery):
    text = "ℹ️ আপনার গ্রুপে বটকে অ্যাডমিন বানিয়ে তারপর আবার চেষ্টা করুন।"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:group")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

@router.callback_query(F.data == "promote:grp:admin_yes")
async def promote_grp_admin_yes(query: CallbackQuery, state: FSMContext):
    text = "⚠️ <b>অনুগ্রহ করে আপনার গ্রুপের ইউজারনেম বা লিংক দিন:</b>\nউদাহরণ: @mygroup"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="promote:cat:group")]
    ])
    await state.set_state(PromoteGroupStates.waiting_for_group_link)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

@router.message(PromoteGroupStates.waiting_for_group_link)
async def receive_group_link(message: Message, state: FSMContext):
    grp_link = message.text.strip()
    await state.update_data(group_link=grp_link)
    
    text = f"Are you sure you want to send 👥 <b>{grp_link}</b> to PR GRAM?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Cancel", callback_data="promote:cat:group"),
            InlineKeyboardButton(text="Send", callback_data="promote:grp:confirm")
        ]
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "promote:grp:confirm")
async def group_confirm_success(query: CallbackQuery):
    text = "✅ গ্রুপ সফলভাবে যোগ করা, হয়েছে! (Channel-er moto baki audience ebong pricing flow eikhane connect korte paren)"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ ফিরে যান", callback_data="menu:promote")]
    ])
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

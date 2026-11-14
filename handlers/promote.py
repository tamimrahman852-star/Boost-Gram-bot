from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from sqlalchemy import select
from database import User
from localization import L10N

router = Router()

def _promote_menu(lang: str, balance) -> tuple:
    text = (
        ("📢 <b>আপনি কী প্রচার করতে চান?</b>\n\n💲 ব্যালেন্স: <b>{b:,.0f} GRAM</b>" if lang == "bn"
         else "📢 <b>What would you like to promote?</b>\n\n💲 Balance: <b>{b:,.0f} GRAM</b>").format(b=balance)
    )
    def B(bn, en, data):
        return InlineKeyboardButton(text=bn if lang == "bn" else en, callback_data=data)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [B("📢 চ্যানেল", "📢 Channel", "promote:cat:channel"), B("👥 গ্রুপ", "👥 Group", "promote:cat:group")],
        [B("👁 পোস্ট", "👁 Post", "promote:soon"), B("🤖 বট", "🤖 Bot", "promote:soon")],
        [B("⚡ প্রিমিয়াম বুস্ট", "⚡ Premium Boost", "promote:soon"), B("❤️ প্রতিক্রিয়া", "❤️ Reaction", "promote:soon")],
        [B("⚙️ অটো-টাস্ক সেটিংস", "⚙️ Auto-task settings", "promote:soon")],
        [B("📋 আমার কাজ", "📋 My tasks", "promote:my_tasks"), B("◀️ ফিরে যান", "◀️ Back", "menu:start")],
    ])
    return text, kb


@router.message(F.text.in_([L10N.T["en"]["menu_promote"], L10N.T["bn"]["menu_promote"]]))
async def promote_main_menu_message(message: Message, session):
    user = (await session.execute(select(User).where(User.id == message.from_user.id))).scalar_one_or_none()
    if not user:
        await message.answer("⚠️ /start")
        return
    text, kb = _promote_menu(user.language, user.balance)
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "menu:promote")
async def promote_main_menu_callback(query: CallbackQuery, session):
    user = (await session.execute(select(User).where(User.id == query.from_user.id))).scalar_one_or_none()
    if not user:
        await query.answer()
        return
    text, kb = _promote_menu(user.language, user.balance)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


# ৩. মূল মেনুতে (Start) ফিরে যাওয়ার হ্যান্ডলার
@router.callback_query(F.data == "menu:start")
async def back_to_start_menu(query: CallbackQuery, session):
    from keyboards import main_menu
    user = (await session.execute(select(User).where(User.id == query.from_user.id))).scalar_one_or_none()
    lang = user.language if user else "bn"
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.message.answer(
        "🏠 " + ("প্রধান মেনু" if lang == "bn" else "Main menu"),
        reply_markup=main_menu(lang),
    )
    await query.answer()


@router.callback_query(F.data == "promote:soon")
async def promote_soon(query: CallbackQuery, session):
    user = (await session.execute(select(User).where(User.id == query.from_user.id))).scalar_one_or_none()
    lang = user.language if user else "bn"
    await query.answer(
        "🚧 এই ধরনের টাস্কে অংশগ্রহণ যাচাই করার নিরাপদ উপায় তৈরি হলে চালু হবে।" if lang == "bn"
        else "🚧 This task type will be enabled once participation can be verified securely.",
        show_alert=True,
    )

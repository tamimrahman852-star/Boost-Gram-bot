"""Admin broadcast: preview -> confirm -> throttled background send with a delivery report."""
import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from config import ADMIN_IDS, logger
from database import AsyncSessionLocal, User, redis_client
from helpers import AdminState

router = Router()
SEND_DELAY = 0.05          # ~20 messages/second, below Telegram's broadcast limit
_running = False


def _confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Send to everyone", callback_data="bc:go"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="bc:cancel"),
    ]])


@router.callback_query(F.data == "adm:broadcast")
async def bc_start(query: CallbackQuery, state: FSMContext):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    await state.set_state(AdminState.broadcast_content)
    await query.message.answer("📣 Send the message to broadcast (text, photo or video with caption). It will be copied exactly as you send it.")
    await query.answer()


@router.message(Command("broadcast"))
async def bc_cmd(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS or message.chat.type != "private":
        return
    await state.set_state(AdminState.broadcast_content)
    await message.answer("📣 Send the message to broadcast (text, photo or video with caption).")


@router.message(AdminState.broadcast_content)
async def bc_preview(message: Message, state: FSMContext, session):
    if message.from_user.id not in ADMIN_IDS:
        return
    total = (await session.execute(select(User.id).where(User.is_blocked == False))).all()  # noqa: E712
    await state.update_data(src_chat=message.chat.id, src_msg=message.message_id)
    await state.set_state(None)
    await message.answer(f"👆 This is what users will receive.\n👥 Recipients: <b>{len(total):,}</b>\n\nSend it?",
                         reply_markup=_confirm_kb(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "bc:cancel")
async def bc_cancel(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text("❌ Broadcast cancelled.")
    await query.answer()


@router.callback_query(F.data == "bc:go")
async def bc_go(query: CallbackQuery, state: FSMContext, bot: Bot):
    global _running
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    data = await state.get_data()
    if "src_msg" not in data:
        await query.answer("Session expired.", show_alert=True)
        return
    if _running:
        await query.answer("A broadcast is already running.", show_alert=True)
        return
    await state.clear()
    await query.message.edit_text("⏳ Broadcast started. You will get a report when it finishes.")
    await query.answer()
    asyncio.create_task(_run(bot, query.from_user.id, data["src_chat"], data["src_msg"]))


async def _run(bot: Bot, admin_id: int, src_chat: int, src_msg: int):
    global _running
    _running = True
    sent = failed = 0
    try:
        async with AsyncSessionLocal() as s:
            ids = [r[0] for r in (await s.execute(select(User.id).where(User.is_blocked == False))).all()]  # noqa: E712
        for uid in ids:
            for attempt in range(3):
                try:
                    await bot.copy_message(uid, src_chat, src_msg)
                    sent += 1
                    break
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except (TelegramForbiddenError, TelegramBadRequest):
                    failed += 1          # user blocked the bot / deleted account
                    break
                except Exception as e:
                    logger.warning(f"broadcast error to {uid}: {e}")
                    failed += 1
                    break
            await asyncio.sleep(SEND_DELAY)
    finally:
        _running = False
        try:
            await bot.send_message(admin_id, f"✅ <b>Broadcast finished</b>\n\n📨 Delivered: <b>{sent:,}</b>\n🚫 Failed/blocked: <b>{failed:,}</b>",
                                   parse_mode=ParseMode.HTML)
        except Exception:
            pass

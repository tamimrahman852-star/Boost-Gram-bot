from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
)
from aiogram.types.chat_administrator_rights import ChatAdministratorRights
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode

router = Router()


class PromoteChannelStates(StatesGroup):
    waiting_for_reward_price = State()
    waiting_for_quantity = State()


# ============================================================
# 1. Channel promotion menu
# ============================================================

@router.callback_query(F.data == "promote:cat:channel")
async def promote_channel_start(query: CallbackQuery):

    text = (
        "📢 <b>Promotion-er jonno chat ba channel beche nin</b>\n\n"
        "Nicher button theke apnar channel ba group select korun."
    )

    # ========================================================
    # Admin rights required when selecting a channel
    # ========================================================

    admin_rights = ChatAdministratorRights(
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=True,
        can_manage_video_chats=True,
        can_restrict_members=True,
        can_promote_members=False,
        can_change_info=True,
        can_invite_users=True,
        can_post_stories=True,
        can_edit_stories=True,
        can_delete_stories=True,
    )

    reply_kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🏠 Ami admin",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=1,
                        chat_is_channel=True,
                        user_administrator_rights=admin_rights,
                    ),
                )
            ],
            [
                KeyboardButton(
                    text="👁️ Ami admin noi",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=2,
                        chat_is_channel=True,
                    ),
                )
            ],
            [
                KeyboardButton(text="◀️ Fire jan")
            ],
        ],
        resize_keyboard=True,
    )

    await query.message.answer(
        text,
        reply_markup=reply_kb,
        parse_mode=ParseMode.HTML,
    )

    await query.answer()


# ============================================================
# 2. Channel selected from Telegram popup
# ============================================================

@router.message(F.chat_shared)
async def handle_shared_chat(
    message: Message,
    state: FSMContext,
):

    chat_id = message.chat_shared.chat_id

    await state.update_data(
        channel_link=str(chat_id),
        target_chat=chat_id,
    )

    text = (
        "🎯 <b>Task-er audience</b>\n\n"
        "Bortoman: Simaboddhota nei\n\n"
        "Task-ti kara dekhte pabe ta beche nin:"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌍 Sabke onumoti din",
                    callback_data="promote:ch:aud:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎯 Dorshok nirbachon korun",
                    callback_data="promote:ch:aud:select",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Fire jan",
                    callback_data="promote:cat:channel",
                )
            ],
        ]
    )

    await message.answer(
        text,
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# 3. Back button
# ============================================================

@router.message(F.text == "◀️ Fire jan")
async def back_to_main_menu(message: Message):

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="💰 Ai"),
                KeyboardButton(text="📢 Prochar korun"),
            ],
            [
                KeyboardButton(text="🎫 Check"),
                KeyboardButton(text="👤 Amar cabinet"),
            ],
            [
                KeyboardButton(text="🛡️ Subscription check"),
                KeyboardButton(text="📊 Amader bot o porisongkhan"),
            ],
            [
                KeyboardButton(text="🔗 Dorkari link"),
                KeyboardButton(text="ℹ️ Nirdeshika"),
            ],
        ],
        resize_keyboard=True,
    )

    await message.answer(
        "🏠 Prodan menu:",
        reply_markup=kb,
    )

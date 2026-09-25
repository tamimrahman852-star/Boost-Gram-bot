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
    waiting_for_link_type = State()
    waiting_for_audience = State()
    waiting_for_audience_type = State()
    waiting_for_language = State()
    waiting_for_reward_price = State()
    waiting_for_quantity = State()


# ============================================================
# 1. Channel promotion menu (Start)
# ============================================================

@router.callback_query(F.data == "promote:cat:channel")
async def promote_channel_start(query: CallbackQuery, state: FSMContext):
    await state.clear()
    
    text = (
        "📢 <b>Promotion-er jonno chat ba channel beche nin</b>\n\n"
        "Nicher button theke apnar channel ba group select korun."
    )

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
# 2. Channel selected from Telegram popup -> Link Type Menu
# ============================================================

@router.message(F.chat_shared)
async def handle_shared_chat(message: Message, state: FSMContext):
    chat_id = message.chat_shared.chat_id

    await state.update_data(
        channel_link=str(chat_id),
        target_chat=chat_id,
        min_price=750,
        filters_added=0,
    )
    await state.set_state(PromoteChannelStates.waiting_for_link_type)

    text = (
        "🟢 <b>চ্যানেল সফলভাবে যোগ করা হয়েছে।</b>\n\n"
        "<b>লিঙ্কের ধরন বেছে নিন:</b>\n"
        "🔘 <b>সাধারণ</b> — সদস্যরা সাথে সাথে যোগ দেয় (\"এড়িয়ে যান\" চাপুন)।\n"
        "🔘 <b>অনুরোধসহ</b> — যারা যোগ দেয় প্রত্যেককে আপনি অনুমোদন করেন।"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="> এড়িয়ে যান",
                    callback_data="promote:link:skip",
                )
            ],
            [
                InlineKeyboardButton(
                    text="+ অনুরোধসহ লিঙ্ক",
                    callback_data="promote:link:request",
                )
            ],
            [
                InlineKeyboardButton(
                    text="← ফিরে যান",
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
# 3. Link Type: Request Confirmation
# ============================================================

@router.callback_query(F.data == "promote:link:request")
async def link_request_confirm(query: CallbackQuery):
    text = (
        "<b>জয়েন-রিকোয়েস্ট লিঙ্ক তৈরি করবেন?</b>\n\n"
        "• সদস্যরা কেবল আপনার অনুমোদনের পরে যোগ দেয়।\n"
        "• জয়েন রিকোয়েস্ট পাঠানোর সাথেই সাথেই সম্পাদককারী পেমেন্ট পান।"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ হ্যাঁ, নিশ্চিত করছি",
                    callback_data="promote:link:request:yes",
                )
            ],
            [
                InlineKeyboardButton(
                    text="← ফিরে যান",
                    callback_data="promote:link:back",
                )
            ],
        ]
    )

    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data.in__{"promote:link:skip", "promote:link:request:yes", "promote:link:back"})
async def handle_link_choices(query: CallbackQuery, state: FSMContext):
    if query.data == "promote:link:back":
        # Return to link type selection
        await handle_shared_chat_from_callback(query, state)
        return

    # Proceed to Audience Menu
    await show_audience_menu(query, state)


async def handle_shared_chat_from_callback(query: CallbackQuery, state: FSMContext):
    text = (
        "<b>লিঙ্কের ধরন বেছে নিন:</b>\n"
        "🔘 <b>সাধারণ</b> — সদস্যরা সাথে সাথে যোগ দেয় (\"এড়িয়ে যান\" চাপুন)।\n"
        "🔘 <b>অনুরোধসহ</b> — যারা যোগ দেয় প্রত্যেককে আপনি অনুমোদন করেন।"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="> এড়িয়ে যান", callback_data="promote:link:skip")],
            [InlineKeyboardButton(text="+ অনুরোধসহ লিঙ্ক", callback_data="promote:link:request")],
            [InlineKeyboardButton(text="← ফিরে যান", callback_data="promote:cat:channel")],
        ]
    )
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


# ============================================================
# 4. Task Audience Menu
# ============================================================

async def show_audience_menu(query: CallbackQuery, state: FSMContext):
    await state.set_state(PromoteChannelStates.waiting_for_audience)
    data = await state.get_data()
    filters_added = data.get("filters_added", 0)

    filter_note = f"\n💡 অডিয়েন্স ফিল্টার প্রতি সম্পাদনায় ন্যূনতম মূল্যে +{filters_added} GRAM যোগ করে।" if filters_added > 0 else ""

    text = (
        "🎯 <b>টাস্কের অডিয়েন্স</b>\n"
        "বর্তমান: সীমাবদ্ধতা নেই\n\n"
        "টাস্কটি কারা দেখতে পাবে তা বেছে নিন:"
        f"{filter_note}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌍 সবাইকে অনুমতি দিন",
                    callback_data="promote:ch:aud:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎯 দর্শক নির্বাচন করুন",
                    callback_data="promote:ch:aud:select",
                )
            ],
            [
                InlineKeyboardButton(
                    text="← ফিরে যান",
                    callback_data="promote:link:back",
                )
            ],
        ]
    )

    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "promote:ch:aud:all")
async def audience_all(query: CallbackQuery, state: FSMContext):
    await state.update_data(min_price=750)
    await ask_reward_price(query, state)


# ============================================================
# 5. Select Audience Type (All users vs Telegram Premium)
# ============================================================

@router.callback_query(F.data == "promote:ch:aud:select")
async def audience_select_type(query: CallbackQuery, state: FSMContext):
    await state.set_state(PromoteChannelStates.waiting_for_audience_type)
    text = (
        "1️⃣ <b>সব ব্যবহারকারী</b>\n"
        "সকল PR GRAM ব্যবহারকারীর মধ্যে বিস্তৃত পৌঁছানো।\n"
        "💡 সর্বনিম্ন মূল্য: 750 GRAM/ইউনিট।\n\n"
        "2️⃣ <b>শুধু Telegram Premium</b>\n"
        "শুধু Telegram Premium ব্যবহারকারীদের দেখানো হবে — আরও মানসম্মত অডিয়েন্স।\n"
        "💡 সর্বনিম্ন মূল্য: 1,400 GRAM/ইউনিট।"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1️⃣ সব ব্যবহারকারী",
                    callback_data="promote:aud:type:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="2️⃣ শুধুমাত্র Telegram Premium",
                    callback_data="promote:aud:type:premium",
                )
            ],
            [
                InlineKeyboardButton(
                    text="← ফিরে যান",
                    callback_data="promote:aud:back_to_main",
                )
            ],
        ]
    )

    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "promote:aud:back_to_main")
async def back_to_audience_main(query: CallbackQuery, state: FSMContext):
    await show_audience_menu(query, state)


@router.callback_query(F.data.in__{"promote:aud:type:all", "promote:aud:type:premium"})
async def audience_type_chosen(query: CallbackQuery, state: FSMContext):
    min_price = 1400 if query.data == "promote:aud:type:premium" else 750
    await state.update_data(min_price=min_price)
    
    # Move to language selection menu
    await show_language_menu(query, state)


# ============================================================
# 6. Language Selection Menu
# ============================================================

async def show_language_menu(query: CallbackQuery, state: FSMContext):
    await state.set_state(PromoteChannelStates.waiting_for_language)
    data = await state.get_data()
    selected_langs = data.get("selected_langs", [])

    langs_display = ", ".join(selected_langs) if selected_langs else "সীমাবদ্ধতা নেই"

    text = (
        f"• দর্শক: {langs_display}\n\n"
        "🌐 <b>এক বা একাধিক ভাষা বেছে নিন</b>\n"
        "💡 অডিয়েন্স ফিল্টার প্রতি সম্পাদনায় ন্যূনতম মূল্যে +100 GRAM যোগ করে।"
    )

    languages = [
        ("🇺🇦 Україн...", "lang:uk"), ("🇷🇺 Русский", "lang:ru"), ("🇬🇧 English", "lang:en"),
        ("🇩🇪 Deutsch", "lang:de"), ("🇨🇳 中文", "lang:zh"), ("🇸🇦 العربية", "lang:ar"),
        ("🇮🇷 فارسی", "lang:fa"), ("🇪🇸 Español", "lang:es"), ("🇮🇩 Bahasa", "lang:id"),
        ("🇧🇷 Portug...", "lang:pt"), ("🇮🇳 हिंदी", "lang:hi"), ("🇧🇩 বাংলা", "lang:bn"),
        ("🇺🇿 O'zbek...", "lang:uz"), ("🇹🇷 Türkçe", "lang:tr"), ("🇰🇿 Қазақ...", "lang:kk"),
        ("🇫🇷 Français", "lang:fr")
    ]

    keyboard_rows = []
    row = []
    for name, code in languages:
        is_selected = name in selected_langs
        btn_text = f"✅ {name}" if is_selected else name
        row.append(InlineKeyboardButton(text=btn_text, callback_data=code))
        if len(row) == 3:
            keyboard_rows.append(row)
            row = []
    if row:
        keyboard_rows.append(row)

    keyboard_rows.append([InlineKeyboardButton(text="✅ সংরক্ষণ করুন ও চালিয়ে যান", callback_data="lang:save")])
    keyboard_rows.append([InlineKeyboardButton(text="← ফিরে যান", callback_data="promote:aud:back_to_main")])

    kb = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data.startswith("lang:"))
async def toggle_language(query: CallbackQuery, state: FSMContext):
    action = query.data.split(":")[1]
    data = await state.get_data()
    selected_langs = data.get("selected_langs", [])

    if action == "save":
        if selected_langs:
            await state.update_data(filters_added=100)
        await ask_reward_price(query, state)
        return

    lang_map = {
        "uk": "🇺🇦 Українська", "ru": "🇷🇺 Русский", "en": "🇬🇧 English",
        "de": "🇩🇪 Deutsch", "zh": "🇨🇳 中文", "ar": "🇸🇦 العربية",
        "fa": "🇮🇷 فارسی", "es": "🇪🇸 Español", "id": "🇮🇩 Bahasa Indonesia",
        "pt": "🇧🇷 Português", "hi": "🇮🇳 हिंदी", "bn": "🇧🇩 বাংলা",
        "uz": "🇺🇿 O'zbekcha", "tr": "🇹🇷 Türkçe", "kk": "🇰🇿 Қазақча",
        "fr": "🇫🇷 Français"
    }

    lang_name = lang_map.get(action)
    if lang_name:
        if lang_name in selected_langs:
            selected_langs.remove(lang_name)
        else:
            selected_langs.append(lang_name)
        await state.update_data(selected_langs=selected_langs)

    await show_language_menu(query, state)


# ============================================================
# 7. Reward Price Input State
# ============================================================

async def ask_reward_price(query: CallbackQuery, state: FSMContext):
    await state.set_state(PromoteChannelStates.waiting_for_reward_price)
    data = await state.get_data()
    min_p = data.get("min_price", 750)
    filters_add = data.get("filters_added", 0)
    total_min = min_p + filters_add
    suggested = total_min + 150

    text = (
        "💲 <b>মূল্য নির্ধারণ করুন: ১টি সাবস্ক্রিপশন — এটি সম্পাদককারীর পুরস্কার।</b>\n\n"
        f"নিউজ / ন্যূনতম — {total_min} GRAM\n"
        f"💡 প্রস্তাবিত — {suggested} GRAM\n"
        "সম্পাদনার গতি আপনার মূল্যের উপর নির্ভর করে।"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← ফিরে যান",
                    callback_data="promote:ch:aud:select",
                )
            ]
        ]
    )

    await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()


# ============================================================
# 8. Back button for Main Menu
# ============================================================

@router.message(F.text == "◀️ Fire jan")
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
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

import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = "8980118908:AAE4NIDIq7YkIB8_40LLn2b7Hg9AfRAJs20"

# Mock User Data (বাস্তব প্রজেক্টে এটি MongoDB/Supabase ডাটাবেসে থাকবে)
user_data = {}

def get_user_profile(user_id):
    if user_id not in user_data:
        user_data[user_id] = {
            "coins": 5445.55,
            "xp": 599,
            "level": "Activist"
        }
    return user_data[user_id]

# Start Command & Main Reply Keyboard
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user_profile(user.id)

    reply_keyboard = [
        ['💰 Earnings', '📣 Promote'],
        ['👤 Cabinet', '📊 Statistics'],
        ['🔗 Useful Links', '⚙️ Settings']
    ]
    markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)

    welcome_msg = (
        f"👋 Hello {user.first_name}!\n"
        f"Welcome to PR GRAM Bot. Earn coins by completing tasks or promote your channels easily!"
    )
    await update.message.reply_text(welcome_msg, reply_markup=markup)

# Handle Buttons Logic
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    profile = get_user_profile(user_id)

    # 1. Promote Button
    if text == '📣 Promote':
        msg = (
            "📣 What do you want to promote?\n\n"
            f"💰 Balance: {profile['coins']:,} GRAM"
        )
        keyboard = [
            [InlineKeyboardButton("📢 চ্যানেল", callback_data="p_channel"), InlineKeyboardButton("👥 গ্রুপ", callback_data="p_group")],
            [InlineKeyboardButton("🖼️ পোস্ট", callback_data="p_post"), InlineKeyboardButton("🤖 বট", callback_data="p_bot")],
            [InlineKeyboardButton("⚡ প্রিমিয়াম বুস্ট", callback_data="p_boost"), InlineKeyboardButton("❤️ প্রতিক্রিয়া", callback_data="p_reaction")],
            [InlineKeyboardButton("⚙️ অটো-টাস্ক সেটিংস", callback_data="p_settings")],
            [InlineKeyboardButton("📋 আমার কাজ", callback_data="p_my_tasks"), InlineKeyboardButton("⬅️ ফিরে যান", callback_data="p_back")]
        ]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    # 2. Cabinet Button
    elif text == '👤 Cabinet':
        msg = (
            "👤 Your Cabinet:\n\n"
            f"🆔 My ID: {user_id}\n"
            f"📈 Level: 🌱{profile['level']} {profile['xp']}/1500 XP\n"
            f"💰 Balance: {profile['coins']:,} GRAM"
        )
        await update.message.reply_text(msg)

    # 3. Statistics Button
    elif text == '📊 Statistics':
        msg = (
            "📊 PR GRAM Statistics\n\n"
            "👤 Users: 2,143,878\n"
            "🆕 today: 735\n\n"
            "Completed all-time:\n"
            "📢 Channel subscriptions: 38,229,408\n"
            "👥 Group joins: 10,684,737\n"
            "👁 Views: 30,144,624\n"
            "❤️ Reactions: 8,227,755\n"
            "🤖 Bot starts: 2,054,718\n"
            "⚡ Premium Boost: 216,451\n\n"
            "Our bots\n"
            "These are official PR GRAM mirror bots with a shared database."
        )
        await update.message.reply_text(msg)

    # 4. Earnings Button
    elif text == '💰 Earnings':
        msg = "🖋 আয়ের জন্য কাজের ক্যাটাগরি বেছে নিন:"
        keyboard = [
            [InlineKeyboardButton("📢 চ্যানেল · 0", callback_data="e_channel"), InlineKeyboardButton("👥 গ্রুপ · 0", callback_data="e_group")],
            [InlineKeyboardButton("👁️ ভিউ · 0", callback_data="e_view"), InlineKeyboardButton("🤖 বট · 0", callback_data="e_bot")],
            [InlineKeyboardButton("❤️ রিঅ্যাকশন · 0", callback_data="e_reaction"), InlineKeyboardButton("⚡ Boost · 0", callback_data="e_boost")],
            [InlineKeyboardButton("📝 নিয়মাবলি", callback_data="e_rules")]
        ]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

# Inline Button Callback Handler
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "e_channel":
        await query.message.reply_text(
            "⚠️ 7 দিনের আগে চ্যানেল ছাড়বেন না। নাহলে কাজ বাতিল হবে।\n\n"
            "💲 +1,020 GRAM | সাবস্ক্রাইব করুন"
        )

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("Bot is running...")
    app.run_polling()

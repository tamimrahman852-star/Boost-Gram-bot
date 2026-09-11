import os
import sqlite3
import logging
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = "8980118908:AAE4NIDIq7YkIB8_40LLn2b7Hg9AfRAJs20"

# --- DATABASE SETUP ---
DB_NAME = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            coins REAL DEFAULT 0.0,
            xp INTEGER DEFAULT 0,
            level TEXT DEFAULT '🌱 সক্রিয়'
        )
    ''')
    # User Completed Tasks Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS completed_tasks (
            user_id INTEGER,
            task_id INTEGER,
            PRIMARY KEY (user_id, task_id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_or_create_user(user_id, first_name):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, first_name, coins, xp, level FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    
    if not user:
        cursor.execute(
            'INSERT INTO users (user_id, first_name, coins, xp, level) VALUES (?, ?, ?, ?, ?)',
            (user_id, first_name, 0.0, 0, '🌱 সক্রিয়')
        )
        conn.commit()
        cursor.execute('SELECT user_id, first_name, coins, xp, level FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        
    conn.close()
    return {
        "user_id": user[0],
        "first_name": user[1],
        "coins": user[2],
        "xp": user[3],
        "level": user[4]
    }

def update_user_balance(user_id, coins_to_add, xp_to_add):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET coins = coins + ?, xp = xp + ? WHERE user_id = ?', (coins_to_add, xp_to_add, user_id))
    conn.commit()
    conn.close()

def is_task_completed(user_id, task_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM completed_tasks WHERE user_id = ? AND task_id = ?', (user_id, task_id))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def mark_task_completed(user_id, task_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO completed_tasks (user_id, task_id) VALUES (?, ?)', (user_id, task_id))
    conn.commit()
    conn.close()

# Sample Tasks
TASKS = [
    {"id": 1, "reward": 1020, "xp": 10, "channel": "@telegram"},
    {"id": 2, "reward": 760, "xp": 8, "channel": "@durov"},
    {"id": 3, "reward": 750, "xp": 5, "channel": "@telegram"},
    {"id": 4, "reward": 750, "xp": 5, "channel": "@durov"},
]

# --- RENDER HEALTH CHECK SERVER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_or_create_user(user.id, user.first_name)

    reply_keyboard = [
        ['💰 আয়', '📢 প্রচার করুন'],
        ['🔝 চেক', '👤 আমার কেবিনেট'],
        ['🛡️ সাবস্ক্রিপশন চেক', '📊 আমাদের বট ও পরিসংখ্যান'],
        ['🔗 দরকারি লিংক', 'ℹ️ নির্দেশিকা']
    ]
    markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)

    welcome_msg = (
        f"👋 Real money 🍅, PR GRAM-এ স্বাগতম!\n\n"
        f"টেলিগ্রামে প্রচারের প্ল্যাটফর্ম"
    )
    await update.message.reply_text(welcome_msg, reply_markup=markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = update.effective_user
    u_data = get_or_create_user(user.id, user.first_name)

    if text == '💰 আয়':
        keyboard = [
            [InlineKeyboardButton("📢 চ্যানেল · 277", callback_data="e_channel"), InlineKeyboardButton("👥 গ্রুপ · 51", callback_data="e_group")],
            [InlineKeyboardButton("👁️ ভিউ · 826", callback_data="e_view"), InlineKeyboardButton("🤖 বট · 2928", callback_data="e_bot")],
            [InlineKeyboardButton("❤️ রিঅ্যাকশন · 430", callback_data="e_reaction"), InlineKeyboardButton("⚡ Boost · 62", callback_data="e_boost")],
            [InlineKeyboardButton("📝 নিয়ামাবলি", callback_data="e_rules")]
        ]
        await update.message.reply_text("🖋 আয় করার জন্য কাজের ক্যাটাগরি বেছে নিন:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif text == '👤 আমার কেবিনেট':
        msg = (
            f"👤 আপনার কেবিনেট:\n\n"
            f"🆔 আমার আইডি: {u_data['user_id']}\n"
            f"📈 লেভেল: {u_data['level']} {u_data['xp']}/1500 XP\n"
            f"💰 ব্যালেন্স: {u_data['coins']:,.2f} GRAM"
        )
        keyboard = [
            [InlineKeyboardButton("💳 ব্যালেন্স রিচার্জ করুন", callback_data="c_recharge")],
            [InlineKeyboardButton("👥 রেফারেল সিস্টেম", callback_data="c_ref")],
            [InlineKeyboardButton("📈 লেভেল সিস্টেম", callback_data="c_level")],
            [InlineKeyboardButton("📋 আমার কাজ", callback_data="c_tasks")],
            [InlineKeyboardButton("🌐 ভাষা পরিবর্তন", callback_data="c_lang")]
        ]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    elif text == '📢 প্রচার করুন':
        msg = f"ADS আপনি কী প্রচার করতে চান?\n\n💰 ব্যালেন্স: {u_data['coins']:,.2f} GRAM"
        keyboard = [
            [InlineKeyboardButton("📢 চ্যানেল", callback_data="p_chan"), InlineKeyboardButton("👥 গ্রুপ", callback_data="p_grp")],
            [InlineKeyboardButton("👁️ পোস্ট", callback_data="p_post"), InlineKeyboardButton("🤖 বট", callback_data="p_bot")],
            [InlineKeyboardButton("⚡ প্রিমিয়াম বুস্ট", callback_data="p_boost"), InlineKeyboardButton("❤️ প্রতিক্রিয়া", callback_data="p_react")]
        ]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    elif text == '📊 আমাদের বট ও পরিসংখ্যান':
        msg = (
            "📊 PR GRAM পরিসংখ্যান\n\n"
            "👤 ব্যবহারকারী: 21,43,151\n"
            "🆕 আজ: 411\n\n"
            "সর্বমোট সম্পন্ন:\n"
            "📢 চ্যানেল সাবস্ক্রিপশন: 3,82,21,498\n"
            "👥 গ্রুপে যোগদান: 1,06,83,532\n"
            "👁 ভিউ: 3,01,40,963\n"
            "❤️ রিঅ্যাকশন: 82,26,346\n"
            "🤖 বট চালু: 20,54,128\n"
            "⚡ Premium Boost: 2,16,426"
        )
        await update.message.reply_text(msg)

    elif text == '🔝 চেক':
        msg = (
            "চেক ব্যবহার করে বার্তায় সরাসরি গ্রাম পাঠানো যায়।\n\n"
            "• পার্সোনাল চেক — একজন ব্যবহারকারীকে পাঠানোর জন্য\n"
            "• মাল্টি চেক — একাধিক ব্যবহারকারীকে পাঠানোর জন্য\n\n"
            "চেকের ধরন বেছে নিন:"
        )
        keyboard = [
            [InlineKeyboardButton("👤 পার্সোনাল", callback_data="chk_p"), InlineKeyboardButton("👥 মাল্টি চেক", callback_data="chk_m")]
        ]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    if query.data == "e_channel":
        await query.message.reply_text("⚠️ 7 দিনের আগে চ্যানেল ছাড়বেন না। নাহলে কাজ করা ব্লক হবে এবং প্রাপ্ত GRAM বাতিল হবে।")
        
        # Available tasks list
        for task in TASKS:
            if not is_task_completed(user.id, task['id']):
                btn = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(f"💲 +{task['reward']:,} | সাবস্ক্রাইব", url=f"https://t.me/{task['channel'][1:]}"),
                        InlineKeyboardButton("🔄 যাচাই করুন", callback_data=f"verify_{task['id']}")
                    ]
                ])
                await query.message.reply_text(f"📢 চ্যানেল সাবস্ক্রাইব করুন:\n{task['channel']}", reply_markup=btn)
                break
        else:
            await query.message.reply_text("✅ আপনার জন্য আপাতত নতুন কোনো টাস্ক নেই!")

    elif query.data.startswith("verify_"):
        task_id = int(query.data.split("_")[1])
        task = next((t for t in TASKS if t['id'] == task_id), None)

        if task:
            if is_task_completed(user.id, task_id):
                await query.message.reply_text("❌ আপনি এই টাস্কটি আগেই সম্পন্ন করেছেন!")
                return

            try:
                # Real subscription check
                member = await context.bot.get_chat_member(chat_id=task['channel'], user_id=user.id)
                if member.status in ['member', 'administrator', 'creator']:
                    update_user_balance(user.id, task['reward'], task['xp'])
                    mark_task_completed(user.id, task_id)
                    await query.message.reply_text(f"🎉 অভিনন্দন! আপনি +{task['reward']:,} GRAM এবং {task['xp']} XP পেয়েছেন।")
                else:
                    await query.message.reply_text(f"⚠️ আপনি এখনও {task['channel']} চ্যানেলে যোগ দেননি। অনুগ্রহ করে জয়েন করে যাচাই করুন।")
            except Exception as e:
                # Fallback if bot is not admin in target channel
                update_user_balance(user.id, task['reward'], task['xp'])
                mark_task_completed(user.id, task_id)
                await query.message.reply_text(f"✅ টাস্ক সম্পন্ন হয়েছে! +{task['reward']:,} GRAM আপনার অ্যাকাউন্টে যোগ করা হয়েছে।")

if __name__ == '__main__':
    Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("Bot is running dynamically with SQLite Database...")
    app.run_polling()


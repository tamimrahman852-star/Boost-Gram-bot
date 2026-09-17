from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from localization import L10N, t


def main_menu(lang: str) -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.row(
        KeyboardButton(text=t(lang, "menu_earnings")),
        KeyboardButton(text=t(lang, "menu_promote")),
    )
    b.row(
        KeyboardButton(text=t(lang, "menu_checks")),
        KeyboardButton(text=t(lang, "menu_cabinet")),
    )
    b.row(
        KeyboardButton(text=t(lang, "menu_sub_check")),
        KeyboardButton(text=t(lang, "menu_stats")),
    )
    b.row(
        KeyboardButton(text=t(lang, "menu_links")),
        KeyboardButton(text=t(lang, "menu_instruction")),
    )
    return b.as_markup(resize_keyboard=True, is_persistent=True)


def cabinet_kb(lang: str, notif_on: bool, can_withdraw: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=t(lang, "btn_replenish"), callback_data="cab:replenish"))
    b.row(InlineKeyboardButton(text=t(lang, "btn_referral"), callback_data="cab:referral"))
    b.row(
        InlineKeyboardButton(text=t(lang, "btn_level"), callback_data="cab:level"),
        InlineKeyboardButton(text=t(lang, "btn_tasks"), callback_data="cab:tasks"),
    )
    if can_withdraw:
        b.row(InlineKeyboardButton(text=t(lang, "menu_withdraw"), callback_data="cab:withdraw"))
    notif_key = "btn_notif_off" if notif_on else "btn_notif_on"
    b.row(InlineKeyboardButton(text=t(lang, notif_key), callback_data="cab:notif"))
    b.row(InlineKeyboardButton(text=t(lang, "btn_lang"), callback_data="cab:lang"))
    return b.as_markup()


def back_to_cabinet_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="cab:back")]
    ])


def language_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    items = [(code, name) for code, name in L10N.LANGS.items()]
    for i in range(0, len(items), 3):
        row = items[i:i + 3]
        b.row(*[InlineKeyboardButton(text=name, callback_data=f"lang:{code}") for code, name in row])
    return b.as_markup()


def category_kb(lang: str, counts: dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=f"📢 Channel · {counts.get('channel', 0)}", callback_data="earn:cat:channel_sub"),
        InlineKeyboardButton(text=f"👥 Group · {counts.get('group', 0)}", callback_data="earn:cat:group_join"),
    )
    b.row(
        InlineKeyboardButton(text=f"👀 Post · {counts.get('post', 0)}", callback_data="earn:cat:post_view"),
        InlineKeyboardButton(text=f"🤖 Bot · {counts.get('bot', 0)}", callback_data="earn:cat:bot_start"),
    )
    b.row(
        InlineKeyboardButton(text=f"❤️ Reaction · {counts.get('reaction', 0)}", callback_data="earn:cat:reaction"),
        InlineKeyboardButton(text=f"⚡ Boost · {counts.get('boost', 0)}", callback_data="earn:cat:boost_7day"),
    )
    b.row(InlineKeyboardButton(text="📋 Rules", callback_data="earn:rules"))
    b.row(InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="cab:back"))
    return b.as_markup()


def promote_type_kb(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="📢 Channel", callback_data="promo:type:channel_sub"),
        InlineKeyboardButton(text="👥 Group", callback_data="promo:type:group_join"),
    )
    b.row(
        InlineKeyboardButton(text="👀 Post View", callback_data="promo:type:post_view"),
        InlineKeyboardButton(text="🤖 Bot Start", callback_data="promo:type:bot_start"),
    )
    b.row(
        InlineKeyboardButton(text="❤️ Reaction", callback_data="promo:type:reaction"),
        InlineKeyboardButton(text="⚡ Premium Boost", callback_data="promo:type:boost_7day"),
    )
    b.row(InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="cab:back"))
    return b.as_markup()


def premium_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "promote_premium_yes"), callback_data="promo:premium:yes")],
        [InlineKeyboardButton(text=t(lang, "promote_premium_no"), callback_data="promo:premium:no")],
    ])


def checks_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "check_create_btn"), callback_data="chk:create")],
        [InlineKeyboardButton(text=t(lang, "check_activate_btn"), callback_data="chk:redeem")],
        [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="cab:back")],
    ])


def task_detail_kb(lang: str, cid: str, link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "task_open"), url=link)],
        [InlineKeyboardButton(text=t(lang, "task_verify"), callback_data=f"earn:verify:{cid}")],
        [InlineKeyboardButton(text=t(lang, "task_back"), callback_data="earn:back")],
    ])


def admin_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="💰 Add/Deduct Coins", callback_data="adm:coins"))
    b.row(InlineKeyboardButton(text="🚨 Fraud Reports", callback_data="adm:reports"))
    b.row(InlineKeyboardButton(text="💸 Withdrawals", callback_data="adm:withdrawals"))
    b.row(InlineKeyboardButton(text="🚫 Ban/Unban User", callback_data="adm:ban"))
    b.row(InlineKeyboardButton(text="📢 Moderate Campaigns", callback_data="adm:tasks"))
    b.row(InlineKeyboardButton(text="📊 Full Stats", callback_data="adm:stats"))
    return b.as_markup()


def withdraw_method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 USDT (TRC20)", callback_data="wd:m:usdt_trc20")],
        [InlineKeyboardButton(text="💎 USDT (ERC20)", callback_data="wd:m:usdt_erc20")],
        [InlineKeyboardButton(text="🏦 Bank Transfer", callback_data="wd:m:bank")],
        [InlineKeyboardButton(text="📱 Mobile Wallet", callback_data="wd:m:mobile")],
    ])

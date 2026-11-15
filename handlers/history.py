"""Paginated transaction history (like a bank statement) in the user's cabinet."""
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from sqlalchemy import select, func

from database import User, Transaction, TransactionType
from helpers import esc, L

router = Router()
PAGE = 8

ICON = {
    TransactionType.TASK_REWARD: "🎯", TransactionType.REFERRAL_REWARD: "👥",
    TransactionType.REFERRAL_TIER_BONUS: "🌱", TransactionType.STAR_TOPUP: "⭐",
    TransactionType.WITHDRAWAL: "💸", TransactionType.CAMPAIGN_PAYMENT: "📢",
    TransactionType.CAMPAIGN_REFUND: "↩️", TransactionType.CHECK_REDEEM: "🎫",
    TransactionType.CHECK_CREATE: "🧾", TransactionType.PENALTY_REVOKE: "⚠️",
    TransactionType.ADMIN_ADJUSTMENT: "🛠",
}


def _icon(tx):
    d = (tx.description or "").lower()
    if d.startswith("deposit"):
        return "💳"
    if d.startswith("voucher"):
        return "🎟"
    return ICON.get(tx.type, "•")


@router.callback_query(F.data.startswith("cab:hist:"))
async def history(query: CallbackQuery, session):
    try:
        page = max(0, int(query.data.split(":")[2]))
    except ValueError:
        page = 0
    uid = query.from_user.id
    user = (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    lang = user.language if user else "bn"
    total = (await session.execute(select(func.count(Transaction.id)).where(Transaction.user_id == uid))).scalar() or 0
    pages = max(1, -(-total // PAGE))
    page = min(page, pages - 1)
    rows = (await session.execute(
        select(Transaction).where(Transaction.user_id == uid)
        .order_by(Transaction.created_at.desc()).offset(page * PAGE).limit(PAGE))).scalars().all()

    head = L(lang, "📜 <b>লেনদেনের ইতিহাস</b>", "📜 <b>Transaction history</b>")
    if not rows:
        body = L(lang, "\n\nএখনো কোনো লেনদেন নেই।", "\n\nNo transactions yet.")
    else:
        lines = []
        for tx in rows:
            sign = "+" if tx.amount >= 0 else "−"
            when = tx.created_at.strftime("%d %b %H:%M") if tx.created_at else ""
            desc = esc((tx.description or tx.type.value)[:34])
            lines.append(f"{_icon(tx)} <b>{sign}{abs(tx.amount):,.0f}</b> · {desc}\n<i>{when} · ⚖️ {tx.balance_after:,.0f}</i>")
        body = "\n\n" + "\n\n".join(lines)

    nav = []
    if pages > 1:
        nav = [
            InlineKeyboardButton(text="‹", callback_data=f"cab:hist:{max(page - 1, 0)}"),
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton(text="›", callback_data=f"cab:hist:{min(page + 1, pages - 1)}"),
        ]
    kb = InlineKeyboardMarkup(inline_keyboard=[nav, [InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="cab:back")]]
                              if nav else [[InlineKeyboardButton(text=L(lang, "◀️ ফিরে যান", "◀️ Back"), callback_data="cab:back")]])
    await query.message.edit_text(head + body, reply_markup=kb, parse_mode=ParseMode.HTML)
    await query.answer()

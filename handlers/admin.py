from decimal import Decimal
from sqlalchemy import select, func
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database import User, FraudReport, WithdrawalRequest, Campaign, Transaction, CampaignStatus, TransactionType
from keyboards import admin_kb
from helpers import AdminState

router = Router()


@router.message(Command("admin"))
async def admin_cmd(message: Message, session):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Not authorized.")
        return
    r = await session.execute(select(func.count(FraudReport.id)).where(FraudReport.status == "pending"))
    reports = r.scalar() or 0
    r = await session.execute(select(func.count(WithdrawalRequest.id)).where(WithdrawalRequest.status == "pending"))
    wds = r.scalar() or 0
    await message.answer(
        f"👑 <b>Admin Panel</b>\n\n🚨 Reports: {reports}\n💸 Withdrawals: {wds}",
        reply_markup=admin_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "adm:stats")
async def adm_stats(query: CallbackQuery, session):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    r = await session.execute(select(func.count(User.id)))
    u = r.scalar() or 0
    r = await session.execute(select(func.sum(User.balance)))
    b = r.scalar() or Decimal("0")
    r = await session.execute(select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.ACTIVE))
    a = r.scalar() or 0
    await query.message.edit_text(
        f"📊 <b>Stats</b>\n\n👥 {u:,}\n💰 {b:,.0f}\n📢 {a:,}",
        reply_markup=admin_kb(), parse_mode=ParseMode.HTML,
    )
    await query.answer()


@router.callback_query(F.data == "adm:reports")
async def adm_rep(query: CallbackQuery, session):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    r = await session.execute(select(FraudReport).where(FraudReport.status == "pending").limit(10))
    reps = r.scalars().all()
    if not reps:
        await query.message.edit_text("✅ No reports.", reply_markup=admin_kb())
    else:
        lines = ["🚨 <b>Reports</b>\n"]
        for rp in reps:
            lines.append(f"• <code>{rp.user_id}</code> — {rp.report_type}")
        await query.message.edit_text("\n".join(lines), reply_markup=admin_kb(), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "adm:withdrawals")
async def adm_wds(query: CallbackQuery, session):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    r = await session.execute(select(WithdrawalRequest).where(WithdrawalRequest.status == "pending").limit(10))
    wds = r.scalars().all()
    if not wds:
        await query.message.edit_text("✅ No pending.", reply_markup=admin_kb())
    else:
        lines = ["💸 <b>Pending</b>\n"]
        for w in wds:
            lines.append(f"• <code>{w.user_id}</code> — {w.amount:,.0f}")
        await query.message.edit_text("\n".join(lines), reply_markup=admin_kb(), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data == "adm:coins")
async def adm_coins(query: CallbackQuery, state: FSMContext):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    await query.message.edit_text("Send user ID:")
    await state.set_state(AdminState.coins_uid)
    await query.answer()


@router.message(AdminState.coins_uid)
async def adm_uid(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        uid = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Invalid.")
        return
    await state.update_data(uid=uid)
    await message.answer("Send amount (e.g. 5000 or -1000):")
    await state.set_state(AdminState.coins_amount)


@router.message(AdminState.coins_amount)
async def adm_amt(message: Message, session, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        amt = Decimal((message.text or "").strip())
    except Exception:
        await message.answer("❌ Invalid.")
        return
    data = await state.get_data()
    await state.clear()
    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == data["uid"]).with_for_update())
        u = r.scalar_one_or_none()
        if not u:
            await message.answer("❌ Not found.")
            return
        u.balance += amt
        session.add(Transaction(
            user_id=u.id, amount=amt, type=TransactionType.ADMIN_ADJUSTMENT,
            description="Admin", balance_after=u.balance,
        ))
    await session.commit()
    await message.answer(f"✅ {amt:+,.0f} GRAM → {data['uid']}", parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "adm:ban")
async def adm_ban(query: CallbackQuery, state: FSMContext):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    await query.message.edit_text("Send user ID:")
    await state.set_state(AdminState.ban_uid)
    await query.answer()


@router.message(AdminState.ban_uid)
async def adm_ban_uid(message: Message, session, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        uid = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Invalid.")
        return
    await state.clear()
    async with session.begin_nested():
        r = await session.execute(select(User).where(User.id == uid).with_for_update())
        u = r.scalar_one_or_none()
        if not u:
            await message.answer("❌ Not found.")
            return
        u.is_blocked = not u.is_blocked
        act = "banned" if u.is_blocked else "unbanned"
    await session.commit()
    await message.answer(f"✅ {uid} {act}.")

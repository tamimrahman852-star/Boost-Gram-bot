from decimal import Decimal
from sqlalchemy import select, func
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from aiogram import Bot
from config import ADMIN_IDS, logger
from datetime import datetime, timezone
from database import User, FraudReport, WithdrawalRequest, Campaign, Transaction, CampaignStatus, TransactionType
from keyboards import admin_kb, withdrawal_review_kb
from localization import t
from helpers import AdminState, esc

router = Router()


@router.message(Command("admin"))
async def admin_cmd(message: Message, session):
    if message.from_user.id not in ADMIN_IDS or message.chat.type != "private":
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
    from datetime import timedelta
    from database import TaskCompletion, DepositRequest, RetentionStatus
    now = datetime.now(timezone.utc)
    day = now - timedelta(hours=24)

    async def one(q):
        return (await session.execute(q)).scalar() or 0

    users = await one(select(func.count(User.id)))
    new24 = await one(select(func.count(User.id)).where(User.created_at >= day))
    active24 = await one(select(func.count(User.id)).where(User.last_active_at >= day))
    blocked = await one(select(func.count(User.id)).where(User.is_blocked == True))  # noqa: E712
    premium = await one(select(func.count(User.id)).where(User.is_premium == True))  # noqa: E712
    balance = await one(select(func.sum(User.balance))) or Decimal("0")
    camp_active = await one(select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.ACTIVE))
    camp_total = await one(select(func.count(Campaign.id)))
    done_all = await one(select(func.count(TaskCompletion.id)))
    done24 = await one(select(func.count(TaskCompletion.id)).where(TaskCompletion.completed_at >= day))
    pen = await one(select(func.count(TaskCompletion.id)).where(TaskCompletion.retention_status == RetentionStatus.PENALIZED))
    pend_wd = await one(select(func.count(WithdrawalRequest.id)).where(WithdrawalRequest.status == "pending"))
    pend_dep = await one(select(func.count(DepositRequest.id)).where(DepositRequest.status == "pending"))
    dep_bdt = await one(select(func.sum(DepositRequest.amount_bdt)).where(DepositRequest.status == "approved")) or Decimal("0")
    reports = await one(select(func.count(FraudReport.id)))

    await query.message.edit_text(
        "📊 <b>Dashboard</b>\n\n"
        f"👥 Users: <b>{users:,}</b>  (+{new24:,} today · {active24:,} active 24h)\n"
        f"⭐ Premium: {premium:,} · 🚫 Blocked: {blocked:,}\n"
        f"💰 Total balance held: <b>{balance:,.0f}</b> GRAM\n\n"
        f"📢 Campaigns: {camp_active:,} active / {camp_total:,} total\n"
        f"🎯 Completions: {done_all:,} (+{done24:,} in 24h) · penalised {pen:,}\n"
        f"🚨 Fraud reports: {reports:,}\n\n"
        f"⏳ Pending withdrawals: <b>{pend_wd:,}</b>\n"
        f"⏳ Pending deposits: <b>{pend_dep:,}</b>\n"
        f"💳 Approved deposits: {dep_bdt:,.0f} BDT",
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
    r = await session.execute(select(WithdrawalRequest).where(WithdrawalRequest.status == "pending").order_by(WithdrawalRequest.created_at).limit(10))
    wds = r.scalars().all()
    if not wds:
        await query.message.edit_text("✅ No pending.", reply_markup=admin_kb())
    else:
        await query.message.edit_text(f"💸 <b>Pending withdrawals</b> ({len(wds)})", reply_markup=admin_kb(), parse_mode=ParseMode.HTML)
        for w in wds:
            await query.message.answer(
                ("🚩 <b>HOLD</b> " + (w.risk_reason or "") + "\n" if w.risk_hold else "") + f"👤 <code>{w.user_id}</code>\n💰 <code>{w.amount:,.0f}</code>\n💳 {w.payment_method}\n📝 <code>{(w.payment_details or '')[:200]}</code>",
                reply_markup=withdrawal_review_kb(w.id, w.risk_hold), parse_mode=ParseMode.HTML)
    await query.answer()


@router.callback_query(F.data.startswith("adm:wd:"))
async def adm_wd_review(query: CallbackQuery, session, bot: Bot):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return
    _, _, action, wd_id = query.data.split(":", 3)
    r = await session.execute(select(WithdrawalRequest).where(WithdrawalRequest.id == wd_id).with_for_update())
    wd = r.scalar_one_or_none()
    # status is re-checked under the row lock => no double approval / double refund
    if not wd or wd.status != "pending":
        await session.rollback()
        await query.answer("Already processed.", show_alert=True)
        return
    if action == "clear":          # risk shield: the admin reviewed the flag and releases the request
        wd.risk_hold = False
        wd.admin_notes = ((wd.admin_notes or "") + f" hold cleared by {query.from_user.id}").strip()
        await session.commit()
        await query.answer("Hold cleared. You can approve it now.", show_alert=True)
        try:
            await query.message.edit_reply_markup(reply_markup=withdrawal_review_kb(wd_id))
        except Exception:
            pass
        return
    if action == "ok" and wd.risk_hold:
        await session.rollback()
        await query.answer("🚩 Held by the risk shield: " + (wd.risk_reason or "")[:150] + "\nClear the hold first.", show_alert=True)
        return
    ru = await session.execute(select(User).where(User.id == wd.user_id).with_for_update())
    user = ru.scalar_one_or_none()
    wd.processed_by = query.from_user.id
    wd.processed_at = datetime.now(timezone.utc)
    if action == "ok":
        wd.status = "approved"
        if user:
            user.total_withdrawn += wd.amount
        note = "✅ Approved"
        user_msg = f"✅ Your withdrawal of {wd.amount:,.0f} GRAM was approved and will be paid soon."
    else:
        wd.status = "rejected"
        if user:
            user.balance += wd.amount
            session.add(Transaction(
                user_id=user.id, amount=wd.amount, type=TransactionType.ADMIN_ADJUSTMENT,
                description="Withdrawal rejected - refund", reference_id=wd.id,
                reference_type="withdrawal", balance_after=user.balance,
            ))
        note = "❌ Rejected & refunded"
        user_msg = f"❌ Your withdrawal of {wd.amount:,.0f} GRAM was rejected. The amount was returned to your balance."
    await session.commit()
    logger.info(f"Admin {query.from_user.id} {note} withdrawal {wd_id}")
    try:
        await query.message.edit_text(f"{query.message.text}\n\n<b>{note}</b>", parse_mode=ParseMode.HTML)
    except Exception:
        pass
    if user:
        try:
            await bot.send_message(user.id, user_msg)
        except Exception:
            pass
    await query.answer(note)


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
        if not amt.is_finite() or abs(amt) > Decimal("100000000"):
            raise ValueError()
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
    logger.warning(f"ADMIN BALANCE ADJUST by {message.from_user.id}: {amt} -> {data['uid']}")
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


@router.message(Command("i18n"))
async def admin_i18n_status(message: Message):
    """/i18n - which languages have manual translation files and how complete each one is."""
    if message.from_user.id not in ADMIN_IDS or message.chat.type != "private":
        return
    import asyncio
    import i18n
    cov = i18n.coverage()
    rows = "\n".join(f"• <code>{l}</code>: {have}/{total}" + (" ✅" if total and have >= total else "") for l, (have, total) in sorted(cov.items()))
    text = ("🌐 <b>Language files</b> (translated texts / total)\n\n" + rows +
            "\n\nEdit <code>locales/&lt;lang&gt;.tsv</code> to improve a translation; missing texts are shown in English.")
    prov = i18n.get_provider()
    if prov is not None:
        text += f"\n\n🤖 Auto-translation is ON ({prov.name}). Testing…"
        await message.answer(text, parse_mode=ParseMode.HTML)
        try:
            results = await asyncio.wait_for(prov.ping(), 60)
        except asyncio.TimeoutError:
            results = [(prov.name, "❌ no answer within 60s")]
        await message.answer("🔎 " + "\n".join(f"{esc(m)}: {esc(r)}" for m, r in results), parse_mode=ParseMode.HTML)
        return
    await message.answer(text, parse_mode=ParseMode.HTML)

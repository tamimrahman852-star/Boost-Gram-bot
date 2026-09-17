from decimal import Decimal
from sqlalchemy import select, func
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode

from config import BOT_USERNAME
from database import User, Campaign, TaskCompletion, Transaction, CampaignStatus, TransactionType
from localization import L10N, t
from helpers import esc

router = Router()


@router.message(F.text.in_([L10N.T["en"]["menu_stats"], L10N.T["bn"]["menu_stats"]]))
async def stats(message: Message, session):
    r = await session.execute(select(func.count(User.id)))
    users = r.scalar() or 0
    r = await session.execute(select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.ACTIVE))
    active = r.scalar() or 0
    r = await session.execute(select(func.count(TaskCompletion.id)))
    done = r.scalar() or 0
    r = await session.execute(select(func.sum(Transaction.amount)).where(Transaction.type == TransactionType.TASK_REWARD))
    paid = r.scalar() or Decimal("0")
    await message.answer(t("en", "stats", users=users, active=active, completed=done, paid=paid), parse_mode=ParseMode.HTML)


@router.message(F.text.in_([L10N.T["en"]["menu_links"], L10N.T["bn"]["menu_links"]]))
async def links(message: Message):
    await message.answer(t("en", "links", bot=BOT_USERNAME), parse_mode=ParseMode.HTML)


@router.message(F.text.in_([L10N.T["en"]["menu_instruction"], L10N.T["bn"]["menu_instruction"]]))
async def instr(message: Message):
    await message.answer(t("en", "instruction"), parse_mode=ParseMode.HTML)


@router.message(F.text.in_([L10N.T["en"]["menu_sub_check"], L10N.T["bn"]["menu_sub_check"]]))
async def subc(message: Message):
    await message.answer(t("en", "sub_check", bot=esc(BOT_USERNAME)), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "noop")
async def noop(query: CallbackQuery):
    await query.answer()

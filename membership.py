"""Telegram membership / boost checks shared by the earn flow, the retention worker and the early-leave handler."""
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.enums import ChatMemberStatus


async def has_active_boost(bot: Bot, target, uid: int) -> bool:
    """Telegram tells us (bot must be admin) whether this user currently boosts the chat."""
    res = await bot.get_user_chat_boosts(target, uid)
    now = datetime.now(timezone.utc)
    for b in res.boosts or []:
        exp = b.expiration_date
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp is None or exp > now:
            return True
    return False


async def is_member(bot: Bot, target, uid: int) -> bool:
    """True if the user is really inside the chat. May raise Telegram errors."""
    m = await bot.get_chat_member(target, uid)
    if m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        return True
    if m.status == ChatMemberStatus.RESTRICTED:
        return bool(getattr(m, "is_member", False))
    return False

import asyncio
import time
import traceback
import uvicorn
from sqlalchemy import select

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.types import BotCommand, ErrorEvent, Message, CallbackQuery
from aiogram.fsm.storage.redis import RedisStorage

from config import BOT_TOKEN, PORT, ADMIN_IDS, BusinessRules, logger
from database import AsyncSessionLocal, redis_client, User, ensure_new_tables
from localization import L10N
from helpers import alert_admins, esc
from web import fastapi_app
from workers import retention_worker

# Handlers import
from handlers.start import router as start_router
from handlers.cabinet import router as cabinet_router
from handlers.earnings import router as earnings_router
from handlers.promote import router as promote_router
from handlers.promote_channel import router as promote_channel_router
from handlers.promote_group import router as promote_group_router
from handlers.promote_my_tasks import router as promote_my_tasks_router
from handlers.checks import router as checks_router
from handlers.static import router as static_router
from handlers.admin import router as admin_router
from handlers.deposits import router as deposits_router
from handlers.vouchers import router as vouchers_router
from handlers.history import router as history_router
from handlers.proofs import router as proofs_router
from handlers.broadcast import router as broadcast_router


class DBSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with AsyncSessionLocal() as session:
            data["session"] = session
            return await handler(event, data)


MENU_TEXTS = {
    v for lang in L10N.T.values() for k, v in lang.items() if k.startswith("menu_")
} | {"❌ বাতিল", "❌ Cancel"}


class GuardMiddleware(BaseMiddleware):
    """Runs for every message/callback:
    1. per-user flood limit (stops scripts hammering the bot)
    2. blocked users are cut off completely (admins excepted)
    3. tapping a main-menu button always aborts a half-finished input flow
    """

    async def __call__(self, handler, event, data):
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)
        uid = tg_user.id

        key = f"rl:{uid}:{int(time.time())}"
        hits = await redis_client.incr(key)
        if hits == 1:
            await redis_client.expire(key, 3)
        if hits > BusinessRules.RATE_LIMIT_PER_SECOND:
            if isinstance(event, CallbackQuery) and hits == BusinessRules.RATE_LIMIT_PER_SECOND + 1:
                try:
                    await event.answer("⚠️ Too fast!", show_alert=False)
                except Exception:
                    pass
            return None

        if uid not in ADMIN_IDS:
            session = data["session"]
            blocked = (await session.execute(select(User.is_blocked).where(User.id == uid))).scalar()
            if blocked:
                if isinstance(event, Message):
                    await event.answer("🚫 Your account is blocked.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 Your account is blocked.", show_alert=True)
                return None

        if isinstance(event, Message) and event.text in MENU_TEXTS:
            state = data.get("state")
            if state is not None:
                await state.clear()

        return await handler(event, data)


async def main():
    await ensure_new_tables()
    storage = RedisStorage(redis=redis_client)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=storage)

    # Middleware registration
    dp.message.outer_middleware(DBSessionMiddleware())
    dp.callback_query.outer_middleware(DBSessionMiddleware())
    dp.pre_checkout_query.outer_middleware(DBSessionMiddleware())
    dp.message.outer_middleware(GuardMiddleware())
    dp.callback_query.outer_middleware(GuardMiddleware())

    # Routers registration
    dp.include_router(start_router)
    dp.include_router(cabinet_router)
    dp.include_router(earnings_router)
    dp.include_router(promote_router)
    dp.include_router(promote_channel_router)
    dp.include_router(promote_group_router)
    dp.include_router(promote_my_tasks_router)
    dp.include_router(checks_router)
    dp.include_router(static_router)
    dp.include_router(deposits_router)
    dp.include_router(vouchers_router)
    dp.include_router(history_router)
    dp.include_router(proofs_router)
    dp.include_router(broadcast_router)
    dp.include_router(admin_router)

    await bot.set_my_commands([BotCommand(command="start", description="🚀 Start")])

    @dp.error()
    async def on_error(event: ErrorEvent):
        logger.exception(f"Error: {event.exception}")
        tb = "".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__))[-3000:]
        await alert_admins(bot, f"🚨 <b>Error</b>\n<pre>{esc(tb)}</pre>")
        return True

    # Start background retention worker
    worker_task = asyncio.create_task(retention_worker(bot))  # noqa: F841 (keep reference)

    # Start FastAPI server in background or via uvicorn runner
    uv_config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=PORT, log_level="warning")
    server = uvicorn.Server(uv_config)

    logger.info("Bot starting...")
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")

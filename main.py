import asyncio
import traceback
import uvicorn

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.types import BotCommand, ErrorEvent
from aiogram.fsm.storage.redis import RedisStorage

from config import BOT_TOKEN, PORT, logger
from database import AsyncSessionLocal, redis_client
from helpers import alert_admins, esc
from web import fastapi_app
from workers import retention_worker

# Handlers import (Puraton ebong Notun shobai)
from handlers.start import router as start_router
from handlers.base_menu import router as base_menu_router
from handlers.cabinet import router as cabinet_router
from handlers.earnings import router as earnings_router
from handlers.earn_channel import router as earn_channel_router
from handlers.earn_group import router as earn_group_router
from handlers.earn_view import router as earn_view_router
from handlers.earn_bot import router as earn_bot_router
from handlers.earn_reaction import router as earn_reaction_router
from handlers.earn_boost import router as earn_boost_router
from handlers.promote import router as promote_router
from handlers.checks import router as checks_router
from handlers.sub_check import router as sub_check_router
from handlers.bots_partners import router as bots_partners_router
from handlers.links import router as links_router
from handlers.guide import router as guide_router
from handlers.static import router as static_router
from handlers.admin import router as admin_router


class DBSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with AsyncSessionLocal() as session:
            data["session"] = session
            return await handler(event, data)


async def main():
    storage = RedisStorage(redis=redis_client)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=storage)

    # Middleware registration
    dp.message.outer_middleware(DBSessionMiddleware())
    dp.callback_query.outer_middleware(DBSessionMiddleware())

    # Routers registration (Order is important for proper handling)
    dp.include_router(start_router)
    dp.include_router(base_menu_router)
    dp.include_router(cabinet_router)
    dp.include_router(earnings_router)
    dp.include_router(earn_channel_router)
    dp.include_router(earn_group_router)
    dp.include_router(earn_view_router)
    dp.include_router(earn_bot_router)
    dp.include_router(earn_reaction_router)
    dp.include_router(earn_boost_router)
    dp.include_router(promote_router)
    dp.include_router(checks_router)
    dp.include_router(sub_check_router)
    dp.include_router(bots_partners_router)
    dp.include_router(links_router)
    dp.include_router(guide_router)
    dp.include_router(static_router)
    dp.include_router(admin_router)

    await bot.set_my_commands([
        BotCommand(command="start", description="🚀 Start"),
        BotCommand(command="admin", description="👑 Admin"),
    ])

    @dp.error()
    async def on_error(event: ErrorEvent):
        logger.exception(f"Error: {event.exception}")
        tb = "".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__))[-3000:]
        await alert_admins(bot, f"🚨 <b>Error</b>\n<pre>{esc(tb)}</pre>")
        return True

    # Start background retention worker
    asyncio.create_task(retention_worker(bot))

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

import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode

from core.config import (
    BOT_TOKEN,
    TELEGRAM_BOT_API_BASE_URL,
    TELEGRAM_BOT_API_IS_LOCAL,
)
from core.database import init_db
from handlers.routes import router
from services.profile_watcher import TikTokProfileWatcher


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )

    await init_db()

    bot_kwargs = {
        'token': BOT_TOKEN,
        'default': DefaultBotProperties(parse_mode=ParseMode.HTML),
    }

    if TELEGRAM_BOT_API_BASE_URL:
        api = TelegramAPIServer.from_base(
            TELEGRAM_BOT_API_BASE_URL,
            is_local=TELEGRAM_BOT_API_IS_LOCAL,
        )
        bot_kwargs['session'] = AiohttpSession(api=api)
        logging.info('Using custom Bot API endpoint: %s', TELEGRAM_BOT_API_BASE_URL)

    bot = Bot(**bot_kwargs)
    dp = Dispatcher()
    profile_watcher = TikTokProfileWatcher()
    watcher_task = None

    dp.include_router(router)

    try:
        watcher_task = asyncio.create_task(profile_watcher.run(bot))
        await dp.start_polling(bot)
    finally:
        if watcher_task:
            watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher_task
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")

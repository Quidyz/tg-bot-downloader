import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import Config
from bot.db.database import create_connection
from bot.db.repo import Repo
from bot.handlers import admin, links, premium, start
from bot.middlewares.access import UserTrackMiddleware

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = Config()
    conn = await create_connection(config.db_path)
    repo = Repo(conn)
    await repo.ensure_default_settings()

    session = None
    if config.telegram_api_base:
        session = AiohttpSession(
            api=TelegramAPIServer.from_base(config.telegram_api_base)
        )
        logger.info("Использую локальный Bot API: %s", config.telegram_api_base)

    bot = Bot(
        token=config.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage(), repo=repo, config=config)

    track = UserTrackMiddleware()
    dp.message.outer_middleware(track)
    dp.callback_query.outer_middleware(track)

    # Порядок важен: links последним — у него catch-all подсказка в личке
    dp.include_routers(start.router, admin.router, premium.router, links.router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

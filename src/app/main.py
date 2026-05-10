import asyncio
import logging
import sys
from pathlib import Path

# Root yo'lini sozlash
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import uvicorn
from src.app.api.app import app

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from logs.logger_conf import setup_logging
from src.app.core.config import load_config
from src.app.database.base import Base, Database
from src.app.bot.middleware import register_middleware

async def start_api():
    config = uvicorn.Config(
        app, host="0.0.0.0", port=8000, loop="asyncio", log_level="info"
    )

    server = uvicorn.Server(config)
    await server.serve()


async def main():
    try:
        # settings = load_config()
        # dp = Dispatcher()
        # dsn = settings.construct_postgresql_url()
        # db = Database(dsn)

        # Base.metadata.create_all odatda migratsiyalar orqali qilinadi, 
        # # lekin birinchi marta ishga tushirish uchun bu yerda ham qoldirish mumkin.
        # async with db.engine.begin() as conn:
        #      await conn.run_sync(Base.metadata.create_all)
        #
        # dp["settings"] = settings
        # dp["session_pool"] = db.session_factory
        #
        # # Middlewarelarni ro'yxatdan o'tkazish
        # register_middleware(dp, db.session_factory)
        #
        # bot = Bot(
        #     token=settings.bot_token,
        #     default=DefaultBotProperties(parse_mode="HTML"),
        # )

        # FastAPI state
        # app.state.db = db
        # app.state.bot = bot
        # app.state.dp = dp
        # app.state.user_cache = {} # {str(id): name}

        # Botni polling rejimida ishga tushirish (background task sifatida)
        # asyncio.create_task(dp.start_polling(bot))

        await start_api()

    except Exception as e:
        print(f"\n❌ STARTUP ERROR: {e}")
        logging.exception(e)


if __name__ == "__main__":
    setup_logging("logs/logger.yml")
    asyncio.run(main())

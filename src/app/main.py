"""
Asosiy kirish nuqtasi: FastAPI + Telegram bot (polling) birga ishga tushadi.

Ishga tushirish:
    python -m src.app.main
"""
import asyncio
import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Logging dan oldin: Windows cp1251 konsolida emoji xatosini oldini olish
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import uvicorn

from logs.logger_conf import setup_logging
from src.app.api.app import app, shutdown_application, startup_application
from src.app.core.config import load_config


async def start_api() -> None:
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        loop="asyncio",
        log_level="info",
        forwarded_allow_ips="*",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    try:
        settings = load_config()
        # DB + Telegram bot polling — shu yerda (app.py lifespan emas)
        await startup_application(
            app, settings, start_bot_polling=True
        )
        app.state.bootstrapped_from_main = True

        print("[OK] Server http://0.0.0.0:8000", flush=True)
        await start_api()
    except Exception as e:
        print(f"\n[ERROR] STARTUP ERROR: {e}")
        logging.exception(e)
    finally:
        if getattr(app.state, "bootstrapped_from_main", False):
            await shutdown_application(app)


if __name__ == "__main__":
    setup_logging("logs/logger.yml")
    asyncio.run(main())

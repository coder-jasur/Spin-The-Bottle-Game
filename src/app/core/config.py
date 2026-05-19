from dataclasses import dataclass
from pathlib import Path

import environs
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

env = environs.Env()
env.read_env(_PROJECT_ROOT / ".env")


@dataclass
class Settings:
    bot_token: str
    main_admin_id: int
    redis_url: str
    secret_key: str
    algorithm: str
    db_name: str
    db_user: str
    db_password: str
    db_host: str
    db_port: int = 5432
    # Telegram Mini App taklif havolalari (t.me/<bot>/<slug>?startapp=<referral_id>)
    telegram_miniapp_bot: str = "SpinbottleTgBot"
    telegram_miniapp_slug: str = "spin_bottle"
    telegram_invite_share_text: str = (
        "Please help me get a free airdrop up to $1 worth.\n"
        "You just need to collect 50 kisses in this game on Telegram."
    )
    # Bot: webhook (bo'sh bo'lsa polling) va webhook maxfiylik tokeni
    telegram_webhook_secret: str = ""
    telegram_use_polling: bool = True
    # Cloudflare tunnel / production: https://your-domain.com (menyu → /index)
    telegram_webapp_url: str = ""
    # Stars chekidan oldin banner (Telegram photo file_id, photo[-1])
    telegram_stars_banner_file_id: str = ""
    # /start welcome banner (Telegram photo file_id)
    telegram_start_banner_file_id: str = ""
    # Sayt foydalanuvchilari Stars sotib olish uchun (@username, @ siz)
    telegram_support_username: str = "SpinTheBottleSupport"
    # Har 24 soatda adminlarga DB backup (admin paneldagi kabi)
    scheduled_backup_enabled: bool = True
    scheduled_backup_interval_hours: float = 24.0
    # Xavfsizlik
    rate_limit_enabled: bool = True
    trusted_hosts: str = "*"
    ws_max_messages_per_10s: int = 80

    def construct_postgresql_url(self):
        postgresql_dsn = (
            f"postgresql+asyncpg://"
            f"{self.db_user}:"
            f"{self.db_password}@"
            f"{self.db_host}:"
            f"{self.db_port}/"
            f"{self.db_name}"
        )
        return postgresql_dsn


def load_config() -> Settings:
    return Settings(
        bot_token=env.str("BOT_TOKEN"),
        main_admin_id=env.int("MAIN_ADMIN_ID"),
        redis_url=env.str("REDIS_URL"),
        secret_key=env.str("SECRET_KEY"),
        algorithm=env.str("ALGORITHM"),
        db_name=env.str("POSTGRES_DB"),
        db_user=env.str("POSTGRES_USER"),
        db_password=env.str("POSTGRES_PASSWORD"),
        db_host=env.str("POSTGRES_HOST"),
        db_port=env.int("POSTGRES_PORT"),
        telegram_miniapp_bot=env.str("TELEGRAM_MINIAPP_BOT", "SpinbottleTgBot"),
        telegram_miniapp_slug=env.str("TELEGRAM_MINIAPP_SLUG", "spin_bottle"),
        telegram_invite_share_text=env.str(
            "TELEGRAM_INVITE_SHARE_TEXT",
            (
                "Please help me get a free airdrop up to $1 worth.\n"
                "You just need to collect 50 kisses in this game on Telegram."
            ),
        ),
        telegram_webhook_secret=env.str("TELEGRAM_WEBHOOK_SECRET", ""),
        telegram_use_polling=env.bool("TELEGRAM_USE_POLLING", True),
        telegram_webapp_url=env.str(
            "TELEGRAM_WEBAPP_URL", "https://spinthebottletg.com"
        ).strip().rstrip("/"),
        telegram_stars_banner_file_id=env.str(
            "TELEGRAM_STARS_BANNER_FILE_ID", ""
        ).strip(),
        telegram_start_banner_file_id=env.str(
            "TELEGRAM_START_BANNER_FILE_ID", ""
        ).strip(),
        telegram_support_username=env.str(
            "TELEGRAM_SUPPORT_USERNAME", "SpinTheBottleSupport"
        ).strip().lstrip("@"),
        scheduled_backup_enabled=env.bool("SCHEDULED_BACKUP_ENABLED", True),
        scheduled_backup_interval_hours=env.float(
            "SCHEDULED_BACKUP_INTERVAL_HOURS", 24.0
        ),
        rate_limit_enabled=env.bool("RATE_LIMIT_ENABLED", True),
        trusted_hosts=env.str(
            "TRUSTED_HOSTS",
            "spinthebottletg.com,www.spinthebottletg.com,localhost,127.0.0.1",
        ).strip()
        or "spinthebottletg.com,www.spinthebottletg.com",
        ws_max_messages_per_10s=env.int("WS_MAX_MESSAGES_PER_10S", 80),
    )

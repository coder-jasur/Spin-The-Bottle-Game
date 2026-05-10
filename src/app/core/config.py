from dataclasses import dataclass
import environs

env = environs.Env()
env.read_env()


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
        db_port=env.int("POSTGRES_PORT")


    )

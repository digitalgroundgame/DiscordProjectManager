from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENVIRONMENT: str = "development"

    # Discord Configuration
    DISCORD_BOT_TOKEN: str = ""
    DISCORD_CLIENT_ID: str = ""
    DISCORD_GUILD_ID: int | None = None

    @field_validator("DISCORD_GUILD_ID", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "" or v is None:
            return None
        return v

    # Database Configuration
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/dgg_pm"
    AUTO_RUN_MIGRATIONS: bool = True

    # Outbox Worker Configuration
    OUTBOX_POLL_INTERVAL_SECONDS: float = 5.0
    OUTBOX_BATCH_SIZE: int = 10

    # API / Health Server Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    DEBUG: bool = False

    # If set, /metrics requires `Authorization: Bearer <API_METRICS_TOKEN>`.
    # When empty, /metrics is left open for local/dev use.
    API_METRICS_TOKEN: str = ""


settings = Settings()

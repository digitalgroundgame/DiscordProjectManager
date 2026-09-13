from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration and static defaults.

    Secrets and deployment targets (such as DISCORD_BOT_TOKEN and DATABASE_URL)
    are loaded dynamically from `.env` or environment variables.

    Operational tuning parameters (Outbox worker parameters, Gateway supervisor
    retry policies, API host/port) have static production-ready defaults defined
    below, but can still be optionally overridden via environment variables.
    """

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
    AUTO_RUN_MIGRATIONS: bool = False

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return "postgresql+asyncpg://" + v[len("postgres://") :]
            if v.startswith("postgresql://") and not v.startswith("postgresql+"):
                return "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v

    # Outbox Worker Configuration
    OUTBOX_POLL_INTERVAL_SECONDS: float = 5.0
    OUTBOX_BATCH_SIZE: int = 10
    OUTBOX_MAX_RETRIES: int = 150
    OUTBOX_BACKOFF_CAP_SECONDS: float = 600.0
    OUTBOX_MAX_RETENTION_HOURS: float = 48.0
    OUTBOX_RECLAIM_LOOKBACK_HOURS: float = 24.0

    # API / Health Server Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    DEBUG: bool = False

    # If set, /metrics requires `Authorization: Bearer <API_METRICS_TOKEN>`.
    # When empty, /metrics is left open for local/dev use.
    API_METRICS_TOKEN: str = ""

    # Command Sync & Startup Backoff Configuration
    SYNC_COMMANDS_ON_STARTUP: bool | None = None
    STARTUP_CRASH_BACKOFF_SECONDS: float = 30.0

    # Gateway Supervisor Configuration
    GATEWAY_RETRY_INITIAL_DELAY_SECONDS: float = 2.0
    GATEWAY_RETRY_MAX_DELAY_SECONDS: float = 60.0
    GATEWAY_RETRY_BACKOFF_FACTOR: float = 2.0
    GATEWAY_RETRY_JITTER: float = 0.2
    GATEWAY_MAX_RETRIES: int | None = None

    @field_validator("GATEWAY_MAX_RETRIES", mode="before")
    @classmethod
    def empty_str_to_none_retries(cls, v):
        if v == "" or v is None:
            return None
        return v

    @model_validator(mode="after")
    def compute_sync_commands_on_startup(self) -> "Settings":
        if self.SYNC_COMMANDS_ON_STARTUP is None:
            self.SYNC_COMMANDS_ON_STARTUP = self.ENVIRONMENT.lower() == "development"
        return self


settings = Settings()

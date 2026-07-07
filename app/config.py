from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    MONGO_URI: str
    PG_DSN: str
    REDIS_URL: str
    LLM_API_KEY: str
    LLM_BASE_URL: str
    LLM_MODEL: str
    # Per-attempt LLM timeout (seconds). Default is a strict production SLA;
    # override in .env for slow local backends (e.g. tinyllama on CPU).
    LLM_ATTEMPT_TIMEOUT: int = 15
    # Cap on generated tokens. None = provider default (unbounded); set a value
    # to bound generation latency on slow models.
    LLM_MAX_TOKENS: int | None = None


settings = Settings()

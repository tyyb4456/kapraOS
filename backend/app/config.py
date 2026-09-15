"""Application configuration.

All configuration is sourced from environment variables (optionally loaded
from a local `.env` file). Nothing here should ever hold a real secret -
`.env` is git-ignored and `.env.example` documents the expected shape.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings, populated from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "KapraOS API"
    app_env: str = "local"
    debug: bool = False

    # Async SQLAlchemy URL, e.g.
    # postgresql+asyncpg://user:password@host:5432/dbname
    # `Field(...)` marks it required while giving mypy an explicit annotation,
    # since the value is supplied by the environment (or .env) at runtime.
    database_url: str = Field(...)

    # Verbose SQL logging - keep off outside of local debugging.
    db_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Cached so we don't re-parse the environment on every call/request, while
    still going through the standard dependency-injection friendly getter
    (tests can call `get_settings.cache_clear()` if they need to reload).
    """

    return Settings()

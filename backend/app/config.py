"""Application configuration.

All configuration is sourced from environment variables (optionally loaded
from a local `.env` file). Nothing here should ever hold a real secret -
`.env` is git-ignored and `.env.example` documents the expected shape.
"""

from functools import lru_cache

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

    database_url: str
    db_echo: bool = False

    # Shop timezone for date-based reporting (e.g. "Asia/Karachi")
    shop_timezone: str = "UTC"

    # Clerk configuration
    clerk_secret_key: str = ""
    clerk_jwt_key: str = ""
    clerk_authorized_parties: str = ""

    @property
    def clerk_authorized_parties_list(self) -> list[str]:
        """Parse authorized parties from comma-separated env var."""
        if not self.clerk_authorized_parties:
            return []
        return [p.strip() for p in self.clerk_authorized_parties.split(",") if p.strip()]

    @property
    def shop_tz(self) -> str:
        """Return the configured shop timezone for datetime operations."""
        return self.shop_timezone


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Cached so we don't re-parse the environment on every call/request, while
    still going through the standard dependency-injection friendly getter
    (tests can call `get_settings.cache_clear()` if they need to reload).
    """

    return Settings()

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

    # Redis cache (Step 10). Empty string = no external Redis configured;
    # the cache layer then uses a process-local in-memory backend with the
    # same TTL/invalidation semantics (safe for local dev and tests).
    # Production should set e.g. REDIS_URL=redis://:password@host:6379/0
    # via the environment (never hardcoded, never committed).
    redis_url: str = ""
    # Master switch: when False, all cache reads are skipped and all
    # invalidations are no-ops (PostgreSQL remains authoritative).
    cache_enabled: bool = True
    # When True, every cache hit/miss/set/invalidation is logged at INFO
    # (key prefix only, never values) so you can watch Redis activity in
    # the terminal. Off by default to avoid flooding production logs.
    cache_log_hits: bool = True

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

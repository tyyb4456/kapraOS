"""Minimal AI configuration.

Follows the existing ``app.config.Settings`` pattern: everything comes
from the environment, nothing secret is hardcoded. The model is served
through xKiro (OpenAI-compatible gateway): only the base URL, key, and
model id are configured here — ``langchain-openai`` reads them from this
object, never from hardcoded values.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AISettings(BaseSettings):
    """Process-wide AI settings, populated from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Model identifier in LangChain ``provider:model`` form, e.g.
    # ``"openai:gpt-5.5"``. Only used when actually invoking the agent
    # with real credentials; Step 1 tests construct the agent with an
    # offline fake model instead.
    ai_model: str = "openai:gpt-5.5"

    # xKiro gateway (OpenAI-compatible). Key lives in ``XKIRO_API_KEY``
    # inside ``backend/.env`` (gitignored) — never commit it, never
    # hardcode it. Model id is ``vendor/model`` from the xKiro dashboard.
    xkiro_api_key: str = ""
    xkiro_base_url: str = "https://api.xkiro.com/v1"
    xkiro_model: str = "mistralai/mistral-large-2512"


@lru_cache
def get_ai_settings() -> AISettings:
    """Return a cached :class:`AISettings` instance."""
    return AISettings()

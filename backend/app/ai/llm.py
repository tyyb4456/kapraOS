"""Chat-model factory for the KapraOS AI layer.

The model is served through xKiro, an OpenAI-compatible gateway, so the
regular ``langchain-openai`` chat model is pointed at xKiro with two
settings: base URL + API key (per xKiro's own OpenAI-SDK guide). The key
is read from :class:`app.ai.config.AISettings` (``XKIRO_API_KEY`` in
``backend/.env``) — constructing the client performs no network I/O, so
this module is safe to import and unit-test offline.
"""

from langchain_openai import ChatOpenAI

from app.ai.config import AISettings, get_ai_settings


class MissingXKiroKeyError(ValueError):
    """``XKIRO_API_KEY`` is not set — the xKiro model cannot be built."""


def build_xkiro_llm(*, settings: AISettings | None = None) -> ChatOpenAI:
    """Build the xKiro-backed chat model from settings.

    Raises :class:`MissingXKiroKeyError` when no key is configured, with
    instructions instead of a cryptic auth failure at invoke time.
    """
    resolved = settings if settings is not None else get_ai_settings()
    if not resolved.xkiro_api_key:
        raise MissingXKiroKeyError(
            "XKIRO_API_KEY is not set. Put your xKiro key in backend/.env "
            "(gitignored, never commit it): XKIRO_API_KEY=<your-key>."
        )
    return ChatOpenAI(
        model=resolved.xkiro_model,
        api_key=resolved.xkiro_api_key,
        base_url=resolved.xkiro_base_url,
    )


__all__ = ["MissingXKiroKeyError", "build_xkiro_llm"]

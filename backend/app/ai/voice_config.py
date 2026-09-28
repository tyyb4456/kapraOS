"""LiveKit voice configuration (Step 11).

Follows the existing ``app.config.Settings`` / ``app.ai.config.AISettings``
pattern: everything comes from the environment, nothing secret is
hardcoded. ``backend/.env`` stays gitignored; ``backend/.env.example``
documents the expected shape.

Only the LiveKit Cloud connection (URL + API key + secret, all from the
LiveKit Cloud dashboard) is required to enable voice. STT/TTS model ids
default to the current documented LiveKit Inference models (see
https://docs.livekit.io/agents/models/) and remain replaceable via
environment variables — no KapraOS business logic depends on them.

Wire-up summary::

    Browser mic -> LiveKit Cloud -> STT -> Master Deep Agent
        -> existing tools/services -> reply text -> TTS -> speaker

Voice is another interface onto the SAME Master Agent; this module holds
transport/model configuration only.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class VoiceSettings(BaseSettings):
    """Process-wide LiveKit voice settings, populated from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LiveKit Cloud connection (https://cloud.livekit.io → project → API keys).
    # Empty = voice disabled; the backend then answers 503 on voice routes
    # with a clear development error instead of failing obscurely.
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # Agent name the worker registers as and the token endpoint dispatches.
    # Must match the ``agent_name`` in ``app.ai.voice_agent``.
    voice_agent_name: str = "kapraos-voice"

    # STT-LLM-TTS pipeline (LiveKit Inference model ids — provider
    # independent, swappable without touching business logic).
    #
    # STT default: Deepgram Nova-3. Rationale (verified against the current
    # https://docs.livekit.io/agents/models/stt/ table): it is the Inference
    # STT with the widest language coverage that explicitly lists Urdu
    # (``ur``) alongside English, Hindi and Arabic, plus a ``multi`` mode
    # for English+Urdu mixed speech and Roman Urdu transliterations. The
    # quickstart default (AssemblyAI Universal-3.5 Pro, ``en``-pinned) would
    # force English-only transcription and drop Urdu input.
    voice_stt_model: str = "deepgram/nova-3"
    # ``multi`` = auto-detect across the model's supported languages
    # (covers en/ur/hi/ar mixes common in Pakistani shops). Pin to ``"ur"``
    # or ``"en"`` only when debugging a single language.
    voice_stt_language: str = "multi"
    # Session LLM placeholder. The voice path overrides ``llm_node`` to
    # delegate every turn to the KapraOS Master Deep Agent, so this model is
    # only a constructor fallback and never the brain. Kept on Inference so
    # no extra provider key is needed.
    voice_llm_model: str = "google/gemma-4-31b-it"
    # TTS default: Cartesia Sonic 3.6. Rationale (verified against the
    # current https://docs.livekit.io/agents/models/tts/ table): it is the
    # Inference TTS that explicitly lists Urdu (``ur``) support with an EU
    # endpoint. The quickstart default (Fish Audio S2.1 Pro) is
    # English/Chinese/Japanese-centric and lists no Urdu support.
    voice_tts_model: str = "cartesia/sonic-3.6"
    # Provider voice id. Empty = provider default voice. Set this when the
    # shop picks a preferred voice from the provider catalogue; never
    # hardcode customer data here.
    voice_tts_voice: str = ""


@lru_cache
def get_voice_settings() -> VoiceSettings:
    """Return a cached :class:`VoiceSettings` instance."""
    return VoiceSettings()


def is_voice_configured(*, settings: VoiceSettings | None = None) -> bool:
    """True when LiveKit Cloud credentials are present (voice enabled)."""
    resolved = settings or get_voice_settings()
    return bool(
        resolved.livekit_url
        and resolved.livekit_api_key
        and resolved.livekit_api_secret
    )


def require_voice_settings(*, settings: VoiceSettings | None = None) -> VoiceSettings:
    """Return settings or raise a clear development error when unconfigured."""
    resolved = settings or get_voice_settings()
    if not is_voice_configured(settings=resolved):
        raise VoiceNotConfiguredError(
            "LiveKit voice is not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY "
            "and LIVEKIT_API_SECRET in backend/.env from your LiveKit Cloud "
            "project (https://cloud.livekit.io), then restart the API server. "
            "See backend/.env.example."
        )
    return resolved


class VoiceNotConfiguredError(RuntimeError):
    """LiveKit credentials are missing; voice routes answer 503 with this."""


__all__ = [
    "VoiceNotConfiguredError",
    "VoiceSettings",
    "get_voice_settings",
    "is_voice_configured",
    "require_voice_settings",
]

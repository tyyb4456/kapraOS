"""xKiro model wiring tests (offline: construction only, no network calls).

Building a ``ChatOpenAI`` client performs no I/O — network happens only
at ``invoke()`` time — so these tests prove the env -> settings ->
client wiring without spending tokens or needing a real key.
"""

import pytest

from app.ai.agent import build_master_agent, resolve_model
from app.ai.config import AISettings, get_ai_settings
from app.ai.llm import MissingXKiroKeyError, build_xkiro_llm


def test_settings_read_xkiro_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XKIRO_API_KEY", "xk-test-123")
    monkeypatch.setenv("XKIRO_MODEL", "deepseek/deepseek-v4.1-flash:free")
    get_ai_settings.cache_clear()
    try:
        settings = get_ai_settings()
        assert settings.xkiro_api_key == "xk-test-123"
        assert settings.xkiro_model == "deepseek/deepseek-v4.1-flash:free"
        assert settings.xkiro_base_url == "https://api.xkiro.com/v1"
    finally:
        get_ai_settings.cache_clear()


def test_build_xkiro_llm_points_at_gateway() -> None:
    llm = build_xkiro_llm(
        settings=AISettings(
            xkiro_api_key="xk-test-123",
            xkiro_base_url="https://api.xkiro.com/v1",
            xkiro_model="deepseek/deepseek-v4.1-flash:free",
        )
    )
    assert llm.model_name == "deepseek/deepseek-v4.1-flash:free"
    assert llm.openai_api_base == "https://api.xkiro.com/v1"


def test_build_xkiro_llm_missing_key_raises_helpful_error() -> None:
    with pytest.raises(MissingXKiroKeyError, match="XKIRO_API_KEY"):
        build_xkiro_llm(settings=AISettings(xkiro_api_key=""))


def test_resolve_model_auto_uses_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin every xKiro var: the developer's real backend/.env may set its
    # own key/model, and tests must never depend on ambient config.
    monkeypatch.setenv("XKIRO_API_KEY", "xk-test-123")
    monkeypatch.setenv("XKIRO_MODEL", "deepseek/deepseek-v4.1-flash:free")
    monkeypatch.setenv("XKIRO_BASE_URL", "https://api.xkiro.com/v1")
    get_ai_settings.cache_clear()
    try:
        llm = resolve_model("auto")
        assert llm.model_name == "deepseek/deepseek-v4.1-flash:free"
        assert llm.openai_api_base == "https://api.xkiro.com/v1"
    finally:
        get_ai_settings.cache_clear()


def test_resolve_model_passes_other_values_through() -> None:
    assert resolve_model(None) is None
    assert resolve_model("openai:gpt-5.5") == "openai:gpt-5.5"


def test_master_agent_constructs_with_xkiro_model() -> None:
    llm = build_xkiro_llm(
        settings=AISettings(
            xkiro_api_key="xk-test-123",
            xkiro_model="deepseek/deepseek-v4.1-flash:free",
        )
    )
    agent = build_master_agent(model=llm)
    assert hasattr(agent, "invoke")

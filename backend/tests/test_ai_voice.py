"""Step 11 voice integration tests (no microphone / LiveKit Cloud needed).

Covers the task's required verifications against the transport boundary
with fake chat models — the realtime audio path (STT/TTS/room) is mocked
out, while the brain path (Master Agent factory, tools, HITL, tenant
isolation, Redis-backed reads) runs for real:

1. Voice agent module imports successfully.
2. LiveKit configuration validation works.
3. Missing configuration produces a clear development error.
4. Tenant identity cannot come from arbitrary frontend/voice metadata.
5. Existing Master Agent factory is reused.
6. Existing tools remain available.
7. Existing HITL configuration remains active.
8. No duplicate AI tool registry exists.
9. Voice requests can reach the existing agent boundary.
10. Redis-backed read tools remain shared.
11. Existing text ``/ai/chat`` behavior is unchanged.
"""

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import READ_MASTER_TOOL_NAMES, WRITE_MASTER_TOOL_NAMES
from app.ai.voice_adapter import (
    VOICE_DATA_TOPIC,
    VOICE_THREAD_PREFIX,
    VoiceAgentError,
    approval_speech_prompt,
    build_voice_tools,
    new_voice_thread_id,
    resume_voice_turn,
    run_voice_turn,
    to_speech_text,
)
from app.ai.voice_config import (
    VoiceNotConfiguredError,
    VoiceSettings,
    is_voice_configured,
    require_voice_settings,
)
from app.api.ai import get_chat_model
from app.main import app
from app.models import Shop, User, UserRole


class _PlainFakeModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "voice-plain-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_PlainFakeModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(content="Tayyab ka khata Rs. 2,900 hai.")
                )
            ]
        )


class _ToolCallingFakeModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "voice-tool-calling-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ToolCallingFakeModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if any(isinstance(m, ToolMessage) for m in messages):
            message = AIMessage(content="done after tool")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "demo_side_effect",
                        "args": {"note": "voice-probe"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


async def _make_mock_user(api_session: AsyncSession) -> User:
    shop = Shop(name="Voice Shop")
    api_session.add(shop)
    await api_session.flush()
    await api_session.refresh(shop)
    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=shop.id,
        name="Voice Owner",
        email="voice-owner@example.com",
        role=UserRole.OWNER,
    )
    api_session.add(user)
    await api_session.flush()
    await api_session.refresh(user)
    return user


def _empty_creds() -> VoiceSettings:
    return VoiceSettings(livekit_url="", livekit_api_key="", livekit_api_secret="")


def _livekit_creds() -> VoiceSettings:
    return VoiceSettings(
        livekit_url="wss://test.livekit.cloud",
        livekit_api_key="APItestkey",
        livekit_api_secret="testsecret0123456789testsecret0123456789",
    )


class TestVoiceModuleImport:
    def test_voice_agent_module_imports_and_registers(self) -> None:
        from app.ai import voice_agent

        assert voice_agent._LIVEKIT_AVAILABLE is True
        assert voice_agent.server is not None
        assert voice_agent.VOICE_GREETING
        # The shell carries no tools of its own — the Master Agent does.
        agent = voice_agent.KapraVoiceAgent()
        assert getattr(agent, "tools", []) == []

    def test_voice_adapter_has_no_tool_registry(self) -> None:
        from app.ai import voice_adapter, voice_agent

        for module in (voice_adapter, voice_agent):
            for name in dir(module):
                if name.startswith("_"):
                    continue
                obj = getattr(module, name)
                assert not isinstance(obj, BaseTool), (
                    f"{module.__name__}.{name} is a second tool registry"
                )

    def test_master_registries_have_expected_shape(self) -> None:
        assert len(READ_MASTER_TOOL_NAMES) == 8
        assert len(WRITE_MASTER_TOOL_NAMES) == 7
        assert VOICE_DATA_TOPIC == "kapraos.voice"


class TestVoiceConfig:
    def test_defaults_are_documented_inference_models(self) -> None:
        settings = VoiceSettings()
        assert settings.voice_stt_model == "deepgram/nova-3"
        assert settings.voice_stt_language == "multi"
        assert settings.voice_tts_model == "cartesia/sonic-3.6"
        assert settings.voice_agent_name == "kapraos-voice"

    def test_unconfigured_by_default(self) -> None:
        assert is_voice_configured(settings=_empty_creds()) is False

    def test_configured_with_credentials(self) -> None:
        assert is_voice_configured(settings=_livekit_creds()) is True

    def test_missing_config_raises_clear_dev_error(self) -> None:
        with pytest.raises(VoiceNotConfiguredError, match="LIVEKIT_URL"):
            require_voice_settings(settings=_empty_creds())

    def test_real_settings_reflect_the_environment(self) -> None:
        # backend/.env may or may not carry LiveKit secrets (local .env is
        # gitignored): the settings object must simply mirror it, never crash.
        import os

        settings = VoiceSettings()
        expected = bool(
            os.getenv("LIVEKIT_URL")
            and os.getenv("LIVEKIT_API_KEY")
            and os.getenv("LIVEKIT_API_SECRET")
        )
        assert is_voice_configured(settings=settings) is expected


class TestVoiceTokenEndpoint:
    @pytest.mark.asyncio
    async def test_token_requires_auth(self, client: AsyncClient) -> None:
        response = await client.post("/ai/voice/token", json={})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_config_requires_auth(self, client: AsyncClient) -> None:
        response = await client.get("/ai/voice/config")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_config_is_503_with_guidance(
        self, mocked_api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        await _make_mock_user(api_session)
        with patch("app.api.voice.get_voice_settings", return_value=_empty_creds()):
            response = await mocked_api_client.post("/ai/voice/token", json={})
        assert response.status_code == 503
        assert "LIVEKIT_URL" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_config_reports_disabled_without_creds(
        self, mocked_api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        await _make_mock_user(api_session)
        with patch("app.api.voice.get_voice_settings", return_value=_empty_creds()):
            response = await mocked_api_client.get("/ai/voice/config")
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_token_mint_binds_authenticated_tenant(
        self, mocked_api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        user = await _make_mock_user(api_session)
        with patch("app.api.voice.get_voice_settings", return_value=_livekit_creds()):
            # Forged tenant/room fields must be ignored — they are not even
            # part of the request model.
            response = await mocked_api_client.post(
                "/ai/voice/token",
                json={
                    "shop_id": str(uuid.uuid4()),
                    "room_name": "evil-room",
                    "participant_identity": "user-evil",
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["server_url"] == "wss://test.livekit.cloud"
        assert body["participant_token"]
        assert body["room_name"].startswith("voice-")
        assert body["room_name"] != "evil-room"
        assert "evil" not in body["room_name"]
        assert body["thread_id"].startswith(VOICE_THREAD_PREFIX)
        assert body["agent_name"] == "kapraos-voice"
        # Identity is derived from the authenticated user, never the body.
        import jwt as pyjwt

        decoded = pyjwt.decode(
            body["participant_token"], options={"verify_signature": False}
        )
        assert decoded["sub"] == f"user-{user.id}"
        assert decoded["video"]["room"] == body["room_name"]
        assert decoded["video"]["roomJoin"] is True

    @pytest.mark.asyncio
    async def test_config_reports_enabled_with_creds(
        self, mocked_api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        await _make_mock_user(api_session)
        with patch("app.api.voice.get_voice_settings", return_value=_livekit_creds()):
            response = await mocked_api_client.get("/ai/voice/config")
        assert response.status_code == 200
        assert response.json() == {"enabled": True, "agent_name": "kapraos-voice"}


class TestVoiceTenantIsolation:
    def test_parse_voice_identity_accepts_minted_shape(self) -> None:
        from app.ai.voice_agent import parse_voice_identity

        user_id = uuid.uuid4()
        assert parse_voice_identity(f"user-{user_id}") == user_id

    @pytest.mark.parametrize(
        "identity", ["", "evil", "user-", "user-not-a-uuid", "shop-123", "user-1; DROP"]
    )
    def test_parse_voice_identity_rejects_untrusted(self, identity: str) -> None:
        from app.ai.voice_agent import parse_voice_identity

        with pytest.raises(VoiceAgentError):
            parse_voice_identity(identity)

    @pytest.mark.asyncio
    async def test_resolve_voice_user_rejects_unknown(self) -> None:
        from app.ai.voice_agent import resolve_voice_user

        with pytest.raises(VoiceAgentError, match="not provisioned"):
            await resolve_voice_user(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_voice_threads_are_namespaced_per_tenant(
        self, api_session: AsyncSession
    ) -> None:
        from app.api.ai import _namespaced_thread

        user = await _make_mock_user(api_session)
        client_id, namespaced = _namespaced_thread(user, new_voice_thread_id())
        assert namespaced.startswith(f"{user.shop_id}:{user.id}:")
        assert str(user.shop_id) not in client_id


class TestVoiceAdapterBrain:
    @pytest.mark.asyncio
    async def test_voice_turn_reaches_master_agent(
        self, api_session: AsyncSession
    ) -> None:
        user = await _make_mock_user(api_session)
        turn = await run_voice_turn(
            db=api_session,
            user=user,
            model=_PlainFakeModel(),
            transcript="Tayyab ka khata check karo",
        )
        assert turn.status == "done"
        assert turn.thread_id.startswith(VOICE_THREAD_PREFIX)
        # Speech-ready: markdown stripped, facts (numbers) preserved.
        assert "2,900" in turn.reply
        assert turn.pending == []

    @pytest.mark.asyncio
    async def test_voice_turn_rejects_empty_transcript(
        self, api_session: AsyncSession
    ) -> None:
        user = await _make_mock_user(api_session)
        with pytest.raises(ValueError, match="Empty voice transcript"):
            await run_voice_turn(
                db=api_session, user=user, model=_PlainFakeModel(), transcript="   "
            )

    @pytest.mark.asyncio
    async def test_hitl_pause_is_preserved_not_bypassed(
        self, api_session: AsyncSession
    ) -> None:
        from app.ai.tools.registry import (
            demo_side_effect_log,
            reset_demo_side_effect_log,
        )

        user = await _make_mock_user(api_session)
        reset_demo_side_effect_log()
        turn = await run_voice_turn(
            db=api_session,
            user=user,
            model=_ToolCallingFakeModel(),
            transcript="run the demo",
        )
        assert turn.status == "paused"
        assert turn.pending[0]["name"] == "demo_side_effect"
        assert demo_side_effect_log() == []
        assert "approval" in turn.reply

        resumed = await resume_voice_turn(
            db=api_session,
            user=user,
            model=_ToolCallingFakeModel(),
            thread_id=turn.thread_id,
            decisions=[{"type": "approve"}],
        )
        assert resumed.status == "done"
        assert demo_side_effect_log() == ["voice-probe"]
        reset_demo_side_effect_log()

    @pytest.mark.asyncio
    async def test_voice_resume_validates_decisions_like_chat(
        self, api_session: AsyncSession
    ) -> None:
        user = await _make_mock_user(api_session)
        with pytest.raises(Exception, match="[Dd]ecision|message|Unknown"):
            await resume_voice_turn(
                db=api_session,
                user=user,
                model=_PlainFakeModel(),
                thread_id=new_voice_thread_id(),
                decisions=[{"type": "maybe"}],
            )

    @pytest.mark.asyncio
    async def test_voice_tools_are_the_master_registries(
        self, api_session: AsyncSession
    ) -> None:
        from app.ai.state import tenant_context_from_user

        user = await _make_mock_user(api_session)
        tenant = tenant_context_from_user(user)
        read_tools, write_tools = build_voice_tools(api_session, tenant)
        assert {t.name for t in read_tools} == set(READ_MASTER_TOOL_NAMES)
        assert {t.name for t in write_tools} == set(WRITE_MASTER_TOOL_NAMES)
        # No tool takes a tenant/shop argument from the LLM.
        for tool in [*read_tools, *write_tools]:
            schema = tool.get_input_schema().model_fields
            assert "shop_id" not in schema, tool.name
            assert "tenant" not in schema, tool.name

    @pytest.mark.asyncio
    async def test_read_path_uses_shared_cached_tools(
        self, api_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ai import voice_adapter

        calls: list[str] = []
        real_builder = voice_adapter.build_read_tools

        def _counting(db: AsyncSession, tenant: Any) -> Any:
            calls.append(str(getattr(tenant, "shop_id", "")))
            return real_builder(db, tenant)

        monkeypatch.setattr(voice_adapter, "build_read_tools", _counting)
        # Rebind the adapter's module-global reference used by run_voice_turn.
        user = await _make_mock_user(api_session)
        with patch.object(
            voice_adapter, "build_voice_tools", wraps=voice_adapter.build_voice_tools
        ):
            await run_voice_turn(
                db=api_session,
                user=user,
                model=_PlainFakeModel(),
                transcript="stock batao",
            )
        assert calls == [str(user.shop_id)]


class TestSpeechFormatting:
    def test_numbers_are_preserved(self) -> None:
        out = to_speech_text("Outstanding: Rs. 19,500, Paid: Rs. 11,000")
        assert "19,500" in out and "11,000" in out
        assert "Rs" not in out
        assert "rupees" in out

    def test_markdown_and_tables_are_stripped(self) -> None:
        out = to_speech_text(
            "**Customer Account Summary:**\n| Total | Rs. 2,900 |\n- item one\n# Hi"
        )
        assert "**" not in out and "|" not in out and "#" not in out
        assert "2,900" in out

    def test_approval_prompt_names_actions_without_facts(self) -> None:
        prompt = approval_speech_prompt(
            [{"name": "create_sale", "args": {}, "allowed_decisions": ["approve"]}]
        )
        assert "create_sale" in prompt
        assert "approval" in prompt


class TestTextChatUnchanged:
    @pytest.mark.asyncio
    async def test_text_chat_still_replies(
        self, mocked_api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        await _make_mock_user(api_session)
        app.dependency_overrides[get_chat_model] = lambda: _PlainFakeModel()
        try:
            response = await mocked_api_client.post(
                "/ai/chat", json={"message": "hello", "thread_id": uuid.uuid4().hex}
            )
        finally:
            app.dependency_overrides.pop(get_chat_model, None)
        assert response.status_code == 200
        assert response.json()["status"] == "done"

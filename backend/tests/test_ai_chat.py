"""Tests for the live AI chat endpoints (``/ai/chat``, ``/ai/chat/resume``).

Uses bind_tools-capable fake models via a dependency override — no LLM
calls, no network, no tokens. Verifies auth, tenant-namespaced threads,
the pause/approve/reject loop, and decision validation.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools.registry import demo_side_effect_log, reset_demo_side_effect_log
from app.api.ai import get_chat_model
from app.main import app
from app.models import Shop, User, UserRole


class _PlainFakeModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "chat-plain-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_PlainFakeModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="hi"))])


class _ToolCallingFakeModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "chat-tool-calling-fake"

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
                        "args": {"note": "chat-probe"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.fixture
def use_plain_fake() -> Any:
    app.dependency_overrides[get_chat_model] = lambda: _PlainFakeModel()
    yield
    app.dependency_overrides.pop(get_chat_model, None)


@pytest.fixture
def use_tool_fake() -> Any:
    app.dependency_overrides[get_chat_model] = lambda: _ToolCallingFakeModel()
    yield
    app.dependency_overrides.pop(get_chat_model, None)


async def _make_mock_user(api_session: AsyncSession) -> Shop:
    shop = Shop(name="AI Chat Shop")
    api_session.add(shop)
    await api_session.flush()
    await api_session.refresh(shop)
    api_session.add(
        User(
            clerk_user_id="mock_clerk_id",
            shop_id=shop.id,
            name="AI Chatter",
            email="ai-chatter@example.com",
            role=UserRole.OWNER,
        )
    )
    await api_session.flush()
    return shop


class TestChatAuth:
    @pytest.mark.asyncio
    async def test_chat_requires_auth(self, client: AsyncClient) -> None:
        response = await client.post("/ai/chat", json={"message": "hi"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_resume_requires_auth(self, client: AsyncClient) -> None:
        response = await client.post(
            "/ai/chat/resume",
            json={"thread_id": "x", "decisions": [{"type": "approve"}]},
        )
        assert response.status_code == 401


class TestChatConfiguration:
    @pytest.mark.asyncio
    async def test_missing_key_returns_503(
        self,
        mocked_api_client: AsyncClient,
        api_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.ai.llm as llm_module
        from app.ai.config import AISettings

        await _make_mock_user(api_session)
        # Force keyless settings regardless of backend/.env contents.
        monkeypatch.setattr(
            llm_module, "get_ai_settings", lambda: AISettings(xkiro_api_key="")
        )
        response = await mocked_api_client.post("/ai/chat", json={"message": "hi"})
        assert response.status_code == 503


class TestChatDone:
    @pytest.mark.asyncio
    async def test_chat_replies_and_echoes_thread(
        self,
        mocked_api_client: AsyncClient,
        api_session: AsyncSession,
        use_plain_fake: Any,
    ) -> None:
        await _make_mock_user(api_session)
        thread_id = uuid.uuid4().hex
        response = await mocked_api_client.post(
            "/ai/chat", json={"message": "hello", "thread_id": thread_id}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "done"
        assert data["reply"] == "hi"
        assert data["thread_id"] == thread_id

    @pytest.mark.asyncio
    async def test_chat_continues_thread(
        self,
        mocked_api_client: AsyncClient,
        api_session: AsyncSession,
        use_plain_fake: Any,
    ) -> None:
        await _make_mock_user(api_session)
        thread_id = uuid.uuid4().hex
        for _ in range(2):
            response = await mocked_api_client.post(
                "/ai/chat", json={"message": "again", "thread_id": thread_id}
            )
            assert response.status_code == 200
            assert response.json()["status"] == "done"


class TestChatHitl:
    @pytest.mark.asyncio
    async def test_pause_then_approve_executes(
        self,
        mocked_api_client: AsyncClient,
        api_session: AsyncSession,
        use_tool_fake: Any,
    ) -> None:
        await _make_mock_user(api_session)
        reset_demo_side_effect_log()
        thread_id = uuid.uuid4().hex

        paused = await mocked_api_client.post(
            "/ai/chat", json={"message": "run the demo", "thread_id": thread_id}
        )
        assert paused.status_code == 200
        body = paused.json()
        assert body["status"] == "paused"
        assert body["interrupts"][0]["name"] == "demo_side_effect"
        assert demo_side_effect_log() == []

        resumed = await mocked_api_client.post(
            "/ai/chat/resume",
            json={"thread_id": thread_id, "decisions": [{"type": "approve"}]},
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "done"
        assert demo_side_effect_log() == ["chat-probe"]
        reset_demo_side_effect_log()

    @pytest.mark.asyncio
    async def test_pause_then_reject_skips(
        self,
        mocked_api_client: AsyncClient,
        api_session: AsyncSession,
        use_tool_fake: Any,
    ) -> None:
        await _make_mock_user(api_session)
        reset_demo_side_effect_log()
        thread_id = uuid.uuid4().hex

        paused = await mocked_api_client.post(
            "/ai/chat", json={"message": "run the demo", "thread_id": thread_id}
        )
        assert paused.json()["status"] == "paused"

        resumed = await mocked_api_client.post(
            "/ai/chat/resume",
            json={
                "thread_id": thread_id,
                "decisions": [{"type": "reject", "message": "No, never."}],
            },
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "done"
        assert demo_side_effect_log() == []

    @pytest.mark.asyncio
    async def test_reject_without_message_is_rejected(
        self,
        mocked_api_client: AsyncClient,
        api_session: AsyncSession,
        use_tool_fake: Any,
    ) -> None:
        await _make_mock_user(api_session)
        response = await mocked_api_client.post(
            "/ai/chat/resume",
            json={"thread_id": uuid.uuid4().hex, "decisions": [{"type": "reject"}]},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_decision_type_is_rejected(
        self,
        mocked_api_client: AsyncClient,
        api_session: AsyncSession,
        use_tool_fake: Any,
    ) -> None:
        await _make_mock_user(api_session)
        response = await mocked_api_client.post(
            "/ai/chat/resume",
            json={"thread_id": uuid.uuid4().hex, "decisions": [{"type": "maybe"}]},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_resume_on_finished_thread_is_tolerated(
        self,
        mocked_api_client: AsyncClient,
        api_session: AsyncSession,
        use_plain_fake: Any,
    ) -> None:
        # Resuming a thread with nothing pending does not crash or leak:
        # the agent simply continues the conversation.
        await _make_mock_user(api_session)
        thread_id = uuid.uuid4().hex
        done = await mocked_api_client.post(
            "/ai/chat", json={"message": "hi", "thread_id": thread_id}
        )
        assert done.json()["status"] == "done"
        resumed = await mocked_api_client.post(
            "/ai/chat/resume",
            json={"thread_id": thread_id, "decisions": [{"type": "approve"}]},
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "done"

    @pytest.mark.asyncio
    async def test_other_shop_cannot_consume_our_approval(
        self,
        mocked_api_client: AsyncClient,
        api_client: AsyncClient,
        api_session: AsyncSession,
        use_tool_fake: Any,
    ) -> None:
        await _make_mock_user(api_session)
        reset_demo_side_effect_log()
        thread_id = uuid.uuid4().hex

        paused = await mocked_api_client.post(
            "/ai/chat", json={"message": "run the demo", "thread_id": thread_id}
        )
        assert paused.json()["status"] == "paused"

        other_shop = Shop(name="Other Shop")
        api_session.add(other_shop)
        await api_session.flush()
        await api_session.refresh(other_shop)
        api_session.add(
            User(
                clerk_user_id="other_clerk_id",
                shop_id=other_shop.id,
                name="Other User",
                email="other@example.com",
                role=UserRole.OWNER,
            )
        )
        await api_session.flush()

        # Same client thread id, different tenant: lands on a fresh,
        # isolated thread — our pending approval stays untouched and
        # nothing executes on our behalf.
        with patch(
            "app.auth.auth._verify_token",
            new_callable=AsyncMock,
            return_value={"sub": "other_clerk_id"},
        ):
            intruder = await api_client.post(
                "/ai/chat/resume",
                json={"thread_id": thread_id, "decisions": [{"type": "approve"}]},
            )
        assert intruder.status_code == 200
        assert demo_side_effect_log() == []

        # Our own approval still works exactly once.
        resumed = await mocked_api_client.post(
            "/ai/chat/resume",
            json={"thread_id": thread_id, "decisions": [{"type": "approve"}]},
        )
        assert resumed.json()["status"] == "done"
        assert demo_side_effect_log() == ["chat-probe"]
        reset_demo_side_effect_log()

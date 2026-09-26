"""Focused Step 1 AI-foundation tests (offline: no DB, no LLM calls).

Covers the boundaries from the brief: master-agent construction via the
current ``create_deep_agent`` API, tenant isolation, operation policy,
read-only analytics, no-DB-mutation registry, and the harmless HITL demo.
"""

import inspect
import uuid
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command

from app.ai.agent import (
    HITL_INTERRUPT_CONFIG,
    MASTER_SYSTEM_PROMPT,
    SKILLS_SOURCE_PATHS,
    approve_decision,
    build_master_agent,
    create_backend,
    create_checkpointer,
    hitl_resume_payload,
    reject_decision,
)
from app.ai.policies.operations import (
    OperationCategory,
    classify_request,
    contains_forbidden_sql,
    is_forbidden,
    is_read_only,
    requires_approval,
)
from app.ai.skills import CORE_SKILL_PATH, list_skill_names, load_core_skill
from app.ai.state import (
    TenantContext,
    TenantMismatchError,
    assert_tenant_matches,
    tenant_context_from_user,
)
from app.ai.subagents.analytics import (
    ANALYTICS_SUBAGENT_NAME,
    ForbiddenSqlError,
    MissingTenantFilterError,
    build_analytics_subagent_spec,
    describe_analytics_boundary,
    validate_read_only_sql,
)
from app.ai.tools.registry import (
    assert_no_db_mutation_tools,
    demo_side_effect,
    demo_side_effect_log,
    get_analytics_tools,
    get_master_tools,
    kapraos_demo_info,
    reset_demo_side_effect_log,
)
from app.models.user import User


def _fake_model() -> object:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    return GenericFakeChatModel(messages=iter(["fake reply"]))


class _PlainFakeModel(BaseChatModel):
    """Deterministic chat model for real ``invoke()`` runs (no LLM, no keys).

    Implements ``bind_tools`` (required by the deep-agent runtime —
    ``GenericFakeChatModel`` raises ``NotImplementedError`` there) and
    always answers without calling tools.
    """

    @property
    def _llm_type(self) -> str:
        return "kapraos-plain-fake"

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
    """Deterministic fake that requests ``demo_side_effect`` once.

    First model call emits a ``demo_side_effect`` tool call; once a
    ``ToolMessage`` is in history it answers directly. Lets tests drive
    the documented interrupt -> human decision -> resume loop.
    """

    @property
    def _llm_type(self) -> str:
        return "kapraos-tool-calling-fake"

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
                        "args": {"note": "hitl-probe"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _make_user() -> User:
    return User(
        id=uuid.uuid4(),
        shop_id=uuid.uuid4(),
        clerk_user_id="clerk_test_123",
        name="Test User",
        email="ai-test@example.com",
        role="owner",
    )


# --- Master agent --------------------------------------------------------


def test_master_agent_constructs_with_fake_model() -> None:
    agent = build_master_agent(model=_fake_model())
    assert hasattr(agent, "invoke")
    assert_no_db_mutation_tools(get_master_tools())


def test_master_agent_constructs_offline_without_model() -> None:
    agent = build_master_agent(model=None)
    assert hasattr(agent, "invoke")


def test_master_agent_hitl_requires_checkpointer() -> None:
    with pytest.raises(ValueError, match="checkpointer"):
        build_master_agent(model=_fake_model(), checkpointer=None)


def test_master_agent_auto_checkpointer_is_created() -> None:
    assert create_checkpointer() is not None
    agent = build_master_agent(model=_fake_model(), checkpointer="auto")
    assert hasattr(agent, "invoke")


def test_master_system_prompt_has_core_rules() -> None:
    lowered = MASTER_SYSTEM_PROMPT.lower()
    for rule in (
        "never invent business data",
        "never bypass authentication",
        "never change the active shop",
        "never write raw sql",
        "never directly mutate the database",
        "approved domain tools",
        "analytics subagent",
        "human approval",
        "deterministic backend",
        "never expose data belonging to another shop",
        "clarification",
        "existing backend services",
    ):
        assert rule in lowered, f"missing rule: {rule}"


def test_no_agent_zoo_and_single_core_skill() -> None:
    assert list_skill_names() == ["kapraos-core"]
    assert CORE_SKILL_PATH.exists()
    skill = load_core_skill()
    assert skill.startswith("---")
    assert "name: kapraos-core" in skill
    spec = build_analytics_subagent_spec()
    assert spec["name"] == ANALYTICS_SUBAGENT_NAME
    # Exactly one sanctioned subagent in Step 1.
    assert ANALYTICS_SUBAGENT_NAME == "analytics"


def test_skills_load_from_disk_via_backend_and_skills_arg() -> None:
    """The documented pattern: FilesystemBackend + skills=[...].

    After a real ``invoke()``, the agent state holds ``skills_metadata``
    with the ``kapraos-core`` frontmatter — proving the master agent
    discovers the on-disk skill through progressive disclosure.
    """
    assert create_backend() is not None
    assert SKILLS_SOURCE_PATHS == ["/skills/"]
    agent = build_master_agent(model=_PlainFakeModel())
    config = {"configurable": {"thread_id": "test-skills-load"}}
    agent.invoke({"messages": [{"role": "user", "content": "hi"}]}, config=config)
    metadata = agent.get_state(config).values.get("skills_metadata")
    assert metadata is not None
    names = [entry["name"] for entry in metadata]
    assert "kapraos-core" in names


# --- Tenant isolation ----------------------------------------------------


def test_tenant_context_comes_from_authenticated_user() -> None:
    user = _make_user()
    context = tenant_context_from_user(user)
    assert context.shop_id == user.shop_id
    assert context.user_id == user.id
    assert_tenant_matches(context, user.shop_id)


def test_tenant_context_rejects_foreign_shop() -> None:
    context = TenantContext(shop_id=uuid.uuid4(), user_id=uuid.uuid4())
    with pytest.raises(TenantMismatchError):
        assert_tenant_matches(context, uuid.uuid4())


def test_tenant_context_is_frozen() -> None:
    context = TenantContext(shop_id=uuid.uuid4(), user_id=uuid.uuid4())
    with pytest.raises(FrozenInstanceError):
        context.shop_id = uuid.uuid4()  # type: ignore[misc]


def test_llm_tools_take_no_shop_id_argument() -> None:
    for tool in get_master_tools():
        func = getattr(tool, "func", None)
        assert func is not None, f"{tool.name} must expose its function"
        params = inspect.signature(func).parameters
        assert "shop_id" not in params, f"{tool.name} must not take shop_id"


# --- Operation policy ----------------------------------------------------


def test_operation_policy_categories() -> None:
    assert set(OperationCategory) == {
        OperationCategory.READ,
        OperationCategory.PREPARE,
        OperationCategory.EXECUTE,
        OperationCategory.FORBIDDEN,
    }
    assert requires_approval(OperationCategory.EXECUTE) is True
    assert requires_approval(OperationCategory.READ) is False
    assert requires_approval(OperationCategory.PREPARE) is False
    assert requires_approval(OperationCategory.FORBIDDEN) is False
    assert is_read_only(OperationCategory.READ) is True
    assert is_forbidden(OperationCategory.FORBIDDEN) is True


def test_classify_request_boundaries() -> None:
    assert classify_request("Kitna stock hai?") is OperationCategory.READ
    assert classify_request("Aaj kitni sale hui?") is OperationCategory.READ
    assert (
        classify_request("Prepare Ali ko 3 gaz black lawn udhaar summary")
        is OperationCategory.PREPARE
    )
    assert classify_request("create sale for Ali") is OperationCategory.EXECUTE
    assert classify_request("record expense 500 rent") is OperationCategory.EXECUTE
    assert classify_request("drop table sales") is OperationCategory.FORBIDDEN
    assert classify_request("please change shop to another") is OperationCategory.FORBIDDEN
    assert classify_request("run migration now") is OperationCategory.FORBIDDEN


def test_forbidden_sql_detection() -> None:
    for stmt in (
        "INSERT INTO sales VALUES (1)",
        "update products set price = 1",
        "DELETE FROM ledger_entries",
        "DROP TABLE shops",
        "ALTER TABLE sales ADD COLUMN x",
        "TRUNCATE sales",
    ):
        assert contains_forbidden_sql(stmt) is True, stmt
    assert contains_forbidden_sql("SELECT total FROM sales WHERE shop_id = 'x'") is False


# --- Analytics boundary --------------------------------------------------


def test_analytics_subagent_is_explicitly_read_only() -> None:
    assert get_analytics_tools() == []
    spec = build_analytics_subagent_spec()
    assert spec["tools"] == []
    assert "read" in spec["description"].lower()
    boundary = describe_analytics_boundary()
    assert boundary["read_only"] is True
    assert boundary["db_connection_in_step_1"] is None
    assert boundary["requires_read_only_db_role"] is True


def test_analytics_sql_validation() -> None:
    validate_read_only_sql("SELECT total FROM sales WHERE shop_id = 'abc'")
    with pytest.raises(ForbiddenSqlError):
        validate_read_only_sql("DELETE FROM sales WHERE shop_id = 'abc'")
    with pytest.raises(ForbiddenSqlError):
        validate_read_only_sql("DROP TABLE shops;")
    with pytest.raises(ForbiddenSqlError):
        validate_read_only_sql("SELECT 1; SELECT 2")
    with pytest.raises(MissingTenantFilterError):
        validate_read_only_sql("SELECT total FROM sales")


# --- Tool registry boundary ----------------------------------------------


def test_master_tool_registry_is_small_and_safe() -> None:
    tools = get_master_tools()
    names = sorted(t.name for t in tools)
    assert names == ["demo_prepare_operation", "demo_side_effect", "kapraos_demo_info"]
    assert_no_db_mutation_tools(tools)
    with pytest.raises(AssertionError):
        assert_no_db_mutation_tools(
            tools + [kapraos_demo_info.model_copy(update={"name": "create_sale"})]
        )


def test_demo_tools_never_touch_the_database() -> None:
    reset_demo_side_effect_log()
    assert demo_side_effect_log() == []
    out = demo_side_effect.invoke({"note": "hitl-probe"})
    assert "hitl-probe" in out
    assert demo_side_effect_log() == ["hitl-probe"]
    reset_demo_side_effect_log()
    assert demo_side_effect_log() == []


# --- HITL foundation -----------------------------------------------------


def test_hitl_interrupt_config_covers_only_demo_tool() -> None:
    assert HITL_INTERRUPT_CONFIG == {"demo_side_effect": True}


def test_hitl_approve_executes_demo_tool() -> None:
    """End-to-end interrupt -> approve -> resume (docs 'Handle interrupts').

    The fake model requests ``demo_side_effect``; the run pauses with an
    interrupt; resuming with an approve decision executes the tool.
    """
    reset_demo_side_effect_log()
    agent = build_master_agent(model=_ToolCallingFakeModel())
    config = {"configurable": {"thread_id": "test-hitl-approve"}}
    paused = agent.invoke(
        {"messages": [{"role": "user", "content": "run the demo"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    action = paused.interrupts[0].value["action_requests"][0]
    assert action["name"] == "demo_side_effect"
    assert demo_side_effect_log() == []  # not executed before approval

    resumed = agent.invoke(
        Command(resume=hitl_resume_payload([approve_decision()])),
        config=config,  # same thread_id, version="v2"
        version="v2",
    )
    assert not resumed.interrupts
    assert demo_side_effect_log() == ["hitl-probe"]
    reset_demo_side_effect_log()


def test_hitl_reject_skips_demo_tool() -> None:
    """End-to-end interrupt -> reject -> resume: the tool never runs."""
    reset_demo_side_effect_log()
    agent = build_master_agent(model=_ToolCallingFakeModel())
    config = {"configurable": {"thread_id": "test-hitl-reject"}}
    paused = agent.invoke(
        {"messages": [{"role": "user", "content": "run the demo"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts

    resumed = agent.invoke(
        Command(
            resume=hitl_resume_payload(
                [reject_decision("User rejected the demo. Do not retry.")]
            )
        ),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    assert demo_side_effect_log() == []  # rejected: never executed


def test_hitl_approve_reject_resume_payloads() -> None:
    assert approve_decision() == {"type": "approve"}
    rejection = reject_decision("User rejected the demo. Do not retry.")
    assert rejection["type"] == "reject"
    assert "not" in rejection["message"].lower() or "reject" in rejection["message"].lower()
    payload = hitl_resume_payload([approve_decision()])
    assert payload == {"decisions": [{"type": "approve"}]}

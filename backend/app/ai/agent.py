"""Master Deep Agent foundation (Step 1).

Uses the current documented ``create_deep_agent`` API
(deepagents 0.7.x, verified against the installed 0.7.19):

* ``create_deep_agent(model=..., tools=..., system_prompt=...,
  subagents=..., interrupt_on=..., checkpointer=...)``
* Human-in-the-loop via ``interrupt_on={"<tool>": True}`` plus a
  LangGraph checkpointer (``MemorySaver``), resumed with
  ``Command(resume={"decisions": [...]})`` on the same ``thread_id``
  with ``version="v2"``.
* Skills via ``SKILL.md`` progressive disclosure (see
  ``app/ai/skills/kapraos-core/SKILL.md``), loaded from disk through
  ``FilesystemBackend`` and the ``skills=[...]`` argument — exactly the
  documented pattern.
* One custom ``analytics`` subagent spec (read-only boundary).

Conceptual flow::

    Understand request -> Determine intent -> Use relevant skill
        -> Choose capability -> Read / prepare / execute -> Return result
"""

from pathlib import Path
from typing import Any

from app.ai.config import get_ai_settings
from app.ai.subagents.analytics import build_analytics_subagent_spec
from app.ai.tools.registry import demo_side_effect, get_master_tools

MASTER_SYSTEM_PROMPT = """You are the KapraOS shop assistant. You help shopkeepers operate and understand their business using natural language.

Core rules:
1. Never invent business data.
2. Never bypass authentication.
3. Never change the active shop/tenant; shop_id comes from authenticated context, never from the user or your own output.
4. Never write raw SQL for transactional operations.
5. Never directly mutate the database.
6. Business mutations must go through approved domain tools/services.
7. Read-only analytics may use the analytics subagent (SELECT-only, always filtered by shop_id).
8. Side-effecting operations (EXECUTE) require human approval (HITL) before running.
9. Follow deterministic backend business rules (server-calculated totals, payment mapping, double-entry balance).
10. Never perform accounting manipulation directly.
11. Never expose data belonging to another shop.
12. When information is ambiguous, resolve it through safe read operations or request clarification.
13. Prefer existing backend services over duplicating business logic.
"""

# Master tools are the Step 1 demo registry only (safe, tiny).
MASTER_TOOL_NAMES: tuple[str, ...] = (
    "kapraos_demo_info",
    "demo_prepare_operation",
    "demo_side_effect",
)

# HITL: the fake side-effect demo pauses for human review.
# ``True`` = approve / edit / reject / respond allowed (docs default).
# A real checkpointer is REQUIRED for this to pause/resume.
HITL_INTERRUPT_CONFIG: dict[str, bool] = {
    "demo_side_effect": True,
}

# Allowed resume decision types (per HITL docs).
HITL_ALLOWED_DECISIONS: tuple[str, ...] = ("approve", "edit", "reject", "respond")

# Backend root for skill loading: this ``app/ai`` directory, resolved from
# ``__file__`` so skill loading never depends on the process CWD.
AI_ROOT = Path(__file__).resolve().parent

# Skill source paths passed to ``create_deep_agent(skills=[...])``.
# Per docs these use forward slashes and are relative to the backend root:
# ``/skills/`` resolves to ``<AI_ROOT>/skills/``, which contains one skill
# directory per skill (``kapraos-core/SKILL.md``).
SKILLS_SOURCE_PATHS: list[str] = ["/skills/"]


def create_backend() -> Any:
    """Create the ``FilesystemBackend`` used for skill loading.

    Skills already on disk under the backend root load without uploading;
    ``virtual_mode=True`` sandboxes all backend paths under ``AI_ROOT``.
    """
    from deepagents.backends.filesystem import FilesystemBackend

    return FilesystemBackend(root_dir=str(AI_ROOT), virtual_mode=True)


def create_checkpointer() -> Any:
    """Create the LangGraph checkpointer required for HITL.

    ``MemorySaver`` (in-memory) is correct for Step 1 / local dev; LangSmith
    deployments configure a persistent checkpointer automatically.
    """
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


def default_model_name() -> str:
    """Configured model identifier (``provider:model`` form)."""
    return get_ai_settings().ai_model


def resolve_model(model: Any) -> Any:
    """Resolve the ``model`` argument of :func:`build_master_agent`.

    ``"auto"`` builds the xKiro-backed chat model from settings
    (``XKIRO_API_KEY`` in ``backend/.env``); anything else is passed
    through unchanged (model instance, ``"provider:model"`` string, or
    ``None`` for offline verification).
    """
    from app.ai.llm import build_xkiro_llm

    if model == "auto":
        return build_xkiro_llm()
    return model




def build_master_agent(
    *,
    model: Any = None,
    checkpointer: Any = "auto",
    backend: Any = "auto",
    include_hitl_demo: bool = True,
) -> Any:
    """Construct the master Deep Agent using the current supported API.

    Args:
        model: A LangChain chat model instance, ``"auto"`` (xKiro model
            from settings — needs ``XKIRO_API_KEY``), a
            ``"provider:model"`` string (requires that provider package +
            credentials at invoke time), or ``None``. ``None`` builds
            offline for architecture verification (deprecated upstream,
            fine for Step 1 tests); pass a fake model or a real model in
            production. Note the model must support ``bind_tools`` for
            ``invoke()`` to run.
        checkpointer: LangGraph checkpointer. ``"auto"`` creates a
            :func:`create_checkpointer` when HITL is enabled, ``None``
            disables persistence (HITL then cannot pause — only use with
            ``include_hitl_demo=False``).
        backend: Filesystem backend for skill loading. ``"auto"`` creates
            one via :func:`create_backend` rooted at ``app/ai``.
        include_hitl_demo: Attach the harmless ``demo_side_effect`` tool
            with ``interrupt_on`` approval. Always True in Step 1 unless
            the caller explicitly opts out.

    Returns the compiled LangGraph ``CompiledStateGraph``.
    """
    from deepagents import create_deep_agent

    resolved_model = resolve_model(model)
    tools = get_master_tools()
    if not include_hitl_demo:
        tools = [t for t in tools if t.name != demo_side_effect.name]

    interrupt_on: dict[str, Any] | None = None
    resolved_checkpointer: Any = None
    if include_hitl_demo:
        interrupt_on = dict(HITL_INTERRUPT_CONFIG)
        resolved_checkpointer = (
            create_checkpointer() if checkpointer == "auto" else checkpointer
        )
        if resolved_checkpointer is None:
            raise ValueError(
                "HITL requires a checkpointer: pass one or leave "
                "'checkpointer=\"auto\"' to create an in-memory saver."
            )
    elif checkpointer not in (None, "auto"):
        resolved_checkpointer = checkpointer

    resolved_backend = create_backend() if backend == "auto" else backend

    return create_deep_agent(
        model=resolved_model,
        tools=tools,
        system_prompt=MASTER_SYSTEM_PROMPT,
        subagents=[build_analytics_subagent_spec()],
        backend=resolved_backend,
        skills=list(SKILLS_SOURCE_PATHS),
        interrupt_on=interrupt_on,
        checkpointer=resolved_checkpointer,
    )


def approve_decision() -> dict[str, str]:
    """HITL resume decision approving the pending demo tool call."""
    return {"type": "approve"}


def reject_decision(message: str) -> dict[str, str]:
    """HITL resume decision rejecting the pending demo tool call.

    ``message`` must tell the agent the tool was NOT executed and what to
    do instead (per docs: never omit it for side-effecting tools).
    """
    return {"type": "reject", "message": message}


def hitl_resume_payload(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the ``Command(resume=...)`` payload for resuming an interrupt.

    Usage (per current HITL docs)::

        from langgraph.types import Command
        agent.invoke(
            Command(resume=hitl_resume_payload(decisions)),
            config={"configurable": {"thread_id": thread_id}},
            version="v2",
        )
    """
    return {"decisions": decisions}


__all__ = [
    "AI_ROOT",
    "HITL_ALLOWED_DECISIONS",
    "HITL_INTERRUPT_CONFIG",
    "MASTER_SYSTEM_PROMPT",
    "MASTER_TOOL_NAMES",
    "SKILLS_SOURCE_PATHS",
    "approve_decision",
    "build_master_agent",
    "create_backend",
    "create_checkpointer",
    "default_model_name",
    "hitl_resume_payload",
    "reject_decision",
    "resolve_model",
]

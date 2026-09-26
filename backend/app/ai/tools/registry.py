"""Tool registry — Step 1 demos plus Step 2 business reads.

Deliberately NOT a giant CRUD registry. Step 1 exposes only safe demos;
Step 2 adds a small set of business-level READ tools (see
``app.ai.tools.business_reads``) built per request over the authenticated
tenant. No tool takes ``shop_id`` — tenant context is always injected
server-side (see ``app.ai.state``).

Hard rules enforced here and in tests:

* No tool takes ``shop_id`` as an argument.
* No tool performs ``session.add / session.delete`` or any raw-SQL write.
  ``assert_no_db_mutation_tools`` guards this.
* The analytics subagent receives zero tools (see ``app.ai.subagents``).
"""

from langchain_core.tools import BaseTool, tool

from app.ai.tools.business_reads import READ_TOOL_NAMES, build_read_tools

# In-memory receipt log for the harmless HITL demo tool. Module-level and
# resettable so tests stay isolated. Never a database table.
_DEMO_SIDE_EFFECT_LOG: list[str] = []


@tool
def kapraos_demo_info(question: str) -> str:
    """Answer a KapraOS orientation question without touching the database.

    Safe read-only demo capability proving Master Agent -> skill/context ->
    result. Use for questions like "What is KapraOS?" or "What is khata?".
    """
    return (
        "KapraOS demo answer (no database access). "
        f"Question received: {question!r}. "
        "See the kapraos-core skill for domain terminology "
        "(shop, khata, udhaar, meter/gaz/thaan) and note that real "
        "operations must go through approved domain tools/services."
    )


@tool
def demo_prepare_operation(summary: str) -> str:
    """Prepare (but never execute) a hypothetical operation. No side effect."""
    return (
        "Prepared (not executed): "
        f"{summary!r}. "
        "Execution would require human approval and an approved domain tool."
    )


@tool
def demo_side_effect(note: str) -> str:
    """Harmless fake side-effect demo for HITL verification.

    Appends ``note`` to an in-memory log only. Never touches PostgreSQL.
    Requires human approval via ``interrupt_on`` before running.
    """
    _DEMO_SIDE_EFFECT_LOG.append(note)
    return f"demo_side_effect recorded (pending human approval in production): {note!r}"


def demo_side_effect_log() -> list[str]:
    """Return a copy of the in-memory demo side-effect log."""
    return list(_DEMO_SIDE_EFFECT_LOG)


def reset_demo_side_effect_log() -> None:
    """Clear the in-memory demo side-effect log (tests only)."""
    _DEMO_SIDE_EFFECT_LOG.clear()


def get_master_tools() -> list[BaseTool]:
    """Tools exposed to the master Deep Agent in Step 1 (safe demos only).

    Kept demo-only so Step 1 architecture/tests stay intact. Step 2 read
    tools are per-request (they close over the tenant session) — use
    :func:`get_master_tools_with_reads`.
    """
    return [kapraos_demo_info, demo_prepare_operation, demo_side_effect]  # type: ignore[list-item]


def get_master_tools_with_reads(session: object, tenant: object) -> list[BaseTool]:
    """Demo tools plus the tenant-bound Step 2 business read tools."""
    from typing import cast

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.ai.state import TenantContext

    reads = build_read_tools(
        cast("AsyncSession", session), cast("TenantContext", tenant)
    )
    return [*get_master_tools(), *reads]


def get_read_tool_names() -> tuple[str, ...]:
    """Names of the Step 2 business read tools (explicit registry)."""
    return READ_TOOL_NAMES


def get_analytics_tools() -> list[BaseTool]:
    """Tools exposed to the analytics subagent: none (read-only boundary)."""
    return []


# Tool-name fragments that must never appear in the Step 1 registry.
# (Future *business-operation* tools such as a single "sales operation"
# tool are allowed; per-function CRUD-style tools are not.)
FORBIDDEN_TOOL_NAME_FRAGMENTS: tuple[str, ...] = (
    "create_sale",
    "create_purchase",
    "create_product",
    "update_product",
    "delete_product",
    "adjust_inventory",
    "create_payment",
    "session",
    "raw_sql",
    "execute_sql",
)


def assert_no_db_mutation_tools(tools: list[BaseTool]) -> None:
    """Raise if any tool looks like an unrestricted DB mutation capability."""
    for t in tools:
        name = (t.name or "").lower()
        for fragment in FORBIDDEN_TOOL_NAME_FRAGMENTS:
            if fragment in name:
                raise AssertionError(
                    f"Tool {t.name!r} looks like an unrestricted DB/CRUD "
                    f"capability (matched {fragment!r}); Step 1 forbids this."
                )

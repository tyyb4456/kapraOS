"""Analytics subagent foundation — explicitly read-only.

The analytics subagent will eventually answer questions such as "What
were my sales last month?" by issuing ``SELECT`` queries scoped to the
authenticated shop. Step 1 creates only the boundary:

* A :class:`SubAgent`-compatible spec dict (``name``/``description``/
  ``system_prompt``/``tools``) the master agent can delegate to.
* Zero mutation tools (``tools=[]``).
* A deterministic ``SELECT``-only SQL validator.
* Documentation of the required future read-only database role.

No database connection is introduced in this step. The eventual
connection MUST use a PostgreSQL role with no write permissions
(``GRANT SELECT`` only); if that role does not exist yet, Step 2 must
create the abstraction without weakening production security.
"""

import re
from typing import TYPE_CHECKING, Any

from app.ai.policies.operations import FORBIDDEN_SQL_KEYWORDS, contains_forbidden_sql
from app.ai.tools.registry import get_analytics_tools

if TYPE_CHECKING:
    from deepagents.middleware.subagents import SubAgent

ANALYTICS_SUBAGENT_NAME = "analytics"

ANALYTICS_DESCRIPTION = (
    "Read-only business analytics for one shop. "
    "Use for questions about past sales, top products, fabric profit, "
    "customer balances (udhaar/khata), supplier dues, and expenses. "
    "Never for creating, updating, or deleting anything."
)

ANALYTICS_SYSTEM_PROMPT = """You are the KapraOS analytics subagent. You answer questions about past business data for ONE authenticated shop.

Hard rules:
1. READ ONLY. Emit SELECT queries only. Never INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, or any schema/data change.
2. Every query MUST filter by the authenticated shop_id provided in the delegated task. Never invent or change shop_id.
3. Never expose data belonging to another shop.
4. If a request needs a write (create sale/purchase/payment/expense, adjust stock), refuse and hand back to the master agent.
5. Prefer narrow, aggregated queries over dumping raw tables.
6. When unsure, ask for clarification instead of guessing figures. Never invent business data.
"""

# Example question kinds the subagent will eventually handle (not all
# implemented in Step 1 — listed here to pin the scope).
ANALYTICS_QUESTION_KINDS: tuple[str, ...] = (
    "sales_last_month",
    "top_products",
    "fabric_profit",
    "customer_outstanding",
    "supplier_dues",
    "expenses_this_month",
)


class ForbiddenSqlError(ValueError):
    """A candidate analytics statement is not read-only."""


class MissingTenantFilterError(ValueError):
    """A candidate analytics statement lacks the mandatory shop filter."""


def validate_read_only_sql(statement: str) -> None:
    """Validate that ``statement`` is a safe read-only analytics query.

    Raises :class:`ForbiddenSqlError` for any mutation keyword or
    multi-statement payload, and :class:`MissingTenantFilterError` when
    the mandatory ``shop_id`` tenant filter is absent.
    """
    stripped = statement.strip()
    if not stripped:
        raise ForbiddenSqlError("Empty SQL statement is not allowed.")
    if ";" in stripped.rstrip(";"):
        # Allow at most one trailing semicolon; anything else smells like
        # stacked statements.
        raise ForbiddenSqlError("Multiple SQL statements are not allowed.")
    if contains_forbidden_sql(stripped):
        raise ForbiddenSqlError(
            f"Statement contains a forbidden keyword {sorted(FORBIDDEN_SQL_KEYWORDS)}; "
            "analytics is SELECT-only."
        )
    if not re.match(r"(?is)^\s*(select|with|values|table|explain)\b", stripped):
        raise ForbiddenSqlError(
            "Analytics statements must start with SELECT (or WITH/VALUES/TABLE/EXPLAIN)."
        )
    if "shop_id" not in stripped.lower():
        raise MissingTenantFilterError(
            "Analytics statements must filter by the authenticated shop_id."
        )


def build_analytics_subagent_spec() -> "SubAgent":
    """Return the analytics subagent spec for ``create_deep_agent``.

    Matches the ``SubAgent`` TypedDict (name, description, system_prompt,
    tools). ``tools`` is intentionally empty: Step 1 analytics performs no
    tool-driven writes; future read-only SQL access arrives in Step 2
    behind a read-only DB role.
    """
    return {
        "name": ANALYTICS_SUBAGENT_NAME,
        "description": ANALYTICS_DESCRIPTION,
        "system_prompt": ANALYTICS_SYSTEM_PROMPT,
        "tools": get_analytics_tools(),
    }


def describe_analytics_boundary() -> dict[str, Any]:
    """Describe the read-only analytics boundary (for docs/tests)."""
    return {
        "name": ANALYTICS_SUBAGENT_NAME,
        "read_only": True,
        "mutation_tools": [],
        "forbidden_sql_keywords": sorted(FORBIDDEN_SQL_KEYWORDS),
        "requires_tenant_filter": "shop_id",
        "requires_read_only_db_role": True,
        "db_connection_in_step_1": None,
        "question_kinds": list(ANALYTICS_QUESTION_KINDS),
    }

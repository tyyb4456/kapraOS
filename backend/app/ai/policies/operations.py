"""Deterministic AI operation policy.

Future AI work follows ``READ -> PREPARE -> EXECUTE`` with human approval
on side effects, and a hard ``FORBIDDEN`` set. This module is pure logic
with no LLM calls so it can be unit-tested exhaustively.

Meaning of each category:

* ``READ`` — read-only information (safe, immediate).
* ``PREPARE`` — construct an operation without changing data.
* ``EXECUTE`` — actual mutation; later requires HITL approval and must go
  through an approved domain tool/service.
* ``FORBIDDEN`` — never allowed (tenant switching, auth changes, schema
  changes, arbitrary SQL writes, accounting-history deletion, ...).
"""

from enum import Enum


class OperationCategory(str, Enum):
    """Future AI operation categories."""

    READ = "read"
    PREPARE = "prepare"
    EXECUTE = "execute"
    FORBIDDEN = "forbidden"


# Substrings that always indicate a forbidden request. Matched
# case-insensitively against the raw request text.
FORBIDDEN_REQUEST_MARKERS: tuple[str, ...] = (
    "change shop",
    "switch shop",
    "switch tenant",
    "change permissions",
    "modify authentication",
    "drop table",
    "drop database",
    "run migration",
    "alter table",
    "truncate",
    "delete accounting history",
    "arbitrary sql",
)

# SQL keywords the analytics (read-only) path must never emit.
# Checked with word boundaries in `contains_forbidden_sql`.
FORBIDDEN_SQL_KEYWORDS: frozenset[str] = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "GRANT",
        "REVOKE",
        "MERGE",
        "REPLACE",
        "VACUUM",
        "CALL",
        "EXECUTE",
    }
)


def requires_approval(category: OperationCategory) -> bool:
    """Only ``EXECUTE`` operations require human approval."""
    return category is OperationCategory.EXECUTE


def is_read_only(category: OperationCategory) -> bool:
    """Whether the category performs no mutation."""
    return category is OperationCategory.READ


def is_forbidden(category: OperationCategory) -> bool:
    """Whether the category is never allowed."""
    return category is OperationCategory.FORBIDDEN


def classify_request(text: str) -> OperationCategory:
    """Deterministically classify a shopkeeper request.

    Step 1 uses a small keyword policy (not an LLM classifier) so the
    boundary is auditable. Later steps may route ambiguous requests
    through safe reads or clarification; the ``EXECUTE``/``FORBIDDEN``
    outcomes here stay conservative by design.
    """
    lowered = text.lower()

    if any(marker in lowered for marker in FORBIDDEN_REQUEST_MARKERS):
        return OperationCategory.FORBIDDEN

    execute_markers = (
        "create sale",
        "record sale",
        "receive purchase",
        "record purchase",
        "record payment",
        "adjust stock",
        "record expense",
        "dena hai",  # e.g. "Ali ko 3 gaz ... dena hai" (intent to transact)
        "execute",
        "confirm and save",
    )
    if any(marker in lowered for marker in execute_markers):
        first_sentence = lowered.split(".")[0]
        preview_markers = ("prepare", "draft", "preview", "batao kya hoga", "show what")
        if any(marker in first_sentence for marker in preview_markers):
            return OperationCategory.PREPARE
        return OperationCategory.EXECUTE

    prepare_markers = ("prepare", "draft", "preview", "tyaar karo")
    if any(marker in lowered for marker in prepare_markers):
        return OperationCategory.PREPARE

    return OperationCategory.READ


def contains_forbidden_sql(statement: str) -> bool:
    """Return True if ``statement`` contains a forbidden SQL keyword.

    Word-boundary aware and case-insensitive, so ``"selected"`` does not
    trip on ``SELECT``-style prefixes and ``"inserted"`` does not trip on
    ``INSERT``.
    """
    import re

    for keyword in FORBIDDEN_SQL_KEYWORDS:
        if re.search(rf"(?<![A-Za-z0-9_]){keyword}(?![A-Za-z0-9_])", statement, re.IGNORECASE):
            return True
    return False

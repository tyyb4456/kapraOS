"""Platform-admin audit log.

The Super Admin Panel introduces a small number of platform-level
operations. The scheme is intentionally minimal: one append-only table
recording *who* performed a sensitive administrative action, *what* the
action was, and *which* entity it targeted.

In this phase the only writer is the ``grant_platform_admin`` bootstrap
script (granting/revoking the platform-admin flag). Read-only admin
endpoints do not write rows - auditing every page view would grow the
table linearly with traffic for no operational value.

Fields:
    id            - UUID primary key
    actor_user_id - the platform admin who performed the action
                    (nullable + ON DELETE SET NULL so the audit history
                    survives actor removal)
    action        - stable machine-readable action name,
                    e.g. ``platform_admin.granted``
    target_type   - entity kind the action was applied to, e.g. ``user``
    target_id     - entity identifier (no cross-table FK: audit rows must
                    outlive their targets)
    details       - small JSON payload with action-specific context
    created_at    - when it happened (database clock)
"""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class AdminAuditLog(Base, UUIDMixin, TimestampMixin):
    """One appended entry describing a platform-admin action."""

    __tablename__ = "admin_audit_logs"

    __table_args__ = (Index("ix_admin_audit_logs_created_at", "created_at"),)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    action: Mapped[str] = mapped_column(String(100), nullable=False)

    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"AdminAuditLog(action={self.action!r}, target_id={self.target_id!r})"

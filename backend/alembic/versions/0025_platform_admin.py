"""platform admin flag and audit log

Adds the Super Admin Panel foundation:

1. ``users.is_platform_admin`` - a backward-compatible boolean that
   defaults to ``false`` for every existing and future user. Platform
   admin access is never granted by ordinary signup/provisioning; it is
   only set by the operator-run ``grant_platform_admin`` script.
2. ``admin_audit_logs`` - a minimal append-only audit trail for
   platform-level administrative actions (currently: granting/revoking
   the platform-admin flag).

Both changes are additive; no existing column, constraint or data is
modified.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=True),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_admin_audit_logs_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_audit_logs_actor_user_id",
        "admin_audit_logs",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_admin_audit_logs_target_id",
        "admin_audit_logs",
        ["target_id"],
    )
    op.create_index(
        "ix_admin_audit_logs_created_at",
        "admin_audit_logs",
        ["created_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_admin_audit_logs_created_at", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_target_id", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_actor_user_id", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")

    op.drop_column("users", "is_platform_admin")

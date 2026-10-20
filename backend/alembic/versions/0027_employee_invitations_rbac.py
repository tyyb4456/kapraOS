"""employee invitations and sales attribution

Adds the persistence for owner-authorized employee onboarding and the
least-privilege role it grants:

1. ``user_role`` gains the ``cashier`` value (treated by the API as the
   limited "salesman" role). Existing rows are untouched; OWNER and STAFF
   keep their current meaning, and STAFF is still a valid non-owner value.
2. ``employee_invitations`` stores one row per invitation: the shop and
   inviter it belongs to, the normalized invitee email, the role to grant,
   the SHA-256 digest of the single-use token (the raw token is never
   stored or logged), expiry, and accepted/revoked state.
3. ``sales.created_by_user_id`` records the authenticated user who rang up
   a sale so a cashier can be restricted to their own sales. Nullable: all
   pre-existing sales keep working and are owner-visible only.

All changes are additive; no row is deleted or rewritten.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_ROLE_ENUM = postgresql.ENUM(
    "owner",
    "cashier",
    "staff",
    name="user_role",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Extend the existing enum type with the employee role. PostgreSQL 12+
    # allows ADD VALUE inside a transaction as long as the new value is not
    # used in the same transaction - the table created below only stores the
    # literal via type reference, and no row is inserted here.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'cashier'")

    # 2. Invitations.
    op.create_table(
        "employee_invitations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("invited_by_user_id", sa.UUID(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", USER_ROLE_ENUM, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["shop_id"],
            ["shops.id"],
            name="fk_employee_invitations_shop_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name="fk_employee_invitations_invited_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name="fk_employee_invitations_accepted_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_employee_invitations_token_hash",
        "employee_invitations",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_employee_invitations_shop_created_at",
        "employee_invitations",
        ["shop_id", "created_at"],
    )
    op.create_index(
        "ix_employee_invitations_email",
        "employee_invitations",
        ["email"],
    )

    # 3. Sale author attribution (server-derived; nullable for legacy rows).
    op.add_column(
        "sales",
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_sales_created_by_user_id",
        "sales",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_sales_shop_created_by_user_id_created_at",
        "sales",
        ["shop_id", "created_by_user_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops only what this migration added. The ``cashier`` enum value is
    recreated away by rebuilding ``user_role`` without it; any rows still
    holding it are first mapped back to ``staff`` (the pre-0027 non-owner
    role), which is inherently lossy but keeps the downgrade total.
    """
    op.drop_index(
        "ix_sales_shop_created_by_user_id_created_at", table_name="sales"
    )
    op.drop_constraint("fk_sales_created_by_user_id", "sales", type_="foreignkey")
    op.drop_column("sales", "created_by_user_id")

    op.drop_index("ix_employee_invitations_email", table_name="employee_invitations")
    op.drop_index(
        "ix_employee_invitations_shop_created_at",
        table_name="employee_invitations",
    )
    op.drop_index(
        "uq_employee_invitations_token_hash", table_name="employee_invitations"
    )
    op.drop_table("employee_invitations")

    # Remove 'cashier' from the enum by recreating the type.
    op.execute("ALTER TYPE user_role RENAME TO user_role_v2")
    op.execute("CREATE TYPE user_role AS ENUM ('owner', 'staff')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role DROP DEFAULT"
    )
    op.execute("UPDATE users SET role = 'staff' WHERE role = 'cashier'")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE user_role "
        "USING role::text::user_role"
    )
    op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'staff'::user_role")
    op.execute("DROP TYPE user_role_v2")

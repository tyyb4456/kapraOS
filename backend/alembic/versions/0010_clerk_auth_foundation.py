"""clerk auth foundation

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-17 00:00:00.000000

Changes:
- Add `clerk_user_id` to `users` (unique)
- Drop `password_hash` from `users`
- Replace `user_role` enum values: owner and staff only
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None

USER_ROLE_V2_ENUM = postgresql.ENUM(
    "owner",
    "staff",
    name="user_role_v2",
    create_type=False,
)

USER_ROLE_V1_ENUM = postgresql.ENUM(
    "owner",
    "manager",
    "cashier",
    "inventory_manager",
    name="user_role_v1",
    create_type=False,
)


def upgrade() -> None:
    # Step 1: Create the new enum type with only owner/staff.
    USER_ROLE_V2_ENUM.create(op.get_bind(), checkfirst=True)

    # Step 2: Add clerk_user_id column.
    op.add_column(
        "users",
        sa.Column("clerk_user_id", sa.String(255), nullable=True),
    )

    # Step 3: Change role column to text temporarily so we can
    # update values without enum constraints.
    op.execute(
        sa.text(
            "ALTER TABLE users ALTER COLUMN role TYPE TEXT"
        )
    )

    # Step 4: Migrate existing role data.
    op.execute(
        sa.text(
            "UPDATE users SET role = 'staff' "
            "WHERE role IN ('manager', 'cashier', 'inventory_manager')"
        )
    )

    # Step 5: Change column to use the new enum type.
    # Drop the server_default first because it references the old type.
    op.execute(
        sa.text(
            "ALTER TABLE users ALTER COLUMN role DROP DEFAULT"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE users ALTER COLUMN role TYPE user_role_v2 "
            "USING role::text::user_role_v2"
        )
    )
    # Restore the default for the new type.
    op.execute(
        sa.text(
            "ALTER TABLE users ALTER COLUMN role SET DEFAULT 'staff'::user_role_v2"
        )
    )

    # Step 6: Drop the old enum type (column no longer references it).
    op.execute(
        sa.text("DROP TYPE user_role")
    )

    # Step 7: Rename user_role_v2 to user_role.
    op.execute(
        sa.text("ALTER TYPE user_role_v2 RENAME TO user_role")
    )

    # Step 8: Create unique constraint on clerk_user_id.
    op.create_unique_constraint(
        "uq_users_clerk_user_id", "users", ["clerk_user_id"]
    )

    # Step 9: Create index on clerk_user_id for lookups.
    op.create_index("ix_users_clerk_user_id", "users", ["clerk_user_id"])

    # Step 10: Drop password_hash column.
    op.drop_column("users", "password_hash")


def downgrade() -> None:
    # Step 1: Restore password_hash column.
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(length=255), nullable=False),
    )

    # Step 2: Remove unique constraint.
    op.drop_constraint(
        "uq_users_clerk_user_id", "users", type_="unique"
    )

    # Step 3: Drop clerk_user_id column and its index.
    op.drop_index("ix_users_clerk_user_id", table_name="users")
    op.drop_column("users", "clerk_user_id")

    # Step 4: Change role column to text temporarily.
    op.execute(
        sa.text("ALTER TABLE users ALTER COLUMN role TYPE TEXT")
    )

    # Step 5: Rename current user_role to user_role_v2 temporarily.
    op.execute(
        sa.text("ALTER TYPE user_role RENAME TO user_role_v2")
    )

    # Step 6: Create the old enum type.
    USER_ROLE_V1_ENUM.create(op.get_bind(), checkfirst=True)

    # Step 7: Change column to use the old enum type.
    op.execute(
        sa.text(
            "ALTER TABLE users ALTER COLUMN role DROP DEFAULT"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE users ALTER COLUMN role TYPE user_role_v1 "
            "USING role::text::user_role_v1"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE users ALTER COLUMN role SET DEFAULT 'cashier'::user_role_v1"
        )
    )

    # Step 8: Drop the v2 enum.
    op.execute(
        sa.text("DROP TYPE user_role_v2")
    )

    # Step 9: Rename old enum back to user_role.
    op.execute(
        sa.text("ALTER TYPE user_role_v1 RENAME TO user_role")
    )
"""add opening to inventory movement type

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-18 00:00:00.000000

Extends the `inventory_movement_type` PostgreSQL enum with `opening`, the
movement type recorded for opening-inventory stock (a shop's starting
balance). Deliberately kept in its own migration, ahead of the one that
creates the opening-inventory tables:

* PostgreSQL cannot *use* a new enum value in the same transaction that
  added it, so no table/row in this migration references `opening`.
* `ALTER TYPE ... ADD VALUE` runs in an Alembic autocommit block, the safe
  pattern for enum extensions on older PostgreSQL versions too.
* `IF NOT EXISTS` makes the migration re-runnable against a database whose
  type already carries the value.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE inventory_movement_type ADD VALUE IF NOT EXISTS 'opening'"
        )


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value, and by design a downgraded
    # database may still hold `opening` movements that reference it. The
    # value is inert once the feature's tables are dropped (migration 0023),
    # and the type itself is removed with the base schema. Deliberate no-op.
    pass

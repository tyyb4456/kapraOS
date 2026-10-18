"""add missing non-negative check on inventory.reorder_level

The ORM model declares ``ck_inventory_reorder_level_non_negative`` but no
migration ever created it (``9f632c58caae`` only added the column), so the
database would accept a negative reorder level. This revision aligns the
schema with the model.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-19

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        "ck_inventory_reorder_level_non_negative",
        "inventory",
        "reorder_level >= 0",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_inventory_reorder_level_non_negative",
        "inventory",
        type_="check",
    )

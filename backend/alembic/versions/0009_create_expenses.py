"""create expenses

Step 10 (expense domain) adds exactly one table (`db_arch.md` section 22):
`expenses`, an immediate-payment operating expense.

Nothing else changes at the database level. The new system accounts - Cost of
Goods Sold plus the expense accounts - are not rows this migration creates:
`app.services.accounting.ensure_system_accounts()` provisions a shop's chart of
accounts idempotently, so every existing shop picks the new accounts up the next
time it posts anything (`step_10_desc.md` sections 3 and 30). That is the
smallest migration the step needs.

Constraints carry the invariants the service layer must not be trusted with
alone (`step_10_desc.md` section 19):

* `amount > 0` - an expense moves a positive amount of money;
* `expense_category` and `payment_method` are PostgreSQL enums, so an invalid
  category or payment method cannot be stored even by a raw write;
* `shop_id` is a real foreign key, so an expense always belongs to one shop.

The composite-FK tenant pattern used elsewhere in the project (e.g.
`payments`, `inventory_movements`) is not needed here: nothing references an
expense by a `(id, shop_id)` pair, because ledger entries point at their source
by `(reference_type, reference_id)` instead of a foreign key.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-16 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPENSE_CATEGORY = postgresql.ENUM(
    "rent",
    "salary",
    "utilities",
    "transport",
    "marketing",
    "maintenance",
    "supplies",
    "other",
    name="expense_category",
    create_type=False,
)

# Created back in migration 0005; reused, never redefined.
PAYMENT_METHOD = postgresql.ENUM(
    "cash",
    "card",
    "bank",
    "jazzcash",
    "easypaisa",
    "other",
    name="payment_method",
    create_type=False,
)


def upgrade() -> None:
    EXPENSE_CATEGORY.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "expenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", EXPENSE_CATEGORY, nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("payment_method", PAYMENT_METHOD, nullable=False),
        sa.Column(
            "expense_date",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
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
            ["shop_id"],
            ["shops.id"],
            name="fk_expenses_shop_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_expenses"),
        sa.CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
    )
    op.create_index("ix_expenses_shop_id", "expenses", ["shop_id"])
    op.create_index(
        "ix_expenses_shop_expense_date", "expenses", ["shop_id", "expense_date"]
    )
    op.create_index(
        "ix_expenses_shop_created_at", "expenses", ["shop_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_expenses_shop_created_at", table_name="expenses")
    op.drop_index("ix_expenses_shop_expense_date", table_name="expenses")
    op.drop_index("ix_expenses_shop_id", table_name="expenses")
    op.drop_table("expenses")

    EXPENSE_CATEGORY.drop(op.get_bind(), checkfirst=True)
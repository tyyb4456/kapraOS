"""ai expense receipts (Step 6 idempotency for AI-assisted expenses)

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-26 00:00:00.000000

Adds `ai_expense_receipts`: one row per approved AI expense operation,
keyed by (shop_id, operation_key). The write tool checks this table
before calling `expenses.create_expense()` and writes the receipt in
the same transaction as the expense, so a duplicate resume/retry
returns the already-created expense instead of creating a second one.
The unique constraint is the final guard under concurrency.

A separate table (rather than reusing `ai_sale_receipts`,
`ai_payment_receipts`, or `ai_supplier_payment_receipts`) keeps the
data model honest: those tables' names and FK history are
sale-/payment-specific.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_expense_receipts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("operation_key", sa.String(length=64), nullable=False),
        sa.Column("expense_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["expense_id"], ["expenses.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_id",
            "operation_key",
            name="uq_ai_expense_receipts_shop_operation",
        ),
        sa.CheckConstraint(
            "length(trim(operation_key)) > 0",
            name="ck_ai_expense_receipts_key_not_blank",
        ),
    )
    op.create_index(
        op.f("ix_ai_expense_receipts_shop_id"),
        "ai_expense_receipts",
        ["shop_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_expense_receipts_expense_id"),
        "ai_expense_receipts",
        ["expense_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_expense_receipts_shop_created_at",
        "ai_expense_receipts",
        ["shop_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_expense_receipts_shop_created_at",
        table_name="ai_expense_receipts",
    )
    op.drop_index(
        op.f("ix_ai_expense_receipts_expense_id"),
        table_name="ai_expense_receipts",
    )
    op.drop_index(
        op.f("ix_ai_expense_receipts_shop_id"),
        table_name="ai_expense_receipts",
    )
    op.drop_table("ai_expense_receipts")

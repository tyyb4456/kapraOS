"""ai purchase receipts (Step 7 idempotency for AI-assisted purchases)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-26 00:00:00.000000

Adds `ai_purchase_receipts`: one row per approved AI purchase operation,
keyed by (shop_id, operation_key). The write tool checks this table
before calling `purchases.create_purchase()` and writes the receipt in
the same transaction as the purchase, so a duplicate resume/retry
returns the already-created purchase instead of creating a second one.
The unique constraint is the final guard under concurrency.

A separate table (rather than reusing `ai_sale_receipts`,
`ai_payment_receipts`, `ai_supplier_payment_receipts`, or
`ai_expense_receipts`) keeps the data model honest: those tables' names
and FK history are sale-/payment-/expense-specific.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_purchase_receipts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("operation_key", sa.String(length=64), nullable=False),
        sa.Column("purchase_id", sa.UUID(), nullable=True),
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
            ["purchase_id"], ["purchases.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_id",
            "operation_key",
            name="uq_ai_purchase_receipts_shop_operation",
        ),
        sa.CheckConstraint(
            "length(trim(operation_key)) > 0",
            name="ck_ai_purchase_receipts_key_not_blank",
        ),
    )
    op.create_index(
        op.f("ix_ai_purchase_receipts_shop_id"),
        "ai_purchase_receipts",
        ["shop_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_purchase_receipts_purchase_id"),
        "ai_purchase_receipts",
        ["purchase_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_purchase_receipts_shop_created_at",
        "ai_purchase_receipts",
        ["shop_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_purchase_receipts_shop_created_at",
        table_name="ai_purchase_receipts",
    )
    op.drop_index(
        op.f("ix_ai_purchase_receipts_purchase_id"),
        table_name="ai_purchase_receipts",
    )
    op.drop_index(
        op.f("ix_ai_purchase_receipts_shop_id"),
        table_name="ai_purchase_receipts",
    )
    op.drop_table("ai_purchase_receipts")

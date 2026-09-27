"""ai return receipts (Step 9 idempotency for AI-assisted returns)

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-27 00:00:00.000000

Adds `ai_customer_return_receipts` and `ai_supplier_return_receipts`: one
row per approved AI return operation, keyed by (shop_id, operation_key).
Each write tool checks its table before calling the authoritative return
service (`returns.create_sale_return()` /
`returns.create_purchase_return()`) and writes the receipt in the same
transaction as the return, so a duplicate resume/retry returns the
already-created return instead of creating a second one. The unique
constraint is the final guard under concurrency.

Separate tables (rather than reusing any earlier receipt table) keep the
data model honest: earlier tables' names and FK history are specific to
their own domains. The core `sale_returns` / `purchase_returns` tables
are unchanged — Step 8 behaviour is untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_customer_return_receipts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("operation_key", sa.String(length=64), nullable=False),
        sa.Column("return_id", sa.UUID(), nullable=True),
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
            ["return_id"], ["sale_returns.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_id",
            "operation_key",
            name="uq_ai_customer_return_receipts_shop_operation",
        ),
        sa.CheckConstraint(
            "length(trim(operation_key)) > 0",
            name="ck_ai_customer_return_receipts_key_not_blank",
        ),
    )
    op.create_index(
        op.f("ix_ai_customer_return_receipts_shop_id"),
        "ai_customer_return_receipts",
        ["shop_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_customer_return_receipts_return_id"),
        "ai_customer_return_receipts",
        ["return_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_customer_return_receipts_shop_created_at",
        "ai_customer_return_receipts",
        ["shop_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "ai_supplier_return_receipts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("operation_key", sa.String(length=64), nullable=False),
        sa.Column("return_id", sa.UUID(), nullable=True),
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
            ["return_id"], ["purchase_returns.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_id",
            "operation_key",
            name="uq_ai_supplier_return_receipts_shop_operation",
        ),
        sa.CheckConstraint(
            "length(trim(operation_key)) > 0",
            name="ck_ai_supplier_return_receipts_key_not_blank",
        ),
    )
    op.create_index(
        op.f("ix_ai_supplier_return_receipts_shop_id"),
        "ai_supplier_return_receipts",
        ["shop_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_supplier_return_receipts_return_id"),
        "ai_supplier_return_receipts",
        ["return_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_supplier_return_receipts_shop_created_at",
        "ai_supplier_return_receipts",
        ["shop_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_supplier_return_receipts_shop_created_at",
        table_name="ai_supplier_return_receipts",
    )
    op.drop_index(
        op.f("ix_ai_supplier_return_receipts_return_id"),
        table_name="ai_supplier_return_receipts",
    )
    op.drop_index(
        op.f("ix_ai_supplier_return_receipts_shop_id"),
        table_name="ai_supplier_return_receipts",
    )
    op.drop_table("ai_supplier_return_receipts")

    op.drop_index(
        "ix_ai_customer_return_receipts_shop_created_at",
        table_name="ai_customer_return_receipts",
    )
    op.drop_index(
        op.f("ix_ai_customer_return_receipts_return_id"),
        table_name="ai_customer_return_receipts",
    )
    op.drop_index(
        op.f("ix_ai_customer_return_receipts_shop_id"),
        table_name="ai_customer_return_receipts",
    )
    op.drop_table("ai_customer_return_receipts")

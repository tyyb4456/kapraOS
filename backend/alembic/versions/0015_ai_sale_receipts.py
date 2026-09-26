"""ai sale receipts (Step 3 idempotency for AI-assisted sale creation)

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-26 00:00:00.000000

Adds `ai_sale_receipts`: one row per approved AI sale operation,
keyed by (shop_id, operation_key). The write tool checks this table
before calling `SaleService.create_sale()` and writes the receipt in
the same transaction as the sale, so a duplicate resume/retry returns
the already-created sale instead of creating a second one. The unique
constraint is the final guard under concurrency.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_sale_receipts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("operation_key", sa.String(length=64), nullable=False),
        sa.Column("sale_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_id",
            "operation_key",
            name="uq_ai_sale_receipts_shop_operation",
        ),
        sa.CheckConstraint(
            "length(trim(operation_key)) > 0",
            name="ck_ai_sale_receipts_key_not_blank",
        ),
    )
    op.create_index(
        op.f("ix_ai_sale_receipts_shop_id"),
        "ai_sale_receipts",
        ["shop_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_sale_receipts_sale_id"),
        "ai_sale_receipts",
        ["sale_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_sale_receipts_shop_created_at",
        "ai_sale_receipts",
        ["shop_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_sale_receipts_shop_created_at",
        table_name="ai_sale_receipts",
    )
    op.drop_index(op.f("ix_ai_sale_receipts_sale_id"), table_name="ai_sale_receipts")
    op.drop_index(op.f("ix_ai_sale_receipts_shop_id"), table_name="ai_sale_receipts")
    op.drop_table("ai_sale_receipts")

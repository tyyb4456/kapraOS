"""core customer & supplier return mechanism

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-27 00:00:00.000000

Adds explicit persistent return records:

* `sale_returns` + `sale_return_items` — customer returns referencing the
  original sale + sale item, with authoritative unit_price/cost_price,
  proportional discount share, and AR-vs-cash split
  (`total = ar_amount + cash_refund`).
* `purchase_returns` + `purchase_return_items` — supplier returns
  referencing the original purchase + purchase item, with authoritative
  unit_cost and proportional header-discount share.

Tenant isolation mirrors sales/purchases: composite FKs
`(sale_id, shop_id)`, `(purchase_id, shop_id)`, `(customer_id, shop_id)`,
`(supplier_id, shop_id)` and `(variant_id, shop_id)` with RESTRICT where
history must block deletion and CASCADE where cleanup must flow.
Line totals reuse the sale/purchase invariant
`total = round(qty * price, 2) - discount`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sale_returns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("sale_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "ar_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cash_refund",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cogs_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("notes", sa.String(length=500), nullable=True),
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
            ["sale_id", "shop_id"],
            ["sales.id", "sales.shop_id"],
            name="fk_sale_returns_sale_same_shop",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id", "shop_id"],
            ["customers.id", "customers.shop_id"],
            name="fk_sale_returns_customer_same_shop",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("total_amount >= 0", name="ck_sale_returns_total_non_negative"),
        sa.CheckConstraint("ar_amount >= 0", name="ck_sale_returns_ar_non_negative"),
        sa.CheckConstraint("cash_refund >= 0", name="ck_sale_returns_cash_non_negative"),
        sa.CheckConstraint("cogs_amount >= 0", name="ck_sale_returns_cogs_non_negative"),
        sa.CheckConstraint(
            "total_amount = ar_amount + cash_refund",
            name="ck_sale_returns_total_matches_split",
        ),
    )
    op.create_index("ix_sale_returns_shop_id", "sale_returns", ["shop_id"], unique=False)
    op.create_index(
        op.f("ix_sale_returns_sale_id"), "sale_returns", ["sale_id"], unique=False
    )
    op.create_index(
        "ix_sale_returns_shop_created_at", "sale_returns", ["shop_id", "created_at"]
    )
    op.create_index(
        "ix_sale_returns_shop_sale_created_at",
        "sale_returns",
        ["shop_id", "sale_id", "created_at"],
    )

    op.create_table(
        "sale_return_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("return_id", sa.UUID(), nullable=False),
        sa.Column("sale_item_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("cost_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "discount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["return_id"], ["sale_returns.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sale_item_id"], ["sale_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["variant_id", "shop_id"],
            ["product_variants.id", "product_variants.shop_id"],
            name="fk_sale_return_items_variant_same_shop",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "return_id", "sale_item_id", name="uq_sale_return_items_return_sale_item"
        ),
        sa.CheckConstraint(
            "total = round(quantity * unit_price, 2) - discount",
            name="ck_sale_return_items_total_matches_line",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_sale_return_items_quantity_positive"),
        sa.CheckConstraint(
            "unit_price >= 0", name="ck_sale_return_items_unit_price_non_negative"
        ),
        sa.CheckConstraint(
            "cost_price >= 0", name="ck_sale_return_items_cost_price_non_negative"
        ),
        sa.CheckConstraint(
            "discount >= 0", name="ck_sale_return_items_discount_non_negative"
        ),
        sa.CheckConstraint("total >= 0", name="ck_sale_return_items_total_non_negative"),
    )
    op.create_index(
        op.f("ix_sale_return_items_return_id"),
        "sale_return_items",
        ["return_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sale_return_items_sale_item_id"),
        "sale_return_items",
        ["sale_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sale_return_items_variant_id"),
        "sale_return_items",
        ["variant_id"],
        unique=False,
    )

    op.create_table(
        "purchase_returns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("purchase_id", sa.UUID(), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
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
            ["purchase_id", "shop_id"],
            ["purchases.id", "purchases.shop_id"],
            name="fk_purchase_returns_purchase_same_shop",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id", "shop_id"],
            ["suppliers.id", "suppliers.shop_id"],
            name="fk_purchase_returns_supplier_same_shop",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "total_amount >= 0", name="ck_purchase_returns_total_non_negative"
        ),
    )
    op.create_index(
        "ix_purchase_returns_shop_id", "purchase_returns", ["shop_id"], unique=False
    )
    op.create_index(
        op.f("ix_purchase_returns_purchase_id"),
        "purchase_returns",
        ["purchase_id"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_returns_shop_created_at",
        "purchase_returns",
        ["shop_id", "created_at"],
    )
    op.create_index(
        "ix_purchase_returns_shop_purchase_created_at",
        "purchase_returns",
        ["shop_id", "purchase_id", "created_at"],
    )

    op.create_table(
        "purchase_return_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("return_id", sa.UUID(), nullable=False),
        sa.Column("purchase_item_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "discount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["return_id"], ["purchase_returns.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["purchase_item_id"], ["purchase_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["variant_id", "shop_id"],
            ["product_variants.id", "product_variants.shop_id"],
            name="fk_purchase_return_items_variant_same_shop",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "return_id",
            "purchase_item_id",
            name="uq_purchase_return_items_return_purchase_item",
        ),
        sa.CheckConstraint(
            "total = round(quantity * unit_cost, 2) - discount",
            name="ck_purchase_return_items_total_matches_line",
        ),
        sa.CheckConstraint(
            "quantity > 0", name="ck_purchase_return_items_quantity_positive"
        ),
        sa.CheckConstraint(
            "unit_cost >= 0", name="ck_purchase_return_items_unit_cost_non_negative"
        ),
        sa.CheckConstraint(
            "discount >= 0", name="ck_purchase_return_items_discount_non_negative"
        ),
        sa.CheckConstraint(
            "total >= 0", name="ck_purchase_return_items_total_non_negative"
        ),
    )
    op.create_index(
        op.f("ix_purchase_return_items_return_id"),
        "purchase_return_items",
        ["return_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_purchase_return_items_purchase_item_id"),
        "purchase_return_items",
        ["purchase_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_purchase_return_items_variant_id"),
        "purchase_return_items",
        ["variant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_purchase_return_items_variant_id"),
        table_name="purchase_return_items",
    )
    op.drop_index(
        op.f("ix_purchase_return_items_purchase_item_id"),
        table_name="purchase_return_items",
    )
    op.drop_index(
        op.f("ix_purchase_return_items_return_id"),
        table_name="purchase_return_items",
    )
    op.drop_table("purchase_return_items")

    op.drop_index(
        "ix_purchase_returns_shop_purchase_created_at",
        table_name="purchase_returns",
    )
    op.drop_index(
        "ix_purchase_returns_shop_created_at", table_name="purchase_returns"
    )
    op.drop_index(
        op.f("ix_purchase_returns_purchase_id"), table_name="purchase_returns"
    )
    op.drop_index("ix_purchase_returns_shop_id", table_name="purchase_returns")
    op.drop_table("purchase_returns")

    op.drop_index(
        op.f("ix_sale_return_items_variant_id"),
        table_name="sale_return_items",
    )
    op.drop_index(
        op.f("ix_sale_return_items_sale_item_id"),
        table_name="sale_return_items",
    )
    op.drop_index(
        op.f("ix_sale_return_items_return_id"),
        table_name="sale_return_items",
    )
    op.drop_table("sale_return_items")

    op.drop_index(
        "ix_sale_returns_shop_sale_created_at", table_name="sale_returns"
    )
    op.drop_index("ix_sale_returns_shop_created_at", table_name="sale_returns")
    op.drop_index(op.f("ix_sale_returns_sale_id"), table_name="sale_returns")
    op.drop_index("ix_sale_returns_shop_id", table_name="sale_returns")
    op.drop_table("sale_returns")

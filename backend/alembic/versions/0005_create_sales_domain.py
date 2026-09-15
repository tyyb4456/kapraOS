"""create sales domain

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same pattern as prefixes 0001-0004: declare each ENUM once with
# `create_type=False` so it is never auto-created/dropped as a side effect of
# table DDL, then create/drop the type explicitly in upgrade/downgrade.
sale_status_enum = postgresql.ENUM(
    "completed",
    "partial",
    "cancelled",
    "returned",
    name="sale_status",
    create_type=False,
)

payment_method_enum = postgresql.ENUM(
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
    # --- Step 5 additions to existing tables ------------------------------

    # V1 stock valuation (`db_arch.md` section 29): moving weighted-average
    # cost of the stock on hand, four decimal places so repeated averages do
    # not compound rounding error. Existing rows start at 0.
    op.add_column(
        "inventory",
        sa.Column(
            "weighted_average_cost",
            sa.Numeric(precision=14, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_inventory_weighted_average_cost_non_negative",
        "inventory",
        "weighted_average_cost >= 0",
    )

    # Supports `payments`' composite foreign key (purchase_id, shop_id).
    op.create_unique_constraint(
        "uq_purchases_id_shop_id",
        "purchases",
        ["id", "shop_id"],
    )

    sale_status_enum.create(op.get_bind(), checkfirst=True)
    payment_method_enum.create(op.get_bind(), checkfirst=True)

    # --- customers: depends on shops --------------------------------------
    op.create_table(
        "customers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("address", sa.String(length=300), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        # Supports `sales`' and `payments`' composite foreign keys
        # (customer_id, shop_id).
        sa.UniqueConstraint("id", "shop_id", name="uq_customers_id_shop_id"),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name="ck_customers_name_not_blank"
        ),
    )
    op.create_index(op.f("ix_customers_shop_id"), "customers", ["shop_id"], unique=False)
    op.create_index("ix_customers_shop_name", "customers", ["shop_id", "name"])
    op.create_index("ix_customers_shop_phone", "customers", ["shop_id", "phone"])

    # --- sales: depends on shops, customers -------------------------------
    op.create_table(
        "sales",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("invoice_number", sa.String(length=50), nullable=True),
        sa.Column("subtotal", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "discount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "paid_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("status", sale_status_enum, nullable=False),
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
        # Composite tenant guard: the customer (when present) must belong to
        # the same shop. customer_id is nullable, so MATCH SIMPLE skips the
        # check for walk-in sales. RESTRICT: a customer with sale history must
        # not be deletable in a way that erases that history.
        sa.ForeignKeyConstraint(
            ["customer_id", "shop_id"],
            ["customers.id", "customers.shop_id"],
            name="fk_sales_customer_same_shop",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        # Supports `payments`' composite foreign key (sale_id, shop_id).
        sa.UniqueConstraint("id", "shop_id", name="uq_sales_id_shop_id"),
        sa.CheckConstraint("subtotal >= 0", name="ck_sales_subtotal_non_negative"),
        sa.CheckConstraint("discount >= 0", name="ck_sales_discount_non_negative"),
        sa.CheckConstraint("total >= 0", name="ck_sales_total_non_negative"),
        sa.CheckConstraint(
            "paid_amount >= 0", name="ck_sales_paid_amount_non_negative"
        ),
        sa.CheckConstraint(
            "discount <= subtotal", name="ck_sales_discount_not_above_subtotal"
        ),
        sa.CheckConstraint(
            "total = subtotal - discount", name="ck_sales_total_matches_subtotal"
        ),
        sa.CheckConstraint(
            "paid_amount <= total", name="ck_sales_paid_not_above_total"
        ),
    )
    op.create_index(op.f("ix_sales_shop_id"), "sales", ["shop_id"], unique=False)
    op.create_index("ix_sales_shop_created_at", "sales", ["shop_id", "created_at"])
    op.create_index("ix_sales_shop_customer_id", "sales", ["shop_id", "customer_id"])
    # Invoice number is unique per shop only when present.
    op.create_index(
        "uq_sales_shop_invoice_number",
        "sales",
        ["shop_id", "invoice_number"],
        unique=True,
        postgresql_where=sa.text("invoice_number IS NOT NULL"),
    )

    # --- sale_items: depends on sales, product_variants -------------------
    op.create_table(
        "sale_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sale_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["variant_id"], ["product_variants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # One line per variant per sale: repeats are combined by the service.
        sa.UniqueConstraint(
            "sale_id", "variant_id", name="uq_sale_items_sale_variant"
        ),
        # `total` must always agree with the line math; this mirrors the
        # service's Decimal rounding (half away from zero).
        sa.CheckConstraint(
            "total = round(quantity * unit_price, 2) - discount",
            name="ck_sale_items_total_matches_line",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_sale_items_quantity_positive"),
        sa.CheckConstraint(
            "unit_price >= 0", name="ck_sale_items_unit_price_non_negative"
        ),
        sa.CheckConstraint(
            "cost_price >= 0", name="ck_sale_items_cost_price_non_negative"
        ),
        sa.CheckConstraint("discount >= 0", name="ck_sale_items_discount_non_negative"),
        sa.CheckConstraint("total >= 0", name="ck_sale_items_total_non_negative"),
    )
    op.create_index(
        op.f("ix_sale_items_sale_id"), "sale_items", ["sale_id"], unique=False
    )
    op.create_index(
        op.f("ix_sale_items_variant_id"), "sale_items", ["variant_id"], unique=False
    )

    # --- payments: depends on shops, customers, suppliers, sales, ---------
    # --- purchases --------------------------------------------------------
    op.create_table(
        "payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("supplier_id", sa.UUID(), nullable=True),
        sa.Column("sale_id", sa.UUID(), nullable=True),
        sa.Column("purchase_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("method", payment_method_enum, nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=True),
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
        # Composite tenant guards: each nullable reference is paired with
        # shop_id so a payment can only point at same-shop rows. RESTRICT for
        # parties (a customer/supplier with payment history is not deletable),
        # CASCADE for documents (deleting a sale/purchase takes its payments).
        sa.ForeignKeyConstraint(
            ["customer_id", "shop_id"],
            ["customers.id", "customers.shop_id"],
            name="fk_payments_customer_same_shop",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id", "shop_id"],
            ["suppliers.id", "suppliers.shop_id"],
            name="fk_payments_supplier_same_shop",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sale_id", "shop_id"],
            ["sales.id", "sales.shop_id"],
            name="fk_payments_sale_same_shop",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_id", "shop_id"],
            ["purchases.id", "purchases.shop_id"],
            name="fk_payments_purchase_same_shop",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
    )
    op.create_index(op.f("ix_payments_shop_id"), "payments", ["shop_id"], unique=False)
    op.create_index("ix_payments_shop_sale_id", "payments", ["shop_id", "sale_id"])
    op.create_index(
        "ix_payments_shop_created_at", "payments", ["shop_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_payments_shop_created_at", table_name="payments")
    op.drop_index("ix_payments_shop_sale_id", table_name="payments")
    op.drop_index(op.f("ix_payments_shop_id"), table_name="payments")
    op.drop_table("payments")

    op.drop_index(op.f("ix_sale_items_variant_id"), table_name="sale_items")
    op.drop_index(op.f("ix_sale_items_sale_id"), table_name="sale_items")
    op.drop_table("sale_items")

    op.drop_index("uq_sales_shop_invoice_number", table_name="sales")
    op.drop_index("ix_sales_shop_customer_id", table_name="sales")
    op.drop_index("ix_sales_shop_created_at", table_name="sales")
    op.drop_index(op.f("ix_sales_shop_id"), table_name="sales")
    op.drop_table("sales")

    op.drop_index("ix_customers_shop_phone", table_name="customers")
    op.drop_index("ix_customers_shop_name", table_name="customers")
    op.drop_index(op.f("ix_customers_shop_id"), table_name="customers")
    op.drop_table("customers")

    payment_method_enum.drop(op.get_bind(), checkfirst=True)
    sale_status_enum.drop(op.get_bind(), checkfirst=True)

    op.drop_constraint("uq_purchases_id_shop_id", "purchases", type_="unique")

    op.drop_constraint(
        "ck_inventory_weighted_average_cost_non_negative",
        "inventory",
        type_="check",
    )
    op.drop_column("inventory", "weighted_average_cost")
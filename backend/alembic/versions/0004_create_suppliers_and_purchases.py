"""create suppliers and purchases

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- suppliers: only depends on shops ---------------------------------
    op.create_table(
        "suppliers",
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
        # Supports `purchases`' composite foreign key (supplier_id, shop_id).
        sa.UniqueConstraint("id", "shop_id", name="uq_suppliers_id_shop_id"),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name="ck_suppliers_name_not_blank"
        ),
    )
    op.create_index(op.f("ix_suppliers_shop_id"), "suppliers", ["shop_id"], unique=False)
    op.create_index("ix_suppliers_shop_name", "suppliers", ["shop_id", "name"])
    op.create_index("ix_suppliers_shop_phone", "suppliers", ["shop_id", "phone"])

    # --- purchases: depends on shops, suppliers ---------------------------
    op.create_table(
        "purchases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
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
        # Composite tenant guard: the supplier must belong to the same shop.
        # RESTRICT (not CASCADE): a supplier with purchase history must not be
        # deletable in a way that silently erases that history.
        sa.ForeignKeyConstraint(
            ["supplier_id", "shop_id"],
            ["suppliers.id", "suppliers.shop_id"],
            name="fk_purchases_supplier_same_shop",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("subtotal >= 0", name="ck_purchases_subtotal_non_negative"),
        sa.CheckConstraint("discount >= 0", name="ck_purchases_discount_non_negative"),
        sa.CheckConstraint("total >= 0", name="ck_purchases_total_non_negative"),
        sa.CheckConstraint(
            "paid_amount >= 0", name="ck_purchases_paid_amount_non_negative"
        ),
        sa.CheckConstraint(
            "discount <= subtotal", name="ck_purchases_discount_not_above_subtotal"
        ),
        sa.CheckConstraint(
            "total = subtotal - discount", name="ck_purchases_total_matches_subtotal"
        ),
        sa.CheckConstraint(
            "paid_amount <= total", name="ck_purchases_paid_not_above_total"
        ),
    )
    op.create_index(op.f("ix_purchases_shop_id"), "purchases", ["shop_id"], unique=False)
    op.create_index("ix_purchases_shop_created_at", "purchases", ["shop_id", "created_at"])
    op.create_index("ix_purchases_supplier_id", "purchases", ["supplier_id"])
    # Invoice number is unique per shop only when present - informal suppliers
    # often provide none, so NULL must repeat.
    op.create_index(
        "uq_purchases_shop_invoice_number",
        "purchases",
        ["shop_id", "invoice_number"],
        unique=True,
        postgresql_where=sa.text("invoice_number IS NOT NULL"),
    )

    # --- purchase_items: depends on purchases, product_variants -----------
    op.create_table(
        "purchase_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("purchase_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["purchase_id"], ["purchases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"], ["product_variants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # One line per variant per purchase: repeats are combined by the
        # service rather than stored twice.
        sa.UniqueConstraint(
            "purchase_id", "variant_id", name="uq_purchase_items_purchase_variant"
        ),
        sa.CheckConstraint(
            "total = round(quantity * unit_cost, 2)",
            name="ck_purchase_items_total_matches_line",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_purchase_items_quantity_positive"),
        sa.CheckConstraint(
            "unit_cost >= 0", name="ck_purchase_items_unit_cost_non_negative"
        ),
        sa.CheckConstraint("total >= 0", name="ck_purchase_items_total_non_negative"),
    )
    op.create_index(
        op.f("ix_purchase_items_purchase_id"),
        "purchase_items",
        ["purchase_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_purchase_items_variant_id"),
        "purchase_items",
        ["variant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_purchase_items_variant_id"), table_name="purchase_items"
    )
    op.drop_index(
        op.f("ix_purchase_items_purchase_id"), table_name="purchase_items"
    )
    op.drop_table("purchase_items")

    op.drop_index("uq_purchases_shop_invoice_number", table_name="purchases")
    op.drop_index("ix_purchases_supplier_id", table_name="purchases")
    op.drop_index("ix_purchases_shop_created_at", table_name="purchases")
    op.drop_index(op.f("ix_purchases_shop_id"), table_name="purchases")
    op.drop_table("purchases")

    op.drop_index("ix_suppliers_shop_phone", table_name="suppliers")
    op.drop_index("ix_suppliers_shop_name", table_name="suppliers")
    op.drop_index(op.f("ix_suppliers_shop_id"), table_name="suppliers")
    op.drop_table("suppliers")
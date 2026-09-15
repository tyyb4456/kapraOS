"""create inventory domain

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same pattern as prefixes 0001/0002: declare the ENUM once with
# `create_type=False` so it is never auto-created/dropped as a side effect of
# table DDL, then create/drop the type explicitly in upgrade/downgrade.
inventory_movement_type_enum = postgresql.ENUM(
    "purchase",
    "sale",
    "customer_return",
    "supplier_return",
    "damage",
    "adjustment",
    name="inventory_movement_type",
    create_type=False,
)


def upgrade() -> None:
    # Supporting unique constraint for `inventory_movements`' composite
    # foreign key (variant_id, shop_id): Postgres needs an explicit unique
    # constraint on exactly that column pair to target it, even though `id`
    # alone is already the primary key.
    op.create_unique_constraint(
        "uq_product_variants_id_shop_id",
        "product_variants",
        ["id", "shop_id"],
    )

    inventory_movement_type_enum.create(op.get_bind(), checkfirst=True)

    # --- inventory: current cached stock per variant ----------------------
    op.create_table(
        "inventory",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column(
            "quantity",
            sa.Numeric(precision=14, scale=3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "reserved_quantity",
            sa.Numeric(precision=14, scale=3),
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
        sa.ForeignKeyConstraint(
            ["variant_id"], ["product_variants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # One inventory row per variant. Also the arbiter for the service
        # layer's `INSERT ... ON CONFLICT DO NOTHING` bootstrap path.
        sa.UniqueConstraint("variant_id", name="uq_inventory_variant_id"),
        sa.CheckConstraint("quantity >= 0", name="ck_inventory_quantity_non_negative"),
        sa.CheckConstraint(
            "reserved_quantity >= 0",
            name="ck_inventory_reserved_quantity_non_negative",
        ),
        sa.CheckConstraint(
            "reserved_quantity <= quantity",
            name="ck_inventory_reserved_not_above_quantity",
        ),
    )

    # --- inventory_movements: append-only stock ledger --------------------
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("movement_type", inventory_movement_type_enum, nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("reference_type", sa.String(length=50), nullable=True),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        # Tenant guard: a movement can never belong to Shop A while pointing
        # at a variant owned by Shop B. Enforced by the database via
        # product_variants' supporting UNIQUE(id, shop_id) above.
        # ON DELETE CASCADE matches the rest of the schema (Shop -> every
        # tenant-owned table): deleting a shop or variant cleans up its
        # ledger rows rather than blocking the delete.
        sa.ForeignKeyConstraint(
            ["variant_id", "shop_id"],
            ["product_variants.id", "product_variants.shop_id"],
            name="fk_inventory_movements_variant_same_shop",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_inventory_movements_shop_id"),
        "inventory_movements",
        ["shop_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_variant_id"),
        "inventory_movements",
        ["variant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_created_at"),
        "inventory_movements",
        ["created_at"],
        unique=False,
    )
    # Per-shop chronological history (the ledger's main read pattern).
    op.create_index(
        "ix_inventory_movements_shop_created_at",
        "inventory_movements",
        ["shop_id", "created_at"],
        unique=False,
    )
    # Per-variant history within a shop.
    op.create_index(
        "ix_inventory_movements_shop_variant_created_at",
        "inventory_movements",
        ["shop_id", "variant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inventory_movements_shop_variant_created_at",
        table_name="inventory_movements",
    )
    op.drop_index(
        "ix_inventory_movements_shop_created_at",
        table_name="inventory_movements",
    )
    op.drop_index(
        op.f("ix_inventory_movements_created_at"), table_name="inventory_movements"
    )
    op.drop_index(
        op.f("ix_inventory_movements_variant_id"), table_name="inventory_movements"
    )
    op.drop_index(
        op.f("ix_inventory_movements_shop_id"), table_name="inventory_movements"
    )
    op.drop_table("inventory_movements")

    op.drop_table("inventory")

    inventory_movement_type_enum.drop(op.get_bind(), checkfirst=True)

    op.drop_constraint(
        "uq_product_variants_id_shop_id", "product_variants", type_="unique"
    )
"""create catalog domain

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same pattern as 0001's `user_role`: declare the ENUM once with
# `create_type=False` so SQLAlchemy never auto-creates/drops it as a side
# effect of table DDL, then create/drop it explicitly in upgrade/downgrade.
# Without this, `downgrade()` drops the tables but leaves the type behind,
# and a later `upgrade()` fails with "type already exists".
product_type_enum = postgresql.ENUM(
    "open_fabric",
    "ready_suit",
    "boutique",
    "other",
    name="product_type",
    create_type=False,
)

product_variant_unit_enum = postgresql.ENUM(
    "meter",
    "yard",
    "piece",
    "set",
    "roll",
    name="product_variant_unit",
    create_type=False,
)


def upgrade() -> None:
    product_type_enum.create(op.get_bind(), checkfirst=True)
    product_variant_unit_enum.create(op.get_bind(), checkfirst=True)

    # --- attributes / brands / categories: only depend on shops ---------

    op.create_table(
        "attributes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
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
        sa.UniqueConstraint("shop_id", "name", name="uq_attributes_shop_name"),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name="ck_attributes_name_not_blank"
        ),
    )
    op.create_index(
        op.f("ix_attributes_shop_id"), "attributes", ["shop_id"], unique=False
    )

    op.create_table(
        "brands",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
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
        # Redundant-looking but required: Postgres needs an explicit unique
        # constraint on exactly (id, shop_id) for `products` to be able to
        # target it with a composite foreign key below.
        sa.UniqueConstraint("id", "shop_id", name="uq_brands_id_shop_id"),
        sa.UniqueConstraint("shop_id", "name", name="uq_brands_shop_name"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_brands_name_not_blank"),
    )
    op.create_index(op.f("ix_brands_shop_id"), "brands", ["shop_id"], unique=False)

    op.create_table(
        "categories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
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
            ["parent_id"], ["categories.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        # Supports `products.category_id`'s composite foreign key below.
        sa.UniqueConstraint("id", "shop_id", name="uq_categories_id_shop_id"),
        # A category's name only needs to be unique among its siblings.
        sa.UniqueConstraint(
            "shop_id", "parent_id", "name", name="uq_categories_shop_parent_name"
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name="ck_categories_name_not_blank"
        ),
        sa.CheckConstraint("id != parent_id", name="ck_categories_no_self_parent"),
    )
    op.create_index(
        op.f("ix_categories_shop_id"), "categories", ["shop_id"], unique=False
    )
    op.create_index(
        op.f("ix_categories_parent_id"), "categories", ["parent_id"], unique=False
    )
    # Partial unique index: the plain UniqueConstraint above never fires for
    # two root categories (parent_id IS NULL), since Postgres treats every
    # NULL as distinct from every other NULL.
    op.create_index(
        "uq_categories_shop_root_name",
        "categories",
        ["shop_id", "name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )

    # --- attribute_values: depends on attributes -------------------------

    op.create_table(
        "attribute_values",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("attribute_id", sa.UUID(), nullable=False),
        sa.Column("value", sa.String(length=150), nullable=False),
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
            ["attribute_id"], ["attributes.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # Supports `variant_attribute_values`' composite foreign key below -
        # this is what actually prevents a variant being tagged with a
        # value that belongs to a different attribute.
        sa.UniqueConstraint(
            "id", "attribute_id", name="uq_attribute_values_id_attribute_id"
        ),
        sa.UniqueConstraint(
            "attribute_id", "value", name="uq_attribute_values_attribute_value"
        ),
        sa.CheckConstraint(
            "length(trim(value)) > 0", name="ck_attribute_values_value_not_blank"
        ),
    )
    op.create_index(
        op.f("ix_attribute_values_attribute_id"),
        "attribute_values",
        ["attribute_id"],
        unique=False,
    )

    # --- products: depends on shops, categories, brands -------------------

    op.create_table(
        "products",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("category_id", sa.UUID(), nullable=False),
        sa.Column("brand_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("product_type", product_type_enum, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        # category_id / brand_id are intentionally *not* plain single-column
        # foreign keys - each is paired with shop_id below so a product can
        # never reference a category or brand belonging to a different shop.
        sa.ForeignKeyConstraint(
            ["category_id", "shop_id"],
            ["categories.id", "categories.shop_id"],
            name="fk_products_category_same_shop",
        ),
        # brand_id is nullable; Postgres's default MATCH SIMPLE means this
        # constraint is skipped entirely whenever brand_id IS NULL, so
        # unbranded products are never blocked by it.
        sa.ForeignKeyConstraint(
            ["brand_id", "shop_id"],
            ["brands.id", "brands.shop_id"],
            name="fk_products_brand_same_shop",
        ),
        sa.PrimaryKeyConstraint("id"),
        # Supports `product_variants.product_id`'s composite foreign key.
        sa.UniqueConstraint("id", "shop_id", name="uq_products_id_shop_id"),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name="ck_products_name_not_blank"
        ),
    )
    op.create_index(op.f("ix_products_shop_id"), "products", ["shop_id"], unique=False)
    op.create_index(
        op.f("ix_products_category_id"), "products", ["category_id"], unique=False
    )
    op.create_index(op.f("ix_products_brand_id"), "products", ["brand_id"], unique=False)

    # --- product_variants: depends on shops, products ---------------------

    op.create_table(
        "product_variants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("barcode", sa.String(length=100), nullable=True),
        sa.Column("purchase_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("selling_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("unit", product_variant_unit_enum, nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
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
        # Mirrors products' category/brand guard: a variant can only ever
        # belong to a product in the *same* shop.
        sa.ForeignKeyConstraint(
            ["product_id", "shop_id"],
            ["products.id", "products.shop_id"],
            name="fk_product_variants_product_same_shop",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shop_id", "sku", name="uq_product_variants_shop_sku"),
        sa.CheckConstraint(
            "purchase_price >= 0",
            name="ck_product_variants_purchase_price_non_negative",
        ),
        sa.CheckConstraint(
            "selling_price >= 0",
            name="ck_product_variants_selling_price_non_negative",
        ),
        sa.CheckConstraint(
            "length(trim(sku)) > 0", name="ck_product_variants_sku_not_blank"
        ),
    )
    op.create_index(
        op.f("ix_product_variants_shop_id"), "product_variants", ["shop_id"], unique=False
    )
    op.create_index(
        op.f("ix_product_variants_product_id"),
        "product_variants",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_product_variants_barcode"), "product_variants", ["barcode"], unique=False
    )
    # Barcode is optional but unique per shop whenever it's provided.
    op.create_index(
        "uq_product_variants_shop_barcode",
        "product_variants",
        ["shop_id", "barcode"],
        unique=True,
        postgresql_where=sa.text("barcode IS NOT NULL"),
    )

    # --- variant_attribute_values: depends on product_variants, ----------
    # --- attributes, attribute_values -------------------------------------

    op.create_table(
        "variant_attribute_values",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("attribute_id", sa.UUID(), nullable=False),
        sa.Column("attribute_value_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["variant_id"], ["product_variants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["attribute_id"], ["attributes.id"], ondelete="CASCADE"
        ),
        # The core "obviously invalid reference" guard: attribute_value_id
        # must actually belong to attribute_id. Enforced by the database,
        # not just the frontend, via attribute_values' supporting
        # UNIQUE(id, attribute_id).
        sa.ForeignKeyConstraint(
            ["attribute_value_id", "attribute_id"],
            ["attribute_values.id", "attribute_values.attribute_id"],
            name="fk_variant_attribute_values_value_matches_attribute",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        # A variant can't be tagged with two different values for the same
        # attribute at once (e.g. two "Color"s).
        sa.UniqueConstraint(
            "variant_id",
            "attribute_id",
            name="uq_variant_attribute_values_variant_attribute",
        ),
    )
    op.create_index(
        op.f("ix_variant_attribute_values_variant_id"),
        "variant_attribute_values",
        ["variant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_variant_attribute_values_attribute_id"),
        "variant_attribute_values",
        ["attribute_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_variant_attribute_values_attribute_value_id"),
        "variant_attribute_values",
        ["attribute_value_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_variant_attribute_values_attribute_value_id"),
        table_name="variant_attribute_values",
    )
    op.drop_index(
        op.f("ix_variant_attribute_values_attribute_id"),
        table_name="variant_attribute_values",
    )
    op.drop_index(
        op.f("ix_variant_attribute_values_variant_id"),
        table_name="variant_attribute_values",
    )
    op.drop_table("variant_attribute_values")

    op.drop_index(
        "uq_product_variants_shop_barcode",
        table_name="product_variants",
        postgresql_where=sa.text("barcode IS NOT NULL"),
    )
    op.drop_index(op.f("ix_product_variants_barcode"), table_name="product_variants")
    op.drop_index(op.f("ix_product_variants_product_id"), table_name="product_variants")
    op.drop_index(op.f("ix_product_variants_shop_id"), table_name="product_variants")
    op.drop_table("product_variants")

    op.drop_index(op.f("ix_products_brand_id"), table_name="products")
    op.drop_index(op.f("ix_products_category_id"), table_name="products")
    op.drop_index(op.f("ix_products_shop_id"), table_name="products")
    op.drop_table("products")

    op.drop_index(op.f("ix_attribute_values_attribute_id"), table_name="attribute_values")
    op.drop_table("attribute_values")

    op.drop_index(
        "uq_categories_shop_root_name",
        table_name="categories",
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.drop_index(op.f("ix_categories_parent_id"), table_name="categories")
    op.drop_index(op.f("ix_categories_shop_id"), table_name="categories")
    op.drop_table("categories")

    op.drop_index(op.f("ix_brands_shop_id"), table_name="brands")
    op.drop_table("brands")

    op.drop_index(op.f("ix_attributes_shop_id"), table_name="attributes")
    op.drop_table("attributes")

    product_variant_unit_enum.drop(op.get_bind(), checkfirst=True)
    product_type_enum.drop(op.get_bind(), checkfirst=True)
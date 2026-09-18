"""Product catalog: Product, ProductVariant, and the VariantAttributeValue
association that tags a variant with its Attribute -> AttributeValue pairs.

See `brand_conf.md`: a product's brand is always optional (`brand_id` is
nullable) - unbranded/boutique/local items are represented by NULL, never a
synthetic "No Brand" row.
"""

import uuid
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.attribute import Attribute, AttributeValue
    from app.models.brand import Brand
    from app.models.category import Category
    from app.models.inventory import Inventory
    from app.models.shop import Shop


class ProductType(str, Enum):
    """High-level shape of a product, per the architecture doc."""

    OPEN_FABRIC = "open_fabric"
    READY_SUIT = "ready_suit"
    BOUTIQUE = "boutique"
    OTHER = "other"


class Unit(str, Enum):
    """Unit a `ProductVariant` is bought/sold in."""

    METER = "meter"
    YARD = "yard"
    PIECE = "piece"
    SET = "set"
    ROLL = "roll"


class Product(Base, UUIDMixin, TimestampMixin):
    """A product identity (e.g. "Embroidered Lawn Suit").

    Sellable items live one level down, on `ProductVariant`.
    """

    __tablename__ = "products"

    __table_args__ = (
        # Referenced by `ProductVariant`'s composite foreign key (product_id,
        # shop_id) below, so a variant can never attach to a product owned
        # by a different shop.
        UniqueConstraint("id", "shop_id", name="uq_products_id_shop_id"),
        # `category_id` / `brand_id` are deliberately *not* given a plain
        # column-level ForeignKey: each is paired with `shop_id` in a
        # composite constraint instead, so a product can never reference a
        # category or brand that belongs to a different shop - even though
        # both rows might otherwise satisfy an ordinary single-column FK.
        ForeignKeyConstraint(
            ["category_id", "shop_id"],
            ["categories.id", "categories.shop_id"],
            name="fk_products_category_same_shop",
        ),
        # `brand_id` is nullable, so Postgres's default MATCH SIMPLE (for
        # multi-column FKs) skips the check entirely whenever brand_id IS
        # NULL - i.e. unbranded products are never blocked by this
        # constraint.
        ForeignKeyConstraint(
            ["brand_id", "shop_id"],
            ["brands.id", "brands.shop_id"],
            name="fk_products_brand_same_shop",
        ),
        CheckConstraint("length(trim(name)) > 0", name="ck_products_name_not_blank"),
        Index("uq_products_code_shop", "code", "shop_id", unique=True, postgresql_where=text("code IS NOT NULL")),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Optional by design - see brand_conf.md. Unbranded/boutique/local
    # products simply leave this NULL; there is no "No Brand" row.
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="meter")

    product_type: Mapped[ProductType] = mapped_column(
        SQLEnum(
            ProductType,
            name="product_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(Text)

    # `shop`, `category`, and `brand` all help populate `shop_id`, since
    # category_id/brand_id are each paired with shop_id in a composite
    # foreign key above - SQLAlchemy calls this an "overlap" between the
    # three relationships (plus their `back_populates` counterparts on
    # Shop/Category/Brand). It's intentional here: `overlaps` tells
    # SQLAlchemy so, instead of leaving a warning on every import.
    shop: Mapped["Shop"] = relationship(
        "Shop",
        back_populates="products",
        overlaps="brand,category,products,shop",
    )

    category: Mapped["Category"] = relationship(
        "Category",
        back_populates="products",
        overlaps="brand,category,products,shop",
    )

    brand: Mapped["Brand | None"] = relationship(
        "Brand",
        back_populates="products",
        overlaps="brand,category,products,shop",
    )

    variants: Mapped[list["ProductVariant"]] = relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Product(id={self.id!r}, name={self.name!r}, type={self.product_type!r})"


class ProductVariant(Base, UUIDMixin, TimestampMixin):
    """A concrete sellable item (SKU) belonging to a `Product`."""

    __tablename__ = "product_variants"

    __table_args__ = (
        UniqueConstraint("shop_id", "sku", name="uq_product_variants_shop_sku"),
        # Referenced by `InventoryMovement`'s composite foreign key
        # (variant_id, shop_id) - see inventory.py - so an inventory movement
        # can never point at a variant belonging to a different shop. Postgres
        # needs an explicit unique constraint on exactly this column pair even
        # though `id` alone is already the primary key.
        UniqueConstraint("id", "shop_id", name="uq_product_variants_id_shop_id"),
        # Barcode is optional, but must be unique within a shop whenever it
        # is provided. A plain UniqueConstraint would happily allow many
        # NULLs (that part is fine), but a partial index keeps the intent
        # explicit and the index itself small.
        Index(
            "uq_product_variants_shop_barcode",
            "shop_id",
            "barcode",
            unique=True,
            postgresql_where=text("barcode IS NOT NULL"),
        ),
        # Mirrors Product's category/brand guard: a variant can only ever
        # belong to a product in the *same* shop.
        ForeignKeyConstraint(
            ["product_id", "shop_id"],
            ["products.id", "products.shop_id"],
            name="fk_product_variants_product_same_shop",
        ),
        CheckConstraint(
            "purchase_price >= 0",
            name="ck_product_variants_purchase_price_non_negative",
        ),
        CheckConstraint(
            "selling_price >= 0",
            name="ck_product_variants_selling_price_non_negative",
        ),
        CheckConstraint("length(trim(sku)) > 0", name="ck_product_variants_sku_not_blank"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    sku: Mapped[str] = mapped_column(String(100), nullable=False)

    barcode: Mapped[str | None] = mapped_column(String(100), index=True)

    # Money - always NUMERIC, never Float.
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    selling_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    unit: Mapped[Unit] = mapped_column(
        SQLEnum(
            Unit,
            name="product_variant_unit",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="variants",
    )

    attribute_values: Mapped[list["VariantAttributeValue"]] = relationship(
        "VariantAttributeValue",
        back_populates="variant",
        cascade="all, delete-orphan",
    )

    # Current stock state for this variant; `uselist=False` because there is
    # exactly one `Inventory` row per variant (enforced in the database too).
    inventory: Mapped["Inventory | None"] = relationship(
        "Inventory",
        back_populates="variant",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"ProductVariant(id={self.id!r}, sku={self.sku!r})"


class VariantAttributeValue(Base, UUIDMixin):
    """Tags a `ProductVariant` with one Attribute -> AttributeValue pair.

    No `TimestampMixin` here, deliberately: like the sale/purchase line
    items and inventory movements described in the wider architecture, this
    is a pure association row with no independent audit trail of its own.
    """

    __tablename__ = "variant_attribute_values"

    __table_args__ = (
        # A variant can't be tagged with two different values for the same
        # attribute at once (e.g. two "Color"s).
        UniqueConstraint(
            "variant_id",
            "attribute_id",
            name="uq_variant_attribute_values_variant_attribute",
        ),
        # The key "obviously invalid reference" guard called for in the
        # spec: `attribute_value_id` must actually belong to `attribute_id`.
        # Because AttributeValue carries a supporting UNIQUE(id,
        # attribute_id), Postgres enforces this composite match itself - a
        # value borrowed from a different attribute is rejected by the
        # database, not just by frontend validation.
        ForeignKeyConstraint(
            ["attribute_value_id", "attribute_id"],
            ["attribute_values.id", "attribute_values.attribute_id"],
            name="fk_variant_attribute_values_value_matches_attribute",
            ondelete="CASCADE",
        ),
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    attribute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attributes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    attribute_value_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    variant: Mapped["ProductVariant"] = relationship(
        "ProductVariant",
        back_populates="attribute_values",
    )

    # `attribute` and `attribute_value` both help populate `attribute_id`
    # (it's part of the composite foreign key above), which is the same
    # kind of intentional overlap as on Product - see the comment there.
    attribute: Mapped["Attribute"] = relationship(
        "Attribute",
        overlaps="attribute_value",
    )

    attribute_value: Mapped["AttributeValue"] = relationship(
        "AttributeValue",
        overlaps="attribute",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"VariantAttributeValue(variant_id={self.variant_id!r}, "
            f"attribute_id={self.attribute_id!r})"
        )
"""Category model - hierarchical, per-shop product categories.

Categories nest via a self-referential `parent_id` (Women -> Open Fabric ->
Lawn). Name uniqueness is scoped per shop *and* per parent, so two
different branches of the tree - or two different shops entirely - can each
have their own "Lawn" category without colliding.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.shop import Shop


class Category(Base, UUIDMixin, TimestampMixin):
    """A (possibly nested) product category, scoped to a single shop."""

    __tablename__ = "categories"

    __table_args__ = (
        # Referenced by `Product`'s composite foreign key (category_id,
        # shop_id) - see product.py - so a product can never be filed under
        # a category belonging to a different shop. Postgres requires an
        # explicit unique constraint on exactly this column pair even
        # though `id` alone is already the primary key.
        UniqueConstraint("id", "shop_id", name="uq_categories_id_shop_id"),
        # A category's name only needs to be unique among its siblings
        # (same shop + same parent) - NULL parents are handled separately
        # below, since Postgres treats every NULL as distinct from every
        # other NULL in a plain unique constraint.
        UniqueConstraint(
            "shop_id", "parent_id", "name", name="uq_categories_shop_parent_name"
        ),
        Index(
            "uq_categories_shop_root_name",
            "shop_id",
            "name",
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
        ),
        CheckConstraint("length(trim(name)) > 0", name="ck_categories_name_not_blank"),
        CheckConstraint("id != parent_id", name="ck_categories_no_self_parent"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    shop: Mapped["Shop"] = relationship("Shop", back_populates="categories")

    parent: Mapped["Category | None"] = relationship(
        "Category",
        remote_side="Category.id",
        back_populates="children",
    )

    children: Mapped[list["Category"]] = relationship(
        "Category",
        back_populates="parent",
    )

    # `Product.shop`, `Product.category`, and `Product.brand` all
    # participate in populating `products.shop_id` (it's part of two
    # composite foreign keys - see product.py), so SQLAlchemy considers
    # this relationship, `Brand.products`, and `Shop.products` mutually
    # "overlapping". That's intentional here, not a modeling mistake, so
    # it's declared explicitly rather than left to warn on every import.
    products: Mapped[list["Product"]] = relationship(
        "Product",
        back_populates="category",
        overlaps="brand,category,products,shop",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Category(id={self.id!r}, name={self.name!r})"
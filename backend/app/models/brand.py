"""Brand model.

Brand is always optional (see `brand_conf.md`): a `Product.brand_id` of
NULL means "unbranded" - a shop's own boutique/local items - and is never
represented as a synthetic "No Brand" row here. The frontend is expected to
render a NULL brand as "No Brand" / "Unbranded".
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.shop import Shop


class Brand(Base, UUIDMixin, TimestampMixin):
    """A named brand (Sapphire, Gul Ahmed, ...) scoped to one shop."""

    __tablename__ = "brands"

    __table_args__ = (
        UniqueConstraint("shop_id", "name", name="uq_brands_shop_name"),
        # Referenced by `Product`'s composite foreign key (brand_id,
        # shop_id) - see product.py - so a product can never be assigned a
        # brand belonging to a different shop.
        UniqueConstraint("id", "shop_id", name="uq_brands_id_shop_id"),
        CheckConstraint("length(trim(name)) > 0", name="ck_brands_name_not_blank"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    shop: Mapped["Shop"] = relationship("Shop", back_populates="brands")

    # See the matching comment on `Category.products` - this overlaps with
    # `Product.shop` / `Product.category` / `Shop.products` by design,
    # since `products.shop_id` is shared across two composite foreign keys.
    products: Mapped[list["Product"]] = relationship(
        "Product",
        back_populates="brand",
        overlaps="brand,category,products,shop",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Brand(id={self.id!r}, name={self.name!r})"
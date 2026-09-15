"""Attribute / AttributeValue models.

`Attribute` (e.g. "Fabric", "Color", "Pieces") and `AttributeValue` (e.g.
"Lawn", "Black", "3 Piece") are what let the catalog describe arbitrary
product characteristics without ever adding columns to `ProductVariant` -
see `VariantAttributeValue` in `product.py` for how a variant is tagged
with a set of these.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.shop import Shop


class Attribute(Base, UUIDMixin, TimestampMixin):
    """A named characteristic a shop wants to track (Fabric, Color, ...)."""

    __tablename__ = "attributes"

    __table_args__ = (
        UniqueConstraint("shop_id", "name", name="uq_attributes_shop_name"),
        CheckConstraint("length(trim(name)) > 0", name="ck_attributes_name_not_blank"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    shop: Mapped["Shop"] = relationship("Shop", back_populates="attributes")

    values: Mapped[list["AttributeValue"]] = relationship(
        "AttributeValue",
        back_populates="attribute",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Attribute(id={self.id!r}, name={self.name!r})"


class AttributeValue(Base, UUIDMixin, TimestampMixin):
    """One concrete value of an `Attribute` (Fabric -> "Lawn")."""

    __tablename__ = "attribute_values"

    __table_args__ = (
        UniqueConstraint(
            "attribute_id", "value", name="uq_attribute_values_attribute_value"
        ),
        # Referenced by `VariantAttributeValue`'s composite foreign key (see
        # product.py) so that a variant can never be tagged with a value
        # that belongs to a *different* attribute than the one it's paired
        # with. Postgres requires an explicit unique constraint on exactly
        # this column pair even though `id` alone is already the primary
        # key.
        UniqueConstraint(
            "id", "attribute_id", name="uq_attribute_values_id_attribute_id"
        ),
        CheckConstraint(
            "length(trim(value)) > 0", name="ck_attribute_values_value_not_blank"
        ),
    )

    attribute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attributes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    value: Mapped[str] = mapped_column(String(150), nullable=False)

    attribute: Mapped["Attribute"] = relationship(
        "Attribute",
        back_populates="values",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"AttributeValue(id={self.id!r}, value={self.value!r})"
"""Inventory domain: current stock state plus its historical movement ledger.

Two concepts live here, deliberately kept separate (see `db_arch.md` sections
13-14):

* `Inventory`         - the *current* cached stock level for one variant.
* `InventoryMovement` - an append-only ledger explaining how stock got there.

`Inventory.quantity` is a mutable counter that always reflects "how much do I
have right now"; `InventoryMovement` is the historical truth ("how did my
stock get here"). Stock must never be changed without also appending a
movement row - that invariant is enforced by `app.services.inventory`, which
is the only intended write path for these tables.

The `ProductVariant`'s `unit` (METER/YARD/PIECE/SET/ROLL) decides how the
quantities are interpreted; there is intentionally no per-unit schema.
Quantities are signed: positive means stock entered, negative means it left.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product import ProductVariant


class InventoryMovementType(str, Enum):
    """Why an `InventoryMovement` happened (architecture's exact set)."""

    PURCHASE = "purchase"
    SALE = "sale"
    CUSTOMER_RETURN = "customer_return"
    SUPPLIER_RETURN = "supplier_return"
    DAMAGE = "damage"
    ADJUSTMENT = "adjustment"


class Inventory(Base, UUIDMixin, TimestampMixin):
    """Current stock state for a single `ProductVariant` (one row per variant).

    `available_quantity` is deliberately *derived* (`quantity -
    reserved_quantity`) rather than stored as a second mutable column: there
    is exactly one number the service layer has to keep correct, so there is
    no room for the two to disagree.
    """

    __tablename__ = "inventory"

    __table_args__ = (
        # One inventory row per variant - enforced by the database, which is
        # also what lets the service layer rely on an upsert-then-lock flow.
        UniqueConstraint("variant_id", name="uq_inventory_variant_id"),
        CheckConstraint("quantity >= 0", name="ck_inventory_quantity_non_negative"),
        CheckConstraint(
            "reserved_quantity >= 0",
            name="ck_inventory_reserved_quantity_non_negative",
        ),
        # Reserved stock can never exceed what physically exists. This is the
        # database-level backstop against accidental negative availability.
        CheckConstraint(
            "reserved_quantity <= quantity",
            name="ck_inventory_reserved_not_above_quantity",
        ),
        CheckConstraint(
            "weighted_average_cost >= 0",
            name="ck_inventory_weighted_average_cost_non_negative",
        ),
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Fabric needs three decimal places (3.500 m, 12.250 yd); NUMERIC, never
    # Float, so the value is exact regardless of unit.
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )

    reserved_quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )

    # V1 stock valuation: moving weighted-average cost of the stock on hand
    # (`db_arch.md` sections 29, 34). It is ``0`` until the first costed
    # receipt, and is updated *only* by `app.services.inventory` whenever
    # stock enters at a known cost. Kept at four decimal places so repeated
    # averages don't compound rounding error; sales snapshot it at two places
    # (`SaleItem.cost_price`) at the moment they happen.
    weighted_average_cost: Mapped[Decimal] = mapped_column(
        Numeric(14, 4),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )

    variant: Mapped["ProductVariant"] = relationship(
        "ProductVariant",
        back_populates="inventory",
    )

    @property
    def available_quantity(self) -> Decimal:
        """Stock that may still be sold: `quantity - reserved_quantity`."""
        return self.quantity - self.reserved_quantity

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"Inventory(variant_id={self.variant_id!r}, quantity={self.quantity!r}, "
            f"reserved={self.reserved_quantity!r})"
        )


class InventoryMovement(Base, UUIDMixin):
    """One immutable line in the inventory ledger.

    No `TimestampMixin` here, on purpose: a movement is an append-only event
    with a single `created_at` and no meaningful "updated_at" - the same
    reasoning as the association rows in the catalog domain.
    """

    __tablename__ = "inventory_movements"

    __table_args__ = (
        # Tenant guard: `shop_id` is paired with `variant_id` in a composite
        # foreign key, so a movement can never claim to belong to Shop A while
        # pointing at a variant owned by Shop B. `product_variants` carries a
        # supporting UNIQUE(id, shop_id) (added alongside this table) so
        # Postgres can enforce the match itself, not just the service layer.
        #
        # ON DELETE CASCADE matches the rest of the model (Shop -> every
        # tenant-owned table, Product -> ProductVariant): deleting a shop or
        # variant cleans up its ledger rows instead of leaving orphaned
        # history that a non-cascading FK would otherwise block.
        ForeignKeyConstraint(
            ["variant_id", "shop_id"],
            ["product_variants.id", "product_variants.shop_id"],
            name="fk_inventory_movements_variant_same_shop",
            ondelete="CASCADE",
        ),
        Index(
            "ix_inventory_movements_shop_created_at",
            "shop_id",
            "created_at",
        ),
        Index(
            "ix_inventory_movements_shop_variant_created_at",
            "shop_id",
            "variant_id",
            "created_at",
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    movement_type: Mapped[InventoryMovementType] = mapped_column(
        SQLEnum(
            InventoryMovementType,
            name="inventory_movement_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    # Signed: +20.000 for a purchase, -3.500 for a sale.
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)

    # Cost at the time of the movement, kept so inventory valuation /
    # historical COGS can be derived correctly later. Money is NUMERIC(14,2).
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    # Application-level polymorphic reference to whatever caused this
    # movement (purchase id, sale id, ...). Deliberately *not* a foreign key
    # yet - those tables don't exist in this step. A manual adjustment leaves
    # `reference_id` NULL and explains itself in `notes`.
    reference_type: Mapped[str | None] = mapped_column(String(50))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    notes: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"InventoryMovement(type={self.movement_type!r}, "
            f"variant_id={self.variant_id!r}, quantity={self.quantity!r})"
        )
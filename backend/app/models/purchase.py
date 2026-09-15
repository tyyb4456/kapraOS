"""Purchase domain: Purchase (one incoming stock transaction) and PurchaseItem
(one line on that purchase).

A `Purchase` records what a shop bought from a `Supplier`, and its items are
what feed stock into the inventory domain. The volume/monetary rules come from
`db_arch.md` sections 19-20:

* Quantities are `NUMERIC(14,3)` (fabric sold by the metre needs three decimal
  places); money is `NUMERIC(14,2)`. Never Float.
* `PurchaseItem.unit_cost` is the *actual* cost paid on that purchase and is
  never rewritten when a supplier later changes their price - this is what
  makes historical COGS/valuation possible later.
* `PurchaseItem.total` is persisted for document purposes, but it is always
  `quantity * unit_cost` and the database enforces that equality so a bad
  write can't leave an inconsistent line.

`due_amount` is intentionally a derived property (`total - paid_amount`)
rather than an independently editable column (`db_arch.md` section 28): once
the Payments domain lands, recorded payments become the source of truth and
`paid_amount` will be maintained from them.

Purchases are business transactions, not CRUD rows - nothing here writes
stock. Creation (and its atomic inventory integration) lives in
`app.services.purchases`.
"""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product import ProductVariant
    from app.models.shop import Shop
    from app.models.supplier import Supplier


class Purchase(Base, UUIDMixin, TimestampMixin):
    """A single incoming-stock transaction from a supplier."""

    __tablename__ = "purchases"

    __table_args__ = (
        # Invoice numbers are unique *per shop* only, and only when present:
        # many local suppliers hand over no invoice at all, so NULL is a valid,
        # repeatable value. A partial unique index expresses that exactly
        # (a plain UniqueConstraint would still allow multiple NULLs, but a
        # partial index documents the intent and stays small).
        Index(
            "uq_purchases_shop_invoice_number",
            "shop_id",
            "invoice_number",
            unique=True,
            postgresql_where=text("invoice_number IS NOT NULL"),
        ),
        # Composite tenant guard: a purchase's supplier must belong to the same
        # shop (see supplier.py for the supporting UNIQUE(id, shop_id)).
        # RESTRICT (not CASCADE) on purpose: a supplier with purchase history
        # must not be deletable in a way that silently erases that history.
        ForeignKeyConstraint(
            ["supplier_id", "shop_id"],
            ["suppliers.id", "suppliers.shop_id"],
            name="fk_purchases_supplier_same_shop",
            ondelete="RESTRICT",
        ),
        Index("ix_purchases_shop_created_at", "shop_id", "created_at"),
        Index("ix_purchases_supplier_id", "supplier_id"),
        CheckConstraint("subtotal >= 0", name="ck_purchases_subtotal_non_negative"),
        CheckConstraint("discount >= 0", name="ck_purchases_discount_non_negative"),
        CheckConstraint("total >= 0", name="ck_purchases_total_non_negative"),
        CheckConstraint(
            "paid_amount >= 0", name="ck_purchases_paid_amount_non_negative"
        ),
        CheckConstraint(
            "discount <= subtotal", name="ck_purchases_discount_not_above_subtotal"
        ),
        # The header totals are computed by the service; these constraints stop
        # any other write path from persisting arithmetic that doesn't add up.
        CheckConstraint(
            "total = subtotal - discount", name="ck_purchases_total_matches_subtotal"
        ),
        # Due is derived (`total - paid_amount`), so paid can never exceed total.
        CheckConstraint(
            "paid_amount <= total", name="ck_purchases_paid_not_above_total"
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Optional: informal/local suppliers often provide no invoice.
    invoice_number: Mapped[str | None] = mapped_column(String(50))

    # Money - always NUMERIC(14,2), never Float.
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    discount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )

    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # Cached "how much has been paid against this purchase". Maintained by the
    # service now, and from recorded payments once the Payments domain exists.
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )

    # `shop` and `supplier` both help populate `shop_id`, since supplier_id is
    # paired with shop_id in the composite foreign key above - the same
    # intentional "overlap" pattern as Product.category/brand in the catalog
    # domain, hence the explicit `overlaps` rather than a warning on import.
    shop: Mapped["Shop"] = relationship(
        "Shop",
        back_populates="purchases",
        overlaps="purchases,shop,supplier",
    )

    supplier: Mapped["Supplier"] = relationship(
        "Supplier",
        back_populates="purchases",
        overlaps="purchases,shop,supplier",
    )

    items: Mapped[list["PurchaseItem"]] = relationship(
        "PurchaseItem",
        back_populates="purchase",
        cascade="all, delete-orphan",
    )

    @property
    def due_amount(self) -> Decimal:
        """What is still payable on this purchase: `total - paid_amount`."""
        return self.total - self.paid_amount

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"Purchase(id={self.id!r}, invoice={self.invoice_number!r}, "
            f"total={self.total!r})"
        )


class PurchaseItem(Base, UUIDMixin):
    """One line item on a `Purchase` (a variant + quantity + cost).

    No `TimestampMixin`: like the other line/association tables in this
    project, an item has no independent audit trail - its lifetime is bound to
    its parent purchase.
    """

    __tablename__ = "purchase_items"

    __table_args__ = (
        # One line per variant per purchase: a purchase that receives the same
        # variant twice must be combined upstream by the service rather than
        # silently stored as two rows.
        UniqueConstraint(
            "purchase_id", "variant_id", name="uq_purchase_items_purchase_variant"
        ),
        # `total` is a document value but must always agree with the line math.
        # `round(quantity * unit_cost, 2)` mirrors the service's Decimal
        # rounding (half away from zero), so the two can't drift.
        CheckConstraint(
            "total = round(quantity * unit_cost, 2)",
            name="ck_purchase_items_total_matches_line",
        ),
        CheckConstraint("quantity > 0", name="ck_purchase_items_quantity_positive"),
        CheckConstraint(
            "unit_cost >= 0", name="ck_purchase_items_unit_cost_non_negative"
        ),
        CheckConstraint("total >= 0", name="ck_purchase_items_total_non_negative"),
    )

    purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Fabric quantities need three decimals (3.500 m, 12.250 yd).
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)

    # The actual historical cost for this purchase - never rewritten later.
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    purchase: Mapped["Purchase"] = relationship(
        "Purchase",
        back_populates="items",
    )

    variant: Mapped["ProductVariant"] = relationship("ProductVariant")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"PurchaseItem(purchase_id={self.purchase_id!r}, "
            f"variant_id={self.variant_id!r}, quantity={self.quantity!r})"
        )
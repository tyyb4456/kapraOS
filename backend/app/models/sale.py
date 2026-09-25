"""Sales domain: Sale (one outgoing stock transaction) and SaleItem (one line
on that sale).

A `Sale` records what a shop sold to a (possibly anonymous) `Customer`, and
its items are what pull stock out of the inventory domain. The rules come from
`db_arch.md` sections 17-18:

* Quantities are `NUMERIC(14,3)` (fabric sold by the metre needs three decimal
  places); money is `NUMERIC(14,2)`. Never Float.
* `SaleItem.unit_price` is the *actual* selling price used for that sale and
  `SaleItem.cost_price` is the weighted-average inventory cost *at the moment
  of sale*. Neither is ever rewritten when the catalog price changes - this is
  what makes historical gross-profit reporting possible.
* `SaleItem.total` is persisted but always `round(quantity * unit_price, 2) -
  discount`, and the database enforces that equality so a bad write can't
  leave an inconsistent line.

`customer_id` is nullable on purpose (`db_arch.md` section 15): a walk-in sale
is represented by NULL, never by a synthetic "Walk-in Customer" row. That is
also what keeps walk-in sales out of every customer's Khata - see
`app.services.receivables`.

`due_amount` is intentionally a derived property (`total - paid_amount`)
rather than an independently editable column (`db_arch.md` section 28): the
recorded `Payment` rows are the source of truth, and `paid_amount` is
maintained from them by `app.services.sales.create_sale` and, for later
settlements, `app.services.receivables.record_customer_payment`.

Sales are business transactions, not CRUD rows - nothing here writes stock.
Creation (and its atomic inventory integration) lives in
`app.services.sales`.
"""

import uuid
from decimal import Decimal
from enum import Enum
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
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.payment import Payment
    from app.models.product import ProductVariant
    from app.models.shop import Shop


class SaleStatus(str, Enum):
    """Payment state of a sale (architecture's exact set).

    `COMPLETED` means fully paid, `PARTIAL` means a balance is outstanding;
    `CANCELLED` / `RETURNED` exist so the enum is future-proof but their
    workflows are not implemented in this step. Only COMPLETED and PARTIAL
    count towards a customer's receivable - see
    `app.services.receivables.QUALIFYING_SALE_STATUSES`.
    """

    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class Sale(Base, UUIDMixin, TimestampMixin):
    """A single outgoing-stock transaction."""

    __tablename__ = "sales"

    __table_args__ = (
        # Supports `Payment`'s composite foreign key (sale_id, shop_id) - see
        # payment.py - so a payment can never attach to a sale from another
        # shop. Postgres needs an explicit unique constraint on exactly this
        # column pair even though `id` alone is already the primary key.
        UniqueConstraint("id", "shop_id", name="uq_sales_id_shop_id"),
        # Supports `Payment`'s composite foreign key (sale_id, customer_id),
        # which stops one customer's payment being booked against another
        # customer's invoice (which would silently move money between two
        # Khatas). Same "redundant-looking but required" pattern as above.
        UniqueConstraint("id", "customer_id", name="uq_sales_id_customer_id"),
        # Invoice numbers are unique *per shop* only, and only when present:
        # mirrors the `purchases` convention. A plain UniqueConstraint would
        # also let many NULLs through, but a partial index documents the
        # intent and stays small.
        Index(
            "uq_sales_shop_invoice_number",
            "shop_id",
            "invoice_number",
            unique=True,
            postgresql_where=text("invoice_number IS NOT NULL"),
        ),
        # Composite tenant guard: a sale's customer must belong to the same
        # shop. customer_id is nullable, so MATCH SIMPLE skips this check for
        # walk-in sales. RESTRICT (not CASCADE) on purpose: a customer with
        # sale history must not be deletable in a way that erases that history.
        ForeignKeyConstraint(
            ["customer_id", "shop_id"],
            ["customers.id", "customers.shop_id"],
            name="fk_sales_customer_same_shop",
            ondelete="RESTRICT",
        ),
        Index("ix_sales_shop_created_at", "shop_id", "created_at"),
        # The Khata read pattern: one customer's sales in chronological order.
        # Wider than the plain (shop_id, customer_id) index it replaced, of
        # which it is a strict prefix, so nothing lost coverage.
        Index(
            "ix_sales_shop_customer_created_at",
            "shop_id",
            "customer_id",
            "created_at",
        ),
        CheckConstraint("subtotal >= 0", name="ck_sales_subtotal_non_negative"),
        CheckConstraint("discount >= 0", name="ck_sales_discount_non_negative"),
        CheckConstraint("total >= 0", name="ck_sales_total_non_negative"),
        CheckConstraint(
            "paid_amount >= 0", name="ck_sales_paid_amount_non_negative"
        ),
        CheckConstraint(
            "discount <= subtotal", name="ck_sales_discount_not_above_subtotal"
        ),
        # Header totals are computed by the service; these constraints stop
        # any other write path from persisting arithmetic that doesn't add up.
        CheckConstraint(
            "total = subtotal - discount", name="ck_sales_total_matches_subtotal"
        ),
        # Due is derived (`total - paid_amount`), so paid can never exceed total.
        CheckConstraint(
            "paid_amount <= total", name="ck_sales_paid_not_above_total"
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # NULL == walk-in / anonymous customer. Never a synthetic row.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Every sale carries an invoice number. The service auto-allocates
    # `INV-000001`-style numbers from the shop's existing sales when the
    # caller does not supply one (see app.services.sales), so new rows are
    # never NULL in practice. The column stays nullable only for legacy rows;
    # the 0013 migration backfills those. Unique per shop when present.
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

    # Cached "how much has been paid against this sale". Maintained from the
    # recorded Payment rows; due is derived from it, not stored.
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )

    status: Mapped[SaleStatus] = mapped_column(
        SQLEnum(
            SaleStatus,
            name="sale_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    # `shop` and `customer` both help populate `shop_id`, since customer_id is
    # paired with shop_id in the composite foreign key above - the same
    # intentional "overlap" pattern as Purchase.shop/supplier.
    shop: Mapped["Shop"] = relationship(
        "Shop",
        back_populates="sales",
        overlaps="customer,sales,shop",
    )

    customer: Mapped["Customer | None"] = relationship(
        "Customer",
        back_populates="sales",
        overlaps="customer,sales,shop",
    )

    items: Mapped[list["SaleItem"]] = relationship(
        "SaleItem",
        back_populates="sale",
        cascade="all, delete-orphan",
    )

    # There are now *two* foreign key paths from `payments` to `sales`
    # ((sale_id, shop_id) and (sale_id, customer_id)), so the join condition
    # has to be stated rather than inferred. The tenant pair is the one to
    # traverse: it is non-nullable on the sale side, whereas customer_id is
    # NULL for walk-in sales.
    payments: Mapped[list["Payment"]] = relationship(
        "Payment",
        back_populates="sale",
        primaryjoin=(
            "and_(Sale.id == Payment.sale_id, Sale.shop_id == Payment.shop_id)"
        ),
        foreign_keys="[Payment.sale_id, Payment.shop_id]",
        cascade="all, delete-orphan",
        overlaps="customer,payments,purchase,sale,shop,supplier",
    )

    @property
    def due_amount(self) -> Decimal:
        """What is still owed on this sale: `total - paid_amount`."""
        return self.total - self.paid_amount

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"Sale(id={self.id!r}, invoice={self.invoice_number!r}, "
            f"total={self.total!r})"
        )


class SaleItem(Base, UUIDMixin):
    """One line item on a `Sale` (a variant + quantity + historical prices).

    No `TimestampMixin`: like the other line tables in this project, an item
    has no independent audit trail - its lifetime is bound to its parent sale.
    """

    __tablename__ = "sale_items"

    __table_args__ = (
        # One line per variant per sale: a sale that repeats the same variant
        # is combined upstream by the service rather than stored as two rows.
        UniqueConstraint(
            "sale_id", "variant_id", name="uq_sale_items_sale_variant"
        ),
        # `total` is a document value but must always agree with the line math:
        # `round(quantity * unit_price, 2) - discount` mirrors the service's
        # Decimal rounding (half away from zero), so the two can't drift.
        CheckConstraint(
            "total = round(quantity * unit_price, 2) - discount",
            name="ck_sale_items_total_matches_line",
        ),
        CheckConstraint("quantity > 0", name="ck_sale_items_quantity_positive"),
        CheckConstraint(
            "unit_price >= 0", name="ck_sale_items_unit_price_non_negative"
        ),
        CheckConstraint(
            "cost_price >= 0", name="ck_sale_items_cost_price_non_negative"
        ),
        CheckConstraint("discount >= 0", name="ck_sale_items_discount_non_negative"),
        CheckConstraint("total >= 0", name="ck_sale_items_total_non_negative"),
    )

    sale_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sales.id", ondelete="CASCADE"),
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

    # The actual historical selling price for this sale - never rewritten.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # Weighted-average inventory cost captured at sale time - never rewritten.
    cost_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    discount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )

    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    sale: Mapped["Sale"] = relationship("Sale", back_populates="items")

    variant: Mapped["ProductVariant"] = relationship("ProductVariant")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"SaleItem(sale_id={self.sale_id!r}, "
            f"variant_id={self.variant_id!r}, quantity={self.quantity!r})"
        )
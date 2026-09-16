"""Payment model - money changing hands, kept separate from sales/purchases
(`db_arch.md` section 21).

A payment can be a customer settling a `Sale`, a customer settling their Khata
without naming an invoice (`sale_id IS NULL`), a shop settling a `Purchase`
with a `Supplier`, a shop settling its overall payable without naming a
purchase (`purchase_id IS NULL`), or - later - any other party settlement.
The shape is deliberately generic so a future payables domain can reuse the
same table without a migration rewrite. For now `app.services.receivables`
treats these rows as the source of truth for money received from a customer.

Tenant integrity is enforced with composite foreign keys: `(sale_id, shop_id)`,
`(purchase_id, shop_id)`, `(customer_id, shop_id)` and `(supplier_id, shop_id)`
each target a supporting `UNIQUE(id, shop_id)` on the referenced table. That
means a payment belonging to Shop A can never point at Shop B's sale,
purchase, customer or supplier - the database rejects it, not just the service
layer.

One further pair guards *within* a shop: `(sale_id, customer_id)` targets
`sales UNIQUE(id, customer_id)`, so Ahmed's payment can never be booked against
Bilal's invoice.

MATCH SIMPLE keeps every legitimate NULL case working - an unallocated
customer/supplier payment has no `sale_id`/`purchase_id`, and a walk-in sale's
payment has no `customer_id` - while any payment naming both a document and a
party must agree with the document's own party.
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
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.purchase import Purchase
    from app.models.sale import Sale
    from app.models.shop import Shop
    from app.models.supplier import Supplier


class PaymentMethod(str, Enum):
    """How a payment was made (architecture's exact set)."""

    CASH = "cash"
    CARD = "card"
    BANK = "bank"
    JAZZCASH = "jazzcash"
    EASYPAISA = "easypaisa"
    OTHER = "other"


class Payment(Base, UUIDMixin, TimestampMixin):
    """One recorded inflow/outflow against a sale, purchase or party."""

    __tablename__ = "payments"

    __table_args__ = (
        # Composite tenant guards. Each nullable reference is paired with
        # shop_id so a payment can only ever point at same-shop rows; MATCH
        # SIMPLE skips the check whenever the reference is NULL. RESTRICT for
        # parties (a customer/supplier with payment history is not deletable);
        # CASCADE for documents (deleting a sale/purchase takes its payments).
        ForeignKeyConstraint(
            ["customer_id", "shop_id"],
            ["customers.id", "customers.shop_id"],
            name="fk_payments_customer_same_shop",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "shop_id"],
            ["suppliers.id", "suppliers.shop_id"],
            name="fk_payments_supplier_same_shop",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["sale_id", "shop_id"],
            ["sales.id", "sales.shop_id"],
            name="fk_payments_sale_same_shop",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["purchase_id", "shop_id"],
            ["purchases.id", "purchases.shop_id"],
            name="fk_payments_purchase_same_shop",
            ondelete="CASCADE",
        ),
        # Customer Khata integrity (Step 6): when a payment names both a
        # customer and a sale, the sale must be that customer's. Without this,
        # Shop A could credit Ahmed's Khata with a payment recorded against
        # Bilal's invoice - a guard the tenant pairs above cannot provide,
        # since both rows are legitimately in the same shop.
        ForeignKeyConstraint(
            ["sale_id", "customer_id"],
            ["sales.id", "sales.customer_id"],
            name="fk_payments_sale_same_customer",
            ondelete="CASCADE",
        ),
        Index("ix_payments_shop_sale_id", "shop_id", "sale_id"),
        Index("ix_payments_shop_created_at", "shop_id", "created_at"),
        # The customer Khata read pattern (Step 6): one customer's payments in
        # chronological order.
        Index(
            "ix_payments_shop_customer_created_at",
            "shop_id",
            "customer_id",
            "created_at",
        ),

        # A payment moves a non-zero amount of money (refunds/negative entries
        # belong to the returns domain, not here).
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # NULL means the payment is not allocated to a particular invoice: a
    # customer settling their Khata in general. It still reduces their
    # outstanding balance - see `app.services.receivables`.
    sale_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # NULL means the payment is not allocated to a particular purchase; a
    # future payables domain will read it the same way receivables reads
    # unallocated customer payments.
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    method: Mapped[PaymentMethod] = mapped_column(
        SQLEnum(
            PaymentMethod,
            name="payment_method",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    # Free-form external reference (JazzCash txn id, cheque number, ...).
    reference: Mapped[str | None] = mapped_column(String(100))

    # Every relationship below participates in populating `shop_id` (it is
    # part of each composite foreign key), hence the explicit `overlaps`
    # rather than a wall of SQLAlchemy warnings on import.
    shop: Mapped["Shop"] = relationship(
        "Shop",
        back_populates="payments",
        overlaps="customer,payments,purchase,sale,shop,supplier",
    )

    customer: Mapped["Customer | None"] = relationship(
        "Customer",
        overlaps="customer,payments,purchase,sale,shop,supplier",
    )

    supplier: Mapped["Supplier | None"] = relationship(
        "Supplier",
        overlaps="customer,payments,purchase,sale,shop,supplier",
    )

    # Two foreign key paths now link payments to sales ((sale_id, shop_id) and
    # (sale_id, customer_id)), so the join has to be stated explicitly instead
    # of inferred. The tenant pair is the right one to traverse - it is
    # non-nullable on the sale side, while customer_id is NULL for walk-ins.
    sale: Mapped["Sale | None"] = relationship(
        "Sale",
        back_populates="payments",
        primaryjoin=(
            "and_(Payment.sale_id == Sale.id, Payment.shop_id == Sale.shop_id)"
        ),
        foreign_keys="[Payment.sale_id, Payment.shop_id]",
        overlaps="customer,payments,purchase,sale,shop,supplier",
    )

    # Stated explicitly for the same reason as `sale` above, and so a future
    # payables relationship can add its own join without ambiguity.
    purchase: Mapped["Purchase | None"] = relationship(
        "Purchase",
        back_populates="payments",
        primaryjoin=(
            "and_(Payment.purchase_id == Purchase.id, "
            "Payment.shop_id == Purchase.shop_id)"
        ),
        foreign_keys="[Payment.purchase_id, Payment.shop_id]",
        overlaps="customer,payments,purchase,sale,shop,supplier",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"Payment(id={self.id!r}, amount={self.amount!r}, "
            f"method={self.method!r})"
        )
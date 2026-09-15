"""Payment model - money changing hands, kept separate from sales/purchases
(`db_arch.md` section 21).

A payment can be a customer settling a `Sale`, a shop paying a `Supplier`
(future step), or - later - a standalone khata settlement. For this step only
customer -> sale payments are created, but the shape is deliberately generic so
the supplier-payment domain can reuse it without a migration rewrite.

Tenant integrity is enforced with composite foreign keys: `(sale_id, shop_id)`,
`(purchase_id, shop_id)`, `(customer_id, shop_id)` and `(supplier_id, shop_id)`
each target a supporting `UNIQUE(id, shop_id)` on the referenced table. That
means a payment belonging to Shop A can never point at Shop B's sale,
purchase, customer or supplier - the database rejects it, not just the service
layer.
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
        # CASCADE for documents (deleting a sale takes its payments with it).
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
        Index("ix_payments_shop_sale_id", "shop_id", "sale_id"),
        Index("ix_payments_shop_created_at", "shop_id", "created_at"),
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

    sale_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

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

    sale: Mapped["Sale | None"] = relationship(
        "Sale",
        back_populates="payments",
        overlaps="customer,payments,purchase,sale,shop,supplier",
    )

    purchase: Mapped["Purchase | None"] = relationship(
        "Purchase",
        back_populates="payments",
        overlaps="customer,payments,purchase,sale,shop,supplier",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"Payment(id={self.id!r}, amount={self.amount!r}, "
            f"method={self.method!r})"
        )
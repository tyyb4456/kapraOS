"""AI operation receipts - idempotency guard for AI-assisted mutations.

Step 3 introduces exactly one AI mutation (sale creation via the
``create_sale`` AI tool). Human-in-the-loop systems resume and retry:
the same approved operation can be delivered twice (duplicate resume,
network retry, model retry, frontend retry). LangGraph itself documents
this discipline — nodes re-run from the start on resume, so side
effects around an interrupt must be idempotent (use idempotency keys).

The receipt table is the smallest safe mechanism:

* The agent generates one ``operation_key`` per prepared sale (a UUID
  hex string). The key is part of the HITL tool-call args, so it is
  serialised through the agent checkpoint — never a live session, never
  an ORM object.
* The write tool checks ``(shop_id, operation_key)`` BEFORE calling
  ``SaleService.create_sale()``. A hit returns the already-created sale
  without mutating anything (check-before-create).
* On a miss, the sale AND its receipt are written in the SAME database
  transaction. A unique constraint on ``(shop_id, operation_key)`` is
  the final guard: a concurrent duplicate rolls back and re-reads the
  winner instead of creating a second sale.

Scope is per shop: keys never collide across tenants, and a receipt
always points at a sale owned by the same shop (composite discipline
mirrors the sale/customer guards). ``sale_id`` is nullable with
``SET NULL`` so deleting/voiding a sale never cascades into the
receipt — a replay after deletion reports "already processed" instead
of silently re-creating the sale.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.expense import Expense
    from app.models.payment import Payment
    from app.models.sale import Sale


class AISaleReceipt(Base, UUIDMixin, TimestampMixin):
    """One idempotency receipt for one approved AI-assisted sale."""

    __tablename__ = "ai_sale_receipts"

    __table_args__ = (
        # At most one sale per (shop, operation key): the duplicate-sale
        # guard. Concurrent duplicates race here; the loser gets an
        # IntegrityError, rolls back, and returns the winner's sale.
        UniqueConstraint(
            "shop_id",
            "operation_key",
            name="uq_ai_sale_receipts_shop_operation",
        ),
        Index(
            "ix_ai_sale_receipts_shop_created_at",
            "shop_id",
            "created_at",
        ),
        CheckConstraint(
            "length(trim(operation_key)) > 0",
            name="ck_ai_sale_receipts_key_not_blank",
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stable operation identity supplied by the agent (UUID hex). Scoped
    # per shop by the unique constraint above.
    operation_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # The sale this operation created. NULL only when the sale row is
    # gone (deleted/voided) — the key stays claimed.
    sale_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sales.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # No back_populates on Sale: receipts are an AI-layer concern, the
    # sales domain stays unaware of them. `overlaps` is unneeded — this
    # is a plain many-to-one with no competing relationship path.
    sale: Mapped["Sale | None"] = relationship("Sale")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"AISaleReceipt(shop_id={self.shop_id!r}, "
            f"operation_key={self.operation_key!r})"
        )


class AIPaymentReceipt(Base, UUIDMixin, TimestampMixin):
    """One idempotency receipt for one approved AI-assisted customer payment.

    Step 4 mirrors the Step 3 sale-receipt pattern, but with its own table:
    forcing payments into ``ai_sale_receipts`` (whose ``sale_id`` FK and
    name are sale-specific) would distort the data model. The mechanics are
    identical:

    * The agent generates one ``operation_key`` per prepared payment (a UUID
      hex string). The key is part of the HITL tool-call args, so it is
      serialised through the agent checkpoint — never a live session, never
      an ORM object.
    * The write tool checks ``(shop_id, operation_key)`` BEFORE calling
      ``receivables.record_customer_payment()``. A hit returns the
      already-created payment without mutating anything.
    * On a miss, the payment AND its receipt are written in the SAME
      database transaction. A unique constraint on
      ``(shop_id, operation_key)`` is the final guard: a concurrent
      duplicate rolls back and re-reads the winner instead of creating a
      second payment.

    Scope is per shop. ``payment_id`` is nullable with ``SET NULL`` so
    voiding a payment never cascades into the receipt — a replay after a
    void reports "already processed" instead of silently re-creating money
    movement.
    """

    __tablename__ = "ai_payment_receipts"

    __table_args__ = (
        # At most one payment per (shop, operation key): the duplicate-payment
        # guard. Concurrent duplicates race here; the loser gets an
        # IntegrityError, rolls back, and returns the winner's payment.
        UniqueConstraint(
            "shop_id",
            "operation_key",
            name="uq_ai_payment_receipts_shop_operation",
        ),
        Index(
            "ix_ai_payment_receipts_shop_created_at",
            "shop_id",
            "created_at",
        ),
        CheckConstraint(
            "length(trim(operation_key)) > 0",
            name="ck_ai_payment_receipts_key_not_blank",
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stable operation identity supplied by the agent (UUID hex). Scoped
    # per shop by the unique constraint above.
    operation_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # The payment this operation created. NULL only when the payment row is
    # gone (voided) — the key stays claimed.
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # No back_populates on Payment: receipts are an AI-layer concern, the
    # receivables domain stays unaware of them.
    payment: Mapped["Payment | None"] = relationship("Payment")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"AIPaymentReceipt(shop_id={self.shop_id!r}, "
            f"operation_key={self.operation_key!r})"
        )


class AISupplierPaymentReceipt(Base, UUIDMixin, TimestampMixin):
    """One idempotency receipt for one approved AI-assisted supplier payment.

    Step 5 mirrors the Step 3/Step 4 receipt pattern, but with its own table:
    forcing supplier payments into ``ai_sale_receipts`` (whose ``sale_id`` FK
    is sale-specific) or into ``ai_payment_receipts`` (whose name and
    customer-payment history are customer-specific) would distort the data
    model. The mechanics are identical:

    * The agent generates one ``operation_key`` per prepared supplier payment
      (a UUID hex string). The key is part of the HITL tool-call args, so it
      is serialised through the agent checkpoint — never a live session,
      never an ORM object.
    * The write tool checks ``(shop_id, operation_key)`` BEFORE calling
      ``payables.record_supplier_payment()``. A hit returns the
      already-created payment without mutating anything.
    * On a miss, the payment AND its receipt are written in the SAME
      database transaction. A unique constraint on
      ``(shop_id, operation_key)`` is the final guard: a concurrent
      duplicate rolls back and re-reads the winner instead of creating a
      second payment.

    Scope is per shop. ``payment_id`` is nullable with ``SET NULL`` so
    voiding a payment never cascades into the receipt — a replay after a
    void reports "already processed" instead of silently re-creating money
    movement.
    """

    __tablename__ = "ai_supplier_payment_receipts"

    __table_args__ = (
        # At most one supplier payment per (shop, operation key): the
        # duplicate-payment guard. Concurrent duplicates race here; the
        # loser gets an IntegrityError, rolls back, and returns the
        # winner's payment.
        UniqueConstraint(
            "shop_id",
            "operation_key",
            name="uq_ai_supplier_payment_receipts_shop_operation",
        ),
        Index(
            "ix_ai_supplier_payment_receipts_shop_created_at",
            "shop_id",
            "created_at",
        ),
        CheckConstraint(
            "length(trim(operation_key)) > 0",
            name="ck_ai_supplier_payment_receipts_key_not_blank",
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stable operation identity supplied by the agent (UUID hex). Scoped
    # per shop by the unique constraint above.
    operation_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # The supplier payment this operation created. NULL only when the payment
    # row is gone (voided) — the key stays claimed.
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # No back_populates on Payment: receipts are an AI-layer concern, the
    # payables domain stays unaware of them.
    payment: Mapped["Payment | None"] = relationship("Payment")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"AISupplierPaymentReceipt(shop_id={self.shop_id!r}, "
            f"operation_key={self.operation_key!r})"
        )


class AIExpenseReceipt(Base, UUIDMixin, TimestampMixin):
    """One idempotency receipt for one approved AI-assisted expense.

    Step 6 mirrors the Step 3/Step 4/Step 5 receipt pattern, but with its own
    table: forcing expenses into ``ai_sale_receipts`` (whose ``sale_id`` FK
    is sale-specific), into ``ai_payment_receipts`` (customer-payment
    specific), or into ``ai_supplier_payment_receipts`` (supplier-payment
    specific) would distort the data model. The mechanics are identical:

    * The agent generates one ``operation_key`` per prepared expense (a UUID
      hex string). The key is part of the HITL tool-call args, so it is
      serialised through the agent checkpoint — never a live session, never
      an ORM object.
    * The write tool checks ``(shop_id, operation_key)`` BEFORE calling
      ``expenses.create_expense()``. A hit returns the already-created
      expense without mutating anything.
    * On a miss, the expense AND its receipt are written in the SAME
      database transaction. A unique constraint on
      ``(shop_id, operation_key)`` is the final guard: a concurrent
      duplicate rolls back and re-reads the winner instead of creating a
      second expense.

    Scope is per shop. ``expense_id`` is nullable with ``SET NULL`` so
    voiding an expense never cascades into the receipt — a replay after a
    void reports "already processed" instead of silently re-creating money
    movement.
    """

    __tablename__ = "ai_expense_receipts"

    __table_args__ = (
        # At most one expense per (shop, operation key): the
        # duplicate-expense guard. Concurrent duplicates race here; the
        # loser gets an IntegrityError, rolls back, and returns the
        # winner's expense.
        UniqueConstraint(
            "shop_id",
            "operation_key",
            name="uq_ai_expense_receipts_shop_operation",
        ),
        Index(
            "ix_ai_expense_receipts_shop_created_at",
            "shop_id",
            "created_at",
        ),
        CheckConstraint(
            "length(trim(operation_key)) > 0",
            name="ck_ai_expense_receipts_key_not_blank",
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stable operation identity supplied by the agent (UUID hex). Scoped
    # per shop by the unique constraint above.
    operation_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # The expense this operation created. NULL only when the expense row is
    # gone (voided) — the key stays claimed.
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expenses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # No back_populates on Expense: receipts are an AI-layer concern, the
    # expense domain stays unaware of them.
    expense: Mapped["Expense | None"] = relationship("Expense")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"AIExpenseReceipt(shop_id={self.shop_id!r}, "
            f"operation_key={self.operation_key!r})"
        )

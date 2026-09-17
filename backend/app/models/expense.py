"""Expense domain - money a shop spends that is not a stock purchase.

`db_arch.md` section 22 sketches exactly this table, and Step 10 implements it
without inventing anything around it:

* An expense is *immediate-payment*: `payment_method` says which asset account
  the money left (Cash or Bank) and `app.services.accounting.post_expense()`
  writes the matching `Debit Expense / Credit Cash|Bank` posting in the same
  transaction that creates the row. There is deliberately no expense payable /
  creditor workflow in V1 - `Accounts Payable` stays supplier-only
  (`step_10_desc.md` sections 12 and 39).
* `category` is a small controlled enum rather than a category table
  (`step_10_desc.md` section 11): V1 does not need per-shop category
  management, and a future step can add one without rewriting history.
* Money is `NUMERIC(14,2)`, never Float, matching every other financial table.

A posted expense is **immutable**. There is no reversal/void workflow in V1, so
deleting an expense would leave its ledger entries behind and silently corrupt
the statements the ledger feeds; `step_10_desc.md` sections 16 and 37 say to
treat posted expenses as immutable instead.
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
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.payment import PaymentMethod

if TYPE_CHECKING:
    from app.models.shop import Shop


class ExpenseCategory(str, Enum):
    """The controlled V1 expense categories (`step_10_desc.md` section 11).

    Each category maps to one system EXPENSE account - see
    `app.services.accounting._CATEGORY_ACCOUNT_CODES`. Several categories share
    the "Other Expense" account, which is the point: the chart of accounts stays
    small and a future step can split it without touching this enum.
    """

    RENT = "rent"
    SALARY = "salary"
    UTILITIES = "utilities"
    TRANSPORT = "transport"
    MARKETING = "marketing"
    MAINTENANCE = "maintenance"
    SUPPLIES = "supplies"
    OTHER = "other"


class Expense(Base, UUIDMixin, TimestampMixin):
    """One immediate-payment operating expense for one shop."""

    __tablename__ = "expenses"

    __table_args__ = (
        # The statement read pattern: one shop's expenses, newest first.
        Index("ix_expenses_shop_expense_date", "shop_id", "expense_date"),
        Index("ix_expenses_shop_created_at", "shop_id", "created_at"),
        CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    category: Mapped[ExpenseCategory] = mapped_column(
        SQLEnum(
            ExpenseCategory,
            name="expense_category",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(String(300))

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    payment_method: Mapped[PaymentMethod] = mapped_column(
        SQLEnum(
            PaymentMethod,
            name="payment_method",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    # When the expense happened (business date). Defaults to "now" in the
    # service; `created_at` stays the immutable audit timestamp.
    expense_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    shop: Mapped["Shop"] = relationship("Shop", back_populates="expenses")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"Expense(id={self.id!r}, category={self.category!r}, "
            f"amount={self.amount!r})"
        )
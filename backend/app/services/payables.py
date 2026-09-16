"""Payables service - the supplier Khata (accounts payable).

Step 7 adds **no new persistent state**. `Purchase` and `Payment` (Step 5)
remain the single source of truth for what a shop was invoiced by a supplier
and what it actually paid; everything in this module is either an aggregate or
a read model assembled from those rows at query time. There is deliberately no
`SupplierBalance` / `SupplierLedger` table and no `supplier.current_balance`
column, so a stored balance can never drift from the records that produced it.

    outstanding = SUM(qualifying Purchase.total)
                - SUM(qualifying supplier Payment.amount)

A positive balance means the shop owes the supplier. This is the exact mirror
of `app.services.receivables` (customer Khata), which is why the two modules
read almost line for line alike.

Decisions taken here:

* **Which purchases count.** `Purchase` (Step 5) has no status column and no
  cancellation/void workflow, so every purchase row is a real payable. Step 7
  must not invent a purchase status merely to have one - the qualifying rule is
  centralised in `_qualifying_purchases_filter()` so a future purchase-return
  or cancellation workflow has exactly one place to change.

* **Which payments count.** A payment counts when it belongs to this shop *and*
  this supplier, is not a customer/sale payment, and - if it references a
  purchase - that purchase itself qualifies. The symmetry matters: crediting a
  payment whose purchase was voided would otherwise leave a phantom balance.

* **Unallocated payments.** `Payment.purchase_id` is nullable, so "we paid
  Al-Madina Rs 5,000 off the account" is representable without naming a
  purchase. Such a payment reduces the supplier's overall payable balance and
  appears on the statement, but it deliberately does *not* touch any
  `Purchase.paid_amount`. A payment that *does* name a purchase is allocated to
  it, which is what keeps Step 5's `Purchase.paid_amount` cache truthful.

* **Negative balances are surfaced, not clamped.** Under V1 rules a settlement
  cannot exceed the outstanding balance, but if historical rows produce a
  negative number (an overpayment/credit with the supplier) the caller sees it
  rather than having it silently rounded up to zero.

* **Debit / credit / balance here are Khata presentation concepts**, not
  double-entry accounting. The general ledger is a later step; nothing in this
  module writes `accounts` or `ledger_entries`.

Tenant isolation: every query is filtered by both `shop_id` and `supplier_id`,
and the supplier is always resolved through `_get_supplier()` first, so a
supplier id from another shop is reported as "not found" rather than read.
Nothing here commits - the caller owns the transaction, exactly like the
inventory/purchases/sales/receivables services.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from sqlalchemy import (
    Integer,
    Numeric,
    String,
    func,
    literal_column,
    null,
    or_,
    select,
    type_coerce,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.models.payment import Payment, PaymentMethod
from app.models.purchase import Purchase
from app.models.supplier import Supplier
from app.services import accounting as accounting_service

# Money is NUMERIC(14,2) everywhere in the schema; every value this module
# returns is quantized to that scale so callers can compare exactly.
_MONEY_SCALE = Decimal("0.01")
_ZERO = Decimal("0.00")


class PayablesError(Exception):
    """Base class for payables-domain failures."""


class SupplierNotFoundError(PayablesError):
    """The supplier does not exist in the caller's shop."""


class PurchaseNotFoundError(PayablesError):
    """The referenced purchase does not exist in the caller's shop."""


class PurchaseSupplierMismatchError(PayablesError):
    """The referenced purchase belongs to a different supplier of this shop."""


class InvalidPaymentAmountError(PayablesError):
    """A payment amount must be greater than zero."""


class InvalidPaymentMethodError(PayablesError):
    """The supplied payment method is not a `PaymentMethod`."""


class PaymentExceedsOutstandingError(PayablesError):
    """The settlement is larger than the supplier's outstanding payable."""


class PaymentExceedsPurchaseDueError(PayablesError):
    """The settlement is larger than the named purchase's remaining due."""


class InvalidStatementRangeError(PayablesError):
    """`start_date` is after `end_date`."""


class InvalidPaginationError(PayablesError):
    """`limit` / `offset` are outside their allowed ranges."""


class StatementEntryType(str, Enum):
    """The two kinds of row a supplier Khata statement can contain."""

    PURCHASE = "PURCHASE"
    PAYMENT = "PAYMENT"


@dataclass(frozen=True)
class SupplierBalance:
    """Aggregate payable position for one supplier of one shop."""

    supplier_id: uuid.UUID
    total_purchases: Decimal
    total_payments: Decimal
    outstanding_balance: Decimal
    number_of_purchases: int
    number_of_payments: int
    last_purchase_at: datetime | None
    last_payment_at: datetime | None


@dataclass(frozen=True)
class SupplierSummary:
    """Dashboard-shaped view of `SupplierBalance`, in the shop's language.

    "Total purchases" is what the shop bought from the supplier; "outstanding"
    is the payable the shop still owes.
    """

    supplier_id: uuid.UUID
    name: str
    phone: str | None
    total_purchases: Decimal
    total_paid: Decimal
    outstanding_balance: Decimal


@dataclass(frozen=True)
class StatementEntry:
    """One line of a supplier Khata statement.

    A purchase increases what the shop owes (debit); a payment decreases it
    (credit). `running_balance` is `previous_balance + debit - credit`.
    """

    entry_type: StatementEntryType
    date: datetime
    reference: str | None
    amount: Decimal
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    purchase_id: uuid.UUID | None
    payment_id: uuid.UUID | None
    invoice_number: str | None
    payment_method: PaymentMethod | None


@dataclass(frozen=True)
class SupplierStatement:
    """A page of a supplier's Khata.

    `opening_balance` is the balance carried *into* the first returned entry -
    it accounts both for entries before `start_date` and for entries this page
    skipped via `offset`, so the running balances are correct on every page.
    `total_entries` counts the entries in the requested date window (ignoring
    pagination) so a caller can page through them.
    """

    supplier_id: uuid.UUID
    opening_balance: Decimal
    closing_balance: Decimal
    total_entries: int
    entries: tuple[StatementEntry, ...]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _money(value: Decimal | int | None) -> Decimal:
    """Normalise a (possibly NULL) SQL aggregate to NUMERIC(14,2) semantics."""

    if value is None:
        return _ZERO
    return Decimal(value).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)


def _as_payment_method(value: PaymentMethod | str | None) -> PaymentMethod | None:
    """Coerce a method read back through a UNION into its enum member."""

    if value is None or isinstance(value, PaymentMethod):
        return value
    return PaymentMethod(value)


def _qualifying_purchases_filter(
    shop_id: uuid.UUID, supplier_id: uuid.UUID
) -> tuple[ColumnElement[bool], ...]:
    """Purchases that count towards this supplier's payable.

    `Purchase` has no status column (Step 5 never implemented a
    cancellation/void workflow), so every purchase is a real supplier debt.
    This is the supplier-side counterpart of
    `receivables.QUALIFYING_SALE_STATUSES`: if a purchase status is introduced
    later, this function is the single place to filter it.
    """

    return (
        Purchase.shop_id == shop_id,
        Purchase.supplier_id == supplier_id,
    )


def _supplier_payments_filter(
    shop_id: uuid.UUID, supplier_id: uuid.UUID
) -> tuple[ColumnElement[bool], ...]:
    """Payments that count towards this supplier's payable.

    Customer/sale payments are excluded outright so the same `Payment` table
    can serve the receivables domain untouched. A payment that names a purchase
    only counts when that purchase qualifies (correlated EXISTS), which keeps
    credits and debits symmetric if a purchase is ever voided.
    """

    qualifying_purchase = (
        select(Purchase.id)
        .where(
            Purchase.id == Payment.purchase_id,
            *_qualifying_purchases_filter(shop_id, supplier_id),
        )
        .correlate(Payment)
        .exists()
    )

    return (
        Payment.shop_id == shop_id,
        Payment.supplier_id == supplier_id,
        Payment.customer_id.is_(None),
        Payment.sale_id.is_(None),
        or_(Payment.purchase_id.is_(None), qualifying_purchase),
    )


def _apply_date_range(
    statement: Select[tuple[object, ...]],
    column: ColumnElement[datetime],
    start_date: datetime | None,
    end_date: datetime | None,
) -> Select[tuple[object, ...]]:
    """Restrict a statement to `[start_date, end_date]` (both inclusive)."""

    if start_date is not None:
        statement = statement.where(column >= start_date)
    if end_date is not None:
        statement = statement.where(column <= end_date)
    return statement


async def _get_supplier(
    session: AsyncSession,
    shop_id: uuid.UUID,
    supplier_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Supplier:
    """Load a supplier, enforcing that it belongs to `shop_id`.

    A supplier from another shop is reported as not found rather than read, so
    no cross-tenant balance can ever be computed - even when two shops have a
    supplier with the same name and phone number.

    `for_update` locks the row, which the settlement path uses to serialise
    concurrent payments for the same supplier against the outstanding check.
    """

    statement = select(Supplier).where(
        Supplier.id == supplier_id,
        Supplier.shop_id == shop_id,
    )
    if for_update:
        statement = statement.with_for_update()

    supplier = (await session.execute(statement)).scalar_one_or_none()
    if supplier is None:
        raise SupplierNotFoundError(
            f"Supplier {supplier_id} not found for shop {shop_id}"
        )
    return supplier


# --------------------------------------------------------------------------
# Balance / summary
# --------------------------------------------------------------------------


async def _compute_balance(
    session: AsyncSession, shop_id: uuid.UUID, supplier_id: uuid.UUID
) -> SupplierBalance:
    """Aggregate the balance in two database-side queries (no row loading)."""

    purchases_row = (
        await session.execute(
            select(
                func.sum(Purchase.total),
                func.count(Purchase.id),
                func.max(Purchase.created_at),
            ).where(*_qualifying_purchases_filter(shop_id, supplier_id))
        )
    ).one()

    payments_row = (
        await session.execute(
            select(
                func.sum(Payment.amount),
                func.count(Payment.id),
                func.max(Payment.created_at),
            ).where(*_supplier_payments_filter(shop_id, supplier_id))
        )
    ).one()

    total_purchases = _money(purchases_row[0])
    total_payments = _money(payments_row[0])

    return SupplierBalance(
        supplier_id=supplier_id,
        total_purchases=total_purchases,
        total_payments=total_payments,
        # Deliberately not clamped at zero - see the module docstring.
        outstanding_balance=total_purchases - total_payments,
        number_of_purchases=purchases_row[1],
        number_of_payments=payments_row[1],
        last_purchase_at=purchases_row[2],
        last_payment_at=payments_row[2],
    )


async def get_supplier_balance(
    session: AsyncSession, *, shop_id: uuid.UUID, supplier_id: uuid.UUID
) -> SupplierBalance:
    """Return how much the shop owes this supplier, and what it is made of."""

    await _get_supplier(session, shop_id, supplier_id)
    return await _compute_balance(session, shop_id, supplier_id)


async def get_supplier_summary(
    session: AsyncSession, *, shop_id: uuid.UUID, supplier_id: uuid.UUID
) -> SupplierSummary:
    """Return the dashboard summary for one supplier."""

    supplier = await _get_supplier(session, shop_id, supplier_id)
    balance = await _compute_balance(session, shop_id, supplier_id)

    return SupplierSummary(
        supplier_id=supplier.id,
        name=supplier.name,
        phone=supplier.phone,
        total_purchases=balance.total_purchases,
        total_paid=balance.total_payments,
        outstanding_balance=balance.outstanding_balance,
    )


# --------------------------------------------------------------------------
# Statement (Khata)
# --------------------------------------------------------------------------


def _statement_entries(
    shop_id: uuid.UUID,
    supplier_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
):
    """Build the statement read model as one `UNION ALL` subquery.

    Assembling it in SQL (rather than merging two result sets in Python) is
    what lets the database do the ordering, counting and `LIMIT/OFFSET`, so a
    long-running supplier's Khata never has to be loaded whole.

    `entry_rank` is the tiebreaker that makes ordering deterministic *and*
    sensible: `created_at` comes from `now()`, which is the transaction
    timestamp, so a purchase and a payment made in the same transaction share
    it exactly - and the purchase must still be listed before its settlement.
    `entry_id` breaks any remaining tie so the order is stable across queries.
    """

    purchase_entries = _apply_date_range(
        select(
            literal_column("'PURCHASE'", String()).label("entry_type"),
            literal_column("0", Integer()).label("entry_rank"),
            Purchase.created_at.label("created_at"),
            Purchase.id.label("entry_id"),
            Purchase.id.label("purchase_id"),
            type_coerce(null(), PGUUID(as_uuid=True)).label("payment_id"),
            Purchase.invoice_number.label("invoice_number"),
            Purchase.invoice_number.label("reference"),
            type_coerce(null(), Payment.__table__.c.method.type).label(
                "payment_method"
            ),
            Purchase.total.label("debit"),
            literal_column("0", Numeric(14, 2)).label("credit"),
        ).where(*_qualifying_purchases_filter(shop_id, supplier_id)),
        Purchase.created_at,
        start_date,
        end_date,
    )

    payment_entries = _apply_date_range(
        select(
            literal_column("'PAYMENT'", String()).label("entry_type"),
            literal_column("1", Integer()).label("entry_rank"),
            Payment.created_at.label("created_at"),
            Payment.id.label("entry_id"),
            Payment.purchase_id.label("purchase_id"),
            Payment.id.label("payment_id"),
            type_coerce(null(), String(50)).label("invoice_number"),
            Payment.reference.label("reference"),
            Payment.method.label("payment_method"),
            literal_column("0", Numeric(14, 2)).label("debit"),
            Payment.amount.label("credit"),
        ).where(*_supplier_payments_filter(shop_id, supplier_id)),
        Payment.created_at,
        start_date,
        end_date,
    )

    return purchase_entries.union_all(payment_entries).subquery("payables_entries")


async def _balance_before(
    session: AsyncSession,
    shop_id: uuid.UUID,
    supplier_id: uuid.UUID,
    before: datetime | None,
) -> Decimal:
    """Net balance of everything qualifying that happened before `before`."""

    if before is None:
        return _ZERO

    purchases = (
        await session.execute(
            select(func.sum(Purchase.total)).where(
                *_qualifying_purchases_filter(shop_id, supplier_id),
                Purchase.created_at < before,
            )
        )
    ).scalar()
    payments = (
        await session.execute(
            select(func.sum(Payment.amount)).where(
                *_supplier_payments_filter(shop_id, supplier_id),
                Payment.created_at < before,
            )
        )
    ).scalar()

    return _money(purchases) - _money(payments)


def _validate_statement_arguments(
    start_date: datetime | None,
    end_date: datetime | None,
    limit: int | None,
    offset: int,
) -> None:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise InvalidStatementRangeError(
            f"start_date {start_date} is after end_date {end_date}"
        )
    if offset < 0:
        raise InvalidPaginationError("offset must be >= 0")
    if limit is not None and limit < 1:
        raise InvalidPaginationError("limit must be >= 1 when provided")


async def get_supplier_statement(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    supplier_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> SupplierStatement:
    """Return a chronological supplier Khata with running balances.

    The statement is a read model: no table stores these entries. Both dates
    are inclusive, and `limit`/`offset` page through the window while
    `opening_balance` keeps the running balances correct on later pages.
    """

    await _get_supplier(session, shop_id, supplier_id)
    _validate_statement_arguments(start_date, end_date, limit, offset)

    entries = _statement_entries(shop_id, supplier_id, start_date, end_date)
    ordering = (
        entries.c.created_at.asc(),
        entries.c.entry_rank.asc(),
        entries.c.entry_id.asc(),
    )

    total_entries = (
        await session.execute(select(func.count()).select_from(entries))
    ).scalar_one()

    # Balance carried into the first returned row: whatever happened before the
    # window, plus the net effect of the rows this page skips.
    opening_balance = await _balance_before(session, shop_id, supplier_id, start_date)
    if offset:
        skipped = (
            select(entries.c.debit, entries.c.credit)
            .order_by(*ordering)
            .limit(offset)
            .subquery("skipped_entries")
        )
        skipped_net = (
            await session.execute(
                select(func.sum(skipped.c.debit - skipped.c.credit))
            )
        ).scalar()
        opening_balance = opening_balance + _money(skipped_net)

    page = select(entries).order_by(*ordering)
    if offset:
        page = page.offset(offset)
    if limit is not None:
        page = page.limit(limit)

    balance = opening_balance
    lines: list[StatementEntry] = []
    for row in (await session.execute(page)).all():
        entry_type = StatementEntryType(row.entry_type)
        debit = _money(row.debit)
        credit = _money(row.credit)
        balance = balance + debit - credit
        lines.append(
            StatementEntry(
                entry_type=entry_type,
                date=row.created_at,
                reference=row.reference,
                amount=(
                    debit if entry_type is StatementEntryType.PURCHASE else credit
                ),
                debit=debit,
                credit=credit,
                running_balance=balance,
                purchase_id=row.purchase_id,
                payment_id=row.payment_id,
                invoice_number=row.invoice_number,
                payment_method=_as_payment_method(row.payment_method),
            )
        )

    return SupplierStatement(
        supplier_id=supplier_id,
        opening_balance=opening_balance,
        closing_balance=balance,
        total_entries=total_entries,
        entries=tuple(lines),
    )


# --------------------------------------------------------------------------
# Settlement
# --------------------------------------------------------------------------


async def _get_settleable_purchase(
    session: AsyncSession,
    shop_id: uuid.UUID,
    supplier_id: uuid.UUID,
    purchase_id: uuid.UUID,
) -> Purchase:
    """Load and lock a purchase a settlement is being allocated to.

    Two separate failures, kept distinct because they mean different things to
    the caller: a purchase from another shop is simply not found (no
    cross-tenant existence leak), while a purchase belonging to another
    supplier of *this* shop is a mismatch.
    """

    purchase = (
        await session.execute(
            select(Purchase)
            .where(Purchase.id == purchase_id, Purchase.shop_id == shop_id)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if purchase is None:
        raise PurchaseNotFoundError(
            f"Purchase {purchase_id} not found for shop {shop_id}"
        )
    if purchase.supplier_id != supplier_id:
        raise PurchaseSupplierMismatchError(
            f"Purchase {purchase_id} does not belong to supplier {supplier_id}"
        )
    return purchase


async def record_supplier_payment(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    supplier_id: uuid.UUID,
    amount: Decimal,
    method: PaymentMethod,
    purchase_id: uuid.UUID | None = None,
    reference: str | None = None,
) -> Payment:
    """Record money paid by the shop to a supplier against their Khata.

    This creates a perfectly ordinary Step 5 `Payment` row - there is no
    supplier-specific payment model. Validation, in order:

    1. the supplier exists and belongs to `shop_id` (its row is locked, so two
       concurrent settlements cannot both pass the outstanding check);
    2. the amount is positive, at NUMERIC(14,2) precision;
    3. the method is a real `PaymentMethod`;
    4. if `purchase_id` is given, that purchase exists, belongs to this shop
       *and* this supplier, and is settleable;
    5. the amount does not exceed the supplier's outstanding payable (V1 has no
       supplier credit/advance accounting, so an overpayment is rejected rather
       than silently banked);
    6. if `purchase_id` is given, the amount does not exceed that purchase's
       remaining due either.

    When `purchase_id` is given the payment is allocated: `Purchase.paid_amount`
    is updated from it, preserving Step 5's invariant that
    `Purchase.paid_amount` reflects the payments recorded against that purchase.
    When it is omitted the payment is unallocated - it reduces the supplier's
    overall outstanding balance and no individual purchase is touched.

    The caller owns the transaction: this flushes but never commits, so a failed
    settlement leaves no `Payment` row behind.
    """

    await _get_supplier(session, shop_id, supplier_id, for_update=True)

    amount = _money(amount)
    if amount <= 0:
        raise InvalidPaymentAmountError(
            f"payment amount must be greater than 0, got {amount}"
        )

    if not isinstance(method, PaymentMethod):
        try:
            method = PaymentMethod(method)
        except ValueError as exc:
            raise InvalidPaymentMethodError(
                f"unknown payment method {method!r}"
            ) from exc

    purchase: Purchase | None = None
    if purchase_id is not None:
        purchase = await _get_settleable_purchase(
            session, shop_id, supplier_id, purchase_id
        )

    balance = await _compute_balance(session, shop_id, supplier_id)
    if amount > balance.outstanding_balance:
        raise PaymentExceedsOutstandingError(
            f"payment {amount} exceeds outstanding payable "
            f"{balance.outstanding_balance} for supplier {supplier_id}"
        )

    if purchase is not None and amount > purchase.due_amount:
        raise PaymentExceedsPurchaseDueError(
            f"payment {amount} exceeds the {purchase.due_amount} still due on "
            f"purchase {purchase.id}"
        )

    payment = Payment(
        shop_id=shop_id,
        supplier_id=supplier_id,
        purchase_id=purchase_id,
        amount=amount,
        method=method,
        reference=reference,
    )
    session.add(payment)

    if purchase is not None:
        purchase.paid_amount = _money(purchase.paid_amount + amount)

    await session.flush()

    # The ledger representation of the same settlement: Dr AP, Cr Cash/Bank.
    await accounting_service.post_supplier_payment(session, payment=payment)

    return payment
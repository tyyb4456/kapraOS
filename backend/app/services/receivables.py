"""Receivables service - the customer Khata (accounts receivable).

Step 6 adds **no new persistent state**. `Sale` and `Payment` (Step 5) remain
the single source of truth for what a customer was invoiced and what they
actually paid; everything in this module is either an aggregate or a read model
assembled from those rows at query time. There is deliberately no
`CustomerBalance` / `CustomerKhata` table and no `customer.current_balance`
column, so a stored balance can never drift from the records that produced it.

    outstanding = SUM(qualifying Sale.total) - SUM(qualifying customer Payment.amount)

Decisions taken here, because `db_arch.md` does not spell them out:

* **Which sales count.** `SaleStatus.COMPLETED` and `SaleStatus.PARTIAL` are
  real invoices and contribute to the receivable. `CANCELLED` is a voided
  document and never does. `RETURNED` is also excluded: the enum value exists
  for forward compatibility but there is no returns workflow in the system yet
  (see `app.models.sale.SaleStatus`), and Step 6 must not invent one - a sale
  that has been returned is not money the customer still owes. When the returns
  domain lands, the reversal (credit note) belongs there, and
  `QUALIFYING_SALE_STATUSES` is the single place to revisit.

* **Which payments count.** A payment counts when it belongs to this shop *and*
  this customer, is not a supplier/purchase payment, and - if it references a
  sale - that sale itself qualifies. The symmetry matters: crediting a payment
  whose invoice was voided would otherwise push the customer into a phantom
  negative balance.

* **Unallocated payments.** `Payment.sale_id` is nullable, so "Ahmed handed
  over Rs 5,000" is representable without naming an invoice. Such a payment
  reduces the customer's overall outstanding balance and appears on the
  statement, but it deliberately does *not* touch any `Sale.paid_amount`:
  invoice-level allocation is a separate feature and is not invented here. A
  payment that *does* name a sale is allocated to it, which is what keeps Step
  5's `Sale.paid_amount` cache truthful (`db_arch.md` section 28 - due is
  always derived from it, never edited directly).

* **Negative balances are surfaced, not clamped.** Under V1 rules
  `sales - payments >= 0`, but if historical rows produce a negative number the
  caller sees it rather than having it silently rounded up to zero.

* **Debit / credit / balance here are Khata presentation concepts**, not
  double-entry accounting. The general ledger is a later step; nothing in this
  module writes `accounts` or `ledger_entries`.

Tenant isolation: every query is filtered by both `shop_id` and `customer_id`,
and the customer is always resolved through `_get_customer()` first, so a
customer id from another shop is reported as "not found" rather than read
(`db_arch.md` section 33). Nothing here commits - the caller owns the
transaction, exactly like the inventory/purchases/sales services.
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

from app.models.customer import Customer
from app.models.payment import Payment, PaymentMethod
from app.models.sale import Sale, SaleStatus
from app.services import accounting as accounting_service

# Money is NUMERIC(14,2) everywhere in the schema; every value this module
# returns is quantized to that scale so callers can compare exactly.
_MONEY_SCALE = Decimal("0.01")
_ZERO = Decimal("0.00")

#: Sale statuses that represent an invoice the customer is actually liable for.
#: See the module docstring for why CANCELLED and RETURNED are excluded.
QUALIFYING_SALE_STATUSES: tuple[SaleStatus, ...] = (
    SaleStatus.COMPLETED,
    SaleStatus.PARTIAL,
)


class ReceivablesError(Exception):
    """Base class for receivables-domain failures."""


class CustomerNotFoundError(ReceivablesError):
    """The customer does not exist in the caller's shop."""


class SaleNotFoundError(ReceivablesError):
    """The referenced sale does not exist in the caller's shop."""


class SaleCustomerMismatchError(ReceivablesError):
    """The referenced sale belongs to a different customer of this shop."""


class SaleNotSettleableError(ReceivablesError):
    """The referenced sale is cancelled/returned, so nothing is owed on it."""


class InvalidPaymentAmountError(ReceivablesError):
    """A payment amount must be greater than zero."""


class InvalidPaymentMethodError(ReceivablesError):
    """The supplied payment method is not a `PaymentMethod`."""


class PaymentExceedsOutstandingError(ReceivablesError):
    """The settlement is larger than the customer's outstanding balance."""


class PaymentExceedsSaleDueError(ReceivablesError):
    """The settlement is larger than the named sale's remaining due."""


class InvalidStatementRangeError(ReceivablesError):
    """`start_date` is after `end_date`."""


class InvalidPaginationError(ReceivablesError):
    """`limit` / `offset` are outside their allowed ranges."""


class StatementEntryType(str, Enum):
    """The two kinds of row a customer Khata statement can contain."""

    SALE = "SALE"
    PAYMENT = "PAYMENT"


@dataclass(frozen=True)
class CustomerBalance:
    """Aggregate receivable position for one customer of one shop."""

    customer_id: uuid.UUID
    total_sales: Decimal
    total_payments: Decimal
    outstanding_balance: Decimal
    number_of_sales: int
    number_of_payments: int
    last_sale_at: datetime | None
    last_payment_at: datetime | None


@dataclass(frozen=True)
class CustomerSummary:
    """Dashboard-shaped view of `CustomerBalance`, in the shop's language.

    "Total purchases" is what the *customer* purchased, i.e. the shop's total
    sales to them; "outstanding" is the receivable the shop is still owed.
    """

    customer_id: uuid.UUID
    name: str
    phone: str | None
    total_purchases: Decimal
    total_paid: Decimal
    outstanding_balance: Decimal


@dataclass(frozen=True)
class StatementEntry:
    """One line of a customer Khata statement.

    A sale increases what the customer owes (debit); a payment decreases it
    (credit). `running_balance` is `previous_balance + debit - credit`.
    """

    entry_type: StatementEntryType
    date: datetime
    reference: str | None
    amount: Decimal
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    sale_id: uuid.UUID | None
    payment_id: uuid.UUID | None
    invoice_number: str | None
    payment_method: PaymentMethod | None


@dataclass(frozen=True)
class CustomerStatement:
    """A page of a customer's Khata.

    `opening_balance` is the balance carried *into* the first returned entry -
    it accounts both for entries before `start_date` and for entries this page
    skipped via `offset`, so the running balances are correct on every page.
    `total_entries` counts the entries in the requested date window (ignoring
    pagination) so a caller can page through them.
    """

    customer_id: uuid.UUID
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


def _qualifying_sales_filter(
    shop_id: uuid.UUID, customer_id: uuid.UUID
) -> tuple[ColumnElement[bool], ...]:
    """Sales that count towards this customer's receivable.

    `Sale.customer_id == customer_id` is what keeps walk-in sales
    (`customer_id IS NULL`) out of every customer's Khata.
    """

    return (
        Sale.shop_id == shop_id,
        Sale.customer_id == customer_id,
        Sale.status.in_(QUALIFYING_SALE_STATUSES),
    )


def _customer_payments_filter(
    shop_id: uuid.UUID, customer_id: uuid.UUID
) -> tuple[ColumnElement[bool], ...]:
    """Payments that count towards this customer's receivable.

    Supplier/purchase payments are excluded outright so the same `Payment`
    table can serve the future payables domain untouched. A payment that names
    a sale only counts when that sale qualifies (correlated EXISTS), which
    keeps credits and debits symmetric for voided invoices.
    """

    qualifying_sale = (
        select(Sale.id)
        .where(
            Sale.id == Payment.sale_id,
            *_qualifying_sales_filter(shop_id, customer_id),
        )
        .correlate(Payment)
        .exists()
    )

    return (
        Payment.shop_id == shop_id,
        Payment.customer_id == customer_id,
        Payment.supplier_id.is_(None),
        Payment.purchase_id.is_(None),
        or_(Payment.sale_id.is_(None), qualifying_sale),
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


async def _get_customer(
    session: AsyncSession,
    shop_id: uuid.UUID,
    customer_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Customer:
    """Load a customer, enforcing that it belongs to `shop_id`.

    A customer from another shop is reported as not found rather than read, so
    no cross-tenant balance can ever be computed - even when two shops have a
    customer with the same name and phone number.

    `for_update` locks the row, which the settlement path uses to serialise
    concurrent payments for the same customer against the outstanding check.
    """

    statement = select(Customer).where(
        Customer.id == customer_id,
        Customer.shop_id == shop_id,
    )
    if for_update:
        statement = statement.with_for_update()

    customer = (await session.execute(statement)).scalar_one_or_none()
    if customer is None:
        raise CustomerNotFoundError(
            f"Customer {customer_id} not found for shop {shop_id}"
        )
    return customer


# --------------------------------------------------------------------------
# Balance / summary
# --------------------------------------------------------------------------


async def _compute_balance(
    session: AsyncSession, shop_id: uuid.UUID, customer_id: uuid.UUID
) -> CustomerBalance:
    """Aggregate the balance in two database-side queries (no row loading)."""

    sales_row = (
        await session.execute(
            select(
                func.sum(Sale.total),
                func.count(Sale.id),
                func.max(Sale.created_at),
            ).where(*_qualifying_sales_filter(shop_id, customer_id))
        )
    ).one()

    payments_row = (
        await session.execute(
            select(
                func.sum(Payment.amount),
                func.count(Payment.id),
                func.max(Payment.created_at),
            ).where(*_customer_payments_filter(shop_id, customer_id))
        )
    ).one()

    total_sales = _money(sales_row[0])
    total_payments = _money(payments_row[0])

    return CustomerBalance(
        customer_id=customer_id,
        total_sales=total_sales,
        total_payments=total_payments,
        # Deliberately not clamped at zero - see the module docstring.
        outstanding_balance=total_sales - total_payments,
        number_of_sales=sales_row[1],
        number_of_payments=payments_row[1],
        last_sale_at=sales_row[2],
        last_payment_at=payments_row[2],
    )


async def get_customer_balance(
    session: AsyncSession, *, shop_id: uuid.UUID, customer_id: uuid.UUID
) -> CustomerBalance:
    """Return how much this customer owes, and what that figure is made of."""

    await _get_customer(session, shop_id, customer_id)
    return await _compute_balance(session, shop_id, customer_id)


async def get_customer_summary(
    session: AsyncSession, *, shop_id: uuid.UUID, customer_id: uuid.UUID
) -> CustomerSummary:
    """Return the dashboard summary for one customer."""

    customer = await _get_customer(session, shop_id, customer_id)
    balance = await _compute_balance(session, shop_id, customer_id)

    return CustomerSummary(
        customer_id=customer.id,
        name=customer.name,
        phone=customer.phone,
        total_purchases=balance.total_sales,
        total_paid=balance.total_payments,
        outstanding_balance=balance.outstanding_balance,
    )


# --------------------------------------------------------------------------
# Statement (Khata)
# --------------------------------------------------------------------------


def _statement_entries(
    shop_id: uuid.UUID,
    customer_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
):
    """Build the statement read model as one `UNION ALL` subquery.

    Assembling it in SQL (rather than merging two result sets in Python) is
    what lets the database do the ordering, counting and `LIMIT/OFFSET`, so a
    long-running customer's Khata never has to be loaded whole.

    `entry_rank` is the tiebreaker that makes ordering deterministic *and*
    sensible: `created_at` comes from `now()`, which is the transaction
    timestamp, so a sale and the payment taken at the till share it exactly -
    and the invoice must still be listed before its settlement. `entry_id`
    breaks any remaining tie so the order is stable across queries.

    Constants are emitted as SQL literals rather than bind parameters so the
    two branches of the UNION always have unambiguous types.
    """

    sale_entries = _apply_date_range(
        select(
            literal_column("'SALE'", String()).label("entry_type"),
            literal_column("0", Integer()).label("entry_rank"),
            Sale.created_at.label("created_at"),
            Sale.id.label("entry_id"),
            Sale.id.label("sale_id"),
            type_coerce(null(), PGUUID(as_uuid=True)).label("payment_id"),
            Sale.invoice_number.label("invoice_number"),
            Sale.invoice_number.label("reference"),
            type_coerce(null(), Payment.__table__.c.method.type).label(
                "payment_method"
            ),
            Sale.total.label("debit"),
            literal_column("0", Numeric(14, 2)).label("credit"),
        ).where(*_qualifying_sales_filter(shop_id, customer_id)),
        Sale.created_at,
        start_date,
        end_date,
    )

    payment_entries = _apply_date_range(
        select(
            literal_column("'PAYMENT'", String()).label("entry_type"),
            literal_column("1", Integer()).label("entry_rank"),
            Payment.created_at.label("created_at"),
            Payment.id.label("entry_id"),
            Payment.sale_id.label("sale_id"),
            Payment.id.label("payment_id"),
            type_coerce(null(), String(50)).label("invoice_number"),
            Payment.reference.label("reference"),
            Payment.method.label("payment_method"),
            literal_column("0", Numeric(14, 2)).label("debit"),
            Payment.amount.label("credit"),
        ).where(*_customer_payments_filter(shop_id, customer_id)),
        Payment.created_at,
        start_date,
        end_date,
    )

    return sale_entries.union_all(payment_entries).subquery("khata_entries")


async def _balance_before(
    session: AsyncSession,
    shop_id: uuid.UUID,
    customer_id: uuid.UUID,
    before: datetime | None,
) -> Decimal:
    """Net balance of everything qualifying that happened before `before`."""

    if before is None:
        return _ZERO

    sales = (
        await session.execute(
            select(func.sum(Sale.total)).where(
                *_qualifying_sales_filter(shop_id, customer_id),
                Sale.created_at < before,
            )
        )
    ).scalar()
    payments = (
        await session.execute(
            select(func.sum(Payment.amount)).where(
                *_customer_payments_filter(shop_id, customer_id),
                Payment.created_at < before,
            )
        )
    ).scalar()

    return _money(sales) - _money(payments)


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


async def get_customer_statement(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    customer_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> CustomerStatement:
    """Return a chronological customer Khata with running balances.

    The statement is a read model: no table stores these entries. Both dates
    are inclusive, and `limit`/`offset` page through the window while
    `opening_balance` keeps the running balances correct on later pages.
    """

    await _get_customer(session, shop_id, customer_id)
    _validate_statement_arguments(start_date, end_date, limit, offset)

    entries = _statement_entries(shop_id, customer_id, start_date, end_date)
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
    opening_balance = await _balance_before(session, shop_id, customer_id, start_date)
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
                amount=debit if entry_type is StatementEntryType.SALE else credit,
                debit=debit,
                credit=credit,
                running_balance=balance,
                sale_id=row.sale_id,
                payment_id=row.payment_id,
                invoice_number=row.invoice_number,
                payment_method=_as_payment_method(row.payment_method),
            )
        )

    return CustomerStatement(
        customer_id=customer_id,
        opening_balance=opening_balance,
        closing_balance=balance,
        total_entries=total_entries,
        entries=tuple(lines),
    )


# --------------------------------------------------------------------------
# Settlement
# --------------------------------------------------------------------------


async def _get_settleable_sale(
    session: AsyncSession,
    shop_id: uuid.UUID,
    customer_id: uuid.UUID,
    sale_id: uuid.UUID,
) -> Sale:
    """Load and lock a sale a settlement is being allocated to.

    Three separate failures, kept distinct because they mean different things
    to the caller: a sale from another shop is simply not found (no
    cross-tenant existence leak), a sale belonging to another customer of *this*
    shop is a mismatch, and a cancelled/returned sale has nothing owed on it.
    """

    sale = (
        await session.execute(
            select(Sale)
            .where(Sale.id == sale_id, Sale.shop_id == shop_id)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if sale is None:
        raise SaleNotFoundError(f"Sale {sale_id} not found for shop {shop_id}")
    if sale.customer_id != customer_id:
        raise SaleCustomerMismatchError(
            f"Sale {sale_id} does not belong to customer {customer_id}"
        )
    if sale.status not in QUALIFYING_SALE_STATUSES:
        raise SaleNotSettleableError(
            f"Sale {sale_id} is {sale.status.value}; nothing is owed on it"
        )
    return sale


async def record_customer_payment(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    customer_id: uuid.UUID,
    amount: Decimal,
    method: PaymentMethod,
    sale_id: uuid.UUID | None = None,
    reference: str | None = None,
) -> Payment:
    """Record money received from a customer against their Khata.

    This creates a perfectly ordinary Step 5 `Payment` row - there is no Khata
    payment model. Validation, in order:

    1. the customer exists and belongs to `shop_id` (its row is locked, so two
       concurrent settlements cannot both pass the outstanding check);
    2. the amount is positive, at NUMERIC(14,2) precision;
    3. the method is a real `PaymentMethod`;
    4. if `sale_id` is given, that sale exists, belongs to this shop *and* this
       customer, and is still settleable;
    5. the amount does not exceed the customer's outstanding balance (V1 has no
       customer credit/advance accounting, so an overpayment is rejected rather
       than silently banked);
    6. if `sale_id` is given, the amount does not exceed that sale's remaining
       due either.

    When `sale_id` is given the payment is allocated: `Sale.paid_amount` and
    `Sale.status` are updated from it, preserving Step 5's invariant that
    `Sale.paid_amount` reflects the payments recorded against that sale. When it
    is omitted the payment is unallocated - it reduces the customer's overall
    outstanding balance and no individual sale is touched.

    The caller owns the transaction: this flushes but never commits, so a failed
    settlement leaves no `Payment` row behind.
    """

    await _get_customer(session, shop_id, customer_id, for_update=True)

    amount = _money(amount)
    if amount <= 0:
        raise InvalidPaymentAmountError(
            f"payment amount must be greater than 0, got {amount}"
        )

    if not isinstance(method, PaymentMethod):
        try:
            method = PaymentMethod(method)
        except ValueError as exc:
            raise InvalidPaymentMethodError(f"unknown payment method {method!r}") from exc

    sale: Sale | None = None
    if sale_id is not None:
        sale = await _get_settleable_sale(session, shop_id, customer_id, sale_id)

    balance = await _compute_balance(session, shop_id, customer_id)
    if amount > balance.outstanding_balance:
        raise PaymentExceedsOutstandingError(
            f"payment {amount} exceeds outstanding balance "
            f"{balance.outstanding_balance} for customer {customer_id}"
        )

    if sale is not None and amount > sale.due_amount:
        raise PaymentExceedsSaleDueError(
            f"payment {amount} exceeds the {sale.due_amount} still due on "
            f"sale {sale.id}"
        )

    payment = Payment(
        shop_id=shop_id,
        customer_id=customer_id,
        sale_id=sale_id,
        amount=amount,
        method=method,
        reference=reference,
    )
    session.add(payment)

    if sale is not None:
        sale.paid_amount = _money(sale.paid_amount + amount)
        sale.status = (
            SaleStatus.COMPLETED
            if sale.paid_amount >= sale.total
            else SaleStatus.PARTIAL
        )

    await session.flush()

    # The ledger representation of the same settlement: Dr Cash/Bank, Cr AR.
    await accounting_service.post_customer_payment(session, payment=payment)

    return payment
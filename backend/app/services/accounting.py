"""Accounting service - the general ledger (Step 8).

This module is the *only* place that writes `accounts` and `ledger_entries`,
and it exists to guarantee the one invariant that matters (`step_8_description.md`
sections 9 and 11):

    SUM(debits) == SUM(credits)

for every posting. Posting functions build their lines, the shared
`_post_group()` helper refuses to persist anything that does not balance, and a
database unique constraint on
`(shop_id, reference_type, reference_id, account_id)` makes a duplicate posting
of the same business event impossible.

Balances are never stored - `get_account_balance()` derives them from the
ledger rows themselves, so a cached figure can never drift from the entries
that produced it. The two `Khata` read models (`app.services.receivables` and
`app.services.payables`) are untouched: the ledger is an *additional*
accounting representation of the same Sale / Purchase / Payment events, not a
replacement for them.

The mapping from a `PaymentMethod` to an asset account is deliberately small
and deterministic (`_METHOD_ACCOUNT_CODES`): cash stays in Cash, every
electronic method (card, bank, JazzCash, Easypaisa) lands in Bank, and anything
unclassified (`other`) falls back to Cash. Step 8 does not add an account per
payment method.

Step 10 adds two more posting events without changing the mechanism:

* `post_cogs()` - `Debit Cost of Goods Sold / Credit Inventory`, using the
  historical `SaleItem.cost_price` snapshot;
* `post_expense()` - `Debit <category> Expense / Credit Cash|Bank` for an
  immediate-payment expense.

Both reuse `_post_group()`, and both are idempotent through their own
`(reference_type, reference_id)` pair (`SALE_COGS` / `EXPENSE`).

Nothing here commits - like every other service in this project, the caller
owns the transaction. `post_*()` flushes and returns the rows it wrote; a
failure anywhere rolls the whole business event back.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account, AccountType
from app.models.expense import Expense, ExpenseCategory
from app.models.ledger_entry import LedgerEntry
from app.models.payment import Payment, PaymentMethod
from app.models.purchase import Purchase
from app.models.sale import Sale, SaleItem, SaleStatus

_MONEY_SCALE = Decimal("0.01")
_ZERO = Decimal("0.00")

#: Reference types for the supported posting events.
REFERENCE_SALE = "SALE"
REFERENCE_SALE_COGS = "SALE_COGS"
REFERENCE_SALE_RETURN = "SALE_RETURN"
REFERENCE_PURCHASE = "PURCHASE"
REFERENCE_CUSTOMER_PAYMENT = "CUSTOMER_PAYMENT"
REFERENCE_SUPPLIER_PAYMENT = "SUPPLIER_PAYMENT"
REFERENCE_EXPENSE = "EXPENSE"

#: Codes referenced by name throughout this module.
CASH = "1000"
BANK = "1010"
ACCOUNTS_RECEIVABLE = "1100"
INVENTORY = "1200"
ACCOUNTS_PAYABLE = "2000"
OWNER_EQUITY = "3000"
SALES_REVENUE = "4000"
COST_OF_GOODS_SOLD = "5000"
RENT_EXPENSE = "5100"
UTILITIES_EXPENSE = "5200"
SALARIES_EXPENSE = "5300"
MARKETING_EXPENSE = "5400"
TRANSPORT_EXPENSE = "5500"
MAINTENANCE_EXPENSE = "5600"
SUPPLIES_EXPENSE = "5700"
OTHER_EXPENSE = "5900"

#: The default chart of accounts every shop is provisioned with. Kept as a
#: module-level tuple so tests and callers can introspect it. Step 10 adds the
#: COGS account and the small set of expense accounts `db_arch.md` section 23
#: calls for; a category is not given its own account unless it needs one.
DEFAULT_ACCOUNTS: tuple[tuple[str, str, AccountType], ...] = (
    (CASH, "Cash", AccountType.ASSET),
    (BANK, "Bank", AccountType.ASSET),
    (ACCOUNTS_RECEIVABLE, "Accounts Receivable", AccountType.ASSET),
    (INVENTORY, "Inventory", AccountType.ASSET),
    (ACCOUNTS_PAYABLE, "Accounts Payable", AccountType.LIABILITY),
    (OWNER_EQUITY, "Owner Equity", AccountType.EQUITY),
    (SALES_REVENUE, "Sales Revenue", AccountType.REVENUE),
    (COST_OF_GOODS_SOLD, "Cost of Goods Sold", AccountType.EXPENSE),
    (RENT_EXPENSE, "Rent Expense", AccountType.EXPENSE),
    (UTILITIES_EXPENSE, "Utilities Expense", AccountType.EXPENSE),
    (SALARIES_EXPENSE, "Salaries Expense", AccountType.EXPENSE),
    (MARKETING_EXPENSE, "Marketing Expense", AccountType.EXPENSE),
    (TRANSPORT_EXPENSE, "Transport Expense", AccountType.EXPENSE),
    (MAINTENANCE_EXPENSE, "Maintenance Expense", AccountType.EXPENSE),
    (SUPPLIES_EXPENSE, "Supplies Expense", AccountType.EXPENSE),
    (OTHER_EXPENSE, "Other Expense", AccountType.EXPENSE),
)

#: Deterministic ExpenseCategory -> EXPENSE account code mapping. Centralised
#: here (not in the expense service) so the chart of accounts stays one
#: decision, and adding a category later is a one-line change.
_CATEGORY_ACCOUNT_CODES: dict[ExpenseCategory, str] = {
    ExpenseCategory.RENT: RENT_EXPENSE,
    ExpenseCategory.SALARY: SALARIES_EXPENSE,
    ExpenseCategory.UTILITIES: UTILITIES_EXPENSE,
    ExpenseCategory.TRANSPORT: TRANSPORT_EXPENSE,
    ExpenseCategory.MARKETING: MARKETING_EXPENSE,
    ExpenseCategory.MAINTENANCE: MAINTENANCE_EXPENSE,
    ExpenseCategory.SUPPLIES: SUPPLIES_EXPENSE,
    ExpenseCategory.OTHER: OTHER_EXPENSE,
}

#: Deterministic PaymentMethod -> asset account code mapping.
_METHOD_ACCOUNT_CODES: dict[PaymentMethod, str] = {
    PaymentMethod.CASH: CASH,
    PaymentMethod.CARD: BANK,
    PaymentMethod.BANK: BANK,
    PaymentMethod.JAZZCASH: BANK,
    PaymentMethod.EASYPAISA: BANK,
    PaymentMethod.OTHER: CASH,
}

#: Which side of an account increases its balance.
_DEBIT_NORMAL = (AccountType.ASSET, AccountType.EXPENSE)

#: Sale statuses that represent a real, revenue-generating sale. Kept local
#: rather than imported from `app.services.receivables` (which imports this
#: module) to avoid a circular import; the rule itself is the same one Step 6
#: and Step 9 use.
_QUALIFYING_SALE_STATUSES = (SaleStatus.COMPLETED, SaleStatus.PARTIAL)


class AccountingError(Exception):
    """Base class for accounting-domain failures."""


class ShopNotFoundError(AccountingError):
    """The shop does not exist."""


class AccountNotFoundError(AccountingError):
    """The account does not exist in the caller's shop."""


class InvalidAccountTypeError(AccountingError):
    """An account type outside the supported set was supplied."""


class UnbalancedPostingError(AccountingError):
    """A posting's debits and credits do not agree."""


class InvalidLedgerLineError(AccountingError):
    """A ledger line is not exactly one positive debit or credit."""


class InvalidStatementRangeError(AccountingError):
    """`start_date` is after `end_date`."""


class InvalidPaginationError(AccountingError):
    """`limit` / `offset` are outside their allowed ranges."""


@dataclass(frozen=True)
class AccountBalance:
    """A derived balance for one account."""

    account_id: uuid.UUID
    code: str
    name: str
    account_type: AccountType
    debit_total: Decimal
    credit_total: Decimal
    balance: Decimal


@dataclass(frozen=True)
class LedgerLine:
    """One ledger entry with its running balance, for a statement view."""

    id: uuid.UUID
    date: datetime
    description: str | None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    reference_type: str
    reference_id: uuid.UUID


@dataclass(frozen=True)
class AccountLedger:
    """A page of one account's ledger."""

    account_id: uuid.UUID
    opening_balance: Decimal
    closing_balance: Decimal
    total_entries: int
    entries: tuple[LedgerLine, ...]


def _money(value: Decimal | int | None) -> Decimal:
    if value is None:
        return _ZERO
    return Decimal(value).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)


def _normal_balance(
    account_type: AccountType, debit: Decimal, credit: Decimal
) -> Decimal:
    """Present a raw debit/credit pair in the account's normal direction."""

    if account_type in _DEBIT_NORMAL:
        return debit - credit
    return credit - debit


async def ensure_system_accounts(
    session: AsyncSession, *, shop_id: uuid.UUID
) -> dict[str, Account]:
    """Create any missing default accounts for a shop, idempotently.

    Safe to call on every posting: an account already present is left alone,
    so calling this twice never duplicates a chart of accounts. Accounts are
    matched by `(shop_id, code)`, which is exactly the unique key the database
    enforces.
    """

    existing = (
        await session.execute(
            select(Account).where(Account.shop_id == shop_id)
        )
    ).scalars()
    by_code = {account.code: account for account in existing}

    created: list[Account] = []
    for code, name, account_type in DEFAULT_ACCOUNTS:
        if code in by_code:
            continue
        account = Account(
            shop_id=shop_id,
            code=code,
            name=name,
            account_type=account_type,
            is_system=True,
        )
        session.add(account)
        by_code[code] = account
        created.append(account)

    if created:
        await session.flush()
    return by_code


def _payment_account_code(method: PaymentMethod) -> str:
    try:
        return _METHOD_ACCOUNT_CODES[method]
    except KeyError as exc:  # pragma: no cover - enum is exhaustive
        raise InvalidAccountTypeError(
            f"no account mapping for payment method {method!r}"
        ) from exc


def _expense_account_code(category: ExpenseCategory) -> str:
    try:
        return _CATEGORY_ACCOUNT_CODES[category]
    except KeyError as exc:  # pragma: no cover - enum is exhaustive
        raise InvalidAccountTypeError(
            f"no account mapping for expense category {category!r}"
        ) from exc


async def _post_group(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    lines: list[tuple[uuid.UUID, Decimal, Decimal, str | None]],
) -> list[LedgerEntry]:
    """Persist one balanced posting group, or no-op if already posted.

    Each line is `(account_id, debit, credit, description)`. The group is
    validated before anything is written:

    * every line must be exactly one positive debit or one positive credit;
    * `SUM(debits)` must equal `SUM(credits)`.

    If the reference already has ledger rows, the group is considered posted
    and nothing new is written - that is what makes posting idempotent without
    a mutable `posted` flag on the source document.
    """

    already = (
        await session.execute(
            select(LedgerEntry.id).where(
                LedgerEntry.shop_id == shop_id,
                LedgerEntry.reference_type == reference_type,
                LedgerEntry.reference_id == reference_id,
            )
        )
    ).first()
    if already is not None:
        return []

    total_debit = _ZERO
    total_credit = _ZERO
    for _account_id, debit, credit, _description in lines:
        debit = _money(debit)
        credit = _money(credit)
        if debit < 0 or credit < 0:
            raise InvalidLedgerLineError("debit and credit must be non-negative")
        if debit > 0 and credit > 0:
            raise InvalidLedgerLineError(
                "a ledger line cannot be both a debit and a credit"
            )
        if debit == 0 and credit == 0:
            raise InvalidLedgerLineError(
                "a ledger line must have a non-zero debit or credit"
            )
        total_debit += debit
        total_credit += credit

    total_debit = _money(total_debit)
    total_credit = _money(total_credit)
    if total_debit != total_credit:
        raise UnbalancedPostingError(
            f"posting {reference_type}:{reference_id} does not balance "
            f"(debits {total_debit} != credits {total_credit})"
        )

    entries: list[LedgerEntry] = []
    for account_id, debit, credit, description in lines:
        entry = LedgerEntry(
            shop_id=shop_id,
            account_id=account_id,
            debit=_money(debit),
            credit=_money(credit),
            reference_type=reference_type,
            reference_id=reference_id,
            description=description,
        )
        session.add(entry)
        entries.append(entry)

    await session.flush()
    return entries


async def post_sale(
    session: AsyncSession, *, sale: Sale, description: str | None = None
) -> list[LedgerEntry]:
    """Post a sale: cash/bank for what was paid, AR for the rest, revenue out.

        Debit   Cash / Bank           paid
        Debit   Accounts Receivable   due
        Credit  Sales Revenue         total
    """

    accounts = await ensure_system_accounts(session, shop_id=sale.shop_id)
    cash = accounts[CASH]
    bank = accounts[BANK]
    receivable = accounts[ACCOUNTS_RECEIVABLE]
    revenue = accounts[SALES_REVENUE]

    total = _money(sale.total)
    if total <= 0:
        return []

    paid = _money(sale.paid_amount)
    due = _money(total - paid)

    payments = (
        await session.execute(
            select(Payment).where(
                Payment.shop_id == sale.shop_id,
                Payment.sale_id == sale.id,
            )
        )
    ).scalars().all()

    by_account: dict[uuid.UUID, Decimal] = {}
    for payment in payments:
        code = _payment_account_code(payment.method)
        account = cash if code == CASH else bank
        by_account[account.id] = by_account.get(account.id, _ZERO) + _money(
            payment.amount
        )

    allocated = sum(by_account.values(), start=_ZERO)
    lines: list[tuple[uuid.UUID, Decimal, Decimal, str | None]] = []
    for account_id, amount in by_account.items():
        if amount > 0:
            lines.append((account_id, amount, _ZERO, description))
    if paid > allocated:
        lines.append((cash.id, paid - allocated, _ZERO, description))
    if due > 0:
        lines.append((receivable.id, due, _ZERO, description))
    lines.append((revenue.id, _ZERO, total, description))

    return await _post_group(
        session,
        shop_id=sale.shop_id,
        reference_type=REFERENCE_SALE,
        reference_id=sale.id,
        lines=lines,
    )


async def post_cogs(
    session: AsyncSession, *, sale: Sale, description: str | None = None
) -> list[LedgerEntry]:
    """Post the cost of a sale: stock value out, COGS up.

        Debit   Cost of Goods Sold    cogs
        Credit  Inventory             cogs

    The amount is `SUM(SaleItem.quantity * SaleItem.cost_price)` using the
    *immutable* historical cost captured on each line at sale time
    (`db_arch.md` sections 18 and 29). The current weighted-average inventory
    cost is deliberately never consulted here, so a later, more expensive
    purchase cannot rewrite what an earlier sale cost.

    A zero-cost sale is possible (zero-cost inventory is allowed), and Step 8
    forbids zero-value ledger lines, so a COGS of zero is skipped entirely -
    the revenue posting is unaffected and no invalid line is written
    (`step_10_desc.md` sections 7 and 26).

    Only qualifying sales generate COGS: a sale whose status is CANCELLED (or
    any future non-qualifying status) is skipped, so the rule stays centralised
    in one place (`step_10_desc.md` section 9). `create_sale()` only ever
    creates COMPLETED/PARTIAL sales, so in practice this guard protects a
    replayed or externally-constructed sale.

    Idempotent through the same mechanism as every other posting: the reference
    is `("SALE_COGS", sale.id)`, so replaying a sale writes no second COGS and
    the revenue group (`("SALE", sale.id)`) is untouched.
    """

    if sale.status not in _QUALIFYING_SALE_STATUSES:
        return []

    accounts = await ensure_system_accounts(session, shop_id=sale.shop_id)
    cogs_account = accounts[COST_OF_GOODS_SOLD]
    inventory = accounts[INVENTORY]

    row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(SaleItem.quantity * SaleItem.cost_price), 0
                )
            ).where(SaleItem.sale_id == sale.id)
        )
    ).scalar()
    cogs = _money(row)
    if cogs <= 0:
        return []

    lines = [
        (cogs_account.id, cogs, _ZERO, description),
        (inventory.id, _ZERO, cogs, description),
    ]
    return await _post_group(
        session,
        shop_id=sale.shop_id,
        reference_type=REFERENCE_SALE_COGS,
        reference_id=sale.id,
        lines=lines,
    )


async def post_sale_return(
    session: AsyncSession,
    *,
    sale: Sale,
    returned_quantity: Decimal,
    refund_amount: Decimal,
    description: str | None = None,
) -> list[LedgerEntry]:
    """Reverse a sale's accounting postings for a customer return.

    This posts the opposite of `post_sale` + `post_cogs` for the returned portion:

        Debit   Sales Revenue         refund_amount
        Credit  Cash / Bank           refund_amount (if paid)
        Credit  Accounts Receivable   refund_amount (if unpaid)

        Debit   Inventory             cogs_on_returned
        Credit  Cost of Goods Sold    cogs_on_returned

    The COGS is calculated proportionally from the sale's historical cost
    snapshot: `SUM(SaleItem.quantity * SaleItem.cost_price) * (returned_qty / total_qty)`.

    Idempotent through reference `("SALE_RETURN", sale.id, returned_qty)`.
    """
    accounts = await ensure_system_accounts(session, shop_id=sale.shop_id)
    cash = accounts[CASH]
    bank = accounts[BANK]
    receivable = accounts[ACCOUNTS_RECEIVABLE]
    revenue = accounts[SALES_REVENUE]
    cogs_account = accounts[COST_OF_GOODS_SOLD]
    inventory = accounts[INVENTORY]

    refund_amount = _money(refund_amount)
    if refund_amount <= 0:
        return []

    total_qty = sum(item.quantity for item in sale.items)
    if total_qty <= 0:
        return []

    # Calculate COGS proportionally for the returned quantity
    row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(SaleItem.quantity * SaleItem.cost_price), 0
                )
            ).where(SaleItem.sale_id == sale.id)
        )
    ).scalar()
    total_cogs = _money(row)
    if total_cogs <= 0:
        cogs_on_returned = _ZERO
    else:
        cogs_on_returned = _money(
            total_cogs * (returned_quantity / total_qty)
        )

    # Refund payment allocation: payments on this sale
    payments = (
        await session.execute(
            select(Payment).where(
                Payment.shop_id == sale.shop_id,
                Payment.sale_id == sale.id,
            )
        )
    ).scalars().all()

    by_account: dict[uuid.UUID, Decimal] = {}
    for payment in payments:
        code = _payment_account_code(payment.method)
        account = cash if code == CASH else bank
        by_account[account.id] = by_account.get(account.id, _ZERO) + _money(
            payment.amount
        )

    paid_total = sum(by_account.values(), start=_ZERO)
    due_total = _money(sale.total - sale.paid_amount)

    # The refund goes back the same way: first to cash/bank up to what was paid,
    # then to AR for the rest (mirroring post_sale logic)
    lines: list[tuple[uuid.UUID, Decimal, Decimal, str | None]] = []

    remaining_refund = refund_amount

    # Reverse cash/bank (what was originally paid)
    for account_id, amount in by_account.items():
        if amount > 0 and remaining_refund > 0:
            refund_to_account = min(amount, remaining_refund)
            lines.append((account_id, _ZERO, refund_to_account, description))
            remaining_refund -= refund_to_account

    # Reverse AR (what was still due)
    if remaining_refund > 0 and due_total > 0:
        refund_to_ar = min(due_total, remaining_refund)
        lines.append((receivable.id, _ZERO, refund_to_ar, description))
        remaining_refund -= refund_to_ar

    # Revenue reversal (always the full refund amount)
    lines.append((revenue.id, refund_amount, _ZERO, description))

    # COGS reversal: Debit Inventory, Credit COGS
    if cogs_on_returned > 0:
        lines.append((inventory.id, cogs_on_returned, _ZERO, description))
        lines.append((cogs_account.id, _ZERO, cogs_on_returned, description))

    return await _post_group(
        session,
        shop_id=sale.shop_id,
        reference_type=REFERENCE_SALE_RETURN,
        reference_id=sale.id,
        lines=lines,
    )


async def post_expense(
    session: AsyncSession, *, expense: Expense, description: str | None = None
) -> list[LedgerEntry]:
    """Post an immediate-payment expense.

        Debit   <category> Expense    amount
        Credit  Cash / Bank           amount

    The asset side reuses Step 8's existing `PaymentMethod` mapping - cash (and
    `other`) leave Cash, every electronic method leaves Bank - so there is no
    second mapping system to keep in step (`step_10_desc.md` section 25).

    V1 has no expense-payable workflow: an expense is paid when it is recorded,
    and `Accounts Payable` stays supplier-only.
    """

    accounts = await ensure_system_accounts(session, shop_id=expense.shop_id)
    expense_account = accounts[_expense_account_code(expense.category)]
    asset = accounts[_payment_account_code(expense.payment_method)]

    amount = _money(expense.amount)
    if amount <= 0:
        return []

    lines = [
        (expense_account.id, amount, _ZERO, description),
        (asset.id, _ZERO, amount, description),
    ]
    return await _post_group(
        session,
        shop_id=expense.shop_id,
        reference_type=REFERENCE_EXPENSE,
        reference_id=expense.id,
        lines=lines,
    )


async def post_purchase(
    session: AsyncSession, *, purchase: Purchase, description: str | None = None
) -> list[LedgerEntry]:
    """Post a purchase: stock in, payable up.

        Debit   Inventory            total
        Credit  Accounts Payable     total

    The paid portion, if any, is settled by a later supplier payment posting -
    keeping `Payment` as the single settlement event.
    """

    accounts = await ensure_system_accounts(session, shop_id=purchase.shop_id)
    inventory = accounts[INVENTORY]
    payable = accounts[ACCOUNTS_PAYABLE]

    total = _money(purchase.total)
    if total <= 0:
        return []

    lines = [
        (inventory.id, total, _ZERO, description),
        (payable.id, _ZERO, total, description),
    ]
    return await _post_group(
        session,
        shop_id=purchase.shop_id,
        reference_type=REFERENCE_PURCHASE,
        reference_id=purchase.id,
        lines=lines,
    )


async def post_customer_payment(
    session: AsyncSession, *, payment: Payment, description: str | None = None
) -> list[LedgerEntry]:
    """Post money received from a customer.

        Debit   Cash / Bank           amount
        Credit  Accounts Receivable   amount
    """

    accounts = await ensure_system_accounts(session, shop_id=payment.shop_id)
    receivable = accounts[ACCOUNTS_RECEIVABLE]
    code = _payment_account_code(payment.method)
    asset = accounts[code]

    amount = _money(payment.amount)
    lines = [
        (asset.id, amount, _ZERO, description),
        (receivable.id, _ZERO, amount, description),
    ]
    return await _post_group(
        session,
        shop_id=payment.shop_id,
        reference_type=REFERENCE_CUSTOMER_PAYMENT,
        reference_id=payment.id,
        lines=lines,
    )


async def post_supplier_payment(
    session: AsyncSession, *, payment: Payment, description: str | None = None
) -> list[LedgerEntry]:
    """Post money paid to a supplier.

        Debit   Accounts Payable      amount
        Credit  Cash / Bank           amount
    """

    accounts = await ensure_system_accounts(session, shop_id=payment.shop_id)
    payable = accounts[ACCOUNTS_PAYABLE]
    code = _payment_account_code(payment.method)
    asset = accounts[code]

    amount = _money(payment.amount)
    lines = [
        (payable.id, amount, _ZERO, description),
        (asset.id, _ZERO, amount, description),
    ]
    return await _post_group(
        session,
        shop_id=payment.shop_id,
        reference_type=REFERENCE_SUPPLIER_PAYMENT,
        reference_id=payment.id,
        lines=lines,
    )


async def list_accounts(
    session: AsyncSession, *, shop_id: uuid.UUID
) -> list[Account]:
    """Return a shop's chart of accounts, ordered by code."""

    return list(
        (
            await session.execute(
                select(Account)
                .where(Account.shop_id == shop_id)
                .order_by(Account.code.asc())
            )
        ).scalars()
    )


async def _load_account(
    session: AsyncSession, *, shop_id: uuid.UUID, account_id: uuid.UUID
) -> Account:
    account = (
        await session.execute(
            select(Account).where(
                Account.id == account_id,
                Account.shop_id == shop_id,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise AccountNotFoundError(
            f"Account {account_id} not found for shop {shop_id}"
        )
    return account


async def get_account_balance(
    session: AsyncSession, *, shop_id: uuid.UUID, account_id: uuid.UUID
) -> AccountBalance:
    """Derive an account's balance from its ledger entries."""

    account = await _load_account(session, shop_id=shop_id, account_id=account_id)

    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(LedgerEntry.debit), 0),
                func.coalesce(func.sum(LedgerEntry.credit), 0),
            ).where(
                LedgerEntry.shop_id == shop_id,
                LedgerEntry.account_id == account_id,
            )
        )
    ).one()

    debit_total = _money(row[0])
    credit_total = _money(row[1])
    return AccountBalance(
        account_id=account.id,
        code=account.code,
        name=account.name,
        account_type=account.account_type,
        debit_total=debit_total,
        credit_total=credit_total,
        balance=_normal_balance(account.account_type, debit_total, credit_total),
    )


def _validate_ledger_arguments(
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


async def get_account_ledger(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    account_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> AccountLedger:
    """Return one account's ledger with running balances.

    Ordering is deterministic (`created_at`, then `id`). Both dates are
    inclusive, and `opening_balance` carries in everything before the window
    (and anything this page skipped via `offset`), so running balances are
    correct on every page.
    """

    account = await _load_account(session, shop_id=shop_id, account_id=account_id)
    _validate_ledger_arguments(start_date, end_date, limit, offset)

    base = select(LedgerEntry).where(
        LedgerEntry.shop_id == shop_id,
        LedgerEntry.account_id == account_id,
    )
    if start_date is not None:
        base = base.where(LedgerEntry.created_at >= start_date)
    if end_date is not None:
        base = base.where(LedgerEntry.created_at <= end_date)

    ordering = (LedgerEntry.created_at.asc(), LedgerEntry.id.asc())

    total_entries = (
        await session.execute(
            select(func.count()).select_from(base.subquery())
        )
    ).scalar_one()

    opening_raw = _ZERO
    if start_date is not None:
        before = (
            await session.execute(
                select(
                    func.coalesce(func.sum(LedgerEntry.debit), 0),
                    func.coalesce(func.sum(LedgerEntry.credit), 0),
                ).where(
                    LedgerEntry.shop_id == shop_id,
                    LedgerEntry.account_id == account_id,
                    LedgerEntry.created_at < start_date,
                )
            )
        ).one()
        opening_raw = _money(before[0]) - _money(before[1])

    if offset:
        skipped_rows = (
            await session.execute(base.order_by(*ordering).limit(offset))
        ).scalars().all()
        for entry in skipped_rows:
            opening_raw += _money(entry.debit) - _money(entry.credit)

    page = base.order_by(*ordering)
    if offset:
        page = page.offset(offset)
    if limit is not None:
        page = page.limit(limit)

    rows = (await session.execute(page)).scalars().all()

    # Running balance is presented in the account's normal direction, so it
    # agrees with `get_account_balance()` at the closing row.
    lines: list[LedgerLine] = []
    raw_balance = opening_raw
    for entry in rows:
        debit = _money(entry.debit)
        credit = _money(entry.credit)
        raw_balance += debit - credit
        lines.append(
            LedgerLine(
                id=entry.id,
                date=entry.created_at,
                description=entry.description,
                debit=debit,
                credit=credit,
                running_balance=_normal_balance(
                    account.account_type, raw_balance, _ZERO
                ),
                reference_type=entry.reference_type,
                reference_id=entry.reference_id,
            )
        )

    return AccountLedger(
        account_id=account.id,
        opening_balance=_normal_balance(account.account_type, opening_raw, _ZERO),
        closing_balance=_normal_balance(account.account_type, raw_balance, _ZERO),
        total_entries=total_entries,
        entries=tuple(lines),
    )
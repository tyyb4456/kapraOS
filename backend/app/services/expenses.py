"""Expense service - the only write path for the expense domain.

`create_expense()` performs the whole thing inside the caller's transaction:

    validate shop
        -> validate amount / category / payment method
        -> create the Expense row
        -> post Debit <Expense account> / Credit Cash|Bank (Step 8 ledger)

If the posting fails the surrounding transaction rolls back, so an expense can
never exist without its ledger entries - the same atomicity contract as
`create_sale()` and `create_purchase()` (`step_10_desc.md` sections 26 and 15).

The accounting itself lives in `app.services.accounting` and is reused, not
reimplemented: this module decides *whether* an expense is valid, the accounting
service decides what a valid expense means in the ledger.

Posted expenses are immutable in V1 (`step_10_desc.md` sections 16 and 37):
there is no reversal/void workflow, so there is deliberately no update or delete
function here. Nothing in this module commits - the caller owns the transaction.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import Expense, ExpenseCategory
from app.models.payment import PaymentMethod
from app.models.shop import Shop
from app.services import accounting as accounting_service

_MONEY_SCALE = Decimal("0.01")


class ExpenseError(Exception):
    """Base class for expense-domain failures."""


class ShopNotFoundError(ExpenseError):
    """The shop does not exist."""


class ExpenseNotFoundError(ExpenseError):
    """The expense does not exist in the caller's shop."""


class InvalidExpenseAmountError(ExpenseError):
    """The amount is not a positive NUMERIC(14,2) value."""


class InvalidExpenseCategoryError(ExpenseError):
    """The supplied category is not an `ExpenseCategory`."""


class InvalidExpensePaymentMethodError(ExpenseError):
    """The supplied payment method is not a `PaymentMethod`."""


class InvalidPaginationError(ExpenseError):
    """`limit` / `offset` are outside their allowed ranges."""


class InvalidStatementRangeError(ExpenseError):
    """`start_date` is after `end_date`."""


@dataclass(frozen=True)
class ExpenseList:
    """A filtered, paged slice of a shop's expenses."""

    total: int
    expenses: tuple[Expense, ...]


async def _get_shop(session: AsyncSession, shop_id: uuid.UUID) -> Shop:
    shop = await session.get(Shop, shop_id)
    if shop is None:
        raise ShopNotFoundError(f"Shop {shop_id} not found")
    return shop


def _normalise_amount(raw: Decimal) -> Decimal:
    amount = Decimal(raw).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise InvalidExpenseAmountError(
            f"expense amount must be greater than 0, got {amount}"
        )
    return amount


def _normalise_category(raw: ExpenseCategory | str) -> ExpenseCategory:
    if isinstance(raw, ExpenseCategory):
        return raw
    try:
        return ExpenseCategory(raw)
    except ValueError as exc:
        raise InvalidExpenseCategoryError(f"unknown expense category {raw!r}") from exc


def _normalise_method(raw: PaymentMethod | str) -> PaymentMethod:
    if isinstance(raw, PaymentMethod):
        return raw
    try:
        return PaymentMethod(raw)
    except ValueError as exc:
        raise InvalidExpensePaymentMethodError(
            f"unknown payment method {raw!r}"
        ) from exc


async def create_expense(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    category: ExpenseCategory,
    amount: Decimal,
    payment_method: PaymentMethod,
    description: str | None = None,
    expense_date: datetime | None = None,
) -> Expense:
    """Create an immediate-payment expense and post it to the ledger.

    The amount, category and payment method are validated here *and* re-checked
    by the database (`amount > 0`, the `expense_category` enum, the
    `payment_method` enum), so a write path that bypasses this service still
    cannot persist nonsense (`step_10_desc.md` section 19).

    The ledger posting is `Debit <category expense account> / Credit Cash|Bank`
    using Step 8's existing payment-method mapping, and it is idempotent through
    the `(shop_id, reference_type, reference_id, account_id)` unique constraint.
    """

    await _get_shop(session, shop_id)

    amount = _normalise_amount(amount)
    category = _normalise_category(category)
    payment_method = _normalise_method(payment_method)

    expense = Expense(
        shop_id=shop_id,
        category=category,
        description=description,
        amount=amount,
        payment_method=payment_method,
        expense_date=expense_date or datetime.now(timezone.utc),
    )
    session.add(expense)
    await session.flush()

    await accounting_service.post_expense(session, expense=expense)

    return expense


async def get_expense(
    session: AsyncSession, *, shop_id: uuid.UUID, expense_id: uuid.UUID
) -> Expense:
    """Load one expense, enforcing tenant ownership.

    An expense from another shop is reported as not found rather than read, so
    no cross-tenant row ever leaks (`db_arch.md` section 33).
    """

    expense = (
        await session.execute(
            select(Expense).where(
                Expense.id == expense_id,
                Expense.shop_id == shop_id,
            )
        )
    ).scalar_one_or_none()
    if expense is None:
        raise ExpenseNotFoundError(
            f"Expense {expense_id} not found for shop {shop_id}"
        )
    return expense


async def list_expenses(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> ExpenseList:
    """List a shop's expenses, newest first.

    Both dates are inclusive and filter on `expense_date` (the business date),
    not `created_at`. `total` counts the filtered window ignoring pagination so
    a caller can page through it.
    """

    if start_date is not None and end_date is not None and start_date > end_date:
        raise InvalidStatementRangeError(
            f"start_date {start_date} is after end_date {end_date}"
        )
    if offset < 0:
        raise InvalidPaginationError("offset must be >= 0")
    if limit is not None and limit < 1:
        raise InvalidPaginationError("limit must be >= 1 when provided")

    base = select(Expense).where(Expense.shop_id == shop_id)
    if start_date is not None:
        base = base.where(Expense.expense_date >= start_date)
    if end_date is not None:
        base = base.where(Expense.expense_date <= end_date)

    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    page = base.order_by(Expense.expense_date.desc(), Expense.id.desc())
    if offset:
        page = page.offset(offset)
    if limit is not None:
        page = page.limit(limit)

    rows = (await session.execute(page)).scalars().all()
    return ExpenseList(total=int(total), expenses=tuple(rows))


async def update_expense(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    expense_id: uuid.UUID,
    category: ExpenseCategory | str | None = None,
    amount: Decimal | None = None,
    payment_method: PaymentMethod | str | None = None,
    description: str | None | object = ...,
    expense_date: datetime | None = None,
) -> Expense:
    """Edit an expense, reposting its ledger group when money moves.

    Description/expense_date edits only touch the row. A category, amount or
    payment-method change deletes the old EXPENSE posting and writes a new
    one in the same transaction, so the P&L never shows a half-edited
    expense. `description` uses a sentinel: omit it to leave it alone, pass
    None to clear it.
    """

    expense = await get_expense(session, shop_id=shop_id, expense_id=expense_id)

    repost = False
    if category is not None:
        new_category = _normalise_category(category)
        if new_category != expense.category:
            expense.category = new_category
            repost = True
    if amount is not None:
        new_amount = _normalise_amount(amount)
        if new_amount != expense.amount:
            expense.amount = new_amount
            repost = True
    if payment_method is not None:
        new_method = _normalise_method(payment_method)
        if new_method != expense.payment_method:
            expense.payment_method = new_method
            repost = True
    if description is not ...:
        expense.description = description  # type: ignore[assignment]
    if expense_date is not None:
        expense.expense_date = expense_date

    await session.flush()

    if repost:
        await accounting_service.delete_postings_for_reference(
            session,
            shop_id=shop_id,
            reference_id=expense.id,
            reference_types=[accounting_service.REFERENCE_EXPENSE],
        )
        await accounting_service.post_expense(session, expense=expense)

    return expense


async def delete_expense(
    session: AsyncSession, *, shop_id: uuid.UUID, expense_id: uuid.UUID
) -> None:
    """Void an expense: drop its EXPENSE ledger group and delete the row."""

    expense = await get_expense(session, shop_id=shop_id, expense_id=expense_id)
    await accounting_service.delete_postings_for_reference(
        session, shop_id=shop_id, reference_id=expense.id
    )
    await session.delete(expense)
    await session.flush()
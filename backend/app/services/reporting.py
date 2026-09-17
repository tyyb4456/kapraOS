"""Reporting service - read-only financial statements and dashboard (Step 9).

This module is the *only* reporting layer, and it is strictly read-only
(`step_9_description.md` sections 1 and 27): nothing here writes a report
snapshot, a daily/monthly balance or a dashboard cache, and nothing here adds
an accounting posting. Every figure is derived at query time from the existing
source-of-truth tables:

* Trial Balance  -> `accounts` + `ledger_entries`
* Profit & Loss  -> Revenue, COGS and Expenses, all from the ledger
* Balance Sheet  -> ASSET / LIABILITY / EQUITY account balances
* Dashboard      -> Sales / Purchases / Payments / Inventory aggregates, plus
                    the Step 6 and Step 7 shop-wide Khatas so reporting can
                    never drift from the receivables/payables services

After Step 10 the P&L is **fully ledger-derived**: Step 8 posts revenue, and
Step 10 posts COGS (from the historical `SaleItem.cost_price`) and expenses, so
`Revenue - COGS - Expenses = Net Profit` is assembled entirely from
`ledger_entries`. The historical `SaleItem.cost_price` is now only the *source*
used to create the COGS posting, never the reporting source.

One V1 limitation is surfaced rather than hidden: inventory has no
reorder/minimum-stock field, so low stock is unavailable and is reported via an
explicit flag instead of a fabricated number.

Money is `Decimal` throughout, quantized to NUMERIC(14,2). "Today" is the
current UTC calendar day - the project has no configured shop timezone
(`app.config.Settings`), so no timezone subsystem is invented here.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account, AccountType
from app.models.inventory import Inventory
from app.models.ledger_entry import LedgerEntry
from app.models.payment import Payment
from app.models.product import ProductVariant
from app.models.purchase import Purchase
from app.models.sale import Sale
from app.services import payables as payables_service
from app.services import receivables as receivables_service
from app.services.accounting import COST_OF_GOODS_SOLD, _normal_balance
from app.services.receivables import QUALIFYING_SALE_STATUSES

_MONEY_SCALE = Decimal("0.01")
_ZERO = Decimal("0.00")


class ReportingError(Exception):
    """Base class for reporting-domain failures."""


class InvalidReportRangeError(ReportingError):
    """`start_date` is after `end_date`."""


@dataclass(frozen=True)
class TrialBalanceRow:
    """One account's period activity and closing balance."""

    account_id: uuid.UUID
    code: str
    name: str
    account_type: AccountType
    debit_total: Decimal
    credit_total: Decimal
    balance: Decimal
    closing_debit_total: Decimal
    closing_credit_total: Decimal
    closing_balance: Decimal


@dataclass(frozen=True)
class TrialBalance:
    """A trial balance: period activity plus closing balances.

    `debit_total` / `credit_total` / `balance` describe the selected period
    (all activity when no dates are given). `closing_*` describe everything up
    to `end_date`, so both interpretations are available and unambiguous.
    """

    start_date: datetime | None
    end_date: datetime | None
    rows: tuple[TrialBalanceRow, ...]
    total_debits: Decimal
    total_credits: Decimal
    total_closing_debits: Decimal
    total_closing_credits: Decimal
    is_balanced: bool


@dataclass(frozen=True)
class ProfitAndLoss:
    """Revenue, COGS, Gross Profit, Expenses and Net Profit for a period.

    Every figure is derived from the ledger: `revenue` from the REVENUE
    accounts, `cogs` from the Cost of Goods Sold account, and `expenses` from
    the remaining EXPENSE accounts (COGS excluded, so it is never counted
    twice). `expense_reporting_available` stays in the contract for
    compatibility and is now always `True`.
    """

    start_date: datetime | None
    end_date: datetime | None
    revenue: Decimal
    cogs: Decimal
    gross_profit: Decimal
    expenses: Decimal | None
    net_profit: Decimal | None
    expense_reporting_available: bool
    notes: tuple[str, ...]


@dataclass(frozen=True)
class BalanceSheetRow:
    """One balance-sheet account line."""

    account_id: uuid.UUID
    code: str
    name: str
    account_type: AccountType
    balance: Decimal


@dataclass(frozen=True)
class BalanceSheet:
    """Assets, liabilities and equity, with the accounting-equation check."""

    end_date: datetime | None
    rows: tuple[BalanceSheetRow, ...]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    total_liabilities_and_equity: Decimal
    difference: Decimal


@dataclass(frozen=True)
class DashboardSummary:
    """A business overview assembled from existing operational data."""

    as_of: datetime
    today_sales: Decimal
    today_sales_count: int
    today_payments_received: Decimal
    today_payment_count: int
    today_purchases: Decimal
    today_purchase_count: int
    today_cogs: Decimal
    today_gross_profit: Decimal
    today_expenses: Decimal
    today_net_profit: Decimal
    receivables_outstanding: Decimal
    payables_outstanding: Decimal
    inventory_quantity: Decimal
    inventory_estimated_value: Decimal
    low_stock_variant_count: int | None
    low_stock_available: bool
    notes: tuple[str, ...]


def _money(value: Decimal | int | None) -> Decimal:
    if value is None:
        return _ZERO
    return Decimal(value).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)


def _validate_range(
    start_date: datetime | None, end_date: datetime | None
) -> None:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise InvalidReportRangeError(
            f"start_date {start_date} is after end_date {end_date}"
        )


async def _load_accounts(
    session: AsyncSession, shop_id: uuid.UUID
) -> list[Account]:
    return list(
        (
            await session.execute(
                select(Account)
                .where(Account.shop_id == shop_id)
                .order_by(Account.code.asc())
            )
        ).scalars()
    )


async def _account_activity(
    session: AsyncSession,
    shop_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """Aggregate debits/credits per account in SQL, for the given window.

    Grouping in the database (rather than loading ledger rows) keeps a report
    cheap regardless of how much history a shop has.
    """

    statement = (
        select(
            LedgerEntry.account_id,
            func.coalesce(func.sum(LedgerEntry.debit), 0),
            func.coalesce(func.sum(LedgerEntry.credit), 0),
        )
        .where(LedgerEntry.shop_id == shop_id)
        .group_by(LedgerEntry.account_id)
    )
    if start_date is not None:
        statement = statement.where(LedgerEntry.created_at >= start_date)
    if end_date is not None:
        statement = statement.where(LedgerEntry.created_at <= end_date)

    rows = (await session.execute(statement)).all()
    return {row[0]: (_money(row[1]), _money(row[2])) for row in rows}


async def get_trial_balance(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> TrialBalance:
    """Derive a trial balance from `accounts` + `ledger_entries`.

    Both dates are inclusive. The period columns cover `[start_date,
    end_date]`; the closing columns cover everything up to `end_date`, so a
    caller never has to guess which accounting interpretation is in play.
    `is_balanced` is `total_debits == total_credits` for the period.
    """

    _validate_range(start_date, end_date)

    accounts = await _load_accounts(session, shop_id)
    period = await _account_activity(session, shop_id, start_date, end_date)
    closing = await _account_activity(session, shop_id, None, end_date)

    rows: list[TrialBalanceRow] = []
    total_debits = _ZERO
    total_credits = _ZERO
    total_closing_debits = _ZERO
    total_closing_credits = _ZERO

    for account in accounts:
        period_debit, period_credit = period.get(account.id, (_ZERO, _ZERO))
        closing_debit, closing_credit = closing.get(account.id, (_ZERO, _ZERO))

        total_debits += period_debit
        total_credits += period_credit
        total_closing_debits += closing_debit
        total_closing_credits += closing_credit

        rows.append(
            TrialBalanceRow(
                account_id=account.id,
                code=account.code,
                name=account.name,
                account_type=account.account_type,
                debit_total=period_debit,
                credit_total=period_credit,
                balance=_money(
                    _normal_balance(
                        account.account_type, period_debit, period_credit
                    )
                ),
                closing_debit_total=closing_debit,
                closing_credit_total=closing_credit,
                closing_balance=_money(
                    _normal_balance(
                        account.account_type, closing_debit, closing_credit
                    )
                ),
            )
        )

    total_debits = _money(total_debits)
    total_credits = _money(total_credits)
    return TrialBalance(
        start_date=start_date,
        end_date=end_date,
        rows=tuple(rows),
        total_debits=total_debits,
        total_credits=total_credits,
        total_closing_debits=_money(total_closing_debits),
        total_closing_credits=_money(total_closing_credits),
        is_balanced=total_debits == total_credits,
    )


async def _revenue_for_period(
    session: AsyncSession,
    shop_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
) -> Decimal:
    """Credit activity of the ledger's REVENUE accounts, net of reversals."""

    statement = (
        select(
            func.coalesce(func.sum(LedgerEntry.credit), 0),
            func.coalesce(func.sum(LedgerEntry.debit), 0),
        )
        .select_from(LedgerEntry)
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(
            LedgerEntry.shop_id == shop_id,
            Account.account_type == AccountType.REVENUE,
        )
    )
    if start_date is not None:
        statement = statement.where(LedgerEntry.created_at >= start_date)
    if end_date is not None:
        statement = statement.where(LedgerEntry.created_at <= end_date)

    row = (await session.execute(statement)).one()
    return _money(_money(row[0]) - _money(row[1]))


async def _expense_activity_for_period(
    session: AsyncSession,
    shop_id: uuid.UUID,
    start_date: datetime | None,
    end_date: datetime | None,
    *,
    include_cogs: bool,
) -> Decimal:
    """Debit activity of the ledger's EXPENSE accounts, net of reversals.

    `include_cogs` selects which half of the EXPENSE accounts participate:
    COGS reporting uses only the Cost of Goods Sold account, while expense
    reporting uses every *other* EXPENSE account - so the two figures never
    overlap and `Revenue - COGS - Expenses` is not double-counting.
    """

    statement = (
        select(
            func.coalesce(func.sum(LedgerEntry.debit), 0),
            func.coalesce(func.sum(LedgerEntry.credit), 0),
        )
        .select_from(LedgerEntry)
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(
            LedgerEntry.shop_id == shop_id,
            Account.account_type == AccountType.EXPENSE,
        )
    )
    if include_cogs:
        statement = statement.where(Account.code == COST_OF_GOODS_SOLD)
    else:
        statement = statement.where(Account.code != COST_OF_GOODS_SOLD)
    if start_date is not None:
        statement = statement.where(LedgerEntry.created_at >= start_date)
    if end_date is not None:
        statement = statement.where(LedgerEntry.created_at <= end_date)

    row = (await session.execute(statement)).one()
    return _money(_money(row[0]) - _money(row[1]))


async def get_profit_and_loss(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> ProfitAndLoss:
    """Revenue - COGS - Expenses = Net Profit, entirely from the ledger.

        Revenue     = credit activity of REVENUE accounts
        COGS        = debit activity of the Cost of Goods Sold account
        Expenses    = debit activity of every other EXPENSE account
        Gross Profit = Revenue - COGS
        Net Profit   = Gross Profit - Expenses

    All three are ledger-derived; the historical `SaleItem.cost_price` is only
    the source of the COGS *posting*, never the reporting source.
    """

    _validate_range(start_date, end_date)

    revenue = await _revenue_for_period(session, shop_id, start_date, end_date)
    cogs = await _expense_activity_for_period(
        session, shop_id, start_date, end_date, include_cogs=True
    )
    expenses = await _expense_activity_for_period(
        session, shop_id, start_date, end_date, include_cogs=False
    )
    gross_profit = _money(revenue - cogs)
    net_profit = _money(gross_profit - expenses)

    return ProfitAndLoss(
        start_date=start_date,
        end_date=end_date,
        revenue=revenue,
        cogs=cogs,
        gross_profit=gross_profit,
        expenses=expenses,
        net_profit=net_profit,
        expense_reporting_available=True,
        notes=(
            "Revenue is derived from the general ledger's REVENUE accounts.",
            "COGS is the ledger balance of the Cost of Goods Sold account; "
            "the posting was created from historical SaleItem.cost_price.",
            "Expenses are the ledger balance of the remaining EXPENSE "
            "accounts, so they never include COGS twice.",
        ),
    )


async def get_balance_sheet(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    end_date: datetime | None = None,
) -> BalanceSheet:
    """Assets, liabilities and equity from ledger account balances.

    Only ASSET / LIABILITY / EQUITY accounts participate. Revenue and expense
    accounts are not yet closed into equity (there are no closing entries in
    V1), so `difference` is surfaced rather than hidden: reporting does not
    invent balancing entries.
    """

    accounts = await _load_accounts(session, shop_id)
    closing = await _account_activity(session, shop_id, None, end_date)

    rows: list[BalanceSheetRow] = []
    total_assets = _ZERO
    total_liabilities = _ZERO
    total_equity = _ZERO

    for account in accounts:
        if account.account_type not in (
            AccountType.ASSET,
            AccountType.LIABILITY,
            AccountType.EQUITY,
        ):
            continue

        debit, credit = closing.get(account.id, (_ZERO, _ZERO))
        balance = _money(
            _normal_balance(account.account_type, debit, credit)
        )

        if account.account_type is AccountType.ASSET:
            total_assets += balance
        elif account.account_type is AccountType.LIABILITY:
            total_liabilities += balance
        else:
            total_equity += balance

        rows.append(
            BalanceSheetRow(
                account_id=account.id,
                code=account.code,
                name=account.name,
                account_type=account.account_type,
                balance=balance,
            )
        )

    total_assets = _money(total_assets)
    total_liabilities = _money(total_liabilities)
    total_equity = _money(total_equity)
    total_liabilities_and_equity = _money(total_liabilities + total_equity)

    return BalanceSheet(
        end_date=end_date,
        rows=tuple(rows),
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_equity=total_equity,
        total_liabilities_and_equity=total_liabilities_and_equity,
        difference=_money(total_assets - total_liabilities_and_equity),
    )


async def get_dashboard_summary(
    session: AsyncSession, *, shop_id: uuid.UUID
) -> DashboardSummary:
    """Business overview for the current UTC day.

    Read-only and derived: sales/purchases/payments come straight from the
    operational tables, receivables/payables reuse the Step 6/7 services, and
    inventory value is `quantity x weighted_average_cost`. Low stock is not
    reported because the schema has no reorder/minimum-stock field.
    """

    now = datetime.now(timezone.utc)
    day_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    sales_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(Sale.total), 0),
                func.count(Sale.id),
            ).where(
                Sale.shop_id == shop_id,
                Sale.status.in_(QUALIFYING_SALE_STATUSES),
                Sale.created_at >= day_start,
                Sale.created_at < day_end,
            )
        )
    ).one()

    cogs_today = await _expense_activity_for_period(
        session, shop_id, day_start, day_end, include_cogs=True
    )
    expenses_today = await _expense_activity_for_period(
        session, shop_id, day_start, day_end, include_cogs=False
    )

    payments_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(Payment.amount), 0),
                func.count(Payment.id),
            ).where(
                Payment.shop_id == shop_id,
                Payment.supplier_id.is_(None),
                Payment.purchase_id.is_(None),
                Payment.created_at >= day_start,
                Payment.created_at < day_end,
            )
        )
    ).one()

    purchases_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(Purchase.total), 0),
                func.count(Purchase.id),
            ).where(
                Purchase.shop_id == shop_id,
                Purchase.created_at >= day_start,
                Purchase.created_at < day_end,
            )
        )
    ).one()

    inventory_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(Inventory.quantity), 0),
                func.coalesce(
                    func.sum(
                        Inventory.quantity * Inventory.weighted_average_cost
                    ),
                    0,
                ),
            )
            .select_from(Inventory)
            .join(ProductVariant, ProductVariant.id == Inventory.variant_id)
            .where(ProductVariant.shop_id == shop_id)
        )
    ).one()

    receivables = await receivables_service.get_total_outstanding(
        session, shop_id=shop_id
    )
    payables = await payables_service.get_total_outstanding(
        session, shop_id=shop_id
    )

    today_sales = _money(sales_row[0])

    return DashboardSummary(
        as_of=now,
        today_sales=today_sales,
        today_sales_count=int(sales_row[1]),
        today_payments_received=_money(payments_row[0]),
        today_payment_count=int(payments_row[1]),
        today_purchases=_money(purchases_row[0]),
        today_purchase_count=int(purchases_row[1]),
        today_cogs=cogs_today,
        today_gross_profit=_money(today_sales - cogs_today),
        today_expenses=expenses_today,
        today_net_profit=_money(today_sales - cogs_today - expenses_today),
        receivables_outstanding=receivables,
        payables_outstanding=payables,
        inventory_quantity=Decimal(inventory_row[0]),
        inventory_estimated_value=_money(inventory_row[1]),
        low_stock_variant_count=None,
        low_stock_available=False,
        notes=(
            "Today is the current UTC calendar day; no shop timezone is "
            "configured yet.",
            "Payments received counts all non-supplier payments, including "
            "POS sale payments.",
            "Low-stock reporting is unavailable: inventory has no "
            "reorder/minimum-stock field.",
        ),
    )
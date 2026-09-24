"""Response schemas for the Step 9 reporting endpoints.

These mirror the frozen dataclasses in `app.services.reporting` and are built
with `model_validate()` so the domain never imports Pydantic. Money is
`Decimal`, which serialises as a JSON string and keeps NUMERIC(14,2) exact.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.account import AccountType


class TrialBalanceRowResponse(BaseModel):
    """One account's period activity and closing balance."""

    model_config = ConfigDict(from_attributes=True)

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


class TrialBalanceResponse(BaseModel):
    """A trial balance plus its period and closing totals."""

    model_config = ConfigDict(from_attributes=True)

    start_date: datetime | None = None
    end_date: datetime | None = None
    rows: list[TrialBalanceRowResponse]
    total_debits: Decimal
    total_credits: Decimal
    total_closing_debits: Decimal
    total_closing_credits: Decimal
    is_balanced: bool


class ProfitAndLossResponse(BaseModel):
    """Revenue, COGS, Gross Profit, Expenses and Net Profit for a period."""

    model_config = ConfigDict(from_attributes=True)

    start_date: datetime | None = None
    end_date: datetime | None = None
    revenue: Decimal
    cogs: Decimal
    gross_profit: Decimal
    expenses: Decimal | None = None
    net_profit: Decimal | None = None
    expense_reporting_available: bool
    notes: list[str]


class BalanceSheetRowResponse(BaseModel):
    """One balance-sheet account line."""

    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    code: str
    name: str
    account_type: AccountType
    balance: Decimal


class BalanceSheetResponse(BaseModel):
    """Assets, liabilities, equity and the accounting-equation check."""

    model_config = ConfigDict(from_attributes=True)

    end_date: datetime | None = None
    rows: list[BalanceSheetRowResponse]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    total_liabilities_and_equity: Decimal
    difference: Decimal


class DashboardResponse(BaseModel):
    """Business overview for the current day."""

    model_config = ConfigDict(from_attributes=True)

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
    low_stock_variant_count: int | None = None
    low_stock_available: bool
    notes: list[str]


class FinancialSummaryResponse(BaseModel):
    """Period P&L plus Khata balances for the Reports page."""

    model_config = ConfigDict(from_attributes=True)

    period_start: datetime | None = None
    period_end: datetime | None = None
    total_sales: Decimal
    total_cogs: Decimal
    gross_profit: Decimal
    total_expenses: Decimal
    net_profit: Decimal
    receivables: Decimal
    payables: Decimal
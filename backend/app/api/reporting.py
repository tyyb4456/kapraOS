"""Reporting endpoints (Step 9) - read-only financial statements.

Six focused read-only routes over `app.services.reporting`:

    GET /reports/trial-balance
    GET /reports/profit-and-loss
    GET /reports/balance-sheet
    GET /reports/dashboard
    GET /reports/financial-summary
    GET /reports/sales-trend

There are deliberately no mutation endpoints. The shop is always resolved
server-side (see `app.api.dependencies`), so a report can never aggregate
across tenants. Domain errors are translated here and nowhere else.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DbSession, ShopId
from app.schemas.reporting import (
    BalanceSheetResponse,
    DashboardResponse,
    FinancialSummaryResponse,
    ProfitAndLossResponse,
    SalesTrendResponse,
    TrialBalanceResponse,
)
from app.services import reporting as reporting_service
from app.services.reporting import InvalidReportRangeError

router = APIRouter(prefix="/reports", tags=["reporting"])

# Legacy alias: an early frontend called `/reporting/financial-summary`
# (singular) before the backend contract settled on `/reports/*`.
# Keeping this compat router avoids a 404 for stale clients; new code
# should use `/reports/financial-summary`.
compat_router = APIRouter(prefix="/reporting", tags=["reporting"])

StartDate = Annotated[
    datetime | None, Query(description="Inclusive lower bound.")
]
EndDate = Annotated[
    datetime | None, Query(description="Inclusive upper bound.")
]


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.get(
    "/trial-balance",
    response_model=TrialBalanceResponse,
    summary="Trial balance",
)
async def read_trial_balance(
    shop_id: ShopId,
    db: DbSession,
    start_date: StartDate = None,
    end_date: EndDate = None,
) -> TrialBalanceResponse:
    try:
        report = await reporting_service.get_trial_balance(
            db, shop_id=shop_id, start_date=start_date, end_date=end_date
        )
    except InvalidReportRangeError as exc:
        raise _unprocessable(exc) from exc
    return TrialBalanceResponse.model_validate(report)


@router.get(
    "/profit-and-loss",
    response_model=ProfitAndLossResponse,
    summary="Profit & loss",
)
async def read_profit_and_loss(
    shop_id: ShopId,
    db: DbSession,
    start_date: StartDate = None,
    end_date: EndDate = None,
) -> ProfitAndLossResponse:
    try:
        report = await reporting_service.get_profit_and_loss(
            db, shop_id=shop_id, start_date=start_date, end_date=end_date
        )
    except InvalidReportRangeError as exc:
        raise _unprocessable(exc) from exc
    return ProfitAndLossResponse.model_validate(report)


@router.get(
    "/balance-sheet",
    response_model=BalanceSheetResponse,
    summary="Balance sheet",
)
async def read_balance_sheet(
    shop_id: ShopId,
    db: DbSession,
    end_date: EndDate = None,
) -> BalanceSheetResponse:
    report = await reporting_service.get_balance_sheet(
        db, shop_id=shop_id, end_date=end_date
    )
    return BalanceSheetResponse.model_validate(report)


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Business dashboard",
)
async def read_dashboard(
    shop_id: ShopId,
    db: DbSession,
) -> DashboardResponse:
    report = await reporting_service.get_dashboard_summary(db, shop_id=shop_id)
    return DashboardResponse.model_validate(report)


async def _financial_summary(
    shop_id: ShopId,
    db: DbSession,
    period: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> FinancialSummaryResponse:
    try:
        report = await reporting_service.get_financial_summary(
            db,
            shop_id=shop_id,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )
    except InvalidReportRangeError as exc:
        raise _unprocessable(exc) from exc
    return FinancialSummaryResponse.model_validate(report)


@router.get(
    "/financial-summary",
    response_model=FinancialSummaryResponse,
    summary="Financial summary for the Reports page",
)
async def read_financial_summary(
    shop_id: ShopId,
    db: DbSession,
    period: str | None = Query(
        default=None,
        description="today | this_week | this_month | this_year | all",
    ),
    start_date: StartDate = None,
    end_date: EndDate = None,
) -> FinancialSummaryResponse:
    return await _financial_summary(
        shop_id, db, period=period, start_date=start_date, end_date=end_date
    )


@router.get(
    "/sales-trend",
    response_model=SalesTrendResponse,
    summary="Revenue/profit/order time series",
)
async def read_sales_trend(
    shop_id: ShopId,
    db: DbSession,
    granularity: str | None = Query(
        default=None,
        description="day | week | month | auto (default: auto)",
    ),
    start_date: StartDate = None,
    end_date: EndDate = None,
) -> SalesTrendResponse:
    try:
        report = await reporting_service.get_sales_trend(
            db,
            shop_id=shop_id,
            start_date=start_date,
            end_date=end_date,
            granularity=granularity,
        )
    except InvalidReportRangeError as exc:
        raise _unprocessable(exc) from exc
    return SalesTrendResponse.model_validate(report)


@compat_router.get(
    "/financial-summary",
    response_model=FinancialSummaryResponse,
    summary="Financial summary (legacy alias)",
    include_in_schema=False,
)
async def read_financial_summary_compat(
    shop_id: ShopId,
    db: DbSession,
    period: str | None = Query(default=None),
    start_date: StartDate = None,
    end_date: EndDate = None,
) -> FinancialSummaryResponse:
    return await _financial_summary(
        shop_id, db, period=period, start_date=start_date, end_date=end_date
    )
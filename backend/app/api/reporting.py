"""Reporting endpoints (Step 9) - read-only financial statements.

Four focused read-only routes over `app.services.reporting`:

    GET /reports/trial-balance
    GET /reports/profit-and-loss
    GET /reports/balance-sheet
    GET /reports/dashboard

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
    ProfitAndLossResponse,
    TrialBalanceResponse,
)
from app.services import reporting as reporting_service
from app.services.reporting import InvalidReportRangeError

router = APIRouter(prefix="/reports", tags=["reporting"])

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
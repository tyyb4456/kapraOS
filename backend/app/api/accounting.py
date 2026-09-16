"""Accounting inspection endpoints.

Three read-only routes over the Step 8 general ledger:

    GET /accounts                        the shop's chart of accounts
    GET /accounts/{account_id}/balance   a derived balance
    GET /accounts/{account_id}/ledger    one account's ledger lines

There is deliberately **no** endpoint for creating ledger entries or accounts:
accounting entries are generated from business events by
`app.services.accounting`, never by a client. The shop is always resolved
server-side (see `app.api.dependencies`), so an account id from another tenant
returns 404 rather than reading anything.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.dependencies import DbSession, ShopId
from app.schemas.accounting import (
    AccountBalanceResponse,
    AccountLedgerResponse,
    AccountResponse,
)
from app.services import accounting as accounting_service
from app.services.accounting import (
    AccountNotFoundError,
    InvalidPaginationError,
    InvalidStatementRangeError,
)

router = APIRouter(prefix="/accounts", tags=["accounting"])

AccountId = Annotated[uuid.UUID, Path(description="Account to report on.")]


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.get(
    "",
    response_model=list[AccountResponse],
    summary="Chart of accounts",
)
async def list_accounts(
    shop_id: ShopId,
    db: DbSession,
) -> list[AccountResponse]:
    accounts = await accounting_service.list_accounts(db, shop_id=shop_id)
    return [AccountResponse.model_validate(account) for account in accounts]


@router.get(
    "/{account_id}/balance",
    response_model=AccountBalanceResponse,
    summary="Account balance",
)
async def read_account_balance(
    account_id: AccountId,
    shop_id: ShopId,
    db: DbSession,
) -> AccountBalanceResponse:
    try:
        balance = await accounting_service.get_account_balance(
            db, shop_id=shop_id, account_id=account_id
        )
    except AccountNotFoundError as exc:
        raise _not_found(exc) from exc
    return AccountBalanceResponse.model_validate(balance)


@router.get(
    "/{account_id}/ledger",
    response_model=AccountLedgerResponse,
    summary="Account ledger",
)
async def read_account_ledger(
    account_id: AccountId,
    shop_id: ShopId,
    db: DbSession,
    start_date: Annotated[
        datetime | None, Query(description="Inclusive lower bound.")
    ] = None,
    end_date: Annotated[
        datetime | None, Query(description="Inclusive upper bound.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AccountLedgerResponse:
    try:
        ledger = await accounting_service.get_account_ledger(
            db,
            shop_id=shop_id,
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
    except AccountNotFoundError as exc:
        raise _not_found(exc) from exc
    except (InvalidStatementRangeError, InvalidPaginationError) as exc:
        raise _unprocessable(exc) from exc
    return AccountLedgerResponse.model_validate(ledger)
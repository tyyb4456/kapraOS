"""Customer Khata endpoints.

Four focused routes over the Step 6 receivables service - deliberately not a
customer CRUD surface, which belongs to its own step:

    GET  /customers/{customer_id}/balance     how much do they owe?
    GET  /customers/{customer_id}/statement   why do they owe it?
    GET  /customers/{customer_id}/summary     dashboard line for one customer
    POST /customers/{customer_id}/payments    record money received

The shop is always resolved server-side (see `app.api.dependencies`), so a
customer id belonging to another tenant returns 404 rather than reading
anything. Domain errors are translated here and nowhere else - the service
raises `ReceivablesError` subclasses and stays free of HTTP concerns.

`get_db` does not commit, so the one write route commits explicitly (the read
routes never need to).
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.dependencies import DbSession, ShopId
from app.schemas.receivables import (
    CustomerBalanceResponse,
    CustomerStatementResponse,
    CustomerSummaryResponse,
    PaymentResponse,
    RecordCustomerPaymentRequest,
    RecordCustomerPaymentResponse,
)
from app.services import receivables as receivables_service
from app.services.receivables import (
    CustomerNotFoundError,
    InvalidPaginationError,
    InvalidPaymentAmountError,
    InvalidPaymentMethodError,
    InvalidStatementRangeError,
    PaymentExceedsOutstandingError,
    PaymentExceedsSaleDueError,
    SaleCustomerMismatchError,
    SaleNotFoundError,
    SaleNotSettleableError,
)

router = APIRouter(prefix="/customers", tags=["khata"])

CustomerId = Annotated[uuid.UUID, Path(description="Customer to report on.")]


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.get(
    "/{customer_id}/balance",
    response_model=CustomerBalanceResponse,
    summary="Customer outstanding balance",
)
async def read_customer_balance(
    customer_id: CustomerId,
    shop_id: ShopId,
    db: DbSession,
) -> CustomerBalanceResponse:
    try:
        balance = await receivables_service.get_customer_balance(
            db, shop_id=shop_id, customer_id=customer_id
        )
    except CustomerNotFoundError as exc:
        raise _not_found(exc) from exc
    return CustomerBalanceResponse.model_validate(balance)


@router.get(
    "/{customer_id}/statement",
    response_model=CustomerStatementResponse,
    summary="Customer Khata statement",
)
async def read_customer_statement(
    customer_id: CustomerId,
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
) -> CustomerStatementResponse:
    try:
        statement = await receivables_service.get_customer_statement(
            db,
            shop_id=shop_id,
            customer_id=customer_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
    except CustomerNotFoundError as exc:
        raise _not_found(exc) from exc
    except (InvalidStatementRangeError, InvalidPaginationError) as exc:
        raise _unprocessable(exc) from exc
    return CustomerStatementResponse.model_validate(statement)


@router.get(
    "/{customer_id}/summary",
    response_model=CustomerSummaryResponse,
    summary="Customer dashboard summary",
)
async def read_customer_summary(
    customer_id: CustomerId,
    shop_id: ShopId,
    db: DbSession,
) -> CustomerSummaryResponse:
    try:
        summary = await receivables_service.get_customer_summary(
            db, shop_id=shop_id, customer_id=customer_id
        )
    except CustomerNotFoundError as exc:
        raise _not_found(exc) from exc
    return CustomerSummaryResponse.model_validate(summary)


@router.post(
    "/{customer_id}/payments",
    response_model=RecordCustomerPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record money received from a customer",
)
async def create_customer_payment(
    customer_id: CustomerId,
    shop_id: ShopId,
    db: DbSession,
    body: RecordCustomerPaymentRequest,
) -> RecordCustomerPaymentResponse:
    try:
        payment = await receivables_service.record_customer_payment(
            db,
            shop_id=shop_id,
            customer_id=customer_id,
            amount=body.amount,
            method=body.method,
            sale_id=body.sale_id,
            reference=body.reference,
        )
        balance = await receivables_service.get_customer_balance(
            db, shop_id=shop_id, customer_id=customer_id
        )
    except (CustomerNotFoundError, SaleNotFoundError) as exc:
        raise _not_found(exc) from exc
    except (
        InvalidPaymentAmountError,
        InvalidPaymentMethodError,
        PaymentExceedsOutstandingError,
        PaymentExceedsSaleDueError,
        SaleCustomerMismatchError,
        SaleNotSettleableError,
    ) as exc:
        raise _unprocessable(exc) from exc

    response = RecordCustomerPaymentResponse(
        payment=PaymentResponse.model_validate(payment),
        balance=CustomerBalanceResponse.model_validate(balance),
    )
    await db.commit()
    return response
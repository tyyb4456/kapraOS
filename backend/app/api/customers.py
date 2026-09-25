"""Customer endpoints.

CRUD routes for customers and the Step 6 receivables (Khata) service:

    GET  /customers                    list all customers
    POST /customers                    create a new customer
    GET  /customers/{customer_id}/balance     outstanding balance
    GET  /customers/{customer_id}/statement   Khata statement
    GET  /customers/{customer_id}/summary     dashboard summary
    POST /customers/{customer_id}/payments    record money received

The shop is always resolved server-side (see `app.api.dependencies`), so a
customer id belonging to another tenant returns 404 rather than reading
anything. Domain errors are translated here and nowhere else - the service
raises `ReceivablesError` subclasses and stays free of HTTP concerns.

`get_db` does not commit, so the write routes commit explicitly (the read
routes never need to).
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import DbSession, ShopId
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.sale import Sale
from app.schemas.receivables import (
    CustomerBalanceResponse,
    CustomerResponse,
    CustomerStatementResponse,
    CustomerSummaryResponse,
    CreateCustomerRequest,
    UpdateCustomerRequest,
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

router = APIRouter(prefix="/customers", tags=["customers"])

CustomerId = Annotated[uuid.UUID, Path(description="Customer to report on.")]


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.get("", response_model=list[CustomerResponse])
async def list_customers(
    shop_id: ShopId,
    db: DbSession,
) -> list[CustomerResponse]:
    customers = (await db.execute(
        select(Customer).where(Customer.shop_id == shop_id).order_by(Customer.name.asc())
    )).scalars().all()

    results = []
    for customer in customers:
        balance = await receivables_service.get_customer_balance(
            db, shop_id=shop_id, customer_id=customer.id
        )
        results.append(CustomerResponse(
            id=customer.id,
            shop_id=customer.shop_id,
            name=customer.name,
            phone=customer.phone,
            email=customer.email,
            current_balance=float(balance.outstanding_balance),
            credit_limit=float(customer.credit_limit) if customer.credit_limit is not None else None,
            address=customer.address,
            notes=customer.notes,
            created_at=customer.created_at,
        ))
    return results


@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    shop_id: ShopId,
    db: DbSession,
    body: CreateCustomerRequest,
) -> CustomerResponse:
    if not body.name or not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Customer name must not be blank",
        )
    customer = Customer(
        shop_id=shop_id,
        name=body.name.strip(),
        phone=body.phone,
        email=body.email,
        credit_limit=body.credit_limit,
        address=body.address,
        notes=body.notes,
    )
    db.add(customer)
    await db.flush()
    await db.refresh(customer)
    await db.commit()
    return CustomerResponse(
        id=customer.id,
        shop_id=customer.shop_id,
        name=customer.name,
        phone=customer.phone,
        email=customer.email,
        current_balance=0.0,
        credit_limit=float(customer.credit_limit) if customer.credit_limit is not None else None,
        address=customer.address,
        notes=customer.notes,
        created_at=customer.created_at,
    )


@router.get(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="Read one customer",
)
async def read_customer(
    customer_id: CustomerId,
    shop_id: ShopId,
    db: DbSession,
) -> CustomerResponse:
    customer = (await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.shop_id == shop_id,
        )
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    balance = await receivables_service.get_customer_balance(
        db, shop_id=shop_id, customer_id=customer.id
    )
    return CustomerResponse(
        id=customer.id,
        shop_id=customer.shop_id,
        name=customer.name,
        phone=customer.phone,
        email=customer.email,
        current_balance=float(balance.outstanding_balance),
        credit_limit=float(customer.credit_limit) if customer.credit_limit is not None else None,
        address=customer.address,
        notes=customer.notes,
        created_at=customer.created_at,
    )


@router.patch(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="Update a customer",
)
async def update_customer(
    customer_id: CustomerId,
    shop_id: ShopId,
    db: DbSession,
    body: UpdateCustomerRequest,
) -> CustomerResponse:
    customer = (await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.shop_id == shop_id,
        )
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is not None:
        name = updates["name"].strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Customer name must not be blank",
            )
        customer.name = name
    if "phone" in updates:
        customer.phone = updates["phone"]
    if "email" in updates:
        customer.email = updates["email"]
    if "credit_limit" in updates:
        customer.credit_limit = updates["credit_limit"]
    if "address" in updates:
        customer.address = updates["address"]
    if "notes" in updates:
        customer.notes = updates["notes"]
    await db.flush()
    await db.commit()
    await db.refresh(customer)
    balance = await receivables_service.get_customer_balance(
        db, shop_id=shop_id, customer_id=customer.id
    )
    return CustomerResponse(
        id=customer.id,
        shop_id=customer.shop_id,
        name=customer.name,
        phone=customer.phone,
        email=customer.email,
        current_balance=float(balance.outstanding_balance),
        credit_limit=float(customer.credit_limit) if customer.credit_limit is not None else None,
        address=customer.address,
        notes=customer.notes,
        created_at=customer.created_at,
    )


@router.delete(
    "/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a customer with no history",
)
async def delete_customer(
    customer_id: CustomerId,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    customer = (await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.shop_id == shop_id,
        )
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    sales_count = (await db.execute(
        select(func.count(Sale.id)).where(
            Sale.shop_id == shop_id,
            Sale.customer_id == customer_id,
        )
    )).scalar_one()
    payments_count = (await db.execute(
        select(func.count(Payment.id)).where(
            Payment.shop_id == shop_id,
            Payment.customer_id == customer_id,
        )
    )).scalar_one()
    if (sales_count or 0) > 0 or (payments_count or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete customer with history "
                f"({sales_count} sale(s), {payments_count} payment(s)). "
                "Keep the record for the Khata audit trail instead."
            ),
        )
    await db.delete(customer)
    await db.commit()


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
@router.get(
    "/{customer_id}/khata",
    response_model=CustomerStatementResponse,
    summary="Customer Khata statement (alias)",
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

"""Supplier Khata endpoints.

Four focused routes over the Step 7 payables service - deliberately not a
supplier CRUD surface, which belongs to its own step:

    GET  /suppliers/{supplier_id}/balance     how much do we owe them?
    GET  /suppliers/{supplier_id}/statement   why do we owe it?
    GET  /suppliers/{supplier_id}/summary     dashboard line for one supplier
    POST /suppliers/{supplier_id}/payments    record money paid out

The shop is always resolved server-side (see `app.api.dependencies`), so a
supplier id belonging to another tenant returns 404 rather than reading
anything. Domain errors are translated here and nowhere else - the service
raises `PayablesError` subclasses and stays free of HTTP concerns.

`get_db` does not commit, so the one write route commits explicitly (the read
routes never need to).
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import DbSession, ShopId
from app.models.supplier import Supplier
from app.schemas.payables import (
    CreateSupplierRequest,
    PaymentResponse,
    RecordSupplierPaymentRequest,
    RecordSupplierPaymentResponse,
    SupplierBalanceResponse,
    SupplierResponse,
    SupplierStatementResponse,
    SupplierSummaryResponse,
)
from app.services import payables as payables_service
from app.services.payables import (
    InvalidPaginationError,
    InvalidPaymentAmountError,
    InvalidPaymentMethodError,
    InvalidStatementRangeError,
    PaymentExceedsOutstandingError,
    PaymentExceedsPurchaseDueError,
    PurchaseNotFoundError,
    PurchaseSupplierMismatchError,
    SupplierNotFoundError,
)

router = APIRouter(prefix="/suppliers", tags=["payables"])

SupplierId = Annotated[uuid.UUID, Path(description="Supplier to report on.")]


@router.get("", response_model=list[SupplierResponse])
async def list_suppliers(
    shop_id: ShopId,
    db: DbSession,
) -> list[SupplierResponse]:
    suppliers = (await db.execute(
        select(Supplier).where(Supplier.shop_id == shop_id).order_by(Supplier.name.asc())
    )).scalars().all()

    results = []
    for supplier in suppliers:
        balance = await payables_service.get_supplier_balance(
            db, shop_id=shop_id, supplier_id=supplier.id
        )
        results.append(SupplierResponse(
            id=supplier.id,
            shop_id=supplier.shop_id,
            name=supplier.name,
            phone=supplier.phone,
            address=supplier.address,
            notes=supplier.notes,
            current_balance=float(balance.outstanding_balance),
            created_at=supplier.created_at,
        ))
    return results


@router.post("", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    shop_id: ShopId,
    db: DbSession,
    body: CreateSupplierRequest,
) -> SupplierResponse:
    supplier = Supplier(
        shop_id=shop_id,
        name=body.name,
        phone=body.phone,
        address=body.address,
        notes=body.notes,
    )
    db.add(supplier)
    await db.flush()
    await db.refresh(supplier)
    await db.commit()
    return SupplierResponse(
        id=supplier.id,
        shop_id=supplier.shop_id,
        name=supplier.name,
        phone=supplier.phone,
        address=supplier.address,
        notes=supplier.notes,
        current_balance=0.0,
        created_at=supplier.created_at,
    )


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.get(
    "/{supplier_id}/balance",
    response_model=SupplierBalanceResponse,
    summary="Supplier outstanding payable",
)
async def read_supplier_balance(
    supplier_id: SupplierId,
    shop_id: ShopId,
    db: DbSession,
) -> SupplierBalanceResponse:
    try:
        balance = await payables_service.get_supplier_balance(
            db, shop_id=shop_id, supplier_id=supplier_id
        )
    except SupplierNotFoundError as exc:
        raise _not_found(exc) from exc
    return SupplierBalanceResponse.model_validate(balance)


@router.get(
    "/{supplier_id}/statement",
    response_model=SupplierStatementResponse,
    summary="Supplier Khata statement",
)
async def read_supplier_statement(
    supplier_id: SupplierId,
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
) -> SupplierStatementResponse:
    try:
        statement = await payables_service.get_supplier_statement(
            db,
            shop_id=shop_id,
            supplier_id=supplier_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
    except SupplierNotFoundError as exc:
        raise _not_found(exc) from exc
    except (InvalidStatementRangeError, InvalidPaginationError) as exc:
        raise _unprocessable(exc) from exc
    return SupplierStatementResponse.model_validate(statement)


@router.get(
    "/{supplier_id}/summary",
    response_model=SupplierSummaryResponse,
    summary="Supplier dashboard summary",
)
async def read_supplier_summary(
    supplier_id: SupplierId,
    shop_id: ShopId,
    db: DbSession,
) -> SupplierSummaryResponse:
    try:
        summary = await payables_service.get_supplier_summary(
            db, shop_id=shop_id, supplier_id=supplier_id
        )
    except SupplierNotFoundError as exc:
        raise _not_found(exc) from exc
    return SupplierSummaryResponse.model_validate(summary)


@router.post(
    "/{supplier_id}/payments",
    response_model=RecordSupplierPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record money paid to a supplier",
)
async def create_supplier_payment(
    supplier_id: SupplierId,
    shop_id: ShopId,
    db: DbSession,
    body: RecordSupplierPaymentRequest,
) -> RecordSupplierPaymentResponse:
    try:
        payment = await payables_service.record_supplier_payment(
            db,
            shop_id=shop_id,
            supplier_id=supplier_id,
            amount=body.amount,
            method=body.method,
            purchase_id=body.purchase_id,
            reference=body.reference,
        )
        balance = await payables_service.get_supplier_balance(
            db, shop_id=shop_id, supplier_id=supplier_id
        )
    except (SupplierNotFoundError, PurchaseNotFoundError) as exc:
        raise _not_found(exc) from exc
    except (
        InvalidPaymentAmountError,
        InvalidPaymentMethodError,
        PaymentExceedsOutstandingError,
        PaymentExceedsPurchaseDueError,
        PurchaseSupplierMismatchError,
    ) as exc:
        raise _unprocessable(exc) from exc

    response = RecordSupplierPaymentResponse(
        payment=PaymentResponse.model_validate(payment),
        balance=SupplierBalanceResponse.model_validate(balance),
    )
    await db.commit()
    return response
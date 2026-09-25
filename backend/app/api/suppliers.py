"""Supplier directory + Khata endpoints.

    GET  /suppliers                         list all suppliers
    POST /suppliers                         create a new supplier
    GET  /suppliers/{supplier_id}           read one supplier
    PATCH /suppliers/{supplier_id}          update name/phone/address/notes
    DELETE /suppliers/{supplier_id}         delete a supplier with no history
    GET  /suppliers/{supplier_id}/balance     how much do we owe them?
    GET  /suppliers/{supplier_id}/statement   why do we owe it?
    GET  /suppliers/{supplier_id}/summary     dashboard line for one supplier
    POST /suppliers/{supplier_id}/payments    record money paid out

The shop is always resolved server-side (see `app.api.dependencies`), so a
supplier id belonging to another tenant returns 404 rather than reading
anything. Domain errors are translated here and nowhere else - the service
raises `PayablesError` subclasses and stays free of HTTP concerns.

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
from app.models.payment import Payment
from app.models.purchase import Purchase
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
    UpdateSupplierRequest,
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
    if not body.name or not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Supplier name must not be blank",
        )
    supplier = Supplier(
        shop_id=shop_id,
        name=body.name.strip(),
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


@router.get(
    "/{supplier_id}",
    response_model=SupplierResponse,
    summary="Read one supplier",
)
async def read_supplier(
    supplier_id: SupplierId,
    shop_id: ShopId,
    db: DbSession,
) -> SupplierResponse:
    supplier = (await db.execute(
        select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.shop_id == shop_id,
        )
    )).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found")
    balance = await payables_service.get_supplier_balance(
        db, shop_id=shop_id, supplier_id=supplier.id
    )
    return SupplierResponse(
        id=supplier.id,
        shop_id=supplier.shop_id,
        name=supplier.name,
        phone=supplier.phone,
        address=supplier.address,
        notes=supplier.notes,
        current_balance=float(balance.outstanding_balance),
        created_at=supplier.created_at,
    )


@router.patch(
    "/{supplier_id}",
    response_model=SupplierResponse,
    summary="Update a supplier",
)
async def update_supplier(
    supplier_id: SupplierId,
    shop_id: ShopId,
    db: DbSession,
    body: UpdateSupplierRequest,
) -> SupplierResponse:
    supplier = (await db.execute(
        select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.shop_id == shop_id,
        )
    )).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found")
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is not None:
        name = updates["name"].strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Supplier name must not be blank",
            )
        supplier.name = name
    if "phone" in updates:
        supplier.phone = updates["phone"]
    if "address" in updates:
        supplier.address = updates["address"]
    if "notes" in updates:
        supplier.notes = updates["notes"]
    await db.flush()
    await db.commit()
    await db.refresh(supplier)
    balance = await payables_service.get_supplier_balance(
        db, shop_id=shop_id, supplier_id=supplier.id
    )
    return SupplierResponse(
        id=supplier.id,
        shop_id=supplier.shop_id,
        name=supplier.name,
        phone=supplier.phone,
        address=supplier.address,
        notes=supplier.notes,
        current_balance=float(balance.outstanding_balance),
        created_at=supplier.created_at,
    )


@router.delete(
    "/{supplier_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a supplier with no history",
)
async def delete_supplier(
    supplier_id: SupplierId,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    supplier = (await db.execute(
        select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.shop_id == shop_id,
        )
    )).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found")
    purchases_count = (await db.execute(
        select(func.count(Purchase.id)).where(
            Purchase.shop_id == shop_id,
            Purchase.supplier_id == supplier_id,
        )
    )).scalar_one()
    payments_count = (await db.execute(
        select(func.count(Payment.id)).where(
            Payment.shop_id == shop_id,
            Payment.supplier_id == supplier_id,
        )
    )).scalar_one()
    if (purchases_count or 0) > 0 or (payments_count or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete supplier with history "
                f"({purchases_count} purchase(s), {payments_count} payment(s)). "
                "Keep the record for the Khata audit trail instead."
            ),
        )
    await db.delete(supplier)
    await db.commit()


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
@router.get(
    "/{supplier_id}/khata",
    response_model=SupplierStatementResponse,
    summary="Supplier Khata statement (alias)",
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
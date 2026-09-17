"""Purchase endpoints (Step 3).

Create and list purchases. Delegates to `app.services.purchases.create_purchase()`.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import DbSession, ShopId
from app.services import purchases as purchases_service
from app.services.purchases import (
    PurchaseError,
    SupplierNotFoundError,
    VariantNotFoundError,
    EmptyPurchaseError,
    InvalidPurchaseItemError,
    InvalidPurchaseTotalsError,
    DuplicatePurchaseItemError,
)
from app.schemas.purchases import (
    CreatePurchaseRequest,
    PurchaseResponse,
    PurchaseListItemResponse,
)
from app.models.purchase import Purchase
from sqlalchemy import func

router = APIRouter(prefix="/purchases", tags=["purchases"])


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.post("", response_model=PurchaseResponse, status_code=status.HTTP_201_CREATED)
async def create_purchase(
    shop_id: ShopId,
    db: DbSession,
    body: CreatePurchaseRequest,
) -> PurchaseResponse:
    try:
        items = [
            purchases_service.PurchaseItemInput(
                variant_id=item.variant_id,
                quantity=item.quantity,
                unit_cost=item.unit_cost,
            )
            for item in body.items
        ]
        purchase = await purchases_service.create_purchase(
            db,
            shop_id=shop_id,
            supplier_id=body.supplier_id,
            items=items,
            invoice_number=body.invoice_number,
            discount=body.discount,
            paid_amount=body.paid_amount,
        )
    except SupplierNotFoundError as exc:
        raise _not_found(exc) from exc
    except VariantNotFoundError as exc:
        raise _not_found(exc) from exc
    except (EmptyPurchaseError, InvalidPurchaseItemError, InvalidPurchaseTotalsError, DuplicatePurchaseItemError) as exc:
        raise _unprocessable(exc) from exc
    return PurchaseResponse.model_validate(purchase)


@router.get("", response_model=list[PurchaseListItemResponse])
async def list_purchases(
    shop_id: ShopId,
    db: DbSession,
    supplier_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PurchaseListItemResponse]:
    stmt = select(Purchase).where(Purchase.shop_id == shop_id)
    if supplier_id is not None:
        stmt = stmt.where(Purchase.supplier_id == supplier_id)
    if start_date is not None:
        stmt = stmt.where(Purchase.created_at >= start_date)
    if end_date is not None:
        stmt = stmt.where(Purchase.created_at <= end_date)
    stmt = stmt.order_by(Purchase.created_at.desc())
    stmt = stmt.offset(offset).limit(limit)
    purchases = (await db.execute(stmt)).scalars().all()
    return [PurchaseListItemResponse.model_validate(p) for p in purchases]


@router.get("/{purchase_id}", response_model=PurchaseResponse)
async def get_purchase(
    purchase_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> PurchaseResponse:
    purchase = await db.get(Purchase, purchase_id)
    if purchase is None or purchase.shop_id != shop_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase not found")
    return PurchaseResponse.model_validate(purchase)

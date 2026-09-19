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
    PurchaseItemResponse,
    PurchaseListItemResponse,
)
from app.models.purchase import Purchase, PurchaseItem
from app.models.supplier import Supplier
from sqlalchemy import func, select

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

    await db.commit()

    # Eagerly load items to avoid greenlet issues with model_validate
    items = (await db.execute(
        select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
    )).scalars().all()

    return PurchaseResponse(
        id=purchase.id,
        supplier_id=purchase.supplier_id,
        invoice_number=purchase.invoice_number,
        subtotal=purchase.subtotal,
        discount=purchase.discount,
        total=purchase.total,
        paid_amount=purchase.paid_amount,
        due_amount=purchase.due_amount,
        shop_id=purchase.shop_id,
        created_at=purchase.created_at,
        items=[
            PurchaseItemResponse(
                id=item.id,
                variant_id=item.variant_id,
                quantity=item.quantity,
                unit_cost=item.unit_cost,
                total=item.total,
            )
            for item in items
        ],
    )


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
    stmt = (
        select(Purchase, Supplier.name)
        .join(Supplier, Purchase.supplier_id == Supplier.id, isouter=True)
        .where(Purchase.shop_id == shop_id)
    )
    if supplier_id is not None:
        stmt = stmt.where(Purchase.supplier_id == supplier_id)
    if start_date is not None:
        stmt = stmt.where(Purchase.created_at >= start_date)
    if end_date is not None:
        stmt = stmt.where(Purchase.created_at <= end_date)
    stmt = stmt.order_by(Purchase.created_at.desc())
    stmt = stmt.offset(offset).limit(limit)
    results = (await db.execute(stmt)).all()

    purchases = []
    for purchase, supplier_name in results:
        # Count items for this purchase
        item_count = (
            await db.execute(
                select(func.count(PurchaseItem.id)).where(
                    PurchaseItem.purchase_id == purchase.id
                )
            )
        ).scalar() or 0

        purchases.append(
            PurchaseListItemResponse(
                id=purchase.id,
                shop_id=purchase.shop_id,
                order_number=purchase.invoice_number,
                supplier_id=purchase.supplier_id,
                supplier_name=supplier_name,
                status="received",
                total_amount=purchase.total,
                paid_amount=purchase.paid_amount,
                items_count=item_count,
                created_at=purchase.created_at,
            )
        )
    return purchases


@router.get("/{purchase_id}", response_model=PurchaseResponse)
async def get_purchase(
    purchase_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> PurchaseResponse:
    purchase = await db.get(Purchase, purchase_id)
    if purchase is None or purchase.shop_id != shop_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase not found")

    # Eagerly load items to avoid greenlet issues with model_validate
    items = (await db.execute(
        select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
    )).scalars().all()

    return PurchaseResponse(
        id=purchase.id,
        supplier_id=purchase.supplier_id,
        invoice_number=purchase.invoice_number,
        subtotal=purchase.subtotal,
        discount=purchase.discount,
        total=purchase.total,
        paid_amount=purchase.paid_amount,
        due_amount=purchase.due_amount,
        shop_id=purchase.shop_id,
        created_at=purchase.created_at,
        items=[
            PurchaseItemResponse(
                id=item.id,
                variant_id=item.variant_id,
                quantity=item.quantity,
                unit_cost=item.unit_cost,
                total=item.total,
            )
            for item in items
        ],
    )

"""Inventory endpoints (Step 3).

Read-only routes for stock levels and movement history.
Delegates to `app.services.inventory` for queries.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.dependencies import DbSession, ShopId
from app.services import inventory as inventory_service
from app.services.inventory import (
    InventoryError,
    VariantNotFoundError,
    InsufficientStockError,
)
from app.schemas.inventory import (
    InventoryResponse,
    InventoryMovementResponse,
    InventoryMovementListResponse,
    LowStockResponse,
    StockValueResponse,
)
from app.models.inventory import Inventory, InventoryMovement
from app.models.product import ProductVariant, Product

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.get("", response_model=list[InventoryResponse])
async def list_inventory(
    shop_id: ShopId,
    db: DbSession,
    variant_id: UUID | None = None,
    low_stock: bool = False,
    search: str | None = None,
) -> list[InventoryResponse]:
    variant_stmt = select(ProductVariant).where(ProductVariant.shop_id == shop_id)
    if search is not None:
        variant_stmt = variant_stmt.where(
            ProductVariant.sku.ilike(f"%{search}%")
            | ProductVariant.barcode.ilike(f"%{search}%")
        )
    variant_subq = variant_stmt.subquery()

    if low_stock:
        inv_stmt = select(Inventory).where(
            Inventory.variant_id.in_(variant_subq),
            Inventory.quantity <= Inventory.reserved_quantity + 1,
        )
    else:
        inv_stmt = select(Inventory).where(Inventory.variant_id.in_(variant_subq))

    if variant_id is not None:
        inv_stmt = inv_stmt.where(Inventory.variant_id == variant_id)

    results = (await db.execute(inv_stmt)).scalars().all()

    variants_map = {}
    if results:
        variant_ids = [r.variant_id for r in results]
        variants = (await db.execute(
            select(ProductVariant).where(ProductVariant.id.in_(variant_ids))
        )).scalars().all()
        products = (await db.execute(
            select(Product).where(Product.id.in_([v.product_id for v in variants]))
        )).scalars().all()
        product_map = {p.id: p for p in products}
        variants_map = {v.id: v for v in variants}

    responses = []
    for inv in results:
        v = variants_map.get(inv.variant_id)
        p = product_map.get(v.product_id) if v else None
        responses.append(InventoryResponse.model_validate(inv))
    return responses


@router.get("/movements", response_model=InventoryMovementListResponse)
async def list_inventory_movements(
    shop_id: ShopId,
    db: DbSession,
    variant_id: UUID | None = None,
    movement_type: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InventoryMovementListResponse:
    stmt = select(InventoryMovement).where(InventoryMovement.shop_id == shop_id)
    if variant_id is not None:
        stmt = stmt.where(InventoryMovement.variant_id == variant_id)
    if movement_type is not None:
        stmt = stmt.where(InventoryMovement.movement_type == movement_type)
    if start_date is not None:
        stmt = stmt.where(InventoryMovement.created_at >= start_date)
    if end_date is not None:
        stmt = stmt.where(InventoryMovement.created_at <= end_date)

    total = (await db.execute(
        select(func.count(InventoryMovement.id)).where(InventoryMovement.shop_id == shop_id)
    )).scalar()

    stmt = stmt.order_by(InventoryMovement.created_at.desc())
    stmt = stmt.offset(offset).limit(limit)
    movements = (await db.execute(stmt)).scalars().all()

    return InventoryMovementListResponse.model_validate(
        {"movements": [InventoryMovementResponse.model_validate(m) for m in movements], "total": total}
    )


@router.get("/{variant_id}", response_model=InventoryResponse)
async def get_inventory(
    variant_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> InventoryResponse:
    result = await db.execute(
        select(Inventory).where(Inventory.variant_id == variant_id)
    )
    inv = result.scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory not found for this variant")

    variant = await db.get(ProductVariant, variant_id)
    product = await db.get(Product, variant.product_id) if variant else None

    return InventoryResponse.model_validate(inv)


@router.post("/{variant_id}/adjust", response_model=InventoryResponse)
async def adjust_stock(
    variant_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    quantity_delta: Annotated[Decimal, Query()],
    notes: str | None = None,
) -> InventoryResponse:
    try:
        movement = await inventory_service.adjust_stock(
            db,
            shop_id=shop_id,
            variant_id=variant_id,
            quantity_delta=quantity_delta,
            notes=notes,
        )
    except VariantNotFoundError as exc:
        raise _not_found(exc) from exc
    except InsufficientStockError as exc:
        raise _unprocessable(exc) from exc
    return InventoryResponse.model_validate(
        await db.get(Inventory, variant_id)
    )

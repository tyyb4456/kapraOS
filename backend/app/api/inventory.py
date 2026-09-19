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
from sqlalchemy.orm import selectinload

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
from app.models.product import ProductVariant, Product, VariantAttributeValue

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


def _extract_attributes(variant: ProductVariant) -> dict[str, str]:
    attrs = {}
    for av in variant.attribute_values or []:
        if av.attribute and av.attribute_value:
            attrs[av.attribute.name] = av.attribute_value.value
    return attrs


def _build_inventory_response(
    inv: Inventory | None,
    variant: ProductVariant,
    product: Product | None,
    attributes: dict[str, str] | None = None,
) -> InventoryResponse:
    qty = inv.quantity if inv else Decimal("0")
    reserved = inv.reserved_quantity if inv else Decimal("0")
    avail = inv.available_quantity if inv else Decimal("0")
    cost = (
        inv.weighted_average_cost
        if inv and inv.weighted_average_cost > Decimal("0")
        else variant.purchase_price
    )
    reorder = inv.reorder_level if inv else Decimal("0")
    unit_val = (
        variant.unit.value
        if hasattr(variant.unit, "value")
        else str(variant.unit)
    )

    return InventoryResponse(
        id=inv.id if inv else variant.id,
        variant_id=variant.id,
        quantity=qty,
        quantity_on_hand=qty,
        reserved_quantity=reserved,
        available_quantity=avail,
        weighted_average_cost=cost,
        unit=unit_val,
        sku=variant.sku,
        product_name=product.name if product else None,
        cost_price=cost,
        selling_price=variant.selling_price,
        attributes=attributes or {},
        low_stock_threshold=reorder,
    )


@router.get("", response_model=list[InventoryResponse])
async def list_inventory(
    shop_id: ShopId,
    db: DbSession,
    variant_id: UUID | None = None,
    low_stock: bool = False,
    search: str | None = None,
) -> list[InventoryResponse]:
    stmt = (
        select(ProductVariant)
        .options(
            selectinload(ProductVariant.product),
            selectinload(ProductVariant.inventory),
            selectinload(ProductVariant.attribute_values).selectinload(
                VariantAttributeValue.attribute
            ),
            selectinload(ProductVariant.attribute_values).selectinload(
                VariantAttributeValue.attribute_value
            ),
        )
        .where(ProductVariant.shop_id == shop_id)
    )

    if search is not None:
        stmt = stmt.outerjoin(Product, Product.id == ProductVariant.product_id)
        stmt = stmt.where(
            ProductVariant.sku.ilike(f"%{search}%")
            | ProductVariant.barcode.ilike(f"%{search}%")
            | Product.name.ilike(f"%{search}%")
        )

    if variant_id is not None:
        stmt = stmt.where(ProductVariant.id == variant_id)

    variants = (await db.execute(stmt)).scalars().all()

    responses = []
    for v in variants:
        inv = v.inventory
        qty = inv.quantity if inv else Decimal("0")
        reserved = inv.reserved_quantity if inv else Decimal("0")
        reorder = inv.reorder_level if inv else Decimal("0")

        if low_stock:
            threshold = reorder if reorder > Decimal("0") else (reserved + 1)
            if qty > threshold:
                continue

        attrs = _extract_attributes(v)
        responses.append(_build_inventory_response(inv, v, v.product, attrs))

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

    variant_ids = list({m.variant_id for m in movements})
    variants_map = {}
    if variant_ids:
        v_stmt = (
            select(ProductVariant)
            .options(selectinload(ProductVariant.product))
            .where(ProductVariant.id.in_(variant_ids))
        )
        variants = (await db.execute(v_stmt)).scalars().all()
        variants_map = {v.id: v for v in variants}

    movement_responses = []
    for m in movements:
        v = variants_map.get(m.variant_id)
        p = v.product if v else None
        unit_val = (
            (v.unit.value if hasattr(v.unit, "value") else str(v.unit))
            if v
            else None
        )
        movement_responses.append(
            InventoryMovementResponse(
                id=m.id,
                shop_id=m.shop_id,
                variant_id=m.variant_id,
                movement_type=m.movement_type,
                quantity=m.quantity,
                unit_cost=m.unit_cost,
                reference_type=m.reference_type,
                reference_id=m.reference_id,
                notes=m.notes,
                created_at=m.created_at,
                sku=v.sku if v else None,
                product_name=p.name if p else None,
                unit=unit_val,
            )
        )

    return InventoryMovementListResponse(
        movements=movement_responses,
        total=total or 0,
    )


@router.get("/{variant_id}", response_model=InventoryResponse)
async def get_inventory(
    variant_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> InventoryResponse:
    stmt = (
        select(ProductVariant)
        .options(
            selectinload(ProductVariant.product),
            selectinload(ProductVariant.inventory),
            selectinload(ProductVariant.attribute_values).selectinload(
                VariantAttributeValue.attribute
            ),
            selectinload(ProductVariant.attribute_values).selectinload(
                VariantAttributeValue.attribute_value
            ),
        )
        .where(ProductVariant.id == variant_id, ProductVariant.shop_id == shop_id)
    )
    variant = (await db.execute(stmt)).scalar_one_or_none()
    if variant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found for this shop")

    attrs = _extract_attributes(variant)
    return _build_inventory_response(variant.inventory, variant, variant.product, attrs)


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

    stmt = (
        select(ProductVariant)
        .options(
            selectinload(ProductVariant.product),
            selectinload(ProductVariant.inventory),
            selectinload(ProductVariant.attribute_values).selectinload(
                VariantAttributeValue.attribute
            ),
            selectinload(ProductVariant.attribute_values).selectinload(
                VariantAttributeValue.attribute_value
            ),
        )
        .where(ProductVariant.id == variant_id, ProductVariant.shop_id == shop_id)
    )
    variant = (await db.execute(stmt)).scalar_one()
    attrs = _extract_attributes(variant)
    return _build_inventory_response(variant.inventory, variant, variant.product, attrs)

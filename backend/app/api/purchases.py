"""Purchase endpoints (Step 3).

Create and list purchases. Delegates to `app.services.purchases.create_purchase()`.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.dependencies import DbSession, ShopId
from app.models.purchase import Purchase, PurchaseItem
from app.models.supplier import Supplier
from app.schemas.purchases import (
    CreatePurchaseRequest,
    PurchaseItemResponse,
    PurchaseListItemResponse,
    PurchaseResponse,
    UpdatePurchaseRequest,
)
from app.schemas.returns import (
    CreatePurchaseReturnRequest,
    PurchaseReturnResponse,
    PurchaseReturnWithRemainingResponse,
)
from app.services import purchases as purchases_service
from app.services import returns as returns_service
from app.services.inventory import InsufficientStockError
from app.services.purchases import (
    DuplicatePurchaseItemError,
    EmptyPurchaseError,
    InvalidPurchaseItemError,
    InvalidPurchaseTotalsError,
    PurchaseNotFoundError,
    SupplierNotFoundError,
    VariantNotFoundError,
)
from app.services.returns import (
    EmptyReturnError,
    ExceedsRemainingQuantityError,
    InvalidReturnQuantityError,
)
from app.services.returns import (
    PurchaseItemNotFoundError as ReturnPurchaseItemNotFoundError,
)
from app.services.returns import (
    PurchaseNotFoundError as ReturnPurchaseNotFoundError,
)

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

    # Fresh purchase has no returns yet.
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
        returned_total=Decimal("0.00"),
        net_total=Decimal(purchase.total),
        returns_count=0,
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

    purchase_ids = [p.id for p, _ in results]

    items_count_by_purchase: dict = {}
    if purchase_ids:
        count_rows = (await db.execute(
            select(PurchaseItem.purchase_id, func.count(PurchaseItem.id))
            .where(PurchaseItem.purchase_id.in_(purchase_ids))
            .group_by(PurchaseItem.purchase_id)
        )).all()
        items_count_by_purchase = {row[0]: int(row[1]) for row in count_rows}

    returns_total_by_purchase: dict = {}
    returns_count_by_purchase: dict = {}
    if purchase_ids:
        from app.models.returns import PurchaseReturn

        return_rows = (await db.execute(
            select(
                PurchaseReturn.purchase_id,
                func.coalesce(func.sum(PurchaseReturn.total_amount), 0),
                func.count(PurchaseReturn.id),
            )
            .where(
                PurchaseReturn.shop_id == shop_id,
                PurchaseReturn.purchase_id.in_(purchase_ids),
            )
            .group_by(PurchaseReturn.purchase_id)
        )).all()
        for row in return_rows:
            returns_total_by_purchase[row[0]] = Decimal(str(row[1]))
            returns_count_by_purchase[row[0]] = int(row[2])

    purchases = []
    for purchase, supplier_name in results:
        item_count = items_count_by_purchase.get(purchase.id, 0)
        # Fall back to per-row count only if batch missed (should not happen).
        if purchase.id not in items_count_by_purchase:
            item_count = (
                await db.execute(
                    select(func.count(PurchaseItem.id)).where(
                        PurchaseItem.purchase_id == purchase.id
                    )
                )
            ).scalar() or 0

        returned_total = returns_total_by_purchase.get(purchase.id, Decimal("0.00"))
        net_total = Decimal(purchase.total) - Decimal(returned_total)

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
                returned_total=returned_total,
                net_total=net_total,
                returns_count=returns_count_by_purchase.get(purchase.id, 0),
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

    from app.models.returns import PurchaseReturn

    return_row = (await db.execute(
        select(
            func.coalesce(func.sum(PurchaseReturn.total_amount), 0),
            func.count(PurchaseReturn.id),
        ).where(
            PurchaseReturn.shop_id == shop_id,
            PurchaseReturn.purchase_id == purchase.id,
        )
    )).one()
    returned_total = Decimal(str(return_row[0]))

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
        returned_total=returned_total,
        net_total=Decimal(purchase.total) - returned_total,
        returns_count=int(return_row[1]),
    )


def _purchase_to_response(
    purchase: Purchase,
    items: list[PurchaseItem],
    *,
    returned_total: Decimal | None = None,
    returns_count: int = 0,
) -> PurchaseResponse:
    rt = Decimal("0.00") if returned_total is None else Decimal(returned_total)
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
        returned_total=rt,
        net_total=Decimal(purchase.total) - rt,
        returns_count=returns_count,
    )


@router.patch("/{purchase_id}", response_model=PurchaseResponse)
async def update_purchase(
    purchase_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    body: UpdatePurchaseRequest,
) -> PurchaseResponse:
    fields_set = body.model_fields_set
    items = None
    if "items" in fields_set and body.items is not None:
        items = [
            purchases_service.PurchaseItemInput(
                variant_id=item.variant_id,
                quantity=item.quantity,
                unit_cost=item.unit_cost,
            )
            for item in body.items
        ]
    invoice_sentinel: object = ...
    if "invoice_number" in fields_set:
        invoice_sentinel = body.invoice_number
    try:
        purchase = await purchases_service.update_purchase(
            db,
            shop_id=shop_id,
            purchase_id=purchase_id,
            items=items,
            supplier_id=body.supplier_id,
            invoice_number=invoice_sentinel,  # type: ignore[arg-type]
            discount=body.discount,
        )
    except PurchaseNotFoundError as exc:
        raise _not_found(exc) from exc
    except (SupplierNotFoundError, VariantNotFoundError) as exc:
        raise _not_found(exc) from exc
    except (
        EmptyPurchaseError,
        InvalidPurchaseItemError,
        InvalidPurchaseTotalsError,
        DuplicatePurchaseItemError,
        returns_service.PurchaseHasReturnsError,
    ) as exc:
        raise _unprocessable(exc) from exc
    except InsufficientStockError as exc:
        raise _unprocessable(exc) from exc
    await db.commit()
    rows = (await db.execute(
        select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
    )).scalars().all()
    return _purchase_to_response(purchase, list(rows))


@router.delete("/{purchase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase(
    purchase_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    try:
        await purchases_service.delete_purchase(
            db, shop_id=shop_id, purchase_id=purchase_id
        )
    except PurchaseNotFoundError as exc:
        raise _not_found(exc) from exc
    except InsufficientStockError as exc:
        raise _unprocessable(exc) from exc
    except returns_service.PurchaseHasReturnsError as exc:
        raise _unprocessable(exc) from exc
    await db.commit()


@router.post(
    "/{purchase_id}/returns",
    response_model=PurchaseReturnWithRemainingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_purchase_return(
    purchase_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    body: CreatePurchaseReturnRequest,
) -> PurchaseReturnWithRemainingResponse:
    """Create a supplier return against a purchase (deterministic, auditable).

    The request carries only intent (which purchase item, how much); all
    costs, totals, inventory, payable and ledger effects are
    backend-authoritative.
    """

    try:
        purchase_return = await returns_service.create_purchase_return(
            db,
            shop_id=shop_id,
            purchase_id=purchase_id,
            lines=[
                returns_service.PurchaseReturnLineInput(
                    purchase_item_id=line.purchase_item_id,
                    quantity=line.quantity,
                )
                for line in body.lines
            ],
            notes=body.notes,
        )
    except ReturnPurchaseNotFoundError as exc:
        raise _not_found(exc) from exc
    except ReturnPurchaseItemNotFoundError as exc:
        raise _not_found(exc) from exc
    except (
        EmptyReturnError,
        InvalidReturnQuantityError,
        ExceedsRemainingQuantityError,
        InsufficientStockError,
    ) as exc:
        raise _unprocessable(exc) from exc

    await db.commit()
    await db.refresh(purchase_return, attribute_names=["items"])
    remaining = await returns_service.get_remaining_purchase_quantities(
        db, shop_id=shop_id, purchase_id=purchase_id
    )
    response = PurchaseReturnResponse.model_validate(purchase_return)
    return PurchaseReturnWithRemainingResponse(
        **{
            "return": response,
            "remaining_quantities": {str(k): v for k, v in remaining.items()},
            "purchase_id": purchase_id,
            "total_return_amount": purchase_return.total_amount,
        }
    )


@router.get("/{purchase_id}/returns", response_model=list[PurchaseReturnResponse])
async def list_purchase_returns(
    purchase_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> list[PurchaseReturnResponse]:
    """List all supplier returns for a purchase (tenant-scoped, audit trail)."""

    from app.models.returns import PurchaseReturn

    purchase = await db.get(Purchase, purchase_id)
    if purchase is None or purchase.shop_id != shop_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Purchase not found"
        )

    rows = (
        await db.execute(
            select(PurchaseReturn)
            .where(
                PurchaseReturn.shop_id == shop_id,
                PurchaseReturn.purchase_id == purchase_id,
            )
            .order_by(PurchaseReturn.created_at.asc())
        )
    ).scalars().all()
    for row in rows:
        await db.refresh(row, attribute_names=["items"])
    return [PurchaseReturnResponse.model_validate(row) for row in rows]

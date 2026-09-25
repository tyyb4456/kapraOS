"""Sales/POS endpoints (Step 4).

Create and list sales. The sale creation delegates to
`app.services.sales.create_sale()` which handles the full atomic
transaction: validate, build sale, take stock, record payments,
post ledger entries.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import DbSession, ShopId
from app.services import sales as sales_service
from app.services.inventory import InsufficientStockError
from app.services.sales import (
    SaleError,
    SaleNotFoundError,
    SaleNotEditableError,
    ShopNotFoundError,
    CustomerNotFoundError,
    VariantNotFoundError,
    EmptySaleError,
    InvalidSaleItemError,
    InvalidSaleTotalsError,
    DuplicateSaleItemError,
)
from app.schemas.sales import (
    CreateSaleRequest,
    UpdateSaleRequest,
    SaleResponse,
    SaleListItemResponse,
    SaleSummaryResponse,
)
from app.models.sale import Sale, SaleItem, SaleStatus
from app.models.customer import Customer
from app.models.payment import Payment, PaymentMethod

router = APIRouter(prefix="/sales", tags=["sales"])


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.post("", response_model=SaleResponse, status_code=status.HTTP_201_CREATED)
async def create_sale(
    shop_id: ShopId,
    db: DbSession,
    body: CreateSaleRequest,
) -> SaleResponse:
    try:
        items = [
            sales_service.SaleItemInput(
                variant_id=item.variant_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount=item.discount,
            )
            for item in body.items
        ]
        payments = [
            sales_service.PaymentInput(
                amount=p.amount,
                method=p.method,
                reference=p.reference,
            )
            for p in (body.payments or [])
        ]
        sale = await sales_service.create_sale(
            db,
            shop_id=shop_id,
            items=items,
            customer_id=body.customer_id,
            invoice_number=body.invoice_number,
            discount=body.discount,
            payments=payments if payments else None,
        )
    except ShopNotFoundError as exc:
        raise _not_found(exc) from exc
    except CustomerNotFoundError as exc:
        raise _not_found(exc) from exc
    except VariantNotFoundError as exc:
        raise _not_found(exc) from exc
    except (EmptySaleError, InvalidSaleItemError, InvalidSaleTotalsError, DuplicateSaleItemError) as exc:
        raise _unprocessable(exc) from exc

    response = SaleResponse.model_validate(sale)
    if sale.customer_id is not None:
        customer = await db.get(Customer, sale.customer_id)
        if customer:
            response.customer_name = customer.name
    await db.commit()
    return response


@router.get("", response_model=list[SaleListItemResponse])
async def list_sales(
    shop_id: ShopId,
    db: DbSession,
    customer_id: UUID | None = None,
    status_filter: SaleStatus | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SaleListItemResponse]:
    stmt = select(Sale).where(Sale.shop_id == shop_id)
    if customer_id is not None:
        stmt = stmt.where(Sale.customer_id == customer_id)
    if status_filter is not None:
        stmt = stmt.where(Sale.status == status_filter)
    if start_date is not None:
        stmt = stmt.where(Sale.created_at >= start_date)
    if end_date is not None:
        stmt = stmt.where(Sale.created_at <= end_date)
    stmt = stmt.order_by(Sale.created_at.desc())
    stmt = stmt.offset(offset).limit(limit)
    sales = (await db.execute(stmt)).scalars().all()

    sale_ids = [s.id for s in sales]
    customer_ids = [s.customer_id for s in sales if s.customer_id is not None]

    customer_names = {}
    if customer_ids:
        customers = (await db.execute(
            select(Customer).where(Customer.id.in_(customer_ids))
        )).scalars().all()
        customer_names = {c.id: c.name for c in customers}

    payments_by_sale = {}
    if sale_ids:
        payments = (await db.execute(
            select(Payment).where(
                Payment.sale_id.in_(sale_ids),
                Payment.shop_id == shop_id,
            )
        )).scalars().all()
        for p in payments:
            if p.sale_id not in payments_by_sale:
                payments_by_sale[p.sale_id] = p.method

    items_count_by_sale = {}
    if sale_ids:
        item_counts = (await db.execute(
            select(SaleItem.sale_id, func.count(SaleItem.id))
            .where(SaleItem.sale_id.in_(sale_ids))
            .group_by(SaleItem.sale_id)
        )).all()
        items_count_by_sale = {row[0]: row[1] for row in item_counts}

    results = []
    for sale in sales:
        customer_name = customer_names.get(sale.customer_id) if sale.customer_id else None
        results.append(SaleListItemResponse(
            id=sale.id,
            invoice_number=sale.invoice_number,
            customer_id=sale.customer_id,
            customer_name=customer_name,
            payment_method=payments_by_sale.get(sale.id),
            subtotal=sale.subtotal,
            discount=sale.discount,
            total_amount=sale.total,
            paid_amount=sale.paid_amount,
            due_amount=sale.due_amount,
            items_count=items_count_by_sale.get(sale.id, 0),
            status=sale.status,
            shop_id=sale.shop_id,
            created_at=sale.created_at,
        ))
    return results


@router.get("/{sale_id}", response_model=SaleResponse)
async def get_sale(
    sale_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> SaleResponse:
    sale = await db.get(Sale, sale_id)
    if sale is None or sale.shop_id != shop_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sale not found")
    response = SaleResponse.model_validate(sale)
    if sale.customer:
        response.customer_name = sale.customer.name
    return response


@router.patch("/{sale_id}", response_model=SaleResponse)
async def update_sale(
    sale_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    body: UpdateSaleRequest,
) -> SaleResponse:
    fields_set = body.model_fields_set
    items = None
    if "items" in fields_set and body.items is not None:
        items = [
            sales_service.SaleItemInput(
                variant_id=item.variant_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount=item.discount,
            )
            for item in body.items
        ]
    customer_sentinel: object = ...
    if "customer_id" in fields_set:
        customer_sentinel = body.customer_id
    invoice_sentinel: object = ...
    if "invoice_number" in fields_set:
        invoice_sentinel = body.invoice_number
    try:
        sale = await sales_service.update_sale(
            db,
            shop_id=shop_id,
            sale_id=sale_id,
            items=items,
            customer_id=customer_sentinel,  # type: ignore[arg-type]
            invoice_number=invoice_sentinel,  # type: ignore[arg-type]
            discount=body.discount,
        )
    except SaleNotFoundError as exc:
        raise _not_found(exc) from exc
    except (ShopNotFoundError, CustomerNotFoundError, VariantNotFoundError) as exc:
        raise _not_found(exc) from exc
    except (
        EmptySaleError,
        InvalidSaleItemError,
        InvalidSaleTotalsError,
        DuplicateSaleItemError,
        SaleNotEditableError,
        InsufficientStockError,
    ) as exc:
        raise _unprocessable(exc) from exc
    await db.commit()
    await db.refresh(sale, attribute_names=["items", "payments"])
    response = SaleResponse.model_validate(sale)
    if sale.customer_id is not None:
        customer = await db.get(Customer, sale.customer_id)
        if customer:
            response.customer_name = customer.name
    return response


@router.delete("/{sale_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sale(
    sale_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    try:
        await sales_service.delete_sale(db, shop_id=shop_id, sale_id=sale_id)
    except SaleNotFoundError as exc:
        raise _not_found(exc) from exc
    except InsufficientStockError as exc:
        raise _unprocessable(exc) from exc
    await db.commit()


@router.get("/summary", response_model=SaleSummaryResponse)
async def sales_summary(
    shop_id: ShopId,
    db: DbSession,
) -> SaleSummaryResponse:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = datetime.utcnow().replace(hour=23, minute=59, second=59, microsecond=999999)
    total_row = (await db.execute(
        select(func.coalesce(func.sum(Sale.total), 0), func.count(Sale.id)).where(
            Sale.shop_id == shop_id,
            Sale.created_at >= today,
            Sale.created_at <= tomorrow,
            Sale.status.in_([SaleStatus.COMPLETED, SaleStatus.PARTIAL]),
        )
    )).one()
    return SaleSummaryResponse(
        today_sales=Decimal(str(total_row[0])),
        today_sales_count=total_row[1],
        today_gross_profit=Decimal("0"),
        today_net_profit=Decimal("0"),
    )

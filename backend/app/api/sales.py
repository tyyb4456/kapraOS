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
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import DbSession, ShopId
from app.services import sales as sales_service
from app.services.sales import (
    SaleError,
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
    SaleResponse,
    SaleListItemResponse,
    SaleSummaryResponse,
)
from app.models.sale import Sale, SaleItem, SaleStatus

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
    return SaleResponse.model_validate(sale)


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
    return [SaleListItemResponse.model_validate(s) for s in sales]


@router.get("/{sale_id}", response_model=SaleResponse)
async def get_sale(
    sale_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> SaleResponse:
    sale = await db.get(Sale, sale_id)
    if sale is None or sale.shop_id != shop_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sale not found")
    return SaleResponse.model_validate(sale)


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


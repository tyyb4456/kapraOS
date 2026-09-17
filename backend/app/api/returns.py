"""Returns endpoints (Step 4).

Process customer returns for completed sales. This reverses stock
via InventoryMovement (CUSTOMER_RETURN), updates the sale status,
and reverses the accounting postings (revenue, COGS, inventory asset).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DbSession, ShopId
from app.services import inventory as inventory_service
from app.services import accounting as accounting_service
from app.services.inventory import (
    InventoryError,
    VariantNotFoundError,
    InsufficientStockError,
)
from app.models.sale import Sale, SaleStatus
from app.models.payment import Payment, PaymentMethod
from app.models.inventory import InventoryMovementType

router = APIRouter(prefix="/returns", tags=["returns"])


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.post(
    "/{sale_id}/return",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def create_return(
    sale_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    quantity: Annotated[Decimal, Query(ge=0.001)],
    notes: str | None = None,
) -> dict:
    if quantity <= 0:
        raise _unprocessable("Return quantity must be greater than 0")

    sale = await db.get(Sale, sale_id)
    if sale is None or sale.shop_id != shop_id:
        raise _not_found(f"Sale {sale_id} not found")

    if sale.status not in (SaleStatus.COMPLETED, SaleStatus.PARTIAL):
        raise _unprocessable(
            f"Cannot return a sale with status {sale.status.value}"
        )

    total_quantity = sum(item.quantity for item in sale.items)
    if quantity > total_quantity:
        raise _unprocessable(
            f"Return quantity {quantity} exceeds sale total {total_quantity}"
        )

    remaining = quantity
    return_qty = Decimal("0")
    refund_amount = Decimal("0")

    try:
        for item in sale.items:
            if remaining <= 0:
                break

            return_qty = min(item.quantity, remaining)
            remaining -= return_qty

            await inventory_service.add_stock(
                db,
                shop_id=shop_id,
                variant_id=item.variant_id,
                quantity=return_qty,
                movement_type=InventoryMovementType.CUSTOMER_RETURN,
                unit_cost=item.cost_price,
                reference_type="RETURN",
                reference_id=sale_id,
                notes=notes or f"Customer return for sale {sale_id}",
            )

            line_refund = (item.unit_price * return_qty) - item.discount
            refund_amount += line_refund

        sale.paid_amount = max(Decimal("0"), sale.paid_amount - refund_amount)
        sale.due_amount = sale.total - sale.paid_amount

        if sale.paid_amount <= Decimal("0") and remaining <= Decimal("0"):
            sale.status = SaleStatus.RETURNED

        await db.flush()

        # Reverse the accounting postings for the returned portion
        await accounting_service.post_sale_return(
            db,
            sale=sale,
            returned_quantity=return_qty,
            refund_amount=refund_amount,
            description=notes or f"Return for sale {sale_id}",
        )

        # Record the refund as an audit trail (no separate accounting posting -
        # the reversal above handles the ledger entries)
        if refund_amount > 0:
            refund_payment = Payment(
                shop_id=shop_id,
                sale_id=sale_id,
                amount=refund_amount,
                method=PaymentMethod.CASH,
                reference=notes or f"Refund for return {sale_id}",
            )
            db.add(refund_payment)
            await db.flush()

    except (VariantNotFoundError, InsufficientStockError) as exc:
        raise _not_found(exc) from exc
    except Exception as exc:
        raise _unprocessable(f"Return processing failed: {exc}") from exc

    return {
        "sale_id": sale_id,
        "returned_quantity": float(return_qty),
        "refund_amount": float(refund_amount),
        "new_status": sale.status.value,
        "new_paid_amount": float(sale.paid_amount),
        "new_due_amount": float(sale.due_amount),
    }

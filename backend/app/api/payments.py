"""Payment settlement endpoints.

    GET    /payments/{payment_id}   read one settlement (tenant-scoped)
    DELETE /payments/{payment_id}   void a Khata settlement

Voiding a payment removes its CUSTOMER_PAYMENT / SUPPLIER_PAYMENT ledger
group, rolls its amount back out of the allocated sale/purchase
(`paid_amount` and sale `status`), then deletes the row. Unallocated Khata
payments simply disappear from the statement; allocated ones re-open the
invoice's due. Till payments created inside `POST /sales` are ordinary
payments too - voiding one only adjusts the ledger and the sale cache, it
never restocks inventory (use the sale void / returns workflow for that).

The shop is always resolved server-side (see `app.api.dependencies`), so a
payment id from another tenant returns 404.
"""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select

from app.api.dependencies import DbSession, ShopId
from app.models.payment import Payment
from app.models.purchase import Purchase
from app.models.sale import Sale, SaleStatus
from app.services import accounting as accounting_service

router = APIRouter(prefix="/payments", tags=["payments"])

PaymentId = Annotated[uuid.UUID, Path(description="Payment to void.")]


@router.get("/{payment_id}")
async def read_payment(
    payment_id: PaymentId,
    shop_id: ShopId,
    db: DbSession,
) -> dict:
    payment = (await db.execute(
        select(Payment).where(Payment.id == payment_id, Payment.shop_id == shop_id)
    )).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return {
        "id": str(payment.id),
        "shop_id": str(payment.shop_id),
        "customer_id": str(payment.customer_id) if payment.customer_id else None,
        "supplier_id": str(payment.supplier_id) if payment.supplier_id else None,
        "sale_id": str(payment.sale_id) if payment.sale_id else None,
        "purchase_id": str(payment.purchase_id) if payment.purchase_id else None,
        "amount": str(payment.amount),
        "method": payment.method.value,
        "reference": payment.reference,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
    }


@router.delete("/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def void_payment(
    payment_id: PaymentId,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    payment = (await db.execute(
        select(Payment).where(Payment.id == payment_id, Payment.shop_id == shop_id)
    )).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    # Roll the allocation back out of the linked document first, so the
    # cached paid_amount never drifts from the remaining Payment rows.
    if payment.sale_id is not None:
        sale = (await db.execute(
            select(Sale).where(Sale.id == payment.sale_id, Sale.shop_id == shop_id)
        )).scalar_one_or_none()
        if sale is not None:
            sale.paid_amount = max(
                Decimal("0"), sale.paid_amount - payment.amount
            )
            if sale.status in (SaleStatus.COMPLETED, SaleStatus.PARTIAL):
                sale.status = (
                    SaleStatus.COMPLETED
                    if sale.paid_amount >= sale.total
                    else SaleStatus.PARTIAL
                )
    if payment.purchase_id is not None:
        purchase = (await db.execute(
            select(Purchase).where(
                Purchase.id == payment.purchase_id, Purchase.shop_id == shop_id
            )
        )).scalar_one_or_none()
        if purchase is not None:
            purchase.paid_amount = max(
                Decimal("0"), purchase.paid_amount - payment.amount
            )

    await accounting_service.delete_postings_for_reference(
        session=db, shop_id=shop_id, reference_id=payment.id
    )
    await db.delete(payment)
    await db.commit()

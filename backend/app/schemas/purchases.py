"""Response schemas for the purchases domain (Step 3).

These mirror the SQLAlchemy models in the purchases service.
"""

from datetime import datetime
from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.payment import PaymentMethod


class PurchaseItemResponse(BaseModel):
    """One line on a purchase."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    variant_id: UUID
    quantity: Decimal
    unit_cost: Decimal
    total: Decimal


class PurchaseResponse(BaseModel):
    """A full purchase with its lines."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    supplier_id: UUID
    invoice_number: str | None = None
    subtotal: Decimal
    discount: Decimal
    total: Decimal
    paid_amount: Decimal
    due_amount: Decimal
    shop_id: UUID
    created_at: datetime
    items: list[PurchaseItemResponse] = []


class PurchaseListItemResponse(BaseModel):
    """Compact purchase for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    shop_id: UUID
    order_number: str | None = None
    supplier_id: UUID
    supplier_name: str | None = None
    status: str = "received"
    total_amount: Decimal
    paid_amount: Decimal
    items_count: int = 0
    created_at: datetime


class CreatePurchaseRequest(BaseModel):
    supplier_id: UUID
    items: list["PurchaseLineRequest"]
    invoice_number: str | None = None
    discount: Decimal = 0
    paid_amount: Decimal = 0


class PurchaseLineRequest(BaseModel):
    variant_id: UUID
    quantity: float
    unit_cost: float

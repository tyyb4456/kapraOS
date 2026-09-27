"""Request/response schemas for the core return mechanism.

The frontend supplies intent only (which original item, how much, optional
notes). All pricing, totals, AR/cash splits and accounting are backend-
authoritative and never accepted from the client.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SaleReturnLineRequest(BaseModel):
    """One line of a customer-return request: which sale item, how much."""

    sale_item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)


class CreateSaleReturnRequest(BaseModel):
    lines: list[SaleReturnLineRequest] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=500)


class SaleReturnItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sale_item_id: uuid.UUID
    variant_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal
    cost_price: Decimal
    discount: Decimal
    total: Decimal


class SaleReturnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shop_id: uuid.UUID
    sale_id: uuid.UUID
    customer_id: uuid.UUID | None = None
    total_amount: Decimal
    ar_amount: Decimal
    cash_refund: Decimal
    cogs_amount: Decimal
    notes: str | None = None
    created_at: datetime
    items: list[SaleReturnItemResponse] = []


class SaleReturnWithRemainingResponse(BaseModel):
    """A created return plus authoritative post-return state."""

    return_: SaleReturnResponse = Field(alias="return")
    remaining_quantities: dict[str, Decimal]
    sale_id: uuid.UUID
    total_return_amount: Decimal
    ar_amount: Decimal
    cash_refund: Decimal

    model_config = ConfigDict(populate_by_name=True)


class PurchaseReturnLineRequest(BaseModel):
    """One line of a supplier-return request: which purchase item, how much."""

    purchase_item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)


class CreatePurchaseReturnRequest(BaseModel):
    lines: list[PurchaseReturnLineRequest] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=500)


class PurchaseReturnItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    purchase_item_id: uuid.UUID
    variant_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal
    discount: Decimal
    total: Decimal


class PurchaseReturnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shop_id: uuid.UUID
    purchase_id: uuid.UUID
    supplier_id: uuid.UUID
    total_amount: Decimal
    notes: str | None = None
    created_at: datetime
    items: list[PurchaseReturnItemResponse] = []


class PurchaseReturnWithRemainingResponse(BaseModel):
    return_: PurchaseReturnResponse = Field(alias="return")
    remaining_quantities: dict[str, Decimal]
    purchase_id: uuid.UUID
    total_return_amount: Decimal

    model_config = ConfigDict(populate_by_name=True)

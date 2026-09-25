"""Response schemas for the sales/POS domain (Step 4).

These mirror the frozen dataclasses and SQLAlchemy models in the
sales service. Money is Decimal, which serialises as a JSON string.
"""

from datetime import datetime
from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.payment import PaymentMethod
from app.models.sale import SaleStatus


class SaleItemResponse(BaseModel):
    """One line on a sale."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    variant_id: UUID
    quantity: Decimal
    unit_price: Decimal
    cost_price: Decimal
    discount: Decimal
    total: Decimal


class SaleResponse(BaseModel):
    """A full sale with its lines and payments."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID | None = None
    customer_name: str | None = None
    invoice_number: str | None = None
    subtotal: Decimal
    discount: Decimal
    total: Decimal
    paid_amount: Decimal
    due_amount: Decimal
    status: SaleStatus
    shop_id: UUID
    created_at: datetime
    items: list[SaleItemResponse] = []


class SaleListItemResponse(BaseModel):
    """Compact sale for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    invoice_number: str | None = None
    customer_id: UUID | None = None
    customer_name: str | None = None
    payment_method: PaymentMethod | None = None
    subtotal: Decimal
    discount: Decimal
    total_amount: Decimal
    paid_amount: Decimal
    due_amount: Decimal
    items_count: int
    status: SaleStatus
    shop_id: UUID
    created_at: datetime


class CreateSaleRequest(BaseModel):
    items: list["SaleLineRequest"]
    customer_id: UUID | None = None
    invoice_number: str | None = None
    discount: Decimal = 0
    payments: list["PaymentRequest"] | None = None


class SaleLineRequest(BaseModel):
    variant_id: UUID
    quantity: float
    unit_price: float
    discount: Decimal = 0


class PaymentRequest(BaseModel):
    amount: float
    method: PaymentMethod
    reference: str | None = None


class UpdateSaleRequest(BaseModel):
    """Partial edit for a sale - omitted keys are left alone.

    `items`, when present, fully replaces the sale's lines. `customer_id`
    accepts null to convert the sale to walk-in. Payments are never edited
    here - use the Khata payment endpoints to settle or void them.
    """

    items: list["SaleLineRequest"] | None = None
    customer_id: UUID | None = None
    invoice_number: str | None = None
    discount: Decimal | None = None


class SaleSummaryResponse(BaseModel):
    """Dashboard summary of sales."""

    model_config = ConfigDict(from_attributes=True)

    today_sales: Decimal
    today_sales_count: int
    today_gross_profit: Decimal
    today_net_profit: Decimal

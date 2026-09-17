"""Response schemas for the inventory domain (Step 3).

These mirror the SQLAlchemy models in the inventory service.
"""

from datetime import datetime
from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.inventory import InventoryMovementType
from app.models.product import Unit


class InventoryResponse(BaseModel):
    """Current stock state for one variant."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    variant_id: UUID
    quantity: Decimal
    reserved_quantity: Decimal
    available_quantity: Decimal
    weighted_average_cost: Decimal
    unit: str | None = None
    sku: str | None = None
    product_name: str | None = None


class InventoryMovementResponse(BaseModel):
    """One line in the inventory ledger."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    shop_id: UUID
    variant_id: UUID
    movement_type: InventoryMovementType
    quantity: Decimal
    unit_cost: Decimal | None = None
    reference_type: str | None = None
    reference_id: UUID | None = None
    notes: str | None = None
    created_at: datetime


class InventoryMovementListResponse(BaseModel):
    """Paginated inventory movements."""

    model_config = ConfigDict(from_attributes=True)

    movements: list[InventoryMovementResponse]
    total: int


class LowStockResponse(BaseModel):
    """Variants below their minimum stock level."""

    model_config = ConfigDict(from_attributes=True)

    variant_id: UUID
    sku: str
    product_name: str
    quantity: Decimal
    reserved_quantity: Decimal
    available_quantity: Decimal
    reorder_level: Decimal


class StockValueResponse(BaseModel):
    """Total inventory value."""

    model_config = ConfigDict(from_attributes=True)

    total_value: Decimal
    total_items: int
    total_variants: int

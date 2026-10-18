"""HTTP schemas for the opening-inventory domain (API layer).

These mirror the read models that `app.services.opening_inventory` already
produces - the dataclasses (`OpeningInventoryPreview`, ...), the ORM models
(`OpeningInventoryBatch` / `OpeningInventoryEntry`) and the variant/movement
rows the routes join against. No business rule lives here: the service is the
single authority on validation, costing and persistence.

Line inputs are intentionally permissive (`Decimal | str | None`): a preview
must be able to report *line-level* errors ("quantity must be greater than
0", "cost status is required") instead of one opaque request-level 422, so
the schema stays out of the way and the service normalises every field from
scratch (`post_opening_inventory()` re-validates too - a preview is never an
authorization token).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.inventory import InventoryMovementType
from app.models.opening_inventory import (
    OpeningInventoryCostStatus,
    OpeningInventorySource,
)


class OpeningInventoryLineRequest(BaseModel):
    """One proposed opening line.

    `quantity` and `unit_cost` accept the raw JSON scalar or its string form;
    the service rejects anything invalid with a line-level, actionable error.
    """

    variant_id: UUID
    quantity: Decimal | str | None = None
    unit_cost: Decimal | str | None = None
    cost_status: str | None = None


class PreviewOpeningInventoryRequest(BaseModel):
    """Dry-run request: validate proposed lines without writing anything."""

    lines: list[OpeningInventoryLineRequest] = Field(default_factory=list)


class PostOpeningInventoryRequest(BaseModel):
    """Post a validated opening inventory batch.

    `idempotency_key` is required: a retried submission (network retry,
    doubled click) returns the already-posted batch instead of creating a
    second one. The service checks it first; `UNIQUE(shop_id,
    idempotency_key)` is the database backstop.
    """

    idempotency_key: str
    lines: list[OpeningInventoryLineRequest] = Field(default_factory=list)
    source: OpeningInventorySource = OpeningInventorySource.MANUAL
    notes: str | None = None


class OpeningInventoryPreviewLineResponse(BaseModel):
    """One line's read-only validation result."""

    model_config = ConfigDict(from_attributes=True)

    index: int
    variant_id: UUID | None = None
    sku: str | None = None
    product_name: str | None = None
    unit: str | None = None
    quantity: Decimal | None = None
    unit_cost: Decimal | None = None
    cost_status: OpeningInventoryCostStatus | None = None
    value: Decimal | None = None
    current_quantity: Decimal
    current_weighted_average_cost: Decimal
    projected_weighted_average_cost: Decimal | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class OpeningInventoryPreviewResponse(BaseModel):
    """The whole submission's read-only validation result.

    `valid` is True only when every line passed; `total_value`,
    `entry_count` and `estimated_entry_count` cover only error-free lines.
    """

    model_config = ConfigDict(from_attributes=True)

    valid: bool
    total_value: Decimal
    entry_count: int
    estimated_entry_count: int
    lines: list[OpeningInventoryPreviewLineResponse]


class OpeningInventoryMovementResponse(BaseModel):
    """The inventory ledger line an opening entry created, when available."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    movement_type: InventoryMovementType
    quantity: Decimal
    unit_cost: Decimal | None = None
    reference_type: str | None = None
    reference_id: UUID | None = None
    created_at: datetime


class OpeningInventoryEntryResponse(BaseModel):
    """One posted opening line: variant details, cost snapshot and ledger link."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    variant_id: UUID
    sku: str | None = None
    product_name: str | None = None
    unit: str
    quantity: Decimal
    unit_cost: Decimal
    cost_status: OpeningInventoryCostStatus
    value: Decimal
    created_at: datetime
    movement: OpeningInventoryMovementResponse | None = None


class OpeningInventoryBatchResponse(BaseModel):
    """One opening-inventory batch (list view: totals, no entries)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    shop_id: UUID
    source: OpeningInventorySource
    notes: str | None = None
    entry_count: int
    estimated_entry_count: int
    total_value: Decimal
    created_by_user_id: UUID | None = None
    created_at: datetime


class OpeningInventoryBatchDetailResponse(OpeningInventoryBatchResponse):
    """A batch with its posted entries."""

    entries: list[OpeningInventoryEntryResponse] = Field(default_factory=list)

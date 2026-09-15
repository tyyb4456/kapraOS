"""Inventory service - the only supported write path for stock levels.

The rules encoded here are the whole point of Step 3:

* Every change to `Inventory.quantity` is paired with an `InventoryMovement`
  row in the same transaction, so the ledger can always reconstruct the
  counter (`db_arch.md` sections 13-14, 35).
* Deductions lock the inventory row with `SELECT ... FOR UPDATE` before
  reading it, so two concurrent cashiers cannot oversell the same stock.
* Stock never goes negative through an ordinary sale/removal, and reserved
  stock is respected via `available_quantity = quantity - reserved_quantity`.

Transaction ownership is the caller's: none of these functions commits. A
future `SaleService.create_sale()` will open one transaction and call
`remove_stock()` inside it, so inventory, sale rows and payment rows all
commit or roll back together (see `db_arch.md` sections 34-35). Callers are
expected to `flush`/commit; each operation flushes so its writes are visible
and constraints are checked before the surrounding transaction ends.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import Inventory, InventoryMovement, InventoryMovementType
from app.models.product import ProductVariant

# `Inventory.weighted_average_cost` is NUMERIC(14,4): four places keep a
# running average from compounding rounding error across many receipts.
_COST_SCALE = Decimal("0.0001")


class InventoryError(Exception):
    """Base class for inventory-domain failures."""


class VariantNotFoundError(InventoryError):
    """The requested variant does not exist in the caller's shop."""


class InsufficientStockError(InventoryError):
    """There is not enough available stock to satisfy a deduction."""

    def __init__(
        self,
        *,
        variant_id: uuid.UUID,
        requested: Decimal,
        available: Decimal,
    ) -> None:
        self.variant_id = variant_id
        self.requested = requested
        self.available = available
        super().__init__(
            f"Insufficient stock for variant {variant_id}: "
            f"requested {requested}, available {available}"
        )


async def _get_variant(
    session: AsyncSession, shop_id: uuid.UUID, variant_id: uuid.UUID
) -> ProductVariant:
    """Load a variant, enforcing that it belongs to `shop_id`.

    This is the service-layer half of the tenant-integrity guarantee (the
    database half is the composite FK on `inventory_movements`). A variant
    from another shop is treated as not found rather than readable, so no
    cross-tenant stock data ever leaks.
    """

    result = await session.execute(
        select(ProductVariant).where(
            ProductVariant.id == variant_id,
            ProductVariant.shop_id == shop_id,
        )
    )
    variant = result.scalar_one_or_none()
    if variant is None:
        raise VariantNotFoundError(
            f"Variant {variant_id} not found for shop {shop_id}"
        )
    return variant


async def _lock_inventory(
    session: AsyncSession, shop_id: uuid.UUID, variant_id: uuid.UUID
) -> Inventory:
    """Return the variant's inventory row, locked `FOR UPDATE`.

    The row is created on demand with a race-safe `INSERT ... ON CONFLICT DO
    NOTHING` (the unique constraint on `inventory.variant_id` arbitrates), then
    re-read with a row lock. Holding that lock is what serializes concurrent
    deductions for the same variant: the second transaction blocks here until
    the first commits, so it re-reads the updated quantity instead of acting
    on a stale snapshot.
    """

    await session.execute(
        pg_insert(Inventory)
        .values(variant_id=variant_id)
        .on_conflict_do_nothing(index_elements=["variant_id"])
    )

    result = await session.execute(
        select(Inventory)
        .where(Inventory.variant_id == variant_id)
        .with_for_update()
    )
    return result.scalar_one()


async def get_or_create_inventory(
    session: AsyncSession, shop_id: uuid.UUID, variant_id: uuid.UUID
) -> Inventory:
    """Fetch (or lazily create) the inventory row for a variant.

    Validates tenant ownership of the variant first. Does *not* take a row
    lock - use this for reads/bootstrap; the mutation helpers lock internally.
    """

    await _get_variant(session, shop_id, variant_id)

    result = await session.execute(
        select(Inventory).where(Inventory.variant_id == variant_id)
    )
    inventory = result.scalar_one_or_none()

    if inventory is None:
        await session.execute(
            pg_insert(Inventory)
            .values(variant_id=variant_id)
            .on_conflict_do_nothing(index_elements=["variant_id"])
        )
        result = await session.execute(
            select(Inventory).where(Inventory.variant_id == variant_id)
        )
        inventory = result.scalar_one()

    return inventory


async def get_weighted_average_cost(
    session: AsyncSession, *, shop_id: uuid.UUID, variant_id: uuid.UUID
) -> Decimal:
    """Return the variant's current weighted-average inventory cost, locked.

    This is the authoritative source for `SaleItem.cost_price`: the sale
    service snapshots this value at sale time and never recalculates it later
    (`db_arch.md` sections 18, 29).

    The inventory row is locked `FOR UPDATE` (creating it on demand) before it
    is read, so the returned cost is the exact value the subsequent
    `remove_stock()` in the same transaction will operate on - a concurrent
    purchase cannot slip a new average in between. The lock is held until the
    caller's transaction ends, which is precisely the sale transaction.
    """

    await _get_variant(session, shop_id, variant_id)
    inventory = await _lock_inventory(session, shop_id, variant_id)
    return inventory.weighted_average_cost


def _apply_receipt_cost(
    inventory: Inventory, quantity: Decimal, unit_cost: Decimal
) -> None:
    """Fold a costed receipt into the moving weighted-average cost.

    Standard moving average (`db_arch.md` section 29):

        new_avg = (qty_on_hand * old_avg + received_qty * receipt_cost)
                  / (qty_on_hand + received_qty)

    Computed entirely in `Decimal`. When nothing is on hand the old average
    contributes zero, so the average naturally becomes the receipt cost.
    Callers update `inventory.quantity` afterwards; this helper reads the
    *old* quantity, so it must run first.
    """

    old_quantity = inventory.quantity
    new_quantity = old_quantity + quantity
    total_value = (
        old_quantity * inventory.weighted_average_cost + quantity * unit_cost
    )
    inventory.weighted_average_cost = (total_value / new_quantity).quantize(
        _COST_SCALE, rounding=ROUND_HALF_UP
    )


async def add_stock(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    variant_id: uuid.UUID,
    quantity: Decimal,
    movement_type: InventoryMovementType,
    unit_cost: Decimal | None = None,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> InventoryMovement:
    """Increase stock for a variant and record a positive movement.

    `quantity` must be strictly positive. The inventory row is locked, the
    cached quantity is increased, and a signed `+quantity` movement is
    appended - all without committing, so this can participate in a larger
    transaction (e.g. a purchase).

    When `unit_cost` is supplied the moving weighted-average cost is updated
    first (`_apply_receipt_cost`), so the variant's valuation reflects the
    receipt even before the quantity write lands. Uncosted receipts (a manual
    adjustment, an opening balance with no price) leave the average alone.
    """

    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValueError("add_stock quantity must be greater than zero")

    variant = await _get_variant(session, shop_id, variant_id)
    inventory = await _lock_inventory(session, shop_id, variant_id)

    if unit_cost is not None:
        _apply_receipt_cost(inventory, quantity, Decimal(unit_cost))

    inventory.quantity = inventory.quantity + quantity

    movement = InventoryMovement(
        shop_id=variant.shop_id,
        variant_id=variant_id,
        movement_type=movement_type,
        quantity=quantity,
        unit_cost=unit_cost,
        reference_type=reference_type,
        reference_id=reference_id,
        notes=notes,
    )
    session.add(movement)
    await session.flush()
    return movement


async def remove_stock(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    variant_id: uuid.UUID,
    quantity: Decimal,
    movement_type: InventoryMovementType,
    unit_cost: Decimal | None = None,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> InventoryMovement:
    """Decrease stock for a variant, rejecting the operation if unavailable.

    `quantity` is the (positive) amount to take out. The inventory row is
    locked first, available stock is computed as `quantity -
    reserved_quantity`, and an `InsufficientStockError` is raised if the
    request exceeds it - no negative stock, no movement row. On success the
    counter is decreased and a signed `-quantity` movement is appended.
    """

    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValueError("remove_stock quantity must be greater than zero")

    variant = await _get_variant(session, shop_id, variant_id)
    inventory = await _lock_inventory(session, shop_id, variant_id)

    available = inventory.available_quantity
    if quantity > available:
        raise InsufficientStockError(
            variant_id=variant_id,
            requested=quantity,
            available=available,
        )

    inventory.quantity = inventory.quantity - quantity

    movement = InventoryMovement(
        shop_id=variant.shop_id,
        variant_id=variant_id,
        movement_type=movement_type,
        quantity=-quantity,
        unit_cost=unit_cost,
        reference_type=reference_type,
        reference_id=reference_id,
        notes=notes,
    )
    session.add(movement)
    await session.flush()
    return movement


async def adjust_stock(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    variant_id: uuid.UUID,
    quantity_delta: Decimal,
    notes: str | None = None,
    unit_cost: Decimal | None = None,
    reference_type: str | None = "ADJUSTMENT",
    reference_id: uuid.UUID | None = None,
) -> InventoryMovement:
    """Apply a signed stock correction and record it as an ADJUSTMENT.

    `quantity_delta` is signed (`+3` for a stock-count surplus, `-3` for a
    shortage). The record is never overwritten silently: the delta is written
    to `InventoryMovement` either way. A negative delta that would push stock
    below `reserved_quantity` (or below zero) is rejected.
    """

    quantity_delta = Decimal(quantity_delta)
    if quantity_delta == 0:
        raise ValueError("adjust_stock quantity_delta must not be zero")

    variant = await _get_variant(session, shop_id, variant_id)
    inventory = await _lock_inventory(session, shop_id, variant_id)

    new_quantity = inventory.quantity + quantity_delta
    if new_quantity < inventory.reserved_quantity:
        raise InsufficientStockError(
            variant_id=variant_id,
            requested=-quantity_delta,
            available=inventory.available_quantity,
        )

    inventory.quantity = new_quantity

    movement = InventoryMovement(
        shop_id=variant.shop_id,
        variant_id=variant_id,
        movement_type=InventoryMovementType.ADJUSTMENT,
        quantity=quantity_delta,
        unit_cost=unit_cost,
        reference_type=reference_type,
        reference_id=reference_id,
        notes=notes,
    )
    session.add(movement)
    await session.flush()
    return movement
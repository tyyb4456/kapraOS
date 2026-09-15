"""Purchase service - supplier purchases and their inventory integration.

A purchase is a *business transaction*, not CRUD (`db_arch.md` sections 19-20,
34-35). `create_purchase()` performs the whole thing inside the caller's
transaction:

    validate shop-owned supplier + variants
        -> build the purchase header (server-calculated totals)
        -> create the purchase and its item lines
        -> increase inventory for every line (via the inventory service)
        -> record a PURCHASE inventory movement per line

If anything fails the surrounding transaction rolls back, so a purchase and its
stock/movements either all commit or none do. This module never writes inventory
tables itself - it delegates to `app.services.inventory` so the ledger/counter
invariants stay in one place.

Aggregate root / decimals: quantities are `Decimal` (3 dp), money is `Decimal`
(2 dp). The seller/service always computes `subtotal`, `total` and line totals;
client-supplied totals are not accepted, and the database re-checks the
arithmetic (see `app.models.purchase`).
"""

import uuid
from collections import OrderedDict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventoryMovementType
from app.models.product import ProductVariant
from app.models.purchase import Purchase, PurchaseItem
from app.models.supplier import Supplier
from app.services import inventory as inventory_service

# Column scales from the models - inputs are normalised to these so the
# database's own arithmetic checks agree with what the service computed.
_QUANTITY_SCALE = Decimal("0.001")
_MONEY_SCALE = Decimal("0.01")

# Value stored in `InventoryMovement.reference_type` for purchase-driven stock.
_PURCHASE_REFERENCE = "PURCHASE"


class PurchaseError(Exception):
    """Base class for purchase-domain failures."""


class SupplierNotFoundError(PurchaseError):
    """The supplier does not exist in the caller's shop."""


class VariantNotFoundError(PurchaseError):
    """A purchase line's variant does not exist in the caller's shop."""


class EmptyPurchaseError(PurchaseError):
    """A purchase must contain at least one item."""


class InvalidPurchaseItemError(PurchaseError):
    """A line has an invalid quantity or unit cost."""


class InvalidPurchaseTotalsError(PurchaseError):
    """Discount / paid amount are outside their allowed ranges."""


class DuplicatePurchaseItemError(PurchaseError):
    """The same variant appears twice with different costs.

    See `create_purchase` for how identical variants are combined.
    """


@dataclass(frozen=True)
class PurchaseItemInput:
    """One requested purchase line before the service normalises it."""

    variant_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal


@dataclass(frozen=True)
class _NormalisedItem:
    variant_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal
    total: Decimal


def _normalise_quantity(raw: Decimal) -> Decimal:
    quantity = Decimal(raw).quantize(_QUANTITY_SCALE, rounding=ROUND_HALF_UP)
    if quantity <= 0:
        raise InvalidPurchaseItemError("purchase item quantity must be greater than 0")
    return quantity


def _normalise_cost(raw: Decimal) -> Decimal:
    unit_cost = Decimal(raw).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    if unit_cost < 0:
        raise InvalidPurchaseItemError("purchase item unit_cost must be >= 0")
    return unit_cost


def _normalise_money(raw: Decimal) -> Decimal:
    return Decimal(raw).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)


def _combine_items(
    items: list[PurchaseItemInput],
) -> "OrderedDict[uuid.UUID, _NormalisedItem]":
    """Normalise lines and merge repeats of the same variant.

    The database enforces one line per (purchase, variant), so a variant that
    appears twice is combined into a single line (quantities summed) when the
    cost matches. Two entries for the same variant at *different* costs are
    ambiguous - there is no lot/batch model yet - so they are rejected rather
    than silently picked apart.
    """

    combined: OrderedDict[uuid.UUID, _NormalisedItem] = OrderedDict()
    for item in items:
        quantity = _normalise_quantity(item.quantity)
        unit_cost = _normalise_cost(item.unit_cost)

        existing = combined.get(item.variant_id)
        if existing is None:
            combined[item.variant_id] = _NormalisedItem(
                variant_id=item.variant_id,
                quantity=quantity,
                unit_cost=unit_cost,
                total=_line_total(quantity, unit_cost),
            )
            continue

        if existing.unit_cost != unit_cost:
            raise DuplicatePurchaseItemError(
                f"variant {item.variant_id} appears twice with different "
                f"unit costs ({existing.unit_cost} and {unit_cost})"
            )

        merged_quantity = (existing.quantity + quantity).quantize(
            _QUANTITY_SCALE, rounding=ROUND_HALF_UP
        )
        combined[item.variant_id] = _NormalisedItem(
            variant_id=item.variant_id,
            quantity=merged_quantity,
            unit_cost=unit_cost,
            total=_line_total(merged_quantity, unit_cost),
        )

    return combined


def _line_total(quantity: Decimal, unit_cost: Decimal) -> Decimal:
    return (quantity * unit_cost).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)


async def _get_supplier(
    session: AsyncSession, shop_id: uuid.UUID, supplier_id: uuid.UUID
) -> Supplier:
    """Load a supplier, enforcing that it belongs to `shop_id`."""

    result = await session.execute(
        select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.shop_id == shop_id,
        )
    )
    supplier = result.scalar_one_or_none()
    if supplier is None:
        raise SupplierNotFoundError(
            f"Supplier {supplier_id} not found for shop {shop_id}"
        )
    return supplier


async def _validate_variants(
    session: AsyncSession, shop_id: uuid.UUID, variant_ids: list[uuid.UUID]
) -> None:
    """Ensure every line's variant exists and belongs to the same shop.

    The database-level half of this guarantee is the composite FK on
    `inventory_movements`; this pass rejects the purchase up front, before any
    rows are written, so a cross-tenant line never even creates a purchase.
    """

    result = await session.execute(
        select(ProductVariant.id).where(
            ProductVariant.id.in_(variant_ids),
            ProductVariant.shop_id == shop_id,
        )
    )
    found = set(result.scalars())
    missing = [variant_id for variant_id in variant_ids if variant_id not in found]
    if missing:
        raise VariantNotFoundError(
            f"Variant(s) {missing} not found for shop {shop_id}"
        )


async def create_purchase(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    supplier_id: uuid.UUID,
    items: list[PurchaseItemInput],
    invoice_number: str | None = None,
    discount: Decimal = Decimal("0"),
    paid_amount: Decimal = Decimal("0"),
) -> Purchase:
    """Create a purchase and push every line into inventory, atomically.

    Totals are computed here, never trusted from the caller:

        line total = round(quantity * unit_cost, 2)
        subtotal   = sum(line totals)
        total      = subtotal - discount
        due        = total - paid_amount   (derived, see Purchase.due_amount)

    The caller owns the transaction: this function flushes but never commits,
    so it can be composed into a larger transaction. On success the returned
    `Purchase` has its `items` populated.

    Raises `PurchaseError` subclasses for bad input; any exception propagates to
    the caller, whose rollback undoes the purchase, its items and the stock
    movements together.
    """

    if not items:
        raise EmptyPurchaseError("a purchase must contain at least one item")

    # 1. Validate the supplier is ours before touching anything.
    await _get_supplier(session, shop_id, supplier_id)

    # 2. Normalise lines (rejects bad quantity/cost, merges duplicates).
    combined = _combine_items(items)

    # 3. Validate every variant is ours, before writing any rows.
    await _validate_variants(session, shop_id, list(combined.keys()))

    # 4. Server-side totals.
    subtotal = sum(
        (line.total for line in combined.values()), start=Decimal("0.00")
    ).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)

    discount = _normalise_money(discount)
    if discount < 0 or discount > subtotal:
        raise InvalidPurchaseTotalsError(
            f"discount {discount} must be between 0 and subtotal {subtotal}"
        )

    total = (subtotal - discount).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)

    paid_amount = _normalise_money(paid_amount)
    if paid_amount < 0 or paid_amount > total:
        raise InvalidPurchaseTotalsError(
            f"paid_amount {paid_amount} must be between 0 and total {total}"
        )

    # 5. Purchase header.
    purchase = Purchase(
        shop_id=shop_id,
        supplier_id=supplier_id,
        invoice_number=invoice_number,
        subtotal=subtotal,
        discount=discount,
        total=total,
        paid_amount=paid_amount,
    )
    session.add(purchase)
    await session.flush()

    # 6. Item lines + inventory increase, reusing the Step 3 inventory service.
    for line in combined.values():
        session.add(
            PurchaseItem(
                purchase_id=purchase.id,
                variant_id=line.variant_id,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                total=line.total,
            )
        )

        await inventory_service.add_stock(
            session,
            shop_id=shop_id,
            variant_id=line.variant_id,
            quantity=line.quantity,
            movement_type=InventoryMovementType.PURCHASE,
            unit_cost=line.unit_cost,
            reference_type=_PURCHASE_REFERENCE,
            reference_id=purchase.id,
        )

    await session.flush()
    return purchase
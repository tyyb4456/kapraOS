"""Core customer & supplier return mechanism (deterministic, no AI).

A return references its original document at line granularity:

* customer return → `Sale` + `SaleItem` + returned quantity
* supplier return → `Purchase` + `PurchaseItem` + returned quantity

The backend is authoritative for eligibility, quantities, prices, totals,
inventory movement, receivable/payable impact and accounting. The frontend
only supplies intent (which original item, how much) — never prices,
totals, accounts or movements.

Financial summary:

* Customer return total is net of proportional line + header discounts
  (`gross - line_share - header_share`), so a full return of every line
  sums exactly to `Sale.total`. The AR-vs-cash split is AR-first:
  `ar = min(total, remaining_due)` where
  `remaining_due = (sale.total - sale.paid_amount) - SUM(prev ar)`;
  `cash = total - ar` is a ledger-only cash refund (never a `Payment` row).
  Khata outstanding includes only the AR portion:
  `sales - payments - SUM(return.ar)`. Walk-in returns (no customer) use
  the same ledger split but never touch any Khata.
* Supplier return total is net of the proportional header-discount share.
  The full amount reduces the payable (`Dr AP / Cr Inventory`). V1 models
  no supplier cash refund: an over-return surfaces as a negative payable
  (supplier credit).

Inventory uses the existing service only:

* customer return → `add_stock(CUSTOMER_RETURN, unit_cost=cost_price)`
  (moving-average receipt at the historical cost);
* supplier return → `remove_stock(SUPPLIER_RETURN)` (average-cost removal,
  rejected when stock is insufficient — never negative).

Concurrency: the sale/purchase row is locked `FOR UPDATE`, so two
simultaneous returns for one document serialize and the second sees the
first's persisted lines when checking remaining quantities. Inventory rows
are locked by the inventory service itself.

Transaction ownership is the caller's (routes own commit), like every
other service: this module flushes but never commits.
"""

import uuid
from collections import OrderedDict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventoryMovementType
from app.models.purchase import Purchase, PurchaseItem
from app.models.returns import (
    PurchaseReturn,
    PurchaseReturnItem,
    SaleReturn,
    SaleReturnItem,
)
from app.models.sale import Sale, SaleItem, SaleStatus
from app.services import accounting as accounting_service
from app.services import inventory as inventory_service

_QUANTITY_SCALE = Decimal("0.001")
_MONEY_SCALE = Decimal("0.01")
_ZERO_QTY = Decimal("0.000")
_ZERO_MONEY = Decimal("0.00")

_SALE_RETURN_REFERENCE = "SALE_RETURN"
_PURCHASE_RETURN_REFERENCE = "PURCHASE_RETURN"


class ReturnError(Exception):
    """Base class for return-domain failures."""


class SaleNotFoundError(ReturnError):
    """The sale does not exist in the caller's shop."""


class PurchaseNotFoundError(ReturnError):
    """The purchase does not exist in the caller's shop."""


class SaleItemNotFoundError(ReturnError):
    """A return line references a sale item outside this sale/shop."""


class PurchaseItemNotFoundError(ReturnError):
    """A return line references a purchase item outside this purchase/shop."""


class EmptyReturnError(ReturnError):
    """A return must contain at least one line."""


class InvalidReturnQuantityError(ReturnError):
    """A return line quantity is not positive."""


class ExceedsRemainingQuantityError(ReturnError):
    """The requested quantity exceeds the remaining returnable quantity."""


class SaleNotReturnableError(ReturnError):
    """The sale's status does not allow returns (cancelled/returned)."""


class SaleHasReturnsError(ReturnError):
    """The sale already has returns and cannot be edited/voided."""


class PurchaseHasReturnsError(ReturnError):
    """The purchase already has returns and cannot be edited/voided."""


@dataclass(frozen=True)
class SaleReturnLineInput:
    """One requested customer-return line (intent only — no pricing)."""

    sale_item_id: uuid.UUID
    quantity: Decimal


@dataclass(frozen=True)
class PurchaseReturnLineInput:
    """One requested supplier-return line (intent only — no pricing)."""

    purchase_item_id: uuid.UUID
    quantity: Decimal


def _normalise_quantity(raw: Decimal) -> Decimal:
    quantity = Decimal(raw).quantize(_QUANTITY_SCALE, rounding=ROUND_HALF_UP)
    if quantity <= 0:
        raise InvalidReturnQuantityError(
            f"return quantity must be greater than 0, got {quantity}"
        )
    return quantity


def _money(raw: Decimal | int | None) -> Decimal:
    if raw is None:
        return _ZERO_MONEY
    return Decimal(raw).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)


def _combine_sale_lines(
    lines: list[SaleReturnLineInput],
) -> "OrderedDict[uuid.UUID, Decimal]":
    combined: OrderedDict[uuid.UUID, Decimal] = OrderedDict()
    for line in lines:
        qty = _normalise_quantity(line.quantity)
        combined[line.sale_item_id] = (
            combined.get(line.sale_item_id, _ZERO_QTY) + qty
        ).quantize(_QUANTITY_SCALE, rounding=ROUND_HALF_UP)
    return combined


def _combine_purchase_lines(
    lines: list[PurchaseReturnLineInput],
) -> "OrderedDict[uuid.UUID, Decimal]":
    combined: OrderedDict[uuid.UUID, Decimal] = OrderedDict()
    for line in lines:
        qty = _normalise_quantity(line.quantity)
        combined[line.purchase_item_id] = (
            combined.get(line.purchase_item_id, _ZERO_QTY) + qty
        ).quantize(_QUANTITY_SCALE, rounding=ROUND_HALF_UP)
    return combined


async def _get_sale_for_return(
    session: AsyncSession, shop_id: uuid.UUID, sale_id: uuid.UUID
) -> Sale:
    sale = (
        await session.execute(
            select(Sale)
            .where(Sale.id == sale_id, Sale.shop_id == shop_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if sale is None:
        raise SaleNotFoundError(f"Sale {sale_id} not found for shop {shop_id}")
    if sale.status not in (SaleStatus.COMPLETED, SaleStatus.PARTIAL):
        raise SaleNotReturnableError(
            f"Sale {sale_id} is {sale.status.value}; only completed/partial sales can be returned"
        )
    await session.refresh(sale, attribute_names=["items"])
    return sale


async def _get_purchase_for_return(
    session: AsyncSession, shop_id: uuid.UUID, purchase_id: uuid.UUID
) -> Purchase:
    purchase = (
        await session.execute(
            select(Purchase)
            .where(Purchase.id == purchase_id, Purchase.shop_id == shop_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if purchase is None:
        raise PurchaseNotFoundError(
            f"Purchase {purchase_id} not found for shop {shop_id}"
        )
    await session.refresh(purchase, attribute_names=["items"])
    return purchase


async def _sale_returned_quantities(
    session: AsyncSession, sale_id: uuid.UUID
) -> dict[uuid.UUID, Decimal]:
    rows = (
        await session.execute(
            select(
                SaleReturnItem.sale_item_id,
                func.coalesce(func.sum(SaleReturnItem.quantity), 0),
            )
            .join(SaleReturn, SaleReturn.id == SaleReturnItem.return_id)
            .where(SaleReturn.sale_id == sale_id)
            .group_by(SaleReturnItem.sale_item_id)
        )
    ).all()
    return {row[0]: Decimal(row[1]) for row in rows}


async def _purchase_returned_quantities(
    session: AsyncSession, purchase_id: uuid.UUID
) -> dict[uuid.UUID, Decimal]:
    rows = (
        await session.execute(
            select(
                PurchaseReturnItem.purchase_item_id,
                func.coalesce(func.sum(PurchaseReturnItem.quantity), 0),
            )
            .join(PurchaseReturn, PurchaseReturn.id == PurchaseReturnItem.return_id)
            .where(PurchaseReturn.purchase_id == purchase_id)
            .group_by(PurchaseReturnItem.purchase_item_id)
        )
    ).all()
    return {row[0]: Decimal(row[1]) for row in rows}


async def _sale_previous_ar_total(session: AsyncSession, sale_id: uuid.UUID) -> Decimal:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(SaleReturn.ar_amount), 0)).where(
                SaleReturn.sale_id == sale_id
            )
        )
    ).scalar()
    return _money(total)


async def get_remaining_sale_quantities(
    session: AsyncSession, *, shop_id: uuid.UUID, sale_id: uuid.UUID
) -> dict[uuid.UUID, Decimal]:
    """Remaining returnable quantity per sale item (authoritative, DB-side)."""

    sale = await _get_sale_for_return(session, shop_id, sale_id)
    returned = await _sale_returned_quantities(session, sale.id)
    remaining: dict[uuid.UUID, Decimal] = {}
    for item in sale.items:
        already = returned.get(item.id, _ZERO_QTY)
        remaining[item.id] = Decimal(item.quantity) - Decimal(already)
    return remaining


async def get_remaining_purchase_quantities(
    session: AsyncSession, *, shop_id: uuid.UUID, purchase_id: uuid.UUID
) -> dict[uuid.UUID, Decimal]:
    """Remaining returnable quantity per purchase item (authoritative)."""

    purchase = await _get_purchase_for_return(session, shop_id, purchase_id)
    returned = await _purchase_returned_quantities(session, purchase.id)
    remaining: dict[uuid.UUID, Decimal] = {}
    for item in purchase.items:
        already = returned.get(item.id, _ZERO_QTY)
        remaining[item.id] = Decimal(item.quantity) - Decimal(already)
    return remaining


def _proportional_share(
    total_discount: Decimal, portion: Decimal, whole: Decimal
) -> Decimal:
    if whole <= 0 or total_discount <= 0:
        return _ZERO_MONEY
    return _money(total_discount * (portion / whole))


@dataclass(frozen=True)
class _PreparedSaleLine:
    item: SaleItem
    quantity: Decimal
    discount: Decimal
    total: Decimal
    cogs: Decimal


@dataclass(frozen=True)
class _PreparedPurchaseLine:
    item: PurchaseItem
    quantity: Decimal
    discount: Decimal
    total: Decimal


async def create_sale_return(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    sale_id: uuid.UUID,
    lines: list[SaleReturnLineInput],
    notes: str | None = None,
) -> SaleReturn:
    """Create a customer return against `sale_id`, atomically.

    Validates tenant ownership, sale status, and per-line remaining
    quantities (original minus already-returned, from persisted rows —
    never trusted from the caller). Prices come from `SaleItem.unit_price`
    / `cost_price`; the request carries only item ids + quantities.
    """

    if not lines:
        raise EmptyReturnError("a return must contain at least one line")
    if notes is not None and len(notes) > 500:
        raise InvalidReturnQuantityError("notes must be at most 500 characters")

    sale = await _get_sale_for_return(session, shop_id, sale_id)
    combined = _combine_sale_lines(lines)

    by_item: dict[uuid.UUID, SaleItem] = {item.id: item for item in sale.items}
    missing = [str(k) for k in combined if k not in by_item]
    if missing:
        raise SaleItemNotFoundError(
            f"Sale item(s) {missing} not found for sale {sale_id}"
        )

    returned = await _sale_returned_quantities(session, sale.id)

    # Header-discount allocation base (proportional to line totals).
    subtotal = _money(sale.subtotal)
    header_discount = _money(sale.discount)

    # Build authoritative line values first (no writes yet).
    prepared: list[_PreparedSaleLine] = []
    for sale_item_id, qty in combined.items():
        item = by_item[sale_item_id]
        original_qty = Decimal(item.quantity)
        already = Decimal(returned.get(sale_item_id, _ZERO_QTY))
        remaining = original_qty - already
        if qty > remaining:
            raise ExceedsRemainingQuantityError(
                f"return quantity {qty} exceeds remaining {remaining} "
                f"for sale item {sale_item_id}"
            )
        gross = _money(Decimal(item.unit_price) * qty)
        # Proportional line-discount share.
        line_share = _proportional_share(Decimal(item.discount), qty, original_qty)
        # Proportional header-discount share via this line's net weight.
        header_share = _ZERO_MONEY
        if header_discount > 0 and subtotal > 0:
            line_weight = _money(Decimal(item.total))
            alloc = _money(header_discount * (line_weight / subtotal))
            header_share = _proportional_share(alloc, qty, original_qty)
        discount_share = _money(line_share + header_share)
        if discount_share > gross:
            discount_share = gross
        line_total = _money(gross - discount_share)
        cogs = _money(Decimal(item.cost_price) * qty)
        prepared.append(
            _PreparedSaleLine(
                item=item,
                quantity=qty,
                discount=discount_share,
                total=line_total,
                cogs=cogs,
            )
        )

    total_refund = _money(sum((p.total for p in prepared), start=Decimal("0")))
    cogs_total = _money(sum((p.cogs for p in prepared), start=Decimal("0")))
    if total_refund <= 0:
        raise InvalidReturnQuantityError("return total must be greater than 0")

    # AR-first split against the sale's remaining due.
    sale_due = _money(Decimal(sale.total) - Decimal(sale.paid_amount))
    previous_ar = await _sale_previous_ar_total(session, sale.id)
    remaining_due = sale_due - previous_ar
    if remaining_due < 0:
        remaining_due = _ZERO_MONEY
    ar_amount = min(total_refund, remaining_due)
    cash_refund = _money(total_refund - ar_amount)

    sale_return = SaleReturn(
        id=uuid.uuid4(),
        shop_id=shop_id,
        sale_id=sale.id,
        customer_id=sale.customer_id,
        total_amount=total_refund,
        ar_amount=ar_amount,
        cash_refund=cash_refund,
        cogs_amount=cogs_total,
        notes=notes,
    )
    session.add(sale_return)
    sale_return.items = []
    await session.flush()

    for p in prepared:
        item = p.item
        qty = p.quantity
        sale_return.items.append(
            SaleReturnItem(
                return_id=sale_return.id,
                sale_item_id=item.id,
                variant_id=item.variant_id,
                shop_id=shop_id,
                quantity=qty,
                unit_price=Decimal(item.unit_price),
                cost_price=Decimal(item.cost_price),
                discount=p.discount,
                total=p.total,
            )
        )
        await inventory_service.add_stock(
            session,
            shop_id=shop_id,
            variant_id=item.variant_id,
            quantity=qty,
            movement_type=InventoryMovementType.CUSTOMER_RETURN,
            unit_cost=Decimal(item.cost_price),
            reference_type=_SALE_RETURN_REFERENCE,
            reference_id=sale_return.id,
            notes=notes or f"Customer return {sale_return.id} for sale {sale.id}",
        )

    await session.flush()
    await accounting_service.post_sale_return(
        session,
        sale=sale,
        sale_return_id=sale_return.id,
        total_refund=total_refund,
        ar_amount=ar_amount,
        cash_refund=cash_refund,
        cogs_amount=cogs_total,
        description=notes or f"Customer return {sale_return.id} for sale {sale.id}",
    )
    return sale_return


async def create_purchase_return(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    purchase_id: uuid.UUID,
    lines: list[PurchaseReturnLineInput],
    notes: str | None = None,
) -> PurchaseReturn:
    """Create a supplier return against `purchase_id`, atomically.

    Validates tenant ownership and per-line remaining quantities. Costs come
    from `PurchaseItem.unit_cost`; the request carries only item ids +
    quantities. Supplier returns check physical stock via `remove_stock`
    (rejected when insufficient — never negative inventory).
    """

    if not lines:
        raise EmptyReturnError("a return must contain at least one line")
    if notes is not None and len(notes) > 500:
        raise InvalidReturnQuantityError("notes must be at most 500 characters")

    purchase = await _get_purchase_for_return(session, shop_id, purchase_id)
    combined = _combine_purchase_lines(lines)

    by_item: dict[uuid.UUID, PurchaseItem] = {item.id: item for item in purchase.items}
    missing = [str(k) for k in combined if k not in by_item]
    if missing:
        raise PurchaseItemNotFoundError(
            f"Purchase item(s) {missing} not found for purchase {purchase_id}"
        )

    returned = await _purchase_returned_quantities(session, purchase.id)
    subtotal = _money(purchase.subtotal)
    header_discount = _money(purchase.discount)

    prepared: list[_PreparedPurchaseLine] = []
    for purchase_item_id, qty in combined.items():
        item = by_item[purchase_item_id]
        original_qty = Decimal(item.quantity)
        already = Decimal(returned.get(purchase_item_id, _ZERO_QTY))
        remaining = original_qty - already
        if qty > remaining:
            raise ExceedsRemainingQuantityError(
                f"return quantity {qty} exceeds remaining {remaining} "
                f"for purchase item {purchase_item_id}"
            )
        gross = _money(Decimal(item.unit_cost) * qty)
        header_share = _ZERO_MONEY
        if header_discount > 0 and subtotal > 0:
            alloc = _money(header_discount * (Decimal(item.total) / subtotal))
            header_share = _proportional_share(alloc, qty, original_qty)
        if header_share > gross:
            header_share = gross
        line_total = _money(gross - header_share)
        prepared.append(
            _PreparedPurchaseLine(
                item=item,
                quantity=qty,
                discount=header_share,
                total=line_total,
            )
        )

    total_amount = _money(sum((p.total for p in prepared), start=Decimal("0")))
    if total_amount <= 0:
        raise InvalidReturnQuantityError("return total must be greater than 0")

    purchase_return = PurchaseReturn(
        id=uuid.uuid4(),
        shop_id=shop_id,
        purchase_id=purchase.id,
        supplier_id=purchase.supplier_id,
        total_amount=total_amount,
        notes=notes,
    )
    session.add(purchase_return)
    purchase_return.items = []
    await session.flush()

    for p in prepared:
        item = p.item
        qty = p.quantity
        purchase_return.items.append(
            PurchaseReturnItem(
                return_id=purchase_return.id,
                purchase_item_id=item.id,
                variant_id=item.variant_id,
                shop_id=shop_id,
                quantity=qty,
                unit_cost=Decimal(item.unit_cost),
                discount=p.discount,
                total=p.total,
            )
        )
        await inventory_service.remove_stock(
            session,
            shop_id=shop_id,
            variant_id=item.variant_id,
            quantity=qty,
            movement_type=InventoryMovementType.SUPPLIER_RETURN,
            unit_cost=Decimal(item.unit_cost),
            reference_type=_PURCHASE_RETURN_REFERENCE,
            reference_id=purchase_return.id,
            notes=notes or f"Supplier return {purchase_return.id} for purchase {purchase.id}",
        )

    await session.flush()
    await accounting_service.post_purchase_return(
        session,
        shop_id=shop_id,
        purchase_return_id=purchase_return.id,
        total_amount=total_amount,
        description=notes or f"Supplier return {purchase_return.id} for purchase {purchase.id}",
    )
    return purchase_return


async def assert_sale_has_no_returns(session: AsyncSession, sale_id: uuid.UUID) -> None:
    """Raise `SaleHasReturnsError` when the sale already has return rows."""

    count = (
        await session.execute(
            select(func.count()).select_from(SaleReturn).where(SaleReturn.sale_id == sale_id)
        )
    ).scalar_one()
    if count:
        raise SaleHasReturnsError(f"Sale {sale_id} already has returns and cannot be edited/voided")


async def assert_purchase_has_no_returns(
    session: AsyncSession, purchase_id: uuid.UUID
) -> None:
    """Raise `PurchaseHasReturnsError` when the purchase already has returns."""

    count = (
        await session.execute(
            select(func.count())
            .select_from(PurchaseReturn)
            .where(PurchaseReturn.purchase_id == purchase_id)
        )
    ).scalar_one()
    if count:
        raise PurchaseHasReturnsError(
            f"Purchase {purchase_id} already has returns and cannot be edited/voided"
        )


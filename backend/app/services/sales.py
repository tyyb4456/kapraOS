"""Sales service - the POS business transaction.

A sale is a *business transaction*, not CRUD (`db_arch.md` sections 17-18,
24, 34). `create_sale()` performs the whole thing inside the caller's
transaction:

    validate shop / customer / variants
        -> build the sale header (server-calculated totals)
        -> snapshot each line's weighted-average cost
        -> create the sale and its item lines
        -> take stock out for every line (via the inventory service)
        -> record SALE inventory movements linked to the sale
        -> record the payment rows
        -> derive paid/due and the payment status
        -> post the revenue group and the COGS group

If anything fails the surrounding transaction rolls back, so the sale, its
items, the stock change, the movements and the payments either all commit or
none do. This module never writes inventory tables itself - it delegates to
`app.services.inventory`, which owns stock mutation and row locking. Sales add
no locking of their own; `remove_stock()`'s `SELECT ... FOR UPDATE` is what
makes concurrent sales safe against overselling.

Decimals: quantities are `Decimal` (3 dp), money is `Decimal` (2 dp), and
costs are snapshotted from the inventory service's `Decimal` weighted average.
Client-supplied totals are never trusted; the database re-checks the line and
header arithmetic (see `app.models.sale`).
"""

import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.inventory import InventoryMovementType
from app.models.payment import Payment, PaymentMethod
from app.models.product import ProductVariant
from app.models.sale import Sale, SaleItem, SaleStatus
from app.models.shop import Shop
from app.services import accounting as accounting_service
from app.services import inventory as inventory_service

# Column scales from the models - inputs are normalised to these so the
# database's own arithmetic checks agree with what the service computed.
_QUANTITY_SCALE = Decimal("0.001")
_MONEY_SCALE = Decimal("0.01")
_COST_SCALE = Decimal("0.01")

# Value stored in `InventoryMovement.reference_type` for sale-driven stock.
_SALE_REFERENCE = "SALE"


class SaleError(Exception):
    """Base class for sales-domain failures."""


class ShopNotFoundError(SaleError):
    """The shop does not exist."""


class CustomerNotFoundError(SaleError):
    """The customer does not exist in the caller's shop."""


class VariantNotFoundError(SaleError):
    """A sale line's variant does not exist in the caller's shop."""


class EmptySaleError(SaleError):
    """A sale must contain at least one item."""


class InvalidSaleItemError(SaleError):
    """A line has an invalid quantity, unit price or discount."""


class InvalidSaleTotalsError(SaleError):
    """Discount / payment amounts are outside their allowed ranges."""


class DuplicateSaleItemError(SaleError):
    """The same variant appears twice with different price/discount.

    See `_combine_items` for how identical variants are combined.
    """


@dataclass(frozen=True)
class SaleItemInput:
    """One requested sale line before the service normalises it."""

    variant_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal = Decimal("0")


@dataclass(frozen=True)
class PaymentInput:
    """One requested payment against a sale."""

    amount: Decimal
    method: PaymentMethod
    reference: str | None = None


@dataclass(frozen=True)
class _NormalisedItem:
    variant_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    total: Decimal
    cost_price: Decimal = field(default=Decimal("0"))


def _normalise_quantity(raw: Decimal) -> Decimal:
    quantity = Decimal(raw).quantize(_QUANTITY_SCALE, rounding=ROUND_HALF_UP)
    if quantity <= 0:
        raise InvalidSaleItemError("sale item quantity must be greater than 0")
    return quantity


def _normalise_money(raw: Decimal) -> Decimal:
    return Decimal(raw).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)


def _line_total(quantity: Decimal, unit_price: Decimal, discount: Decimal) -> Decimal:
    return (quantity * unit_price - discount).quantize(
        _MONEY_SCALE, rounding=ROUND_HALF_UP
    )


def _combine_items(
    items: list[SaleItemInput],
) -> "OrderedDict[uuid.UUID, _NormalisedItem]":
    """Normalise lines and merge repeats of the same variant.

    The database enforces one line per (sale, variant), so a variant that
    appears twice is combined into a single line (quantities summed) only when
    price and discount match. Two entries for the same variant at *different*
    prices are ambiguous, so they are rejected rather than silently mixed.
    """

    combined: OrderedDict[uuid.UUID, _NormalisedItem] = OrderedDict()
    for item in items:
        quantity = _normalise_quantity(item.quantity)
        unit_price = _normalise_money(item.unit_price)
        if unit_price < 0:
            raise InvalidSaleItemError("sale item unit_price must be >= 0")
        discount = _normalise_money(item.discount)
        if discount < 0:
            raise InvalidSaleItemError("sale item discount must be >= 0")
        if discount > (quantity * unit_price).quantize(
            _MONEY_SCALE, rounding=ROUND_HALF_UP
        ):
            raise InvalidSaleItemError(
                "sale item discount cannot exceed quantity * unit_price"
            )

        existing = combined.get(item.variant_id)
        if existing is None:
            combined[item.variant_id] = _NormalisedItem(
                variant_id=item.variant_id,
                quantity=quantity,
                unit_price=unit_price,
                discount=discount,
                total=_line_total(quantity, unit_price, discount),
            )
            continue

        if existing.unit_price != unit_price or existing.discount != discount:
            raise DuplicateSaleItemError(
                f"variant {item.variant_id} appears twice with different "
                "prices or discounts"
            )

        merged_quantity = (existing.quantity + quantity).quantize(
            _QUANTITY_SCALE, rounding=ROUND_HALF_UP
        )
        combined[item.variant_id] = _NormalisedItem(
            variant_id=item.variant_id,
            quantity=merged_quantity,
            unit_price=unit_price,
            discount=discount,
            total=_line_total(merged_quantity, unit_price, discount),
        )

    return combined


async def _get_shop(session: AsyncSession, shop_id: uuid.UUID) -> Shop:
    shop = await session.get(Shop, shop_id)
    if shop is None:
        raise ShopNotFoundError(f"Shop {shop_id} not found")
    return shop


async def _get_customer(
    session: AsyncSession, shop_id: uuid.UUID, customer_id: uuid.UUID
) -> Customer:
    """Load a customer, enforcing that it belongs to `shop_id`.

    A customer from another shop is treated as not found rather than
    readable, so no cross-tenant reference can be created.
    """

    result = await session.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.shop_id == shop_id,
        )
    )
    customer = result.scalar_one_or_none()
    if customer is None:
        raise CustomerNotFoundError(
            f"Customer {customer_id} not found for shop {shop_id}"
        )
    return customer


async def _validate_variants(
    session: AsyncSession, shop_id: uuid.UUID, variant_ids: list[uuid.UUID]
) -> None:
    """Ensure every line's variant exists and belongs to the same shop.

    Rejected up front, before any rows are written, so a cross-tenant line
    never even creates a sale. The database-level half of the guarantee is the
    composite FK on `inventory_movements`.
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


def _normalise_payments(payments: list[PaymentInput]) -> list[PaymentInput]:
    normalised: list[PaymentInput] = []
    for payment in payments:
        amount = _normalise_money(payment.amount)
        if amount <= 0:
            raise InvalidSaleTotalsError("payment amount must be greater than 0")
        normalised.append(
            PaymentInput(
                amount=amount,
                method=payment.method,
                reference=payment.reference,
            )
        )
    return normalised


def _format_invoice_number(seq: int) -> str:
    """Format a sequential sale invoice number: `INV-000001`."""
    return f"INV-{seq:06d}"


async def _allocate_invoice_number(
    session: AsyncSession, shop_id: uuid.UUID
) -> str:
    """Allocate the next free invoice number for a shop.

    Every sale must carry an invoice number. When the caller does not supply
    one, the next `INV-000001`-style number is derived from the existing
    sales of that shop (max numeric suffix + 1, skipping collisions).

    Concurrency: the shop row is locked with `SELECT id ... FOR UPDATE`
    (raw SQL on `id` only, so it works whether or not older/newer schema
    extras exist) which serializes concurrent POS checkouts for the same
    shop. The partial unique index `uq_sales_shop_invoice_number` remains
    the final guard - on the rare race that still slips through, the
    surrounding transaction fails with an IntegrityError and the caller
    can retry.
    """

    import re

    # Serialize per shop without depending on any extra shops columns.
    shop_row = (
        await session.execute(
            text("SELECT id FROM shops WHERE id = :shop_id FOR UPDATE"),
            {"shop_id": shop_id},
        )
    ).first()
    if shop_row is None:
        raise ShopNotFoundError(f"Shop {shop_id} not found")

    rows = (
        await session.execute(
            select(Sale.invoice_number).where(
                Sale.shop_id == shop_id,
                Sale.invoice_number.is_not(None),
            )
        )
    ).all()
    existing = {r[0] for r in rows if r[0]}
    max_seq = 0
    pattern = re.compile(r"^INV-(\d+)$")
    for inv in existing:
        m = pattern.match(inv or "")
        if m:
            try:
                max_seq = max(max_seq, int(m.group(1)))
            except ValueError:
                pass

    seq = max_seq + 1 if max_seq else 1
    while True:
        candidate = _format_invoice_number(seq)
        if candidate not in existing:
            # Double-check against uncommitted / concurrent rows visible
            # in this transaction.
            exists = await session.execute(
                select(Sale.id)
                .where(
                    Sale.shop_id == shop_id,
                    Sale.invoice_number == candidate,
                )
                .limit(1)
            )
            if exists.scalar_one_or_none() is None:
                return candidate
        seq += 1


async def create_sale(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    items: list[SaleItemInput],
    customer_id: uuid.UUID | None = None,
    invoice_number: str | None = None,
    discount: Decimal = Decimal("0"),
    payments: list[PaymentInput] | None = None,
) -> Sale:
    """Create a sale, decrement stock and record payments, atomically.

    Totals are computed here, never trusted from the caller:

        line total = round(quantity * unit_price, 2) - line discount
        subtotal   = sum(line totals)
        total      = subtotal - header discount
        paid       = sum(payment amounts)
        due        = total - paid          (derived, see Sale.due_amount)

    `cost_price` on each line is the variant's current weighted-average
    inventory cost, snapshotted at this moment and never recalculated.

    Every sale gets an invoice number: when `invoice_number` is None or blank
    the next `INV-000001`-style number is allocated from the shop's existing
    sales (see `_allocate_invoice_number`). An explicit value is kept as-is
    so pre-printed books still work.

    The caller owns the transaction: this function flushes but never commits,
    so it composes into a larger transaction. On success the returned `Sale`
    has its `items` and `payments` populated.

    Raises `SaleError` subclasses for bad input; any exception propagates to
    the caller, whose rollback undoes the sale, its items, the stock change,
    the movements and the payments together.
    """

    if not items:
        raise EmptySaleError("a sale must contain at least one item")

    # Normalise the invoice number up front: blank/whitespace means "auto".
    if invoice_number is not None:
        invoice_number = invoice_number.strip() or None
        if invoice_number is not None and len(invoice_number) > 50:
            raise InvalidSaleTotalsError(
                "invoice_number must be at most 50 characters"
            )

    # 1. Validate the shop and (optional) customer before touching anything.
    await _get_shop(session, shop_id)
    if customer_id is not None:
        await _get_customer(session, shop_id, customer_id)

    # 2. Normalise lines (rejects bad input, merges duplicates).
    combined = _combine_items(items)

    # 3. Validate every variant is ours, before writing any rows.
    await _validate_variants(session, shop_id, list(combined.keys()))

    # 4. Server-side totals.
    subtotal = sum(
        (line.total for line in combined.values()), start=Decimal("0.00")
    ).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)

    discount = _normalise_money(discount)
    if discount < 0 or discount > subtotal:
        raise InvalidSaleTotalsError(
            f"discount {discount} must be between 0 and subtotal {subtotal}"
        )
    total = (subtotal - discount).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)

    normalised_payments = _normalise_payments(payments or [])
    paid_amount = sum(
        (payment.amount for payment in normalised_payments), start=Decimal("0.00")
    ).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    if paid_amount > total:
        raise InvalidSaleTotalsError(
            f"paid_amount {paid_amount} cannot exceed total {total}"
        )

    status = SaleStatus.COMPLETED if paid_amount >= total else SaleStatus.PARTIAL

    # 4b. Every sale leaves with an invoice number. Auto-allocate when the
    # caller did not supply one, after validation but before any rows are
    # written so a failure still rolls back the counter bump with everything
    # else (the caller owns the transaction).
    if invoice_number is None:
        invoice_number = await _allocate_invoice_number(session, shop_id)

    # 5. Sale header. The UUID is assigned here (not left to the flush) so the
    # inventory movements and child rows can reference it immediately, and the
    # `items`/`payments` collections are populated while the instance is still
    # transient - so no async lazy-load is ever triggered by a mid-loop flush.
    sale = Sale(
        id=uuid.uuid4(),
        shop_id=shop_id,
        customer_id=customer_id,
        invoice_number=invoice_number,
        subtotal=subtotal,
        discount=discount,
        total=total,
        paid_amount=paid_amount,
        status=status,
    )
    session.add(sale)
    # Initialise the child collections now, while the instance is still
    # transient: the inventory service flushes mid-loop, and appending to an
    # unloaded collection on a persistent instance would trigger an async
    # lazy-load (which cannot happen inside a sync attribute access).
    sale.items = []
    sale.payments = []

    # 6. Item lines: snapshot cost, persist the line, then take the stock out
    # through the inventory service (which owns the movement + row locking).
    for line in combined.values():
        cost_price = (
            await inventory_service.get_weighted_average_cost(
                session, shop_id=shop_id, variant_id=line.variant_id
            )
        ).quantize(_COST_SCALE, rounding=ROUND_HALF_UP)

        sale.items.append(
            SaleItem(
                variant_id=line.variant_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
                cost_price=cost_price,
                discount=line.discount,
                total=line.total,
            )
        )

        await inventory_service.remove_stock(
            session,
            shop_id=shop_id,
            variant_id=line.variant_id,
            quantity=line.quantity,
            movement_type=InventoryMovementType.SALE,
            unit_cost=cost_price,
            reference_type=_SALE_REFERENCE,
            reference_id=sale.id,
        )

    # 7. Payment rows. A customer payment carries the sale's customer (may be
    # NULL for a walk-in); the sale linkage and shop are always present.
    for payment in normalised_payments:
        sale.payments.append(
            Payment(
                shop_id=shop_id,
                customer_id=customer_id,
                sale_id=sale.id,
                amount=payment.amount,
                method=payment.method,
                reference=payment.reference,
            )
        )

    await session.flush()

    # 8. Accounting representation of the same event (Step 8 + Step 10).
    # Posting is part of the same transaction, so an accounting failure rolls
    # the sale back with everything else; both postings are idempotent, so a
    # replay writes nothing new. COGS is the historical cost snapshot taken in
    # step 6 - never recomputed from current inventory value.
    await accounting_service.post_sale(session, sale=sale)
    await accounting_service.post_cogs(session, sale=sale)

    return sale

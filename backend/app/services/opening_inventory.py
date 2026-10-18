"""Opening inventory service - the one-time starting stock of a shop.

When a shop adopts KapraOS it already has goods on the shelf. Those goods are
not a purchase: there is no supplier, no invoice and no payable. They are the
point at which the shop's books start, and that is exactly what this module
records - once per variant, ever.

Costing policy (deliberate, V1, "Option A"). The pooled moving-average model
cannot represent stock with no basis, so **every posted line must carry a
strictly positive unit cost whose status is `known` or `estimated`**. A
missing, `unknown`, zero or negative cost is rejected outright - it is never
silently converted to zero, and unknown-cost inventory is never added.
`unknown` exists only as an import/preview resolution state. Do not change the
WAC algorithm, COGS snapshots or inventory schema semantics here; posting
delegates every stock write to `app.services.inventory.add_stock()` with
movement type `OPENING`.

Two operations, matching the two-step flow the API exposes later:

* :func:`preview_opening_inventory` - a read-only dry run. It validates every
  proposed line, warns (without rejecting) when a variant already has stock
  or movement history, prices each line with the same rounding the database
  will check, and projects the post-opening weighted-average cost using the
  exact formula `inventory_service._apply_receipt_cost()` uses. It writes
  nothing at all - not even a lazily-created `Inventory` row.
* :func:`post_opening_inventory` - the real write path. It re-validates every
  line from scratch (preview results are never trusted as authorization),
  creates the immutable batch and its entries, pushes each line through
  `add_stock()`, and posts the aggregate value through the accounting service
  as `Debit Inventory (1200) / Credit Opening Balance Equity (3100)`. No
  purchase, supplier, payable or historical purchase record is ever created.

Transaction ownership follows every other service in this project: nothing
here commits or rolls back. The API route opens the transaction; a failure
anywhere (validation, inventory, accounting) propagates, and the caller's
rollback discards the batch, its entries, the stock movements and any ledger
lines together.

Idempotency
-----------

`OpeningInventoryBatch` carries `UNIQUE(shop_id, idempotency_key)`, and
posting serializes per shop by locking the shop row (`SELECT ... FOR UPDATE`)
*before* the check-then-insert, so two concurrent submissions of the same key
cannot race between the `SELECT` and the `INSERT`:

* same key + same logical payload (the normalized
  `variant -> (quantity, unit_cost, cost_status)` map) returns the committed
  batch untouched - no second stock receipt, no second ledger posting;
* same key + different payload raises
  :class:`OpeningInventoryIdempotencyConflictError`.

The unique constraint remains the final backstop for a writer that bypasses
the lock. A violation surfaces as `IntegrityError` after PostgreSQL has
aborted the transaction; this service deliberately does not catch it and
continue, because reading afterwards would be unsafe. The route owning the
transaction must roll back and may retry the same request - the retry then
finds the winner and returns it. The service never commits to work around
this.
"""

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.inventory import Inventory, InventoryMovement, InventoryMovementType
from app.models.opening_inventory import (
    OpeningInventoryBatch,
    OpeningInventoryCostStatus,
    OpeningInventoryEntry,
    OpeningInventorySource,
)
from app.models.product import Product, ProductVariant
from app.services import accounting as accounting_service
from app.services import inventory as inventory_service

# Column scales from the models - inputs are normalised to these so the
# database's own arithmetic checks agree with what the service computed.
_QUANTITY_SCALE = Decimal("0.001")
_MONEY_SCALE = Decimal("0.01")
# `Inventory.weighted_average_cost` is NUMERIC(14,4); the projection must use
# the same scale and rounding as `inventory_service._apply_receipt_cost()`.
_COST_SCALE = Decimal("0.0001")
_ZERO = Decimal("0.00")

# Value stored in `InventoryMovement.reference_type` for opening-driven stock.
_OPENING_REFERENCE = "OPENING"

_MAX_KEY_LENGTH = 64
_MAX_NOTES_LENGTH = 500


class OpeningInventoryError(Exception):
    """Base class for opening-inventory-domain failures."""


class ShopNotFoundError(OpeningInventoryError):
    """The shop does not exist."""


class EmptyOpeningInventoryError(OpeningInventoryError):
    """An opening batch must contain at least one line."""


class InvalidOpeningInventoryKeyError(OpeningInventoryError):
    """The idempotency key is missing, blank or too long."""


class InvalidOpeningInventoryRequestError(OpeningInventoryError):
    """Batch-level metadata (source, notes) is invalid."""


class InvalidOpeningInventoryLineError(OpeningInventoryError):
    """A line's quantity, unit cost or cost status is invalid."""


class DuplicateOpeningInventoryVariantError(OpeningInventoryError):
    """The same variant appears more than once in one request."""


class VariantNotFoundError(OpeningInventoryError):
    """A line's variant does not exist in the caller's shop."""


class VariantAlreadyHasOpeningError(OpeningInventoryError):
    """A variant may have only one opening entry ever - this one has one."""


class OpeningInventoryIdempotencyConflictError(OpeningInventoryError):
    """The key was already used for a different opening request."""


@dataclass(frozen=True)
class OpeningInventoryLineInput:
    """One proposed opening line before the service normalises it.

    `unit_cost` must be strictly positive and `cost_status` must be `known`
    or `estimated`; `unknown` is a resolution state for imports/preview and
    can never be posted.
    """

    variant_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal | None = None
    cost_status: OpeningInventoryCostStatus | str | None = None


@dataclass(frozen=True)
class OpeningInventoryPreviewLine:
    """One line's read-only validation result.

    `errors` make the line unpostable; `warnings` never do. `value` and
    `projected_weighted_average_cost` are only populated for an error-free
    line, because a projection for an unpostable line would be misleading.
    """

    index: int
    variant_id: uuid.UUID | None
    sku: str | None
    product_name: str | None
    unit: str | None
    quantity: Decimal | None
    unit_cost: Decimal | None
    cost_status: OpeningInventoryCostStatus | None
    value: Decimal | None
    current_quantity: Decimal
    current_weighted_average_cost: Decimal
    projected_weighted_average_cost: Decimal | None
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class OpeningInventoryPreview:
    """The whole submission's read-only validation result.

    `total_value` and the counts cover only the error-free lines; `valid` is
    True only when every line passed.
    """

    lines: tuple[OpeningInventoryPreviewLine, ...]
    valid: bool
    total_value: Decimal
    entry_count: int
    estimated_entry_count: int


@dataclass
class _LineParse:
    """Field-level parsing result - errors are collected, never raised."""

    index: int
    variant_id: uuid.UUID | None = None
    quantity: Decimal | None = None
    unit_cost: Decimal | None = None
    cost_status: OpeningInventoryCostStatus | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def value(self) -> Decimal | None:
        if self.quantity is None or self.unit_cost is None:
            return None
        return (self.quantity * self.unit_cost).quantize(
            _MONEY_SCALE, rounding=ROUND_HALF_UP
        )


@dataclass(frozen=True)
class _NormalisedLine:
    index: int
    variant_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal
    cost_status: OpeningInventoryCostStatus
    value: Decimal


# --------------------------------------------------------------------------
# Field normalisation
# --------------------------------------------------------------------------


def _normalise_variant_id(raw: object) -> uuid.UUID:
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw).strip())
    except (ValueError, AttributeError, TypeError):
        raise InvalidOpeningInventoryLineError(
            "variant id is not a valid UUID"
        ) from None


def _normalise_quantity(raw: object) -> Decimal:
    if raw is None:
        raise InvalidOpeningInventoryLineError("quantity is required")
    try:
        quantity = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise InvalidOpeningInventoryLineError(
            f"quantity {raw!r} is not a valid number"
        ) from None
    if quantity.is_nan() or quantity.is_infinite():
        raise InvalidOpeningInventoryLineError(
            f"quantity {raw!r} is not a valid number"
        )
    quantized = quantity.quantize(_QUANTITY_SCALE, rounding=ROUND_HALF_UP)
    if quantized != quantity:
        raise InvalidOpeningInventoryLineError(
            "quantity must have at most 3 decimal places"
        )
    if quantized <= 0:
        raise InvalidOpeningInventoryLineError("quantity must be greater than 0")
    return quantized


def _normalise_cost(raw: object) -> Decimal:
    if raw is None:
        raise InvalidOpeningInventoryLineError("unit cost is required")
    try:
        cost = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise InvalidOpeningInventoryLineError(
            f"unit cost {raw!r} is not a valid number"
        ) from None
    if cost.is_nan() or cost.is_infinite():
        raise InvalidOpeningInventoryLineError(
            f"unit cost {raw!r} is not a valid number"
        )
    cost = cost.quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    if cost <= 0:
        raise InvalidOpeningInventoryLineError("unit cost must be greater than 0")
    return cost


def _normalise_cost_status(raw: object) -> OpeningInventoryCostStatus:
    if raw is None:
        raise InvalidOpeningInventoryLineError(
            "cost status is required ('known' or 'estimated')"
        )
    if isinstance(raw, OpeningInventoryCostStatus):
        return raw
    text_value = str(raw).strip().lower()
    if text_value == "unknown":
        raise InvalidOpeningInventoryLineError(
            "cost status 'unknown' cannot be posted; provide a known or "
            "estimated cost"
        )
    try:
        return OpeningInventoryCostStatus(text_value)
    except ValueError:
        raise InvalidOpeningInventoryLineError(
            f"cost status {raw!r} must be 'known' or 'estimated'"
        ) from None


def _parse_line(index: int, line: OpeningInventoryLineInput) -> _LineParse:
    """Normalise one line's fields, collecting every field-level error."""

    parsed = _LineParse(index=index)
    try:
        parsed.variant_id = _normalise_variant_id(getattr(line, "variant_id", None))
    except OpeningInventoryError as exc:
        parsed.errors.append(str(exc))
    try:
        parsed.quantity = _normalise_quantity(getattr(line, "quantity", None))
    except OpeningInventoryError as exc:
        parsed.errors.append(str(exc))
    try:
        parsed.unit_cost = _normalise_cost(getattr(line, "unit_cost", None))
    except OpeningInventoryError as exc:
        parsed.errors.append(str(exc))
    try:
        parsed.cost_status = _normalise_cost_status(getattr(line, "cost_status", None))
    except OpeningInventoryError as exc:
        parsed.errors.append(str(exc))
    return parsed


def _line_error(parsed: _LineParse) -> InvalidOpeningInventoryLineError:
    return InvalidOpeningInventoryLineError(
        f"line {parsed.index}: " + "; ".join(parsed.errors)
    )


def _normalise_idempotency_key(raw: object) -> str:
    key = str(raw or "").strip()
    if not key:
        raise InvalidOpeningInventoryKeyError("idempotency_key is required")
    if len(key) > _MAX_KEY_LENGTH:
        raise InvalidOpeningInventoryKeyError(
            f"idempotency_key must be at most {_MAX_KEY_LENGTH} characters"
        )
    return key


def _normalise_source(raw: object) -> OpeningInventorySource:
    if isinstance(raw, OpeningInventorySource):
        return raw
    try:
        return OpeningInventorySource(str(raw).strip().lower())
    except ValueError:
        raise InvalidOpeningInventoryRequestError(
            f"source {raw!r} must be 'manual' or 'import'"
        ) from None


def _normalise_notes(raw: object) -> str | None:
    if raw is None:
        return None
    notes = str(raw).strip() or None
    if notes is not None and len(notes) > _MAX_NOTES_LENGTH:
        raise InvalidOpeningInventoryRequestError(
            f"notes must be at most {_MAX_NOTES_LENGTH} characters"
        )
    return notes


def _project_weighted_average_cost(
    current_quantity: Decimal,
    current_cost: Decimal,
    quantity: Decimal,
    unit_cost: Decimal,
) -> Decimal:
    """Mirror `inventory_service._apply_receipt_cost()` exactly.

    The preview must show the average the real receipt will produce, so the
    arithmetic, scale and rounding here are intentionally identical:

        new_avg = (qty_on_hand * old_avg + received_qty * receipt_cost)
                  / (qty_on_hand + received_qty)
    """

    new_quantity = current_quantity + quantity
    total_value = current_quantity * current_cost + quantity * unit_cost
    return (total_value / new_quantity).quantize(
        _COST_SCALE, rounding=ROUND_HALF_UP
    )


# --------------------------------------------------------------------------
# Batched, tenant-scoped reads (no lazy loading, no N+1 writes)
# --------------------------------------------------------------------------


async def _lock_shop(session: AsyncSession, shop_id: uuid.UUID) -> None:
    """Lock the shop row, serializing opening postings per shop.

    Taken before the idempotency check-then-insert and before the
    duplicate-opening checks, so concurrent submissions queue behind each
    other instead of racing. The lock is released by the caller's commit or
    rollback; this module never ends the transaction itself.
    """

    row = (
        await session.execute(
            text("SELECT id FROM shops WHERE id = :shop_id FOR UPDATE"),
            {"shop_id": shop_id},
        )
    ).first()
    if row is None:
        raise ShopNotFoundError(f"Shop {shop_id} not found")


async def _find_batch(
    session: AsyncSession, shop_id: uuid.UUID, idempotency_key: str
) -> OpeningInventoryBatch | None:
    return (
        await session.execute(
            select(OpeningInventoryBatch)
            .options(selectinload(OpeningInventoryBatch.entries))
            .where(
                OpeningInventoryBatch.shop_id == shop_id,
                OpeningInventoryBatch.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()


async def _load_variants(
    session: AsyncSession, shop_id: uuid.UUID, variant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[ProductVariant, str]]:
    """Batch-load variants with their product name, tenant-scoped.

    A variant from another shop is simply absent, exactly like the
    inventory service's `_get_variant` - no cross-tenant existence leak.
    """

    rows = await session.execute(
        select(ProductVariant, Product.name)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(
            ProductVariant.id.in_(variant_ids),
            ProductVariant.shop_id == shop_id,
        )
    )
    return {variant.id: (variant, name) for variant, name in rows.all()}


async def _load_inventory(
    session: AsyncSession, variant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Inventory]:
    rows = await session.execute(
        select(Inventory).where(Inventory.variant_id.in_(variant_ids))
    )
    return {inventory.variant_id: inventory for inventory in rows.scalars()}


async def _load_existing_opening_variant_ids(
    session: AsyncSession, shop_id: uuid.UUID, variant_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    rows = await session.execute(
        select(OpeningInventoryEntry.variant_id).where(
            OpeningInventoryEntry.shop_id == shop_id,
            OpeningInventoryEntry.variant_id.in_(variant_ids),
        )
    )
    return set(rows.scalars())


async def _load_movement_variant_ids(
    session: AsyncSession, shop_id: uuid.UUID, variant_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    rows = await session.execute(
        select(InventoryMovement.variant_id)
        .where(
            InventoryMovement.shop_id == shop_id,
            InventoryMovement.variant_id.in_(variant_ids),
        )
        .distinct()
    )
    return set(rows.scalars())


# --------------------------------------------------------------------------
# Preview (read-only)
# --------------------------------------------------------------------------


async def preview_opening_inventory(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    lines: list[OpeningInventoryLineInput],
) -> OpeningInventoryPreview:
    """Validate proposed opening lines without writing anything.

    Every variant, inventory and movement lookup is done as a batched,
    tenant-scoped `SELECT`; no `Inventory` row is created and no movement,
    batch, entry or ledger line is written. Line-level problems are returned
    in each line's `errors` (they reject the line) and informational
    situations in `warnings` (existing stock, existing movements - never
    automatic rejection). `value` and the projected post-opening weighted-
    average cost are only populated for error-free lines, and the projected
    average mirrors the real receipt-cost formula exactly.
    """

    if not lines:
        raise EmptyOpeningInventoryError(
            "an opening inventory batch must contain at least one line"
        )

    parsed = [_parse_line(index, line) for index, line in enumerate(lines)]
    known_ids = list(
        dict.fromkeys(
            item.variant_id for item in parsed if item.variant_id is not None
        )
    )

    variants = await _load_variants(session, shop_id, known_ids)
    inventory = await _load_inventory(session, known_ids)
    existing_openings = await _load_existing_opening_variant_ids(
        session, shop_id, known_ids
    )
    moved_variants = await _load_movement_variant_ids(session, shop_id, known_ids)

    seen: set[uuid.UUID] = set()
    preview_lines: list[OpeningInventoryPreviewLine] = []
    total_value = _ZERO
    entry_count = 0
    estimated_count = 0

    for item in parsed:
        errors = list(item.errors)
        warnings: list[str] = []

        variant: ProductVariant | None = None
        product_name: str | None = None
        if item.variant_id is not None:
            if item.variant_id in seen:
                errors.append(
                    f"variant {item.variant_id} appears more than once in "
                    "the request"
                )
            seen.add(item.variant_id)

            found = variants.get(item.variant_id)
            if found is None:
                errors.append(
                    f"variant {item.variant_id} not found for this shop"
                )
            else:
                variant, product_name = found

        current_quantity = _ZERO
        current_cost = Decimal("0.0000")

        if variant is not None:
            if item.variant_id in existing_openings:
                errors.append(
                    f"variant {variant.sku} already has an opening entry for "
                    "this shop"
                )

            stock = inventory.get(variant.id)
            if stock is not None:
                current_quantity = stock.quantity
                current_cost = stock.weighted_average_cost
                if stock.quantity > 0:
                    warnings.append(
                        f"variant {variant.sku} already has "
                        f"{stock.quantity} {variant.unit.value} in stock"
                    )
            if item.variant_id in moved_variants:
                warnings.append(
                    f"variant {variant.sku} already has inventory movements"
                )

        value = None
        projected = None
        if not errors:
            value = item.value
            projected = _project_weighted_average_cost(
                current_quantity,
                current_cost,
                item.quantity,  # type: ignore[arg-type]
                item.unit_cost,  # type: ignore[arg-type]
            )
            total_value += value  # type: ignore[operator]
            entry_count += 1
            if item.cost_status is OpeningInventoryCostStatus.ESTIMATED:
                estimated_count += 1

        preview_lines.append(
            OpeningInventoryPreviewLine(
                index=item.index,
                variant_id=item.variant_id,
                sku=variant.sku if variant is not None else None,
                product_name=product_name,
                unit=variant.unit.value if variant is not None else None,
                quantity=item.quantity,
                unit_cost=item.unit_cost,
                cost_status=item.cost_status,
                value=value,
                current_quantity=current_quantity,
                current_weighted_average_cost=current_cost,
                projected_weighted_average_cost=projected,
                errors=tuple(errors),
                warnings=tuple(warnings),
            )
        )

    return OpeningInventoryPreview(
        lines=tuple(preview_lines),
        valid=all(not line.errors for line in preview_lines),
        total_value=total_value.quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP),
        entry_count=entry_count,
        estimated_entry_count=estimated_count,
    )


# --------------------------------------------------------------------------
# Posting (atomic, idempotent)
# --------------------------------------------------------------------------


def _normalise_lines(
    lines: list[OpeningInventoryLineInput],
) -> list[_NormalisedLine]:
    """Validate every submitted line, rejecting the first that is invalid.

    Repeats of the same variant are a validation error, never merged: a
    variant may have only one opening entry ever, and one opening line per
    request (`UNIQUE(batch_id, variant_id)`).
    """

    normalised: list[_NormalisedLine] = []
    seen: set[uuid.UUID] = set()
    for index, line in enumerate(lines):
        parsed = _parse_line(index, line)
        if parsed.errors:
            raise _line_error(parsed)
        assert parsed.variant_id is not None
        assert parsed.quantity is not None
        assert parsed.unit_cost is not None
        assert parsed.cost_status is not None
        assert parsed.value is not None
        if parsed.variant_id in seen:
            raise DuplicateOpeningInventoryVariantError(
                f"variant {parsed.variant_id} appears more than once in "
                "the request"
            )
        seen.add(parsed.variant_id)
        normalised.append(
            _NormalisedLine(
                index=index,
                variant_id=parsed.variant_id,
                quantity=parsed.quantity,
                unit_cost=parsed.unit_cost,
                cost_status=parsed.cost_status,
                value=parsed.value,
            )
        )
    return normalised


def _batch_matches(
    batch: OpeningInventoryBatch, normalised: list[_NormalisedLine]
) -> bool:
    """Whether an already-posted batch is the logical payload being retried.

    The logical payload is the normalized line set - the mapping from
    variant to quantity, unit cost and cost status - because that is what
    determines the stock and accounting effects. Metadata that does not
    change those effects (notes, source label) is ignored.
    """

    if len(batch.entries) != len(normalised):
        return False
    incoming = {
        line.variant_id: (line.quantity, line.unit_cost, line.cost_status)
        for line in normalised
    }
    stored = {
        entry.variant_id: (entry.quantity, entry.unit_cost, entry.cost_status)
        for entry in batch.entries
    }
    return incoming == stored


async def post_opening_inventory(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    idempotency_key: str,
    lines: list[OpeningInventoryLineInput],
    source: OpeningInventorySource = OpeningInventorySource.MANUAL,
    notes: str | None = None,
    created_by_user_id: uuid.UUID | None = None,
) -> OpeningInventoryBatch:
    """Post an opening inventory batch: batch + entries + stock + ledger.

    Validation is performed here from scratch on every call - a preview is a
    UX aid, never an authorization token. In order:

    1. the idempotency key, source, notes and line payloads are normalised
       and validated (pure, no database writes);
    2. the shop row is locked, serializing opening postings per shop;
    3. the idempotency key is resolved: a committed batch with the same key
       and the same logical payload is returned as-is; a different payload
       raises :class:`OpeningInventoryIdempotencyConflictError`;
    4. variants are re-resolved tenant-scoped, repeats are rejected and any
       variant that already has an opening entry is rejected;
    5. the batch and its entries are created, each line goes through
       `inventory_service.add_stock(..., movement_type=OPENING)` and the
       aggregate value is posted as `Debit Inventory / Credit Opening
       Balance Equity`.

    Nothing is committed or rolled back here: the caller owns the
    transaction, so a failure partway through leaves no partial batch,
    movement or journal entry once the caller rolls back. Stock movements
    are dated by the database (`now()`), never backdated.
    """

    key = _normalise_idempotency_key(idempotency_key)
    normalised_source = _normalise_source(source)
    normalised_notes = _normalise_notes(notes)

    if not lines:
        raise EmptyOpeningInventoryError(
            "an opening inventory batch must contain at least one line"
        )
    normalised = _normalise_lines(lines)

    # Serialize per shop before any check-then-write, so the idempotency
    # SELECT below cannot race a concurrent submission's INSERT.
    await _lock_shop(session, shop_id)

    existing = await _find_batch(session, shop_id, key)
    if existing is not None:
        if _batch_matches(existing, normalised):
            return existing
        raise OpeningInventoryIdempotencyConflictError(
            f"idempotency_key {key!r} was already used for a different "
            "opening inventory request"
        )

    variant_ids = [line.variant_id for line in normalised]
    variants = await _load_variants(session, shop_id, variant_ids)
    missing = [variant_id for variant_id in variant_ids if variant_id not in variants]
    if missing:
        raise VariantNotFoundError(
            f"Variant(s) {missing} not found for shop {shop_id}"
        )

    already_opened = await _load_existing_opening_variant_ids(
        session, shop_id, variant_ids
    )
    if already_opened:
        raise VariantAlreadyHasOpeningError(
            f"Variant(s) {sorted(already_opened, key=str)} already have an "
            f"opening entry for shop {shop_id}"
        )

    total_value = sum(
        (line.value for line in normalised), start=Decimal("0.00")
    ).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    if total_value <= 0:
        # Unreachable while every line is strictly positive above; kept as a
        # guard so a future refactor can never post a zero-value batch.
        raise InvalidOpeningInventoryLineError(
            "opening inventory total must be greater than 0"
        )

    estimated_count = sum(
        1
        for line in normalised
        if line.cost_status is OpeningInventoryCostStatus.ESTIMATED
    )

    batch = OpeningInventoryBatch(
        id=uuid.uuid4(),
        shop_id=shop_id,
        idempotency_key=key,
        source=normalised_source,
        notes=normalised_notes,
        entry_count=len(normalised),
        estimated_entry_count=estimated_count,
        total_value=total_value,
        created_by_user_id=created_by_user_id,
    )
    # Initialise the collection while the batch is still transient, so
    # appending entries below never triggers an async lazy-load mid-flush
    # (the same pattern `sales.create_sale` uses for its line items).
    batch.entries = []
    session.add(batch)
    await session.flush()

    for line in normalised:
        movement = await inventory_service.add_stock(
            session,
            shop_id=shop_id,
            variant_id=line.variant_id,
            quantity=line.quantity,
            movement_type=InventoryMovementType.OPENING,
            unit_cost=line.unit_cost,
            reference_type=_OPENING_REFERENCE,
            reference_id=batch.id,
        )
        variant = variants[line.variant_id][0]
        batch.entries.append(
            OpeningInventoryEntry(
                batch_id=batch.id,
                shop_id=shop_id,
                variant_id=line.variant_id,
                quantity=line.quantity,
                unit=variant.unit.value,
                cost_status=line.cost_status,
                unit_cost=line.unit_cost,
                value=line.value,
                movement_id=movement.id,
            )
        )

    await session.flush()

    await accounting_service.post_opening_inventory(
        session,
        shop_id=shop_id,
        batch_id=batch.id,
        total_value=total_value,
        description=f"Opening inventory ({len(normalised)} line(s))",
    )

    return batch

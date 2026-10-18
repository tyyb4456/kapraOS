"""Focused service tests for `app.services.opening_inventory`.

Covers the two operations the API layer will expose:

* `preview_opening_inventory()` - read-only validation, line-level errors and
  warnings, line-value rounding, and the projected post-opening weighted
  average cost (which must match `_apply_receipt_cost()` exactly);
* `post_opening_inventory()` - atomic, idempotent posting: batch + entries,
  stock receipts through `inventory_service.add_stock()` with movement type
  `OPENING`, and the balanced `Inventory / Opening Balance Equity` journal
  entry. No purchase, supplier or payable is ever created.

The critical transaction and concurrency behavior is exercised against real
PostgreSQL with independent sessions (no mocks), including one duplicate-key
race and one duplicate-opening race.
"""

import asyncio
import uuid
from decimal import Decimal
from typing import NamedTuple

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

import app.database.session as session_module
from app.models import (
    Category,
    Inventory,
    InventoryMovement,
    InventoryMovementType,
    LedgerEntry,
    OpeningInventoryBatch,
    OpeningInventoryCostStatus,
    OpeningInventoryEntry,
    OpeningInventorySource,
    Payment,
    Product,
    ProductType,
    ProductVariant,
    Purchase,
    Shop,
    Supplier,
    Unit,
)
from app.models.payment import PaymentMethod
from app.services import accounting as accounting_service
from app.services import inventory as inventory_service
from app.services.accounting import (
    ACCOUNTS_PAYABLE,
    COST_OF_GOODS_SOLD,
    INVENTORY,
    OPENING_BALANCE_EQUITY,
)
from app.services.opening_inventory import (
    DuplicateOpeningInventoryVariantError,
    EmptyOpeningInventoryError,
    InvalidOpeningInventoryKeyError,
    InvalidOpeningInventoryLineError,
    OpeningInventoryIdempotencyConflictError,
    OpeningInventoryLineInput,
    VariantAlreadyHasOpeningError,
    VariantNotFoundError,
    post_opening_inventory,
    preview_opening_inventory,
)
from app.services.purchases import PurchaseItemInput, create_purchase
from app.services.sales import PaymentInput, SaleItemInput, create_sale

KNOWN = OpeningInventoryCostStatus.KNOWN
ESTIMATED = OpeningInventoryCostStatus.ESTIMATED


class VariantFixture(NamedTuple):
    shop: Shop
    product: Product
    variant: ProductVariant


async def _make_variant(
    db_session: AsyncSession,
    *,
    shop_name: str = "Ahmed Fabrics",
    sku: str = "LINEN-WHT-001",
    unit: Unit = Unit.METER,
) -> VariantFixture:
    """Create a shop/category/product/variant chain (same shape as sibling tests)."""

    shop = Shop(name=shop_name)
    category = Category(shop=shop, name="Open Fabric")
    db_session.add_all([shop, category])
    await db_session.flush()

    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Premium Linen",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku=sku,
        purchase_price=Decimal("420.00"),
        selling_price=Decimal("650.00"),
        unit=unit,
    )
    db_session.add(variant)
    await db_session.flush()

    return VariantFixture(shop=shop, product=product, variant=variant)


async def _add_variant(
    db_session: AsyncSession,
    fixture: VariantFixture,
    *,
    sku: str = "LINEN-WHT-002",
    unit: Unit = Unit.METER,
) -> ProductVariant:
    variant = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku=sku,
        purchase_price=Decimal("420.00"),
        selling_price=Decimal("650.00"),
        unit=unit,
    )
    db_session.add(variant)
    await db_session.flush()
    return variant


def _line(
    variant_id: uuid.UUID,
    *,
    quantity: str | Decimal = "5.000",
    unit_cost: str | Decimal | None = "200.00",
    cost_status: OpeningInventoryCostStatus | str | None = KNOWN,
) -> OpeningInventoryLineInput:
    return OpeningInventoryLineInput(
        variant_id=variant_id,
        quantity=Decimal(str(quantity)),
        unit_cost=None if unit_cost is None else Decimal(str(unit_cost)),
        cost_status=cost_status,
    )


async def _inventory_quantity(
    db_session: AsyncSession, shop_id: uuid.UUID, variant_id: uuid.UUID
) -> Decimal:
    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=shop_id, variant_id=variant_id
    )
    return inventory.quantity


async def _movements(
    db_session: AsyncSession, variant_id: uuid.UUID
) -> list[InventoryMovement]:
    result = await db_session.execute(
        sa.select(InventoryMovement)
        .where(InventoryMovement.variant_id == variant_id)
        .order_by(InventoryMovement.created_at, InventoryMovement.quantity)
    )
    return list(result.scalars())


async def _entries(
    db_session: AsyncSession, batch_id: uuid.UUID
) -> list[OpeningInventoryEntry]:
    result = await db_session.execute(
        sa.select(OpeningInventoryEntry).where(
            OpeningInventoryEntry.batch_id == batch_id
        )
    )
    return list(result.scalars())


async def _ledger_for(
    db_session: AsyncSession,
    shop_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
) -> list[LedgerEntry]:
    result = await db_session.execute(
        sa.select(LedgerEntry).where(
            LedgerEntry.shop_id == shop_id,
            LedgerEntry.reference_type == reference_type,
            LedgerEntry.reference_id == reference_id,
        )
    )
    return list(result.scalars())


async def _counts(
    db_session: AsyncSession, shop_id: uuid.UUID, variant_ids: list[uuid.UUID]
) -> dict[str, int]:
    """Row counts used to prove "nothing was written"."""

    async def _count(stmt) -> int:
        return int((await db_session.execute(stmt)).scalar_one())

    return {
        "batches": await _count(
            sa.select(sa.func.count())
            .select_from(OpeningInventoryBatch)
            .where(OpeningInventoryBatch.shop_id == shop_id)
        ),
        "entries": await _count(
            sa.select(sa.func.count())
            .select_from(OpeningInventoryEntry)
            .where(OpeningInventoryEntry.shop_id == shop_id)
        ),
        "movements": await _count(
            sa.select(sa.func.count())
            .select_from(InventoryMovement)
            .where(
                InventoryMovement.shop_id == shop_id,
                InventoryMovement.variant_id.in_(variant_ids),
            )
        ),
        "ledger": await _count(
            sa.select(sa.func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.shop_id == shop_id)
        ),
        "inventory_rows": await _count(
            sa.select(sa.func.count())
            .select_from(Inventory)
            .where(Inventory.variant_id.in_(variant_ids))
        ),
    }


# --------------------------------------------------------------------------
# Preview - valid input
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_valid_manual_line_reports_value_and_projection(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[_line(fixture.variant.id, quantity="5.000", unit_cost="200.00")],
    )

    assert preview.valid is True
    line = preview.lines[0]
    assert line.errors == ()
    assert line.warnings == ()
    assert line.sku == "LINEN-WHT-001"
    assert line.product_name == "Premium Linen"
    assert line.unit == "meter"
    assert line.quantity == Decimal("5.000")
    assert line.unit_cost == Decimal("200.00")
    assert line.cost_status is KNOWN
    assert line.value == Decimal("1000.00")
    assert line.current_quantity == Decimal("0.000")
    assert line.current_weighted_average_cost == Decimal("0.0000")
    assert line.projected_weighted_average_cost == Decimal("200.0000")
    assert preview.total_value == Decimal("1000.00")
    assert preview.entry_count == 1
    assert preview.estimated_entry_count == 0


@pytest.mark.asyncio
async def test_preview_estimated_costs_are_usable_and_keep_their_label(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[
            _line(fixture.variant.id, quantity="2", unit_cost="150", cost_status=ESTIMATED)
        ],
    )

    assert preview.valid is True
    line = preview.lines[0]
    assert line.cost_status is ESTIMATED
    assert line.value == Decimal("300.00")
    assert line.projected_weighted_average_cost == Decimal("150.0000")
    assert preview.estimated_entry_count == 1


@pytest.mark.asyncio
async def test_preview_fractional_quantities_keep_three_decimal_precision(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[
            _line(fixture.variant.id, quantity="3.500", unit_cost="10"),
        ],
    )

    assert preview.valid is True
    assert preview.lines[0].quantity == Decimal("3.500")
    assert preview.lines[0].value == Decimal("35.00")


@pytest.mark.asyncio
async def test_preview_line_value_rounding_and_total(db_session: AsyncSession) -> None:
    """Values use `round(quantity * unit_cost, 2)` after the cost is reduced
    to the stored NUMERIC(14,2) precision - the same arithmetic the database
    check constraint verifies."""

    fixture = await _make_variant(db_session)
    second = await _add_variant(db_session, fixture)

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[
            # 2.333 * 100.01 = 233.32333 -> 233.32
            _line(fixture.variant.id, quantity="2.333", unit_cost="100.005"),
            # 0.005 -> 0.01 (half-up), 1.000 * 0.01 = 0.01
            _line(second.id, quantity="1.000", unit_cost="0.005"),
        ],
    )

    assert preview.valid is True
    assert preview.lines[0].unit_cost == Decimal("100.01")
    assert preview.lines[0].value == Decimal("233.32")
    assert preview.lines[1].unit_cost == Decimal("0.01")
    assert preview.lines[1].value == Decimal("0.01")
    assert preview.total_value == Decimal("233.33")


@pytest.mark.asyncio
async def test_preview_projection_matches_the_actual_receipt_cost_formula(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("8.000"),
        movement_type=InventoryMovementType.PURCHASE,
        unit_cost=Decimal("125.00"),
    )

    lines = [_line(fixture.variant.id, quantity="2.500", unit_cost="100.00")]
    preview = await preview_opening_inventory(
        db_session, shop_id=fixture.shop.id, lines=lines
    )

    # (8 * 125 + 2.5 * 100) / 10.5 = 119.047619... -> 119.0476
    projected = preview.lines[0].projected_weighted_average_cost
    assert projected == Decimal("119.0476")

    await post_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        idempotency_key="projection-match",
        lines=lines,
    )
    actual = await inventory_service.get_weighted_average_cost(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    assert actual == projected


# --------------------------------------------------------------------------
# Preview - no side effects
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_creates_no_persistent_records(db_session: AsyncSession) -> None:
    fixture = await _make_variant(db_session)
    before = await _counts(db_session, fixture.shop.id, [fixture.variant.id])

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[_line(fixture.variant.id)],
    )
    assert preview.valid is True

    after = await _counts(db_session, fixture.shop.id, [fixture.variant.id])
    assert after == before
    # Not even the lazily-created Inventory row appears.
    assert after["inventory_rows"] == 0

    batches = (
        await db_session.execute(
            sa.select(OpeningInventoryBatch).where(
                OpeningInventoryBatch.shop_id == fixture.shop.id
            )
        )
    ).scalars().all()
    assert batches == []


# --------------------------------------------------------------------------
# Preview - line-level errors
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_rejects_missing_unknown_zero_and_negative_costs(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    second = await _add_variant(db_session, fixture)
    third = await _add_variant(db_session, fixture, sku="LINEN-WHT-003")
    fourth = await _add_variant(db_session, fixture, sku="LINEN-WHT-004")
    fifth = await _add_variant(db_session, fixture, sku="LINEN-WHT-005")

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[
            _line(fixture.variant.id, unit_cost=None),
            _line(second.id, unit_cost="50", cost_status="unknown"),
            _line(third.id, unit_cost="0"),
            _line(fourth.id, unit_cost="-5"),
            _line(fifth.id, unit_cost="50", cost_status=None),
        ],
    )

    assert preview.valid is False
    assert all(line.errors for line in preview.lines)
    assert "required" in preview.lines[0].errors[0]
    assert "unknown" in preview.lines[1].errors[0]
    assert "greater than 0" in preview.lines[2].errors[0]
    assert "greater than 0" in preview.lines[3].errors[0]
    assert "cost status is required" in preview.lines[4].errors[0]
    assert preview.total_value == Decimal("0.00")
    assert preview.entry_count == 0


@pytest.mark.asyncio
async def test_preview_rejects_zero_negative_and_invalid_precision_quantities(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    second = await _add_variant(db_session, fixture)
    third = await _add_variant(db_session, fixture, sku="LINEN-WHT-003")

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[
            _line(fixture.variant.id, quantity="0"),
            _line(second.id, quantity="-1.000"),
            _line(third.id, quantity="1.2345"),
            _line(
                fixture.variant.id, quantity=Decimal("NaN")
            ),
        ],
    )

    assert preview.valid is False
    assert "greater than 0" in preview.lines[0].errors[0]
    assert "greater than 0" in preview.lines[1].errors[0]
    assert "at most 3 decimal places" in preview.lines[2].errors[0]
    assert "not a valid number" in preview.lines[3].errors[0]


@pytest.mark.asyncio
async def test_preview_reports_missing_and_cross_tenant_variants(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session, shop_name="Shop A", sku="A-1")
    other = await _make_variant(db_session, shop_name="Shop B", sku="B-1")

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[
            _line(uuid.uuid4()),
            _line(other.variant.id),
        ],
    )

    assert preview.valid is False
    for line in preview.lines:
        assert any("not found for this shop" in error for error in line.errors)


@pytest.mark.asyncio
async def test_preview_reports_duplicate_variants(db_session: AsyncSession) -> None:
    fixture = await _make_variant(db_session)

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[_line(fixture.variant.id), _line(fixture.variant.id, quantity="1")],
    )

    assert preview.valid is False
    assert preview.lines[0].errors == ()
    assert any("more than once" in error for error in preview.lines[1].errors)


@pytest.mark.asyncio
async def test_preview_reports_a_variant_that_already_has_an_opening_entry(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await post_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        idempotency_key="already-open",
        lines=[_line(fixture.variant.id)],
    )

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[_line(fixture.variant.id, quantity="1")],
    )

    assert preview.valid is False
    assert any(
        "already has an opening entry" in error
        for error in preview.lines[0].errors
    )


@pytest.mark.asyncio
async def test_preview_warns_about_existing_stock_and_movements_without_rejecting(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("4.500"),
        movement_type=InventoryMovementType.PURCHASE,
        unit_cost=Decimal("120.00"),
    )

    preview = await preview_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        lines=[_line(fixture.variant.id, quantity="1")],
    )

    line = preview.lines[0]
    assert preview.valid is True
    assert line.errors == ()
    assert any("already has" in warning and "in stock" in warning for warning in line.warnings)
    assert any("inventory movements" in warning for warning in line.warnings)
    assert line.current_quantity == Decimal("4.500")
    assert line.current_weighted_average_cost == Decimal("120.0000")


# --------------------------------------------------------------------------
# Posting - happy path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_posting_creates_batch_entries_and_opening_movements(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    second = await _add_variant(db_session, fixture, sku="SILK-BLK-002", unit=Unit.YARD)

    batch = await post_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        idempotency_key="manual-open-1",
        lines=[
            _line(fixture.variant.id, quantity="5.000", unit_cost="200.00"),
            _line(second.id, quantity="2.500", unit_cost="400.00", cost_status=ESTIMATED),
        ],
    )

    assert batch.shop_id == fixture.shop.id
    assert batch.source is OpeningInventorySource.MANUAL
    assert batch.idempotency_key == "manual-open-1"
    assert batch.entry_count == 2
    assert batch.estimated_entry_count == 1
    assert batch.total_value == Decimal("2000.00")
    assert batch.created_by_user_id is None

    entries = await _entries(db_session, batch.id)
    by_variant = {entry.variant_id: entry for entry in entries}
    meter_entry = by_variant[fixture.variant.id]
    assert meter_entry.quantity == Decimal("5.000")
    assert meter_entry.unit == "meter"
    assert meter_entry.unit_cost == Decimal("200.00")
    assert meter_entry.value == Decimal("1000.00")
    assert meter_entry.cost_status is KNOWN
    assert meter_entry.movement_id is not None

    yard_entry = by_variant[second.id]
    assert yard_entry.unit == "yard"
    assert yard_entry.quantity == Decimal("2.500")
    assert yard_entry.unit_cost == Decimal("400.00")
    assert yard_entry.value == Decimal("1000.00")
    assert yard_entry.cost_status is ESTIMATED

    meter_movements = await _movements(db_session, fixture.variant.id)
    assert len(meter_movements) == 1
    movement = meter_movements[0]
    assert movement.movement_type is InventoryMovementType.OPENING
    assert movement.reference_type == "OPENING"
    assert movement.reference_id == batch.id
    assert movement.quantity == Decimal("5.000")
    assert movement.unit_cost == Decimal("200.00")
    assert movement.id == meter_entry.movement_id

    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("5.000")
    assert await _inventory_quantity(
        db_session, fixture.shop.id, second.id
    ) == Decimal("2.500")
    assert await inventory_service.get_weighted_average_cost(
        db_session, shop_id=fixture.shop.id, variant_id=second.id
    ) == Decimal("400.0000")


# --------------------------------------------------------------------------
# Posting - rejections
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_posting_rejects_invalid_lines_without_writing_anything(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    bad_payloads = [
        [_line(fixture.variant.id, unit_cost=None)],
        [_line(fixture.variant.id, unit_cost="10", cost_status="unknown")],
        [_line(fixture.variant.id, unit_cost="0")],
        [_line(fixture.variant.id, unit_cost="-1")],
        [_line(fixture.variant.id, quantity="0")],
        [_line(fixture.variant.id, quantity="-2")],
        [_line(fixture.variant.id, quantity="1.2345")],
    ]
    for index, payload in enumerate(bad_payloads):
        with pytest.raises(InvalidOpeningInventoryLineError):
            await post_opening_inventory(
                db_session,
                shop_id=fixture.shop.id,
                idempotency_key=f"bad-line-{index}",
                lines=payload,
            )

    counts = await _counts(db_session, fixture.shop.id, [fixture.variant.id])
    assert counts == {
        "batches": 0,
        "entries": 0,
        "movements": 0,
        "ledger": 0,
        "inventory_rows": 0,
    }


@pytest.mark.asyncio
async def test_posting_rejects_missing_and_cross_tenant_variants(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session, shop_name="Shop A", sku="A-1")
    other = await _make_variant(db_session, shop_name="Shop B", sku="B-1")

    for index, variant_id in enumerate((uuid.uuid4(), other.variant.id)):
        with pytest.raises(VariantNotFoundError):
            await post_opening_inventory(
                db_session,
                shop_id=fixture.shop.id,
                idempotency_key=f"missing-{index}",
                lines=[_line(variant_id)],
            )

    counts = await _counts(db_session, fixture.shop.id, [fixture.variant.id])
    assert counts["batches"] == 0
    assert counts["movements"] == 0


@pytest.mark.asyncio
async def test_posting_rejects_duplicate_variants(db_session: AsyncSession) -> None:
    fixture = await _make_variant(db_session)

    with pytest.raises(DuplicateOpeningInventoryVariantError):
        await post_opening_inventory(
            db_session,
            shop_id=fixture.shop.id,
            idempotency_key="duplicate-variant",
            lines=[_line(fixture.variant.id), _line(fixture.variant.id, quantity="1")],
        )

    counts = await _counts(db_session, fixture.shop.id, [fixture.variant.id])
    assert counts["batches"] == 0
    assert counts["movements"] == 0


@pytest.mark.asyncio
async def test_posting_rejects_a_variant_with_an_existing_opening_entry(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    second = await _add_variant(db_session, fixture)

    await post_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        idempotency_key="first-batch",
        lines=[_line(fixture.variant.id, quantity="2")],
    )

    with pytest.raises(VariantAlreadyHasOpeningError):
        await post_opening_inventory(
            db_session,
            shop_id=fixture.shop.id,
            idempotency_key="second-batch",
            lines=[
                _line(fixture.variant.id, quantity="1"),
                _line(second.id, quantity="1"),
            ],
        )

    # The rejected batch left nothing behind - not even the new variant's line.
    assert await _inventory_quantity(
        db_session, fixture.shop.id, second.id
    ) == Decimal("0.000")
    batches = (
        await db_session.execute(
            sa.select(OpeningInventoryBatch).where(
                OpeningInventoryBatch.shop_id == fixture.shop.id
            )
        )
    ).scalars().all()
    assert len(batches) == 1


@pytest.mark.asyncio
async def test_posting_requires_a_valid_idempotency_key(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    for bad_key in ("", "   ", None, "x" * 65):
        with pytest.raises(InvalidOpeningInventoryKeyError):
            await post_opening_inventory(
                db_session,
                shop_id=fixture.shop.id,
                idempotency_key=bad_key,  # type: ignore[arg-type]
                lines=[_line(fixture.variant.id)],
            )


@pytest.mark.asyncio
async def test_posting_requires_at_least_one_line(db_session: AsyncSession) -> None:
    fixture = await _make_variant(db_session)

    with pytest.raises(EmptyOpeningInventoryError):
        await post_opening_inventory(
            db_session,
            shop_id=fixture.shop.id,
            idempotency_key="empty",
            lines=[],
        )


# --------------------------------------------------------------------------
# Accounting
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_posting_creates_a_balanced_inventory_equity_journal_entry(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    batch = await post_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        idempotency_key="ledger-balanced",
        lines=[_line(fixture.variant.id, quantity="3.500", unit_cost="120.00")],
    )

    ledger = await _ledger_for(
        db_session,
        fixture.shop.id,
        accounting_service.REFERENCE_OPENING_INVENTORY,
        batch.id,
    )
    assert len(ledger) == 2

    accounts = await accounting_service.ensure_system_accounts(
        db_session, shop_id=fixture.shop.id
    )
    by_account = {entry.account_id: entry for entry in ledger}
    inventory_entry = by_account[accounts[INVENTORY].id]
    equity_entry = by_account[accounts[OPENING_BALANCE_EQUITY].id]

    assert inventory_entry.debit == Decimal("420.00")
    assert inventory_entry.credit == Decimal("0.00")
    assert equity_entry.debit == Decimal("0.00")
    assert equity_entry.credit == Decimal("420.00")
    assert batch.total_value == Decimal("420.00")
    total_debit = sum((entry.debit for entry in ledger), Decimal("0.00"))
    total_credit = sum((entry.credit for entry in ledger), Decimal("0.00"))
    assert total_debit == total_credit == batch.total_value

    inventory_balance = await accounting_service.get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[INVENTORY].id
    )
    equity_balance = await accounting_service.get_account_balance(
        db_session,
        shop_id=fixture.shop.id,
        account_id=accounts[OPENING_BALANCE_EQUITY].id,
    )
    assert inventory_balance.balance == Decimal("420.00")
    assert equity_balance.balance == Decimal("420.00")


@pytest.mark.asyncio
async def test_posting_creates_no_purchase_supplier_or_payable(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    await post_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        idempotency_key="no-purchase",
        lines=[_line(fixture.variant.id)],
    )

    purchases = (
        await db_session.execute(
            sa.select(Purchase).where(Purchase.shop_id == fixture.shop.id)
        )
    ).scalars().all()
    suppliers = (
        await db_session.execute(
            sa.select(Supplier).where(Supplier.shop_id == fixture.shop.id)
        )
    ).scalars().all()
    payments = (
        await db_session.execute(
            sa.select(Payment).where(Payment.shop_id == fixture.shop.id)
        )
    ).scalars().all()
    assert purchases == []
    assert suppliers == []
    assert payments == []

    accounts = await accounting_service.ensure_system_accounts(
        db_session, shop_id=fixture.shop.id
    )
    payable_balance = await accounting_service.get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[ACCOUNTS_PAYABLE].id
    )
    assert payable_balance.balance == Decimal("0.00")
    ledger = (
        await db_session.execute(
            sa.select(LedgerEntry).where(
                LedgerEntry.shop_id == fixture.shop.id,
                LedgerEntry.reference_type.in_(
                    ["PURCHASE", "SUPPLIER_PAYMENT"]
                ),
            )
        )
    ).scalars().all()
    assert ledger == []


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_key_same_payload_returns_the_same_batch_without_reapplying(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    lines = [_line(fixture.variant.id, quantity="4.000", unit_cost="250.00")]

    first = await post_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        idempotency_key="retry-same",
        lines=lines,
    )
    quantity_after_first = await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    )
    ledger_after_first = await _ledger_for(
        db_session,
        fixture.shop.id,
        accounting_service.REFERENCE_OPENING_INVENTORY,
        first.id,
    )

    second = await post_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        idempotency_key="retry-same",
        lines=lines,
    )

    assert second.id == first.id
    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == quantity_after_first
    movements = await _movements(db_session, fixture.variant.id)
    assert len(movements) == 1
    assert await _ledger_for(
        db_session,
        fixture.shop.id,
        accounting_service.REFERENCE_OPENING_INVENTORY,
        first.id,
    ) == ledger_after_first
    assert len(await _entries(db_session, first.id)) == 1


@pytest.mark.asyncio
async def test_same_key_different_payload_is_rejected(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    await post_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        idempotency_key="retry-different",
        lines=[_line(fixture.variant.id, quantity="5.000", unit_cost="200.00")],
    )

    # Different quantity.
    with pytest.raises(OpeningInventoryIdempotencyConflictError):
        await post_opening_inventory(
            db_session,
            shop_id=fixture.shop.id,
            idempotency_key="retry-different",
            lines=[_line(fixture.variant.id, quantity="6.000", unit_cost="200.00")],
        )

    # Different cost.
    with pytest.raises(OpeningInventoryIdempotencyConflictError):
        await post_opening_inventory(
            db_session,
            shop_id=fixture.shop.id,
            idempotency_key="retry-different",
            lines=[_line(fixture.variant.id, quantity="5.000", unit_cost="210.00")],
        )

    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("5.000")


@pytest.mark.asyncio
async def test_same_key_is_scoped_per_shop(db_session: AsyncSession) -> None:
    shop_a = await _make_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_variant(db_session, shop_name="Shop B", sku="B-1")

    batch_a = await post_opening_inventory(
        db_session,
        shop_id=shop_a.shop.id,
        idempotency_key="shared-key",
        lines=[_line(shop_a.variant.id, quantity="1")],
    )
    batch_b = await post_opening_inventory(
        db_session,
        shop_id=shop_b.shop.id,
        idempotency_key="shared-key",
        lines=[_line(shop_b.variant.id, quantity="2")],
    )

    assert batch_a.id != batch_b.id
    assert batch_a.total_value == Decimal("200.00")
    assert batch_b.total_value == Decimal("400.00")


# --------------------------------------------------------------------------
# Transaction atomicity (caller-owned rollback)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_during_inventory_mutation_rolls_back_everything(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = await _make_variant(db_session)
    second = await _add_variant(db_session, fixture)

    real_add_stock = inventory_service.add_stock
    calls = {"n": 0}

    async def flaky_add_stock(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated inventory failure")
        return await real_add_stock(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("app.services.inventory.add_stock", flaky_add_stock)

    savepoint = await db_session.begin_nested()
    with pytest.raises(RuntimeError):
        await post_opening_inventory(
            db_session,
            shop_id=fixture.shop.id,
            idempotency_key="inventory-failure",
            lines=[
                _line(fixture.variant.id),
                _line(second.id, quantity="2"),
            ],
        )
    await savepoint.rollback()

    counts = await _counts(
        db_session, fixture.shop.id, [fixture.variant.id, second.id]
    )
    assert counts == {
        "batches": 0,
        "entries": 0,
        "movements": 0,
        "ledger": 0,
        "inventory_rows": 0,
    }


@pytest.mark.asyncio
async def test_failure_during_accounting_posting_rolls_back_everything(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = await _make_variant(db_session)

    async def failing_post(*args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated accounting failure")

    monkeypatch.setattr(
        "app.services.accounting.post_opening_inventory", failing_post
    )

    savepoint = await db_session.begin_nested()
    with pytest.raises(RuntimeError):
        await post_opening_inventory(
            db_session,
            shop_id=fixture.shop.id,
            idempotency_key="accounting-failure",
            lines=[_line(fixture.variant.id)],
        )
    await savepoint.rollback()

    counts = await _counts(db_session, fixture.shop.id, [fixture.variant.id])
    assert counts == {
        "batches": 0,
        "entries": 0,
        "movements": 0,
        "ledger": 0,
        "inventory_rows": 0,
    }


# --------------------------------------------------------------------------
# Compatibility with existing costing (purchases / sales)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_stock_flows_through_normal_purchase_and_sale_costing(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    supplier = Supplier(shop_id=fixture.shop.id, name="Al-Madina Textile")
    db_session.add(supplier)
    await db_session.flush()

    await post_opening_inventory(
        db_session,
        shop_id=fixture.shop.id,
        idempotency_key="costing-flow",
        lines=[_line(fixture.variant.id, quantity="10.000", unit_cost="100.00")],
    )
    assert await inventory_service.get_weighted_average_cost(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    ) == Decimal("100.0000")

    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("10.000"),
                unit_cost=Decimal("200.00"),
            )
        ],
    )
    assert await inventory_service.get_weighted_average_cost(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    ) == Decimal("150.0000")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("4.000"),
                unit_price=Decimal("300.00"),
            )
        ],
        payments=[
            PaymentInput(amount=Decimal("1200.00"), method=PaymentMethod.CASH)
        ],
    )

    cost_row = (
        await db_session.execute(
            sa.text(
                "SELECT cost_price FROM sale_items WHERE sale_id = :sale_id"
            ),
            {"sale_id": sale.id},
        )
    ).scalar_one()
    assert Decimal(cost_row) == Decimal("150.00")

    cogs = await _ledger_for(
        db_session,
        fixture.shop.id,
        accounting_service.REFERENCE_SALE_COGS,
        sale.id,
    )
    accounts = await accounting_service.ensure_system_accounts(
        db_session, shop_id=fixture.shop.id
    )
    cogs_entry = next(e for e in cogs if e.account_id == accounts[COST_OF_GOODS_SOLD].id)
    assert cogs_entry.debit == Decimal("600.00")

    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("16.000")

    movements = await _movements(db_session, fixture.variant.id)
    movement_types = [m.movement_type for m in movements]
    assert InventoryMovementType.OPENING in movement_types
    assert InventoryMovementType.PURCHASE in movement_types
    assert InventoryMovementType.SALE in movement_types


# --------------------------------------------------------------------------
# Real concurrency (independent committed sessions)
# --------------------------------------------------------------------------


async def _committed_variant(
    *, shop_name: str, sku: str
) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_module.AsyncSessionLocal() as setup:
        shop = Shop(name=shop_name)
        category = Category(shop=shop, name="Open Fabric")
        setup.add_all([shop, category])
        await setup.flush()

        product = Product(
            shop_id=shop.id,
            category_id=category.id,
            name="Premium Linen",
            product_type=ProductType.OPEN_FABRIC,
        )
        setup.add(product)
        await setup.flush()

        variant = ProductVariant(
            shop_id=shop.id,
            product_id=product.id,
            sku=sku,
            purchase_price=Decimal("420.00"),
            selling_price=Decimal("650.00"),
            unit=Unit.METER,
        )
        setup.add(variant)
        await setup.flush()
        await setup.commit()
        return shop.id, variant.id


async def _delete_shop(shop_id: uuid.UUID) -> None:
    async with session_module.AsyncSessionLocal() as cleanup:
        shop_to_delete = await cleanup.get(Shop, shop_id)
        if shop_to_delete is not None:
            await cleanup.delete(shop_to_delete)
            await cleanup.commit()


@pytest.mark.asyncio
async def test_concurrent_same_key_posts_create_exactly_one_batch() -> None:
    """The idempotency race, for real: two sessions, one key, one effect.

    Both workers run `post_opening_inventory()` with the same key and the
    same payload against independent committed connections. The per-shop row
    lock serializes them, so the loser re-reads the winner's committed batch
    instead of racing the unique constraint.
    """

    shop_id, variant_id = await _committed_variant(
        shop_name="Concurrent Opening Shop", sku="OPEN-RACE-001"
    )
    key = "concurrent-same-key"

    async def worker() -> uuid.UUID:
        async with session_module.AsyncSessionLocal() as session:
            batch = await post_opening_inventory(
                session,
                shop_id=shop_id,
                idempotency_key=key,
                lines=[_line(variant_id, quantity="5.000", unit_cost="200.00")],
            )
            await session.commit()
            return batch.id

    try:
        first_id, second_id = await asyncio.gather(worker(), worker())
        assert first_id == second_id

        async with session_module.AsyncSessionLocal() as check:
            batches = (
                await check.execute(
                    sa.select(OpeningInventoryBatch).where(
                        OpeningInventoryBatch.shop_id == shop_id
                    )
                )
            ).scalars().all()
            assert len(batches) == 1

            movements = await _movements(check, variant_id)
            assert len(movements) == 1
            assert movements[0].movement_type is InventoryMovementType.OPENING

            ledger = (
                await check.execute(
                    sa.select(LedgerEntry).where(
                        LedgerEntry.shop_id == shop_id,
                        LedgerEntry.reference_id == first_id,
                    )
                )
            ).scalars().all()
            assert len(ledger) == 2

            inventory = (
                await check.execute(
                    sa.select(Inventory).where(Inventory.variant_id == variant_id)
                )
            ).scalar_one()
            assert inventory.quantity == Decimal("5.000")
    finally:
        await _delete_shop(shop_id)


@pytest.mark.asyncio
async def test_concurrent_different_keys_for_one_variant_serialize() -> None:
    """One variant, one opening entry ever - even under a real race.

    Two sessions post different keys for the same variant at the same time.
    The shop lock serializes them: the first commits, and the second is
    rejected with `VariantAlreadyHasOpeningError` before writing anything.
    """

    shop_id, variant_id = await _committed_variant(
        shop_name="Concurrent Duplicate Shop", sku="OPEN-RACE-002"
    )

    async def worker(key: str) -> bool:
        async with session_module.AsyncSessionLocal() as session:
            try:
                await post_opening_inventory(
                    session,
                    shop_id=shop_id,
                    idempotency_key=key,
                    lines=[_line(variant_id, quantity="5.000", unit_cost="200.00")],
                )
                await session.commit()
                return True
            except VariantAlreadyHasOpeningError:
                await session.rollback()
                return False

    try:
        results = await asyncio.gather(worker("race-key-a"), worker("race-key-b"))
        assert results.count(True) == 1
        assert results.count(False) == 1

        async with session_module.AsyncSessionLocal() as check:
            batches = (
                await check.execute(
                    sa.select(OpeningInventoryBatch).where(
                        OpeningInventoryBatch.shop_id == shop_id
                    )
                )
            ).scalars().all()
            assert len(batches) == 1

            entries = (
                await check.execute(
                    sa.select(OpeningInventoryEntry).where(
                        OpeningInventoryEntry.shop_id == shop_id
                    )
                )
            ).scalars().all()
            assert len(entries) == 1

            movements = await _movements(check, variant_id)
            assert len(movements) == 1

            inventory = (
                await check.execute(
                    sa.select(Inventory).where(Inventory.variant_id == variant_id)
                )
            ).scalar_one()
            assert inventory.quantity == Decimal("5.000")
    finally:
        await _delete_shop(shop_id)

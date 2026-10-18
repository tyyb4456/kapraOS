"""Opening-inventory foundation tests.

Step 1 of the opening-inventory feature is the database/accounting
foundation, so these tests pin the schema guarantees the later
service/API layers will rely on:

* `InventoryMovementType.OPENING` round-trips through the PostgreSQL enum
  (proving migration 0022 actually extended the type).
* `opening_inventory_batches` / `opening_inventory_entries` persist and
  enforce the strict costing policy at the database level: positive
  quantity, positive unit cost, checked line math, positive batch value,
  one opening entry per variant per shop, one line per variant per batch,
  and composite tenant foreign keys that reject cross-shop references.

Service-level tests (preview, posting, idempotency, accounting, API,
import) are added in the steps that implement those layers.
"""

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Category,
    InventoryMovement,
    InventoryMovementType,
    OpeningInventoryBatch,
    OpeningInventoryCostStatus,
    OpeningInventoryEntry,
    OpeningInventorySource,
    Product,
    ProductType,
    ProductVariant,
    Shop,
    Unit,
)
from app.services import inventory as inventory_service
from app.services.accounting import DEFAULT_ACCOUNTS, OPENING_BALANCE_EQUITY


class VariantFixture:
    """A shop plus one variant, built the same way `test_inventory` does."""

    def __init__(self, shop: Shop, variant: ProductVariant) -> None:
        self.shop = shop
        self.variant = variant


async def _make_variant(
    db_session: AsyncSession,
    *,
    shop_name: str = "Ahmed Fabrics",
    sku: str = "LINEN-WHT-001",
    unit: Unit = Unit.METER,
) -> VariantFixture:
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

    return VariantFixture(shop=shop, variant=variant)


async def _make_batch(
    db_session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    idempotency_key: str | None = None,
    entry_count: int = 1,
    total_value: str = "1000.00",
    estimated_entry_count: int = 0,
    source: OpeningInventorySource = OpeningInventorySource.MANUAL,
) -> OpeningInventoryBatch:
    batch = OpeningInventoryBatch(
        shop_id=shop_id,
        idempotency_key=idempotency_key or uuid.uuid4().hex,
        source=source,
        entry_count=entry_count,
        total_value=Decimal(total_value),
        estimated_entry_count=estimated_entry_count,
    )
    db_session.add(batch)
    await db_session.flush()
    return batch


def _entry(
    *,
    batch: OpeningInventoryBatch,
    variant: ProductVariant,
    quantity: str = "5.000",
    unit_cost: str = "200.00",
    value: str = "1000.00",
    unit: str = "meter",
    cost_status: OpeningInventoryCostStatus = OpeningInventoryCostStatus.KNOWN,
    movement_id: uuid.UUID | None = None,
    shop_id: uuid.UUID | None = None,
) -> OpeningInventoryEntry:
    return OpeningInventoryEntry(
        batch_id=batch.id,
        shop_id=shop_id if shop_id is not None else batch.shop_id,
        variant_id=variant.id,
        quantity=Decimal(quantity),
        unit=unit,
        cost_status=cost_status,
        unit_cost=Decimal(unit_cost),
        value=Decimal(value),
        movement_id=movement_id,
    )


# --------------------------------------------------------------------------
# Enum + account provisioning
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_movement_type_round_trips_through_postgres(
    db_session: AsyncSession,
) -> None:
    """Migration 0022 extended the enum; the value must survive a DB round-trip."""

    fixture = await _make_variant(db_session)

    movement = await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("3.500"),
        movement_type=InventoryMovementType.OPENING,
        unit_cost=Decimal("200.00"),
    )

    await db_session.refresh(movement)
    assert movement.movement_type is InventoryMovementType.OPENING

    raw = (
        await db_session.execute(
            sa.text(
                "SELECT movement_type::text FROM inventory_movements "
                "WHERE id = :id"
            ),
            {"id": movement.id},
        )
    ).scalar_one()
    assert raw == "opening"


def test_opening_balance_equity_account_definition() -> None:
    """3100 is a system account, separate from Owner Equity (3000)."""

    codes = {code for code, _name, _type in DEFAULT_ACCOUNTS}
    assert "3000" in codes
    assert OPENING_BALANCE_EQUITY == "3100"
    entry = {
        code: (name, account_type) for code, name, account_type in DEFAULT_ACCOUNTS
    }[OPENING_BALANCE_EQUITY]
    assert entry[0] == "Opening Balance Equity"
    assert entry[1].value == "equity"


# --------------------------------------------------------------------------
# Batch + entry persistence
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_and_entry_persist_with_movement_link(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    batch = await _make_batch(db_session, shop_id=fixture.shop.id)

    movement = await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("5.000"),
        movement_type=InventoryMovementType.OPENING,
        unit_cost=Decimal("200.00"),
    )

    entry = _entry(
        batch=batch,
        variant=fixture.variant,
        cost_status=OpeningInventoryCostStatus.ESTIMATED,
        movement_id=movement.id,
    )
    db_session.add(entry)
    await db_session.flush()

    await db_session.refresh(batch)
    assert batch.source is OpeningInventorySource.MANUAL
    assert batch.entry_count == 1
    assert batch.total_value == Decimal("1000.00")

    fetched = (
        await db_session.execute(
            sa.select(OpeningInventoryEntry).where(
                OpeningInventoryEntry.id == entry.id
            )
        )
    ).scalar_one()
    assert fetched.cost_status is OpeningInventoryCostStatus.ESTIMATED
    assert fetched.unit == "meter"
    assert fetched.quantity == Decimal("5.000")
    assert fetched.unit_cost == Decimal("200.00")
    assert fetched.value == Decimal("1000.00")
    assert fetched.movement_id == movement.id
    assert fetched.created_at is not None


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_is_rejected_per_shop(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await _make_batch(db_session, shop_id=fixture.shop.id, idempotency_key="retry-1")

    db_session.add(
        OpeningInventoryBatch(
            shop_id=fixture.shop.id,
            idempotency_key="retry-1",
            source=OpeningInventorySource.MANUAL,
            entry_count=1,
            total_value=Decimal("1.00"),
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_same_idempotency_key_is_allowed_across_shops(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_variant(db_session, shop_name="Shop B", sku="B-1")

    await _make_batch(db_session, shop_id=shop_a.shop.id, idempotency_key="retry-1")
    await _make_batch(db_session, shop_id=shop_b.shop.id, idempotency_key="retry-1")


# --------------------------------------------------------------------------
# Strict costing policy enforced by the database
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_unit_cost_is_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_variant(db_session)
    batch = await _make_batch(db_session, shop_id=fixture.shop.id)

    db_session.add(
        _entry(
            batch=batch,
            variant=fixture.variant,
            quantity="5.000",
            unit_cost="0.00",
            value="0.00",
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_negative_unit_cost_is_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_variant(db_session)
    batch = await _make_batch(db_session, shop_id=fixture.shop.id)

    db_session.add(
        _entry(
            batch=batch,
            variant=fixture.variant,
            quantity="5.000",
            unit_cost="-1.00",
            value="-5.00",
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_non_positive_quantity_is_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_variant(db_session)
    batch = await _make_batch(db_session, shop_id=fixture.shop.id)

    db_session.add(
        _entry(
            batch=batch,
            variant=fixture.variant,
            quantity="0.000",
            unit_cost="200.00",
            value="0.00",
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_value_must_match_quantity_times_unit_cost(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    batch = await _make_batch(db_session, shop_id=fixture.shop.id)

    db_session.add(
        _entry(
            batch=batch,
            variant=fixture.variant,
            quantity="5.000",
            unit_cost="200.00",
            value="999.00",
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_batch_total_value_must_be_positive(db_session: AsyncSession) -> None:
    fixture = await _make_variant(db_session)

    db_session.add(
        OpeningInventoryBatch(
            shop_id=fixture.shop.id,
            idempotency_key="zero-value",
            source=OpeningInventorySource.MANUAL,
            entry_count=1,
            total_value=Decimal("0.00"),
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_batch_entry_count_must_be_positive(db_session: AsyncSession) -> None:
    fixture = await _make_variant(db_session)

    db_session.add(
        OpeningInventoryBatch(
            shop_id=fixture.shop.id,
            idempotency_key="no-entries",
            source=OpeningInventorySource.MANUAL,
            entry_count=0,
            total_value=Decimal("10.00"),
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_estimated_count_cannot_exceed_entry_count(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    db_session.add(
        OpeningInventoryBatch(
            shop_id=fixture.shop.id,
            idempotency_key="bad-estimated-count",
            source=OpeningInventorySource.MANUAL,
            entry_count=1,
            estimated_entry_count=2,
            total_value=Decimal("10.00"),
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


# --------------------------------------------------------------------------
# One opening entry per variant, ever + per-batch uniqueness
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variant_can_have_only_one_opening_entry_ever(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    first_batch = await _make_batch(db_session, shop_id=fixture.shop.id)
    second_batch = await _make_batch(db_session, shop_id=fixture.shop.id)

    db_session.add(_entry(batch=first_batch, variant=fixture.variant))
    await db_session.flush()

    db_session.add(_entry(batch=second_batch, variant=fixture.variant))
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_variant_cannot_appear_twice_in_one_batch(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    batch = await _make_batch(db_session, shop_id=fixture.shop.id)

    db_session.add(_entry(batch=batch, variant=fixture.variant))
    await db_session.flush()

    db_session.add(
        _entry(
            batch=batch,
            variant=fixture.variant,
            quantity="1.000",
            unit_cost="100.00",
            value="100.00",
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_two_variants_can_each_have_an_opening_entry(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    # A second variant in the *same* shop (a cross-shop variant would be
    # rejected by the tenant FK, which is what the isolation tests prove).
    second_variant = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.variant.product_id,
        sku="LINEN-WHT-002",
        purchase_price=Decimal("420.00"),
        selling_price=Decimal("650.00"),
        unit=Unit.METER,
    )
    db_session.add(second_variant)
    await db_session.flush()

    batch = await _make_batch(
        db_session,
        shop_id=fixture.shop.id,
        entry_count=2,
        total_value="1500.00",
    )

    db_session.add(_entry(batch=batch, variant=fixture.variant))
    db_session.add(
        _entry(
            batch=batch,
            variant=second_variant,
            quantity="2.500",
            unit_cost="200.00",
            value="500.00",
        )
    )
    await db_session.flush()


# --------------------------------------------------------------------------
# Tenant isolation (composite foreign keys)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_cannot_reference_another_shops_variant(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_variant(db_session, shop_name="Shop B", sku="B-1")

    batch = await _make_batch(db_session, shop_id=shop_a.shop.id)
    db_session.add(
        _entry(batch=batch, variant=shop_b.variant, shop_id=shop_a.shop.id)
    )

    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_entry_cannot_attach_to_another_shops_batch(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_variant(db_session, shop_name="Shop B", sku="B-1")

    batch_b = await _make_batch(db_session, shop_id=shop_b.shop.id)
    db_session.add(
        _entry(batch=batch_b, variant=shop_a.variant, shop_id=shop_a.shop.id)
    )

    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_batch_cannot_belong_to_a_missing_shop(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        OpeningInventoryBatch(
            shop_id=uuid.uuid4(),
            idempotency_key="ghost-shop",
            source=OpeningInventorySource.MANUAL,
            entry_count=1,
            total_value=Decimal("10.00"),
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_movement_link_is_optional_and_set_null_on_delete(
    db_session: AsyncSession,
) -> None:
    """A batch entry survives stock-ledger cleanup; only the link is cleared."""

    fixture = await _make_variant(db_session)
    batch = await _make_batch(db_session, shop_id=fixture.shop.id)

    movement = await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("1.000"),
        movement_type=InventoryMovementType.OPENING,
        unit_cost=Decimal("100.00"),
    )
    entry = _entry(
        batch=batch,
        variant=fixture.variant,
        quantity="1.000",
        unit_cost="100.00",
        value="100.00",
        movement_id=movement.id,
    )
    db_session.add(entry)
    await db_session.flush()

    await db_session.execute(
        sa.delete(InventoryMovement).where(InventoryMovement.id == movement.id)
    )
    await db_session.flush()
    await db_session.refresh(entry)
    assert entry.movement_id is None

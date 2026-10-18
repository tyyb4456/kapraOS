"""Inventory domain tests: the Inventory/InventoryMovement models and the
`app.services.inventory` operations, including tenant integrity, decimal
precision, unit-agnostic stock, ledger history and concurrency safety.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import NamedTuple
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.database.session as session_module
from app.main import app
from app.models import (
    Category,
    Inventory,
    InventoryMovement,
    InventoryMovementType,
    Product,
    ProductType,
    ProductVariant,
    Shop,
    Unit,
    User,
)
from app.models.user import UserRole
from app.services import inventory as inventory_service
from app.services.inventory import (
    InsufficientStockError,
    VariantNotFoundError,
)

# The inventory row is always keyed by variant, so tests tend to build the
# same little catalog chain (shop -> category -> product -> variant) over and
# over. This bundles the ids/objects those helpers produce.
VariantFixture = NamedTuple(
    "VariantFixture",
    [
        ("shop", Shop),
        ("product", Product),
        ("variant", ProductVariant),
    ],
)


async def _make_variant(
    db_session: AsyncSession,
    *,
    shop_name: str = "Ahmed Fabrics",
    sku: str = "LINEN-WHT-001",
    unit: Unit = Unit.METER,
    purchase_price: str = "420.00",
    selling_price: str = "650.00",
) -> VariantFixture:
    """Create a shop/category/product/variant chain and flush it."""

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
        purchase_price=Decimal(purchase_price),
        selling_price=Decimal(selling_price),
        unit=unit,
    )
    db_session.add(variant)
    await db_session.flush()

    return VariantFixture(shop=shop, product=product, variant=variant)


async def _movements_for(
    db_session: AsyncSession, variant_id: uuid.UUID
) -> list[InventoryMovement]:
    result = await db_session.execute(
        sa.select(InventoryMovement)
        .where(InventoryMovement.variant_id == variant_id)
        .order_by(InventoryMovement.created_at, InventoryMovement.quantity)
    )
    return list(result.scalars())


# --------------------------------------------------------------------------
# 1. Create inventory
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inventory_can_be_created_for_a_variant(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )

    assert inventory.variant_id == fixture.variant.id
    assert inventory.quantity == Decimal("0")
    assert inventory.reserved_quantity == Decimal("0")
    assert inventory.available_quantity == Decimal("0")


@pytest.mark.asyncio
async def test_variant_has_exactly_one_inventory_record(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )

    # A second row for the same variant must be rejected by the unique
    # constraint, not merely discouraged by convention.
    db_session.add(Inventory(variant_id=fixture.variant.id))
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_get_or_create_inventory_is_idempotent(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    first = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    second = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )

    assert first.id == second.id


# --------------------------------------------------------------------------
# 2. Add stock
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_stock_increases_quantity_and_writes_positive_movement(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    movement = await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("10.500"),
        movement_type=InventoryMovementType.PURCHASE,
        unit_cost=Decimal("420.00"),
        reference_type="PURCHASE",
    )

    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )

    assert inventory.quantity == Decimal("10.500")
    assert movement.movement_type is InventoryMovementType.PURCHASE
    assert movement.quantity == Decimal("10.500")
    assert movement.unit_cost == Decimal("420.00")
    assert movement.shop_id == fixture.shop.id

    movements = await _movements_for(db_session, fixture.variant.id)
    assert len(movements) == 1
    assert movements[0].quantity == Decimal("10.500")


@pytest.mark.asyncio
async def test_add_stock_rejects_non_positive_quantity(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    for bad in (Decimal("0"), Decimal("-1")):
        with pytest.raises(ValueError):
            await inventory_service.add_stock(
                db_session,
                shop_id=fixture.shop.id,
                variant_id=fixture.variant.id,
                quantity=bad,
                movement_type=InventoryMovementType.PURCHASE,
            )


# --------------------------------------------------------------------------
# 3. Remove stock
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_stock_decreases_quantity_and_writes_negative_movement(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("10"),
        movement_type=InventoryMovementType.PURCHASE,
    )

    movement = await inventory_service.remove_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("3.500"),
        movement_type=InventoryMovementType.SALE,
        reference_type="SALE",
    )

    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )

    assert inventory.quantity == Decimal("6.500")
    assert movement.movement_type is InventoryMovementType.SALE
    assert movement.quantity == Decimal("-3.500")


# --------------------------------------------------------------------------
# 4. Prevent insufficient stock
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_stock_rejects_more_than_available(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("2"),
        movement_type=InventoryMovementType.PURCHASE,
    )

    with pytest.raises(InsufficientStockError):
        await inventory_service.remove_stock(
            db_session,
            shop_id=fixture.shop.id,
            variant_id=fixture.variant.id,
            quantity=Decimal("3"),
            movement_type=InventoryMovementType.SALE,
        )

    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    assert inventory.quantity == Decimal("2")

    # Only the purchase movement exists - the rejected sale left no trace.
    movements = await _movements_for(db_session, fixture.variant.id)
    assert [m.movement_type for m in movements] == [InventoryMovementType.PURCHASE]
    assert all(m.quantity > 0 for m in movements)


# --------------------------------------------------------------------------
# 5. Adjustment
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_positive_adjustment_records_adjustment_movement(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("20"),
        movement_type=InventoryMovementType.PURCHASE,
    )

    movement = await inventory_service.adjust_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity_delta=Decimal("3"),
        notes="Physical stock count correction",
    )

    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    assert inventory.quantity == Decimal("23")
    assert movement.movement_type is InventoryMovementType.ADJUSTMENT
    assert movement.quantity == Decimal("3")
    assert movement.reference_id is None
    assert movement.notes == "Physical stock count correction"


@pytest.mark.asyncio
async def test_negative_adjustment_records_negative_movement(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("50"),
        movement_type=InventoryMovementType.PURCHASE,
    )

    movement = await inventory_service.adjust_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity_delta=Decimal("-3"),
        notes="Short by 3m after stock take",
    )

    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    assert inventory.quantity == Decimal("47")
    assert movement.movement_type is InventoryMovementType.ADJUSTMENT
    assert movement.quantity == Decimal("-3")


# --------------------------------------------------------------------------
# 6. Reserved quantity / availability
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_available_quantity_is_quantity_minus_reserved(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("10"),
        movement_type=InventoryMovementType.PURCHASE,
    )

    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    inventory.reserved_quantity = Decimal("3")
    await db_session.flush()

    assert inventory.quantity == Decimal("10")
    assert inventory.reserved_quantity == Decimal("3")
    assert inventory.available_quantity == Decimal("7")

    # Reserved stock is not sellable.
    with pytest.raises(InsufficientStockError):
        await inventory_service.remove_stock(
            db_session,
            shop_id=fixture.shop.id,
            variant_id=fixture.variant.id,
            quantity=Decimal("8"),
            movement_type=InventoryMovementType.SALE,
        )


@pytest.mark.asyncio
async def test_reserved_quantity_cannot_exceed_quantity(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("10"),
        movement_type=InventoryMovementType.PURCHASE,
    )

    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    inventory.reserved_quantity = Decimal("11")
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_negative_quantity_is_rejected_by_the_database(
    db_session: AsyncSession,
) -> None:
    """Direct writes cannot create negative stock either."""

    fixture = await _make_variant(db_session)
    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    inventory.quantity = Decimal("-1")
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


# --------------------------------------------------------------------------
# 7. Decimal precision
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decimal_quantities_are_preserved_exactly(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)

    for amount in (Decimal("3.500"), Decimal("12.250"), Decimal("0.125")):
        await inventory_service.add_stock(
            db_session,
            shop_id=fixture.shop.id,
            variant_id=fixture.variant.id,
            quantity=amount,
            movement_type=InventoryMovementType.PURCHASE,
        )

    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    assert inventory.quantity == Decimal("15.875")
    assert isinstance(inventory.quantity, Decimal)

    movements = await _movements_for(db_session, fixture.variant.id)
    assert [m.quantity for m in movements] == [
        Decimal("0.125"),
        Decimal("3.500"),
        Decimal("12.250"),
    ]


# --------------------------------------------------------------------------
# 8. Different units share one schema
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inventory_works_across_units(
    db_session: AsyncSession,
) -> None:
    meter = await _make_variant(
        db_session, shop_name="Shop M", sku="LIN-M-001", unit=Unit.METER
    )
    piece = await _make_variant(
        db_session, shop_name="Shop P", sku="SUIT-P-001", unit=Unit.PIECE
    )
    set_item = await _make_variant(
        db_session, shop_name="Shop S", sku="SUIT-S-001", unit=Unit.SET
    )

    for fixture, amount in ((meter, "3.500"), (piece, "2"), (set_item, "20")):
        await inventory_service.add_stock(
            db_session,
            shop_id=fixture.shop.id,
            variant_id=fixture.variant.id,
            quantity=Decimal(amount),
            movement_type=InventoryMovementType.PURCHASE,
        )

    meter_inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=meter.shop.id, variant_id=meter.variant.id
    )
    piece_inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=piece.shop.id, variant_id=piece.variant.id
    )
    set_inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=set_item.shop.id, variant_id=set_item.variant.id
    )

    assert meter_inventory.quantity == Decimal("3.500")
    assert piece_inventory.quantity == Decimal("2")
    assert set_inventory.quantity == Decimal("20")

    # Same table, driven entirely by the variant's unit.
    result = await db_session.execute(sa.select(Inventory))
    assert len(result.scalars().all()) == 3


# --------------------------------------------------------------------------
# 9. Movement history
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_movement_history_reconstructs_current_stock(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_variant(db_session)
    shop_id, variant_id = fixture.shop.id, fixture.variant.id

    await inventory_service.add_stock(
        db_session,
        shop_id=shop_id,
        variant_id=variant_id,
        quantity=Decimal("50"),
        movement_type=InventoryMovementType.PURCHASE,
    )
    await inventory_service.remove_stock(
        db_session,
        shop_id=shop_id,
        variant_id=variant_id,
        quantity=Decimal("5.5"),
        movement_type=InventoryMovementType.SALE,
    )
    await inventory_service.remove_stock(
        db_session,
        shop_id=shop_id,
        variant_id=variant_id,
        quantity=Decimal("1"),
        movement_type=InventoryMovementType.DAMAGE,
    )
    await inventory_service.add_stock(
        db_session,
        shop_id=shop_id,
        variant_id=variant_id,
        quantity=Decimal("2"),
        movement_type=InventoryMovementType.CUSTOMER_RETURN,
    )

    movements = await _movements_for(db_session, variant_id)
    assert [m.quantity for m in movements] == [
        Decimal("-5.5"),
        Decimal("-1"),
        Decimal("2"),
        Decimal("50"),
    ]
    assert [m.movement_type for m in movements] == [
        InventoryMovementType.SALE,
        InventoryMovementType.DAMAGE,
        InventoryMovementType.CUSTOMER_RETURN,
        InventoryMovementType.PURCHASE,
    ]

    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=shop_id, variant_id=variant_id
    )
    assert inventory.quantity == Decimal("45.5")
    assert sum(m.quantity for m in movements) == inventory.quantity


@pytest.mark.asyncio
async def test_all_movement_types_are_supported(db_session: AsyncSession) -> None:
    fixture = await _make_variant(db_session)
    shop_id, variant_id = fixture.shop.id, fixture.variant.id

    # Seed enough stock that every removal below has something to take.
    await inventory_service.add_stock(
        db_session,
        shop_id=shop_id,
        variant_id=variant_id,
        quantity=Decimal("5"),
        movement_type=InventoryMovementType.PURCHASE,
    )

    for movement_type in InventoryMovementType:
        # Enter with every type so the ledger stays non-negative.
        if movement_type in (
            InventoryMovementType.PURCHASE,
            InventoryMovementType.CUSTOMER_RETURN,
            InventoryMovementType.ADJUSTMENT,
        ):
            await inventory_service.add_stock(
                db_session,
                shop_id=shop_id,
                variant_id=variant_id,
                quantity=Decimal("1"),
                movement_type=movement_type,
            )
        else:
            await inventory_service.remove_stock(
                db_session,
                shop_id=shop_id,
                variant_id=variant_id,
                quantity=Decimal("1"),
                movement_type=movement_type,
            )

    movements = await _movements_for(db_session, variant_id)
    assert {m.movement_type for m in movements} == set(InventoryMovementType)


# --------------------------------------------------------------------------
# Tenant integrity
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cannot_operate_on_a_variant_from_another_shop(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_variant(db_session, shop_name="Shop A", sku="A-001")
    shop_b = await _make_variant(db_session, shop_name="Shop B", sku="B-001")

    with pytest.raises(VariantNotFoundError):
        await inventory_service.add_stock(
            db_session,
            shop_id=shop_a.shop.id,
            variant_id=shop_b.variant.id,  # belongs to Shop B
            quantity=Decimal("10"),
            movement_type=InventoryMovementType.PURCHASE,
        )


@pytest.mark.asyncio
async def test_movement_cannot_point_at_a_variant_from_another_shop(
    db_session: AsyncSession,
) -> None:
    """Database-level guard: the composite FK rejects cross-shop movements."""

    shop_a = await _make_variant(db_session, shop_name="Shop A", sku="A-001")
    shop_b = await _make_variant(db_session, shop_name="Shop B", sku="B-001")

    db_session.add(
        InventoryMovement(
            shop_id=shop_a.shop.id,  # Shop A
            variant_id=shop_b.variant.id,  # but Shop B's variant
            movement_type=InventoryMovementType.PURCHASE,
            quantity=Decimal("1"),
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_movement_for_own_variant_is_accepted(
    db_session: AsyncSession,
) -> None:
    """The mirror of the cross-shop guard: a matching (variant, shop) passes."""

    fixture = await _make_variant(db_session, shop_name="Shop A", sku="A-001")

    movement = await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("1"),
        movement_type=InventoryMovementType.PURCHASE,
    )

    assert movement.shop_id == fixture.shop.id
    assert movement.variant_id == fixture.variant.id


# --------------------------------------------------------------------------
# 10. Concurrency
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_removals_cannot_oversell() -> None:
    """Two simultaneous deductions of the same stock must not both succeed.

    This is a real database-level concurrency test: it runs two independent
    sessions (each with its own asyncpg connection) that both try to take
    3 units from a variant with 5 in stock. Without `SELECT ... FOR UPDATE`
    in `remove_stock` both could read 5 and succeed, leaving 2 in the counter
    while 6 units were sold. With row locking, one transaction waits for the
    other and then correctly rejects the second deduction.

    Committed setup/cleanup is used because the two workers must see the
    seeded row across separate connections.
    """

    async with session_module.AsyncSessionLocal() as setup:
        shop = Shop(name="Concurrency Shop")
        category = Category(shop=shop, name="Open Fabric")
        setup.add_all([shop, category])
        await setup.flush()

        product = Product(
            shop_id=shop.id,
            category_id=category.id,
            name="Limited Fabric",
            product_type=ProductType.OPEN_FABRIC,
        )
        setup.add(product)
        await setup.flush()

        variant = ProductVariant(
            shop_id=shop.id,
            product_id=product.id,
            sku="CONCURRENCY-001",
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("200.00"),
            unit=Unit.METER,
        )
        setup.add(variant)
        await setup.flush()

        await inventory_service.add_stock(
            setup,
            shop_id=shop.id,
            variant_id=variant.id,
            quantity=Decimal("5"),
            movement_type=InventoryMovementType.PURCHASE,
        )
        await setup.commit()
        shop_id, variant_id = shop.id, variant.id

    async def sell() -> bool:
        async with session_module.AsyncSessionLocal() as worker:
            try:
                await inventory_service.remove_stock(
                    worker,
                    shop_id=shop_id,
                    variant_id=variant_id,
                    quantity=Decimal("3"),
                    movement_type=InventoryMovementType.SALE,
                )
                await worker.commit()
                return True
            except InsufficientStockError:
                await worker.rollback()
                return False

    try:
        # A barrier makes both workers hit the row lock at (almost) the same
        # instant, so this exercises the lock rather than merely two
        # sequential calls.
        results = await asyncio.gather(sell(), sell())

        assert results.count(True) == 1
        assert results.count(False) == 1

        async with session_module.AsyncSessionLocal() as check:
            inventory = await inventory_service.get_or_create_inventory(
                check, shop_id=shop_id, variant_id=variant_id
            )
            assert inventory.quantity == Decimal("2")

            movements = await _movements_for(check, variant_id)
            sale_movements = [
                m for m in movements if m.movement_type is InventoryMovementType.SALE
            ]
            assert len(sale_movements) == 1
            assert sale_movements[0].quantity == Decimal("-3")
    finally:
        # Remove the committed rows so this test leaves no trace for others.
        async with session_module.AsyncSessionLocal() as cleanup:
            shop_to_delete = await cleanup.get(Shop, shop_id)
            if shop_to_delete is not None:
                await cleanup.delete(shop_to_delete)
                await cleanup.commit()


# --------------------------------------------------------------------------
# 11. HTTP API - the adjustment endpoint owns its transaction
# --------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


async def _make_user(api_session: AsyncSession, shop_id: uuid.UUID) -> User:
    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=shop_id,
        name="Mock User",
        email="mock@example.com",
        role=UserRole.OWNER,
    )
    api_session.add(user)
    await api_session.flush()
    return user


@pytest_asyncio.fixture
async def committed_client() -> AsyncGenerator[AsyncClient, None]:
    """An auth-bypassed client whose requests use real per-request sessions.

    `mocked_api_client` shares the test's savepoint-bound session, so a route
    that never commits still looks successful. These endpoint tests need the
    production behavior instead: every request opens its own session, and
    closing that session rolls back anything the route failed to commit.
    """

    patcher = patch(
        "app.auth.auth._verify_token",
        new_callable=AsyncMock,
        return_value={"sub": "mock_clerk_id"},
    )
    patcher.start()

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_module.AsyncSessionLocal() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[session_module.get_db] = _override_get_db
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(session_module.get_db, None)
        patcher.stop()


async def _committed_catalog(
    *, shop_name: str = "Adjust API Shop", sku: str = "ADJ-API-001"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Commit a shop/catalog/variant/User so request sessions can see it."""

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
        user = User(
            clerk_user_id="mock_clerk_id",
            shop_id=shop.id,
            name="Mock User",
            email="mock@example.com",
            role=UserRole.OWNER,
        )
        setup.add_all([variant, user])
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
async def test_adjust_endpoint_commits_quantity_and_movement(
    committed_client: AsyncClient,
) -> None:
    """Regression: without `db.commit()` both writes die with the session.

    The buggy route returned 200 while its flushed writes were rolled back
    when the request session closed. A separate session can only see rows
    that were actually committed.
    """

    shop_id, variant_id = await _committed_catalog()
    try:
        response = await committed_client.post(
            f"/inventory/{variant_id}/adjust",
            params={"quantity_delta": "7.5", "notes": "stock take surplus"},
            headers=_auth_headers(),
        )
        assert response.status_code == 200
        assert Decimal(str(response.json()["quantity"])) == Decimal("7.5")

        async with session_module.AsyncSessionLocal() as check:
            inventory = await inventory_service.get_or_create_inventory(
                check, shop_id=shop_id, variant_id=variant_id
            )
            assert inventory.quantity == Decimal("7.5")

            movements = await _movements_for(check, variant_id)
            adjustments = [
                m
                for m in movements
                if m.movement_type is InventoryMovementType.ADJUSTMENT
            ]
            assert len(adjustments) == 1
            assert adjustments[0].quantity == Decimal("7.5")
            assert adjustments[0].notes == "stock take surplus"
    finally:
        await _delete_shop(shop_id)


@pytest.mark.asyncio
async def test_adjust_endpoint_rejects_delta_below_reserved(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    """A negative adjustment below reserved stock fails and persists nothing."""

    fixture = await _make_variant(api_session)
    await _make_user(api_session, fixture.shop.id)
    await inventory_service.add_stock(
        api_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("10"),
        movement_type=InventoryMovementType.PURCHASE,
    )
    inventory = await inventory_service.get_or_create_inventory(
        api_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    inventory.reserved_quantity = Decimal("8")
    await api_session.flush()

    response = await mocked_api_client.post(
        f"/inventory/{fixture.variant.id}/adjust",
        params={"quantity_delta": "-3"},
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert inventory.quantity == Decimal("10")
    movements = await _movements_for(api_session, fixture.variant.id)
    assert all(
        m.movement_type is not InventoryMovementType.ADJUSTMENT for m in movements
    )


@pytest.mark.asyncio
async def test_adjust_endpoint_rejects_negative_delta_beyond_stock(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    """A deduction past zero is refused without a movement or a write."""

    fixture = await _make_variant(api_session)
    await _make_user(api_session, fixture.shop.id)
    await inventory_service.add_stock(
        api_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("2"),
        movement_type=InventoryMovementType.PURCHASE,
    )

    response = await mocked_api_client.post(
        f"/inventory/{fixture.variant.id}/adjust",
        params={"quantity_delta": "-5"},
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    inventory = await inventory_service.get_or_create_inventory(
        api_session, shop_id=fixture.shop.id, variant_id=fixture.variant.id
    )
    assert inventory.quantity == Decimal("2")
    movements = await _movements_for(api_session, fixture.variant.id)
    assert [m.movement_type for m in movements] == [InventoryMovementType.PURCHASE]


@pytest.mark.asyncio
async def test_adjust_endpoint_commit_failure_leaves_no_partial_changes(
    committed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database failure is never reported as success and writes nothing."""

    shop_id, variant_id = await _committed_catalog()
    try:
        async with session_module.AsyncSessionLocal() as seed:
            await inventory_service.add_stock(
                seed,
                shop_id=shop_id,
                variant_id=variant_id,
                quantity=Decimal("5"),
                movement_type=InventoryMovementType.PURCHASE,
            )
            await seed.commit()

        async def _failing_commit(self: AsyncSession) -> None:
            raise RuntimeError("simulated database failure")

        monkeypatch.setattr(session_module.AsyncSession, "commit", _failing_commit)
        try:
            response = await committed_client.post(
                f"/inventory/{variant_id}/adjust",
                params={"quantity_delta": "2"},
                headers=_auth_headers(),
            )
            assert response.status_code == 500
        finally:
            monkeypatch.undo()

        async with session_module.AsyncSessionLocal() as check:
            inventory = await inventory_service.get_or_create_inventory(
                check, shop_id=shop_id, variant_id=variant_id
            )
            assert inventory.quantity == Decimal("5")
            movements = await _movements_for(check, variant_id)
            assert [m.movement_type for m in movements] == [
                InventoryMovementType.PURCHASE
            ]
    finally:
        await _delete_shop(shop_id)


@pytest.mark.asyncio
async def test_adjust_endpoint_cannot_touch_another_shops_variant(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    """Tenant isolation: another shop's variant is a 404, not a write."""

    shop_a = await _make_variant(api_session, shop_name="Shop A", sku="A-001")
    shop_b = await _make_variant(api_session, shop_name="Shop B", sku="B-001")
    await _make_user(api_session, shop_a.shop.id)
    await inventory_service.add_stock(
        api_session,
        shop_id=shop_b.shop.id,
        variant_id=shop_b.variant.id,
        quantity=Decimal("5"),
        movement_type=InventoryMovementType.PURCHASE,
    )

    response = await mocked_api_client.post(
        f"/inventory/{shop_b.variant.id}/adjust",
        params={"quantity_delta": "3"},
        headers=_auth_headers(),
    )

    assert response.status_code == 404
    inventory = await inventory_service.get_or_create_inventory(
        api_session, shop_id=shop_b.shop.id, variant_id=shop_b.variant.id
    )
    assert inventory.quantity == Decimal("5")
    movements = await _movements_for(api_session, shop_b.variant.id)
    assert [m.movement_type for m in movements] == [InventoryMovementType.PURCHASE]

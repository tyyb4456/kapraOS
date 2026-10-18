"""HTTP API tests for the opening-inventory endpoints.

Covers the contract `app.api.opening_inventory` adds on top of the service:
the read-only preview, the route-owned transaction on posting (commit on
success, rollback on every failure, cache eviction only after commit),
idempotency semantics over HTTP, tenant isolation, and the safe handling of
database-level data exceptions.

Two client styles are used deliberately (same split as `test_inventory.py`):

* `mocked_api_client` shares the test's savepoint-bound session, so fixture
  rows and request rows live in one transaction - ideal for reading back
  what a request wrote within the same test.
* `committed_client` gives every request its own real session, proving the
  route actually commits (and that failed posts persist nothing).
"""

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
from app.cache import keys as cache_keys
from app.cache import service as cache_service
from app.main import app
from app.models import (
    Account,
    Category,
    Inventory,
    InventoryMovement,
    InventoryMovementType,
    LedgerEntry,
    OpeningInventoryBatch,
    OpeningInventoryEntry,
    OpeningInventorySource,
    Product,
    ProductType,
    ProductVariant,
    Shop,
    Unit,
    User,
)
from app.models.user import UserRole
from app.services import inventory as inventory_service
from app.services.accounting import (
    INVENTORY,
    OPENING_BALANCE_EQUITY,
    REFERENCE_OPENING_INVENTORY,
)

PREVIEW_URL = "/inventory/opening/preview"
POST_URL = "/inventory/opening"
LIST_URL = "/inventory/opening"


class CatalogFixture(NamedTuple):
    shop: Shop
    product: Product
    variant: ProductVariant


async def _make_catalog(
    api_session: AsyncSession,
    *,
    shop_name: str = "Opening Fabrics",
    sku: str = "OPEN-001",
    unit: Unit = Unit.METER,
) -> CatalogFixture:
    """Create a shop/category/product/variant chain in the test session."""

    shop = Shop(name=shop_name)
    category = Category(shop=shop, name="Open Fabric")
    api_session.add_all([shop, category])
    await api_session.flush()

    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Premium Linen",
        product_type=ProductType.OPEN_FABRIC,
    )
    api_session.add(product)
    await api_session.flush()

    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku=sku,
        purchase_price=Decimal("420.00"),
        selling_price=Decimal("650.00"),
        unit=unit,
    )
    api_session.add(variant)
    await api_session.flush()

    return CatalogFixture(shop=shop, product=product, variant=variant)


async def _add_variant(
    api_session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    product_id: uuid.UUID,
    sku: str,
    unit: Unit = Unit.PIECE,
) -> ProductVariant:
    variant = ProductVariant(
        shop_id=shop_id,
        product_id=product_id,
        sku=sku,
        purchase_price=Decimal("100.00"),
        selling_price=Decimal("150.00"),
        unit=unit,
    )
    api_session.add(variant)
    await api_session.flush()
    return variant


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


def _line(
    variant_id: uuid.UUID,
    quantity: str,
    unit_cost: str,
    cost_status: str = "known",
) -> dict[str, str]:
    return {
        "variant_id": str(variant_id),
        "quantity": quantity,
        "unit_cost": unit_cost,
        "cost_status": cost_status,
    }


def _post_payload(
    lines: list[dict[str, str]],
    *,
    key: str | None = None,
    source: str = "manual",
    notes: str | None = None,
) -> dict:
    payload: dict = {
        "idempotency_key": key if key is not None else uuid.uuid4().hex,
        "lines": lines,
    }
    if source:
        payload["source"] = source
    if notes is not None:
        payload["notes"] = notes
    return payload


async def _batch_count(session: AsyncSession, shop_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(OpeningInventoryBatch)
                .where(OpeningInventoryBatch.shop_id == shop_id)
            )
        ).scalar_one()
    )


async def _inventory_row(
    session: AsyncSession, variant_id: uuid.UUID
) -> Inventory | None:
    return (
        await session.execute(
            sa.select(Inventory).where(Inventory.variant_id == variant_id)
        )
    ).scalar_one_or_none()


async def _movements_for(
    session: AsyncSession, variant_id: uuid.UUID
) -> list[InventoryMovement]:
    rows = await session.execute(
        sa.select(InventoryMovement)
        .where(InventoryMovement.variant_id == variant_id)
        .order_by(InventoryMovement.created_at, InventoryMovement.id)
    )
    return list(rows.scalars())


async def _ledger_lines(
    session: AsyncSession, shop_id: uuid.UUID, batch_id: uuid.UUID
) -> list[LedgerEntry]:
    rows = await session.execute(
        sa.select(LedgerEntry)
        .where(
            LedgerEntry.shop_id == shop_id,
            LedgerEntry.reference_type == REFERENCE_OPENING_INVENTORY,
            LedgerEntry.reference_id == batch_id,
        )
        .order_by(LedgerEntry.debit.desc())
    )
    return list(rows.scalars())


async def _account_code(
    session: AsyncSession, shop_id: uuid.UUID, account_id: uuid.UUID
) -> str | None:
    return (
        await session.execute(
            sa.select(Account.code).where(
                Account.id == account_id, Account.shop_id == shop_id
            )
        )
    ).scalar_one_or_none()


# --------------------------------------------------------------------------
# committed_client - real per-request sessions (commit/rollback proof)
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def committed_client() -> AsyncGenerator[AsyncClient, None]:
    """An auth-bypassed client whose requests use real per-request sessions."""

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
    *, shop_name: str = "Committed Opening Shop", sku: str = "CMT-OPEN-001"
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


async def _committed_two_shops() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Commit two shops: A has the authenticated user, B only a variant.

    Returns ``(shop_a_id, variant_a_id, shop_b_id, variant_b_id)``. The mock
    identity (`mock_clerk_id`) belongs to shop A, so any write aimed at
    shop B's variant must fail tenant-scoped.
    """

    async with session_module.AsyncSessionLocal() as setup:
        shop_a = Shop(name="Committed Tenant A")
        shop_b = Shop(name="Committed Tenant B")
        setup.add_all([shop_a, shop_b])
        await setup.flush()

        category_a = Category(shop=shop_a, name="Open Fabric")
        category_b = Category(shop=shop_b, name="Open Fabric")
        setup.add_all([category_a, category_b])
        await setup.flush()

        product_a = Product(
            shop_id=shop_a.id,
            category_id=category_a.id,
            name="Premium Linen",
            product_type=ProductType.OPEN_FABRIC,
        )
        product_b = Product(
            shop_id=shop_b.id,
            category_id=category_b.id,
            name="Premium Linen",
            product_type=ProductType.OPEN_FABRIC,
        )
        setup.add_all([product_a, product_b])
        await setup.flush()

        variant_a = ProductVariant(
            shop_id=shop_a.id,
            product_id=product_a.id,
            sku="CTA-001",
            purchase_price=Decimal("420.00"),
            selling_price=Decimal("650.00"),
            unit=Unit.METER,
        )
        variant_b = ProductVariant(
            shop_id=shop_b.id,
            product_id=product_b.id,
            sku="CTB-001",
            purchase_price=Decimal("420.00"),
            selling_price=Decimal("650.00"),
            unit=Unit.METER,
        )
        user = User(
            clerk_user_id="mock_clerk_id",
            shop_id=shop_a.id,
            name="Mock User",
            email="mock@example.com",
            role=UserRole.OWNER,
        )
        setup.add_all([variant_a, variant_b, user])
        await setup.flush()
        await setup.commit()
        return shop_a.id, variant_a.id, shop_b.id, variant_b.id


# --------------------------------------------------------------------------
# Cache-enabled fixture (the general suite runs with CACHE_ENABLED=false)
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def enabled_cache(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None, None]:
    """Enable the in-memory cache backend for one test, empty at both ends."""

    monkeypatch.setattr(cache_service, "_cache_disabled", lambda: False)
    monkeypatch.setattr(cache_service, "_get_client", lambda: None)
    await cache_service.clear_memory_cache()
    cache_service.reset_metrics()
    yield
    await cache_service.clear_memory_cache()


# --------------------------------------------------------------------------
# 1. Preview - read-only, line-level results
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_reports_line_results_without_writing(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    """Preview validates every line, projects WAC, and writes nothing."""

    fixture = await _make_catalog(api_session, shop_name="Preview Shop")
    second = await _add_variant(
        api_session,
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="OPEN-002",
    )
    await _make_user(api_session, fixture.shop.id)
    await inventory_service.add_stock(
        api_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal(10),
        movement_type=InventoryMovementType.PURCHASE,
        unit_cost=Decimal("400.00"),
    )

    unknown_id = uuid.uuid4()
    response = await mocked_api_client.post(
        PREVIEW_URL,
        json={
            "lines": [
                _line(fixture.variant.id, "5", "500.00"),
                _line(unknown_id, "1", "10.00"),
                _line(fixture.variant.id, "1", "10.00"),
                _line(second.id, "2", "150.50", cost_status="estimated"),
            ]
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
    assert Decimal(str(data["total_value"])) == Decimal("2801.00")
    assert data["entry_count"] == 2
    assert data["estimated_entry_count"] == 1

    first = data["lines"][0]
    assert first["sku"] == "OPEN-001"
    assert first["errors"] == []
    assert any("already has" in warning for warning in first["warnings"])
    assert Decimal(str(first["value"])) == Decimal("2500.00")
    assert Decimal(str(first["current_quantity"])) == Decimal("10.000")
    # (10 * 400 + 5 * 500) / 15 = 433.333333... -> 4dp ROUND_HALF_UP
    assert Decimal(str(first["projected_weighted_average_cost"])) == Decimal("433.3333")

    second_line = data["lines"][1]
    assert second_line["sku"] is None
    assert any("not found" in error for error in second_line["errors"])

    duplicate_line = data["lines"][2]
    assert any("more than once" in error for error in duplicate_line["errors"])

    estimated_line = data["lines"][3]
    assert estimated_line["errors"] == []
    assert estimated_line["cost_status"] == "estimated"
    assert Decimal(str(estimated_line["value"])) == Decimal("301.00")

    # Read-only: no batch, no stock movement and no lazily-created inventory
    # row for the never-stocked variant.
    assert await _batch_count(api_session, fixture.shop.id) == 0
    assert await _movements_for(api_session, second.id) == []
    assert await _inventory_row(api_session, second.id) is None


@pytest.mark.asyncio
async def test_preview_rejects_empty_line_list(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_catalog(api_session, shop_name="Empty Preview Shop")
    await _make_user(api_session, fixture.shop.id)

    response = await mocked_api_client.post(
        PREVIEW_URL, json={"lines": []}, headers=_auth_headers()
    )

    assert response.status_code == 422
    assert "at least one line" in response.json()["detail"]


@pytest.mark.asyncio
async def test_preview_treats_another_shops_variant_as_a_line_error(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    """Cross-tenant preview is a line error, never a 404 existence leak."""

    shop_a = await _make_catalog(api_session, shop_name="Preview A", sku="PA-001")
    shop_b = await _make_catalog(api_session, shop_name="Preview B", sku="PB-001")
    await _make_user(api_session, shop_a.shop.id)

    response = await mocked_api_client.post(
        PREVIEW_URL,
        json={"lines": [_line(shop_b.variant.id, "5", "100.00")]},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
    assert data["entry_count"] == 0
    assert any("not found" in error for error in data["lines"][0]["errors"])


# --------------------------------------------------------------------------
# 2. Posting - the happy path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_creates_batch_stock_movements_and_balanced_ledger(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_catalog(api_session, shop_name="Post Shop")
    second = await _add_variant(
        api_session,
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="POST-002",
    )
    user = await _make_user(api_session, fixture.shop.id)

    response = await mocked_api_client.post(
        POST_URL,
        json=_post_payload(
            [
                _line(fixture.variant.id, "10", "420.00"),
                _line(second.id, "3.5", "1000.00", cost_status="estimated"),
            ],
            notes="counted at adoption",
        ),
        headers=_auth_headers(),
    )

    assert response.status_code == 201
    data = response.json()
    batch_id = uuid.UUID(data["id"])
    assert uuid.UUID(data["shop_id"]) == fixture.shop.id
    assert data["source"] == "manual"
    assert data["notes"] == "counted at adoption"
    assert data["entry_count"] == 2
    assert data["estimated_entry_count"] == 1
    assert Decimal(str(data["total_value"])) == Decimal("7700.00")
    assert uuid.UUID(data["created_by_user_id"]) == user.id

    entries_by_sku = {entry["sku"]: entry for entry in data["entries"]}
    assert set(entries_by_sku) == {"OPEN-001", "POST-002"}
    first = entries_by_sku["OPEN-001"]
    assert first["product_name"] == "Premium Linen"
    assert first["unit"] == "meter"
    assert Decimal(str(first["quantity"])) == Decimal("10.000")
    assert Decimal(str(first["unit_cost"])) == Decimal("420.00")
    assert Decimal(str(first["value"])) == Decimal("4200.00")
    assert first["cost_status"] == "known"
    movement = first["movement"]
    assert movement is not None
    assert movement["movement_type"] == InventoryMovementType.OPENING.value
    assert movement["reference_type"] == "OPENING"
    assert uuid.UUID(movement["reference_id"]) == batch_id
    assert Decimal(str(movement["quantity"])) == Decimal("10.000")
    assert entries_by_sku["POST-002"]["cost_status"] == "estimated"

    # Stock landed with the exact submitted costs.
    inventory_first = await _inventory_row(api_session, fixture.variant.id)
    assert inventory_first is not None
    assert inventory_first.quantity == Decimal("10.000")
    assert inventory_first.weighted_average_cost == Decimal("420.0000")
    inventory_second = await _inventory_row(api_session, second.id)
    assert inventory_second is not None
    assert inventory_second.quantity == Decimal("3.500")

    movements = await _movements_for(api_session, fixture.variant.id)
    assert [m.movement_type for m in movements] == [InventoryMovementType.OPENING]
    assert movements[0].reference_id == batch_id

    # Ledger: Debit Inventory 1200 / Credit Opening Balance Equity 3100.
    ledger = await _ledger_lines(api_session, fixture.shop.id, batch_id)
    assert len(ledger) == 2
    debit_line = next(line for line in ledger if line.debit > 0)
    credit_line = next(line for line in ledger if line.credit > 0)
    assert debit_line.credit == Decimal("0.00")
    assert credit_line.debit == Decimal("0.00")
    assert debit_line.debit == Decimal("7700.00")
    assert credit_line.credit == Decimal("7700.00")
    assert (
        await _account_code(api_session, fixture.shop.id, debit_line.account_id)
        == INVENTORY
    )
    assert (
        await _account_code(api_session, fixture.shop.id, credit_line.account_id)
        == OPENING_BALANCE_EQUITY
    )


@pytest.mark.asyncio
async def test_post_success_commits_for_separate_sessions(
    committed_client: AsyncClient,
) -> None:
    """The route owns the transaction: a 201 means the rows are committed."""

    shop_id, variant_id = await _committed_catalog()
    try:
        key = uuid.uuid4().hex
        response = await committed_client.post(
            POST_URL,
            json=_post_payload([_line(variant_id, "7", "420.00")], key=key),
            headers=_auth_headers(),
        )
        assert response.status_code == 201
        batch_id = uuid.UUID(response.json()["id"])

        async with session_module.AsyncSessionLocal() as check:
            batch = await check.get(OpeningInventoryBatch, batch_id)
            assert batch is not None
            assert batch.idempotency_key == key
            assert batch.total_value == Decimal("2940.00")

            inventory = await _inventory_row(check, variant_id)
            assert inventory is not None
            assert inventory.quantity == Decimal("7.000")

            movements = await _movements_for(check, variant_id)
            assert len(movements) == 1
            assert movements[0].movement_type is InventoryMovementType.OPENING

            ledger = await _ledger_lines(check, shop_id, batch_id)
            assert len(ledger) == 2
            assert sum(line.debit for line in ledger) == Decimal("2940.00")
            assert sum(line.credit for line in ledger) == Decimal("2940.00")
    finally:
        await _delete_shop(shop_id)


# --------------------------------------------------------------------------
# 3. Idempotency over HTTP
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_replay_of_same_key_and_payload_returns_same_batch(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_catalog(api_session, shop_name="Replay Shop")
    await _make_user(api_session, fixture.shop.id)
    key = uuid.uuid4().hex
    payload = _post_payload([_line(fixture.variant.id, "10", "420.00")], key=key)

    first = await mocked_api_client.post(
        POST_URL, json=payload, headers=_auth_headers()
    )
    assert first.status_code == 201

    # Same key, same logical payload, different metadata: the committed batch
    # comes back untouched (metadata does not change stock or accounting).
    replay_payload = dict(payload, notes="a later note")
    replay = await mocked_api_client.post(
        POST_URL, json=replay_payload, headers=_auth_headers()
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["notes"] is None

    assert await _batch_count(api_session, fixture.shop.id) == 1
    movements = await _movements_for(api_session, fixture.variant.id)
    assert len(movements) == 1
    inventory = await _inventory_row(api_session, fixture.variant.id)
    assert inventory is not None
    assert inventory.quantity == Decimal("10.000")
    ledger = await _ledger_lines(
        api_session, fixture.shop.id, uuid.UUID(first.json()["id"])
    )
    assert len(ledger) == 2


@pytest.mark.asyncio
async def test_post_conflicts_when_key_reused_for_a_different_payload(
    committed_client: AsyncClient,
) -> None:
    """A reused key with a different payload is a 409 and changes nothing.

    Real per-request sessions are required here: a rejected 409 rolls the
    request's transaction back, and only committed fixture rows (and the
    committed first post) survive to be verified.
    """

    shop_id, variant_id = await _committed_catalog(shop_name="Conflict Shop")
    try:
        key = uuid.uuid4().hex
        first = await committed_client.post(
            POST_URL,
            json=_post_payload([_line(variant_id, "10", "420.00")], key=key),
            headers=_auth_headers(),
        )
        assert first.status_code == 201

        conflict = await committed_client.post(
            POST_URL,
            json=_post_payload([_line(variant_id, "99", "420.00")], key=key),
            headers=_auth_headers(),
        )
        assert conflict.status_code == 409
        assert "different" in conflict.json()["detail"]

        async with session_module.AsyncSessionLocal() as check:
            assert await _batch_count(check, shop_id) == 1
            inventory = await _inventory_row(check, variant_id)
            assert inventory is not None
            assert inventory.quantity == Decimal("10.000")
            movements = await _movements_for(check, variant_id)
            assert len(movements) == 1
    finally:
        await _delete_shop(shop_id)


@pytest.mark.asyncio
async def test_post_rejects_a_second_opening_for_the_same_variant(
    committed_client: AsyncClient,
) -> None:
    """One opening entry per variant, ever - even under a new key."""

    shop_id, variant_id = await _committed_catalog(shop_name="Once Shop")
    try:
        first = await committed_client.post(
            POST_URL,
            json=_post_payload([_line(variant_id, "5", "100.00")]),
            headers=_auth_headers(),
        )
        assert first.status_code == 201

        second = await committed_client.post(
            POST_URL,
            json=_post_payload([_line(variant_id, "5", "100.00")]),
            headers=_auth_headers(),
        )
        assert second.status_code == 409
        assert "already have an opening entry" in second.json()["detail"]

        async with session_module.AsyncSessionLocal() as check:
            assert await _batch_count(check, shop_id) == 1
            movements = await _movements_for(check, variant_id)
            assert len(movements) == 1
    finally:
        await _delete_shop(shop_id)


@pytest.mark.asyncio
async def test_post_requires_an_idempotency_key(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_catalog(api_session, shop_name="Keyless Shop")
    await _make_user(api_session, fixture.shop.id)

    response = await mocked_api_client.post(
        POST_URL,
        json={"lines": [_line(fixture.variant.id, "5", "100.00")]},
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert await _batch_count(api_session, fixture.shop.id) == 0


# --------------------------------------------------------------------------
# 4. Validation and tenant isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_enforces_the_strict_costing_policy(
    committed_client: AsyncClient,
) -> None:
    """`unknown` or zero costs are rejected with nothing written."""

    shop_id, variant_id = await _committed_catalog(shop_name="Costing Shop")
    try:
        unknown = await committed_client.post(
            POST_URL,
            json=_post_payload(
                [_line(variant_id, "5", "100.00", cost_status="unknown")]
            ),
            headers=_auth_headers(),
        )
        assert unknown.status_code == 422
        assert "cannot be posted" in unknown.json()["detail"]

        zero_cost = await committed_client.post(
            POST_URL,
            json=_post_payload([_line(variant_id, "5", "0")]),
            headers=_auth_headers(),
        )
        assert zero_cost.status_code == 422
        assert "greater than 0" in zero_cost.json()["detail"]

        zero_quantity = await committed_client.post(
            POST_URL,
            json=_post_payload([_line(variant_id, "0", "100.00")]),
            headers=_auth_headers(),
        )
        assert zero_quantity.status_code == 422

        async with session_module.AsyncSessionLocal() as check:
            assert await _batch_count(check, shop_id) == 0
            assert await _inventory_row(check, variant_id) is None
            assert await _movements_for(check, variant_id) == []
    finally:
        await _delete_shop(shop_id)


@pytest.mark.asyncio
async def test_post_rejects_unknown_variant_with_404(
    committed_client: AsyncClient,
) -> None:
    shop_id, _ = await _committed_catalog(shop_name="Unknown Shop")
    try:
        response = await committed_client.post(
            POST_URL,
            json=_post_payload([_line(uuid.uuid4(), "5", "100.00")]),
            headers=_auth_headers(),
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

        async with session_module.AsyncSessionLocal() as check:
            assert await _batch_count(check, shop_id) == 0
    finally:
        await _delete_shop(shop_id)


@pytest.mark.asyncio
async def test_post_cannot_open_another_shops_variant(
    committed_client: AsyncClient,
) -> None:
    shop_a_id, _, shop_b_id, variant_b_id = await _committed_two_shops()
    try:
        response = await committed_client.post(
            POST_URL,
            json=_post_payload([_line(variant_b_id, "5", "100.00")]),
            headers=_auth_headers(),
        )

        assert response.status_code == 404

        async with session_module.AsyncSessionLocal() as check:
            assert await _batch_count(check, shop_b_id) == 0
            assert await _batch_count(check, shop_a_id) == 0
            assert await _inventory_row(check, variant_b_id) is None
            assert await _movements_for(check, variant_b_id) == []
    finally:
        await _delete_shop(shop_a_id)
        await _delete_shop(shop_b_id)


@pytest.mark.asyncio
async def test_post_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(
        POST_URL,
        json=_post_payload([_line(uuid.uuid4(), "5", "100.00")]),
    )
    assert response.status_code == 401

    listing = await client.get(LIST_URL)
    assert listing.status_code == 401


# --------------------------------------------------------------------------
# 5. List and detail reads
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_is_tenant_scoped_and_filterable_by_source(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_catalog(api_session, shop_name="List Shop")
    await _make_user(api_session, fixture.shop.id)
    other = await _make_catalog(api_session, shop_name="List Other", sku="LO-001")

    manual = await mocked_api_client.post(
        POST_URL,
        json=_post_payload([_line(fixture.variant.id, "1", "100.00")]),
        headers=_auth_headers(),
    )
    assert manual.status_code == 201
    second_variant = await _add_variant(
        api_session,
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="LIST-002",
    )
    imported = await mocked_api_client.post(
        POST_URL,
        json=_post_payload([_line(second_variant.id, "2", "50.00")], source="import"),
        headers=_auth_headers(),
    )
    assert imported.status_code == 201

    # Another shop's batch must never appear in this shop's list.
    api_session.add(
        OpeningInventoryBatch(
            shop_id=other.shop.id,
            idempotency_key=uuid.uuid4().hex,
            source=OpeningInventorySource.MANUAL,
            entry_count=1,
            total_value=Decimal("10.00"),
        )
    )
    await api_session.flush()

    listing = await mocked_api_client.get(LIST_URL, headers=_auth_headers())
    assert listing.status_code == 200
    ids = {item["id"] for item in listing.json()}
    assert ids == {manual.json()["id"], imported.json()["id"]}

    filtered = await mocked_api_client.get(
        LIST_URL, params={"source": "import"}, headers=_auth_headers()
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [imported.json()["id"]]

    limited = await mocked_api_client.get(
        LIST_URL, params={"limit": 1}, headers=_auth_headers()
    )
    assert limited.status_code == 200
    assert len(limited.json()) == 1


@pytest.mark.asyncio
async def test_get_batch_detail_and_404_for_other_shops_batch(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_catalog(api_session, shop_name="Detail Shop")
    await _make_user(api_session, fixture.shop.id)
    other = await _make_catalog(api_session, shop_name="Detail Other", sku="DO-001")

    posted = await mocked_api_client.post(
        POST_URL,
        json=_post_payload([_line(fixture.variant.id, "4", "250.00")]),
        headers=_auth_headers(),
    )
    assert posted.status_code == 201
    batch_id = posted.json()["id"]

    detail = await mocked_api_client.get(
        f"{LIST_URL}/{batch_id}", headers=_auth_headers()
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == batch_id
    assert len(body["entries"]) == 1
    assert body["entries"][0]["sku"] == "OPEN-001"
    assert body["entries"][0]["movement"] is not None

    missing = await mocked_api_client.get(
        f"{LIST_URL}/{uuid.uuid4()}", headers=_auth_headers()
    )
    assert missing.status_code == 404

    other_batch = OpeningInventoryBatch(
        shop_id=other.shop.id,
        idempotency_key=uuid.uuid4().hex,
        source=OpeningInventorySource.MANUAL,
        entry_count=1,
        total_value=Decimal("10.00"),
    )
    api_session.add(other_batch)
    await api_session.flush()

    cross = await mocked_api_client.get(
        f"{LIST_URL}/{other_batch.id}", headers=_auth_headers()
    )
    assert cross.status_code == 404


# --------------------------------------------------------------------------
# 6. Database-level failures are safe and never "success"
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_numeric_overflow_is_an_actionable_422_and_writes_nothing(
    committed_client: AsyncClient,
) -> None:
    """A value past NUMERIC(14,x) capacity is a safe 422, never a fake 201.

    The asyncpg dialect surfaces the overflow as a generic ``DBAPIError``
    carrying SQLSTATE 22003; the route maps SQLSTATE class 22 to a 422 and
    rolls the transaction back.
    """

    shop_id, variant_id = await _committed_catalog(shop_name="Overflow Shop")
    try:
        response = await committed_client.post(
            POST_URL,
            json=_post_payload([_line(variant_id, "9999999999999.000", "1.00")]),
            headers=_auth_headers(),
        )

        assert response.status_code == 422
        assert "numeric range" in response.json()["detail"]

        async with session_module.AsyncSessionLocal() as check:
            assert await _batch_count(check, shop_id) == 0
            assert await _inventory_row(check, variant_id) is None
            assert await _movements_for(check, variant_id) == []
    finally:
        await _delete_shop(shop_id)


@pytest.mark.asyncio
async def test_post_commit_failure_leaves_no_partial_changes(
    committed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit failure is a 500 and persists nothing - not even flushed rows."""

    shop_id, variant_id = await _committed_catalog()
    try:

        async def _failing_commit(self: AsyncSession) -> None:
            raise RuntimeError("simulated database failure")

        monkeypatch.setattr(session_module.AsyncSession, "commit", _failing_commit)
        try:
            response = await committed_client.post(
                POST_URL,
                json=_post_payload([_line(variant_id, "5", "100.00")]),
                headers=_auth_headers(),
            )
            assert response.status_code == 500
        finally:
            monkeypatch.undo()

        async with session_module.AsyncSessionLocal() as check:
            assert await _batch_count(check, shop_id) == 0
            assert await _inventory_row(check, variant_id) is None
            assert await _movements_for(check, variant_id) == []

            entries = (
                await check.execute(
                    sa.select(sa.func.count())
                    .select_from(OpeningInventoryEntry)
                    .where(OpeningInventoryEntry.shop_id == shop_id)
                )
            ).scalar_one()
            assert entries == 0
    finally:
        await _delete_shop(shop_id)


# --------------------------------------------------------------------------
# 7. Cache eviction only after a committed post
# --------------------------------------------------------------------------


async def _seed_read_caches(
    shop_id: uuid.UUID,
    variant_id: uuid.UUID,
    *,
    other_shop_id: uuid.UUID | None = None,
) -> dict[str, str]:
    keys = {
        "variant": cache_keys.inventory_variant_key(shop_id, variant_id),
        "list": cache_keys.inventory_list_key(shop_id),
        "status": cache_keys.inventory_status_key(shop_id),
        "dashboard": cache_keys.dashboard_key(shop_id),
        "financial": cache_keys.financial_summary_key(shop_id),
        # Not part of the opening-inventory footprint: must survive.
        "sales": cache_keys.sales_summary_today_key(shop_id),
    }
    for key in keys.values():
        assert await cache_service.set_json(key, {"cached": 1}, 60) is True
    if other_shop_id is not None:
        other_key = cache_keys.dashboard_key(other_shop_id)
        assert await cache_service.set_json(other_key, {"cached": 1}, 60) is True
        keys["other_dashboard"] = other_key
    return keys


@pytest.mark.asyncio
async def test_successful_post_evicts_read_caches_after_commit(
    mocked_api_client: AsyncClient,
    api_session: AsyncSession,
    enabled_cache: None,
) -> None:
    fixture = await _make_catalog(api_session, shop_name="Cache Shop")
    await _make_user(api_session, fixture.shop.id)
    other = await _make_catalog(api_session, shop_name="Cache Other", sku="CO-001")

    keys = await _seed_read_caches(
        fixture.shop.id, fixture.variant.id, other_shop_id=other.shop.id
    )

    response = await mocked_api_client.post(
        POST_URL,
        json=_post_payload([_line(fixture.variant.id, "5", "100.00")]),
        headers=_auth_headers(),
    )
    assert response.status_code == 201

    for name in ("variant", "list", "status", "dashboard", "financial"):
        assert await cache_service.get_json(keys[name]) is None, name
    assert await cache_service.get_json(keys["sales"]) is not None
    assert await cache_service.get_json(keys["other_dashboard"]) is not None


@pytest.mark.asyncio
async def test_failed_post_does_not_evict_read_caches(
    mocked_api_client: AsyncClient,
    api_session: AsyncSession,
    enabled_cache: None,
) -> None:
    fixture = await _make_catalog(api_session, shop_name="Cache Fail Shop")
    await _make_user(api_session, fixture.shop.id)
    keys = await _seed_read_caches(fixture.shop.id, fixture.variant.id)

    response = await mocked_api_client.post(
        POST_URL,
        json=_post_payload(
            [_line(fixture.variant.id, "5", "100.00", cost_status="unknown")]
        ),
        headers=_auth_headers(),
    )
    assert response.status_code == 422

    assert await cache_service.get_json(keys["dashboard"]) is not None
    assert await cache_service.get_json(keys["variant"]) is not None

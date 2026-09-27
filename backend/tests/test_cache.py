"""Step 10 Redis cache-layer tests.

Focused coverage for the spec section 22 checklist, plus the section 23
read/mutate/read integration cycle for customer Khata, supplier Khata,
inventory and dashboard.

All tests run against the in-memory fallback backend (no Redis server
needed): an autouse fixture pins ``_get_client`` to ``None`` and clears
state, while the Redis-failure tests inject a broken client explicitly.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import app.cache.service as cache_service
from app.ai.state import TenantContext
from app.cache import cached_reads, keys


@pytest_asyncio.fixture(autouse=True)
async def _isolated_cache(monkeypatch):
    """Enable the cache on the memory backend, reset state for every test.

    The general suite runs with ``CACHE_ENABLED=false`` (see conftest);
    these focused tests explicitly opt back in so they exercise the real
    read-through/invalidation paths without needing a Redis server.
    """
    monkeypatch.setattr(cache_service, "_cache_disabled", lambda: False)
    monkeypatch.setattr(cache_service, "_get_client", lambda: None)
    await cache_service.clear_memory_cache()
    cache_service.reset_metrics()
    yield
    await cache_service.clear_memory_cache()


class _BrokenRedis:
    """A Redis client that fails every operation (outage simulation)."""

    async def get(self, *args, **kwargs):
        raise RuntimeError("redis down")

    async def set(self, *args, **kwargs):
        raise RuntimeError("redis down")

    async def delete(self, *args, **kwargs):
        raise RuntimeError("redis down")

    async def scan(self, *args, **kwargs):
        raise RuntimeError("redis down")


def _shop() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Basic: miss -> store -> hit avoids the loader (spec 22.1-4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_miss_calls_loader_then_hit_avoids_it():
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return {"status": "ok", "n": calls}

    first, hit1 = await cache_service.cached_json("shop:x:probe", 60, loader)
    assert hit1 is False
    assert first == {"status": "ok", "n": 1}
    second, hit2 = await cache_service.cached_json("shop:x:probe", 60, loader)
    assert hit2 is True
    assert second == {"status": "ok", "n": 1}
    assert calls == 1  # second read never touched the loader (DB)

    metrics = cache_service.get_metrics()
    assert metrics["misses"] >= 1
    assert metrics["hits"] >= 1
    assert metrics["sets"] >= 1


# ---------------------------------------------------------------------------
# TTL (spec 22.5-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ttl_expiry_falls_back_to_loader():
    await cache_service.set_json("shop:x:ttl", {"v": 1}, ttl=1)
    assert await cache_service.get_json("shop:x:ttl") == {"v": 1}
    await asyncio.sleep(1.2)
    assert await cache_service.get_json("shop:x:ttl") is None


# ---------------------------------------------------------------------------
# Redis failure: fail open, app stays functional (spec 22.7-9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_get_failure_still_serves_db_result(monkeypatch):
    monkeypatch.setattr(cache_service, "_get_client", _BrokenRedis)
    served = await cache_service.cached_json(
        "shop:x:down", 60, lambda: asyncio.sleep(0, result={"ok": True})
    )
    assert served[0] == {"ok": True}
    assert cache_service.get_metrics()["get_errors"] >= 1


@pytest.mark.asyncio
async def test_redis_set_failure_response_still_served(monkeypatch):
    real_get = cache_service.get_json

    class _SetFails(_BrokenRedis):
        async def get(self, *args, **kwargs):
            return None

    monkeypatch.setattr(cache_service, "_get_client", _SetFails)
    served = await cache_service.cached_json(
        "shop:x:down", 60, lambda: asyncio.sleep(0, result={"ok": 1})
    )
    assert served[0] == {"ok": 1}
    assert cache_service.get_metrics()["set_errors"] >= 1
    assert await real_get("shop:x:down") is None  # nothing poisoned


@pytest.mark.asyncio
async def test_verbose_logging_emits_hit_miss_lines(monkeypatch, caplog):
    """CACHE_LOG_HITS=true makes hits/misses visible at INFO level."""
    import logging

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "cache_log_hits", True)
    # NOTE: something in the test-process import chain (LLM libs) leaves
    # logging disable flags set on app loggers, which would hide every
    # record from caplog. Neutralize it: this test verifies OUR emit
    # behavior, not the environment's logging surgery.
    lac = logging.getLogger("app.cache")
    monkeypatch.setattr(lac, "disabled", False)
    with caplog.at_level(logging.INFO, logger="app.cache"):
        await cache_service.set_json("shop:x:verbose", {"v": 1}, 60)
        assert await cache_service.get_json("shop:x:verbose") == {"v": 1}
        assert await cache_service.get_json("shop:x:missing") is None
    messages = [r.getMessage() for r in caplog.records]
    assert any("cache HIT" in m for m in messages)
    assert any("cache MISS" in m for m in messages)
    assert any("cache SET" in m for m in messages)


@pytest.mark.asyncio
async def test_redis_factory_failure_fails_open(monkeypatch):
    import app.cache.service as svc

    monkeypatch.setattr(
        svc, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    # Nothing raises; the memory fallback keeps the app functional.
    assert await svc.set_json("shop:x:any", {"a": 1}, 10) is True
    assert await svc.get_json("shop:x:any") == {"a": 1}
    assert await svc.delete("shop:x:any") is True
    assert await svc.delete_pattern("shop:x:any*") == 0


# ---------------------------------------------------------------------------
# Tenant isolation (spec 22.10-11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_cache_keys_are_isolated():
    shop_a, shop_b = _shop(), _shop()
    customer = uuid.uuid4()
    assert keys.customer_summary_key(shop_a, customer) != keys.customer_summary_key(
        shop_b, customer
    )
    assert keys.dashboard_key(shop_a) != keys.dashboard_key(shop_b)

    await cache_service.set_json(
        keys.customer_summary_key(shop_a, customer), {"balance": "10.00"}, 60
    )
    # Shop B cannot read shop A's cached data.
    assert (
        await cache_service.get_json(keys.customer_summary_key(shop_b, customer))
        is None
    )
    assert await cache_service.get_json(
        keys.customer_summary_key(shop_a, customer)
    ) == {"balance": "10.00"}


def test_identical_resource_ids_produce_different_keys():
    shop_a, shop_b = uuid.uuid4(), uuid.uuid4()
    resource = uuid.uuid4()
    pairs = [
        (
            keys.customer_summary_key(shop_a, resource),
            keys.customer_summary_key(shop_b, resource),
        ),
        (
            keys.supplier_balance_key(shop_a, resource),
            keys.supplier_balance_key(shop_b, resource),
        ),
        (keys.dashboard_key(shop_a), keys.dashboard_key(shop_b)),
        (
            keys.inventory_variant_key(shop_a, resource),
            keys.inventory_variant_key(shop_b, resource),
        ),
    ]
    for left, right in pairs:
        assert left != right
        assert str(shop_a) in left and str(shop_b) in right


def test_all_builders_scope_to_shop_prefix():
    shop = uuid.uuid4()
    sample_keys = [
        keys.dashboard_key(shop),
        keys.customer_summary_key(shop, uuid.uuid4()),
        keys.customer_balance_key(shop, uuid.uuid4()),
        keys.supplier_summary_key(shop, uuid.uuid4()),
        keys.inventory_variant_key(shop, uuid.uuid4()),
        keys.product_detail_key(shop, uuid.uuid4()),
        keys.sales_summary_today_key(shop),
    ]
    for key in sample_keys:
        assert key.startswith(f"shop:{shop}:")


# ---------------------------------------------------------------------------
# Invalidation mapping (spec 22.12-18 + section 14: no global flush)
# ---------------------------------------------------------------------------


async def _seed_shop_cache(shop, customer, supplier, variant):
    await cache_service.set_json(keys.dashboard_key(shop), {"d": 1}, 60)
    await cache_service.set_json(
        keys.financial_summary_key(shop, period="this_month"), {"f": 1}, 60
    )
    await cache_service.set_json(
        keys.customer_summary_key(shop, customer), {"c": 1}, 60
    )
    await cache_service.set_json(
        keys.customer_balance_key(shop, customer), {"c": 1}, 60
    )
    await cache_service.set_json(
        keys.supplier_summary_key(shop, supplier), {"s": 1}, 60
    )
    await cache_service.set_json(
        keys.supplier_balance_key(shop, supplier), {"s": 1}, 60
    )
    await cache_service.set_json(
        keys.inventory_variant_key(shop, variant), {"i": 1}, 60
    )
    await cache_service.set_json(
        keys.sales_aggregate_key(shop, start="2026-01-01"), {"sa": 1}, 60
    )
    await cache_service.set_json(
        keys.purchase_aggregate_key(shop, start="2026-01-01"), {"pa": 1}, 60
    )
    await cache_service.set_json(
        keys.expense_aggregate_key(shop, start="2026-01-01"), {"ea": 1}, 60
    )


@pytest.mark.asyncio
async def test_sale_invalidates_customer_inventory_dashboard_sales():
    shop, customer, supplier, variant = (
        _shop(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    await _seed_shop_cache(shop, customer, supplier, variant)
    await cache_service.after_sale_committed(
        shop, customer_id=customer, variant_ids=[variant]
    )
    assert await cache_service.get_json(keys.dashboard_key(shop)) is None
    assert (
        await cache_service.get_json(keys.customer_summary_key(shop, customer)) is None
    )
    assert (
        await cache_service.get_json(keys.inventory_variant_key(shop, variant)) is None
    )
    assert (
        await cache_service.get_json(keys.sales_aggregate_key(shop, start="2026-01-01"))
        is None
    )
    # Untouched domains survive (no over-invalidation).
    assert await cache_service.get_json(keys.supplier_summary_key(shop, supplier)) == {
        "s": 1
    }
    assert await cache_service.get_json(
        keys.purchase_aggregate_key(shop, start="2026-01-01")
    ) == {"pa": 1}
    assert await cache_service.get_json(
        keys.expense_aggregate_key(shop, start="2026-01-01")
    ) == {"ea": 1}


@pytest.mark.asyncio
async def test_customer_payment_invalidates_customer_caches():
    shop, customer, supplier, variant = (
        _shop(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    await _seed_shop_cache(shop, customer, supplier, variant)
    await cache_service.after_customer_payment_committed(shop, customer)
    assert (
        await cache_service.get_json(keys.customer_summary_key(shop, customer)) is None
    )
    assert (
        await cache_service.get_json(keys.customer_balance_key(shop, customer)) is None
    )
    assert await cache_service.get_json(keys.dashboard_key(shop)) is None
    assert await cache_service.get_json(keys.supplier_summary_key(shop, supplier)) == {
        "s": 1
    }


@pytest.mark.asyncio
async def test_supplier_payment_invalidates_supplier_caches():
    shop, customer, supplier, variant = (
        _shop(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    await _seed_shop_cache(shop, customer, supplier, variant)
    await cache_service.after_supplier_payment_committed(shop, supplier)
    assert (
        await cache_service.get_json(keys.supplier_summary_key(shop, supplier)) is None
    )
    assert (
        await cache_service.get_json(keys.supplier_balance_key(shop, supplier)) is None
    )
    assert await cache_service.get_json(keys.dashboard_key(shop)) is None
    assert await cache_service.get_json(keys.customer_summary_key(shop, customer)) == {
        "c": 1
    }


@pytest.mark.asyncio
async def test_purchase_invalidates_inventory_supplier_dashboard():
    shop, customer, supplier, variant = (
        _shop(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    await _seed_shop_cache(shop, customer, supplier, variant)
    await cache_service.after_purchase_committed(
        shop, supplier_id=supplier, variant_ids=[variant]
    )
    assert (
        await cache_service.get_json(keys.supplier_summary_key(shop, supplier)) is None
    )
    assert (
        await cache_service.get_json(keys.inventory_variant_key(shop, variant)) is None
    )
    assert await cache_service.get_json(keys.dashboard_key(shop)) is None
    assert (
        await cache_service.get_json(
            keys.purchase_aggregate_key(shop, start="2026-01-01")
        )
        is None
    )
    assert await cache_service.get_json(keys.customer_summary_key(shop, customer)) == {
        "c": 1
    }


@pytest.mark.asyncio
async def test_expense_invalidates_dashboard_and_expense_caches():
    shop, customer, supplier, variant = (
        _shop(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    await _seed_shop_cache(shop, customer, supplier, variant)
    await cache_service.after_expense_committed(shop)
    assert (
        await cache_service.get_json(
            keys.expense_aggregate_key(shop, start="2026-01-01")
        )
        is None
    )
    assert await cache_service.get_json(keys.dashboard_key(shop)) is None
    assert await cache_service.get_json(keys.customer_summary_key(shop, customer)) == {
        "c": 1
    }
    assert await cache_service.get_json(
        keys.sales_aggregate_key(shop, start="2026-01-01")
    ) == {"sa": 1}


@pytest.mark.asyncio
async def test_returns_invalidate_affected_caches():
    shop, customer, supplier, variant = (
        _shop(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    await _seed_shop_cache(shop, customer, supplier, variant)
    await cache_service.after_customer_return_committed(
        shop, customer_id=customer, variant_ids=[variant]
    )
    assert (
        await cache_service.get_json(keys.customer_summary_key(shop, customer)) is None
    )
    assert (
        await cache_service.get_json(keys.inventory_variant_key(shop, variant)) is None
    )
    assert await cache_service.get_json(keys.dashboard_key(shop)) is None

    await _seed_shop_cache(shop, customer, supplier, variant)
    await cache_service.after_supplier_return_committed(
        shop, supplier_id=supplier, variant_ids=[variant]
    )
    assert (
        await cache_service.get_json(keys.supplier_summary_key(shop, supplier)) is None
    )
    assert (
        await cache_service.get_json(keys.inventory_variant_key(shop, variant)) is None
    )


@pytest.mark.asyncio
async def test_no_global_flush_pattern_is_refused():
    shop = _shop()
    await cache_service.set_json(keys.dashboard_key(shop), {"d": 1}, 60)
    assert await cache_service.delete_pattern("*") == 0
    assert await cache_service.delete_pattern("other:*") == 0
    assert await cache_service.delete_pattern("shop:") == 0
    # Tenant data untouched.
    assert await cache_service.get_json(keys.dashboard_key(shop)) == {"d": 1}


# ---------------------------------------------------------------------------
# Query differentiation (spec 22.19-20)
# ---------------------------------------------------------------------------


def test_different_date_ranges_produce_different_keys():
    shop = _shop()
    assert keys.sales_aggregate_key(
        shop, start="2026-01-01", end="2026-01-31"
    ) != keys.sales_aggregate_key(shop, start="2026-02-01", end="2026-02-28")
    assert keys.purchase_aggregate_key(
        shop, start="2026-01-01"
    ) != keys.purchase_aggregate_key(shop, start=None)
    assert keys.expense_aggregate_key(
        shop, category="rent"
    ) != keys.expense_aggregate_key(shop, category="salary")


def test_different_pagination_produce_different_keys():
    shop = _shop()
    customer = uuid.uuid4()
    assert keys.sales_list_key(shop, limit=100, offset=0) != keys.sales_list_key(
        shop, limit=100, offset=100
    )
    assert keys.sales_list_key(shop, limit=50, offset=0) != keys.sales_list_key(
        shop, limit=100, offset=0
    )
    assert keys.customer_statement_key(
        shop, customer, limit=100, offset=0
    ) != keys.customer_statement_key(shop, customer, limit=100, offset=100)


def test_same_params_same_key_regardless_of_order():
    assert keys.filters_hash({"a": 1, "b": 2}) == keys.filters_hash({"b": 2, "a": 1})
    shop = _shop()
    assert keys.sales_list_key(
        shop, customer_id="c", status="x", limit=10, offset=0
    ) == keys.sales_list_key(shop, limit=10, offset=0, status="x", customer_id="c")


# ---------------------------------------------------------------------------
# AI shares the same cache (spec 22.21-22)
# ---------------------------------------------------------------------------


def test_ai_reads_share_single_cache_implementation():
    import app.ai.tools.business_reads as reads

    assert reads.cached_reads is cached_reads
    import pathlib

    source = pathlib.Path(reads.__file__).read_text()
    # No second Redis client / cache framework inside the AI layer: the only
    # cache access goes through the shared cached_reads wrappers.
    assert "import redis" not in source
    assert "from redis" not in source
    assert "get_redis" not in source
    assert "Redis(" not in source


# ---------------------------------------------------------------------------
# DB-backed integration (spec 23)
# ---------------------------------------------------------------------------


async def _make_shop_with_catalog(db: AsyncSession, name: str = "Cache Shop"):
    from app.models import (
        Category,
        Product,
        ProductType,
        ProductVariant,
        Shop,
        Unit,
    )

    shop = Shop(name=name)
    db.add(shop)
    await db.flush()
    category = Category(shop_id=shop.id, name="Lawn")
    db.add(category)
    await db.flush()
    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Black Lawn",
        product_type=ProductType.OPEN_FABRIC,
    )
    db.add(product)
    await db.flush()
    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku=f"BL-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("500.00"),
        selling_price=Decimal("800.00"),
        unit=Unit.METER,
    )
    db.add(variant)
    await db.flush()
    return shop, variant


@pytest.mark.asyncio
async def test_customer_khata_read_mutate_read_cycle(db_session: AsyncSession):
    """First read misses, second hits, payment invalidation forces refresh."""
    from app.models import Customer
    from app.services import receivables as receivables_service

    shop, variant = await _make_shop_with_catalog(db_session)
    customer = Customer(shop_id=shop.id, name="Ahmed")
    db_session.add(customer)
    await db_session.flush()

    # Cold read populates Redis; identical read hits without touching the DB.
    first = await cached_reads.get_cached_customer_summary(
        db_session, shop_id=shop.id, customer_id=customer.id
    )
    assert first.outstanding_balance == Decimal("0.00")
    metrics_before = cache_service.get_metrics()

    calls = 0
    original = receivables_service.get_customer_summary

    async def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    # NOTE: cached_reads looks the symbol up on the receivables module at
    # call time, so patching the module attribute is observed.
    from unittest import mock

    with mock.patch.object(
        receivables_service, "get_customer_summary", side_effect=counting
    ):
        second = await cached_reads.get_cached_customer_summary(
            db_session, shop_id=shop.id, customer_id=customer.id
        )
    assert second.outstanding_balance == Decimal("0.00")
    assert calls == 0  # cache hit avoided the DB query
    assert cache_service.get_metrics()["hits"] > metrics_before["hits"]

    # A sale mutates the Khata: post-commit invalidation, next read is fresh.
    from app.models.inventory import InventoryMovementType
    from app.services import inventory as inventory_service
    from app.services.sales import SaleItemInput, create_sale

    await inventory_service.add_stock(
        db_session,
        shop_id=shop.id,
        variant_id=variant.id,
        quantity=Decimal("10.000"),
        movement_type=InventoryMovementType.PURCHASE,
        unit_cost=Decimal("500.00"),
    )
    await create_sale(
        db_session,
        shop_id=shop.id,
        items=[
            SaleItemInput(
                variant_id=variant.id,
                quantity=Decimal("2.000"),
                unit_price=Decimal("800.00"),
            )
        ],
        customer_id=customer.id,
    )
    await db_session.flush()
    await cache_service.after_sale_committed(
        shop.id, customer_id=customer.id, variant_ids=[variant.id]
    )
    third = await cached_reads.get_cached_customer_summary(
        db_session, shop_id=shop.id, customer_id=customer.id
    )
    assert third.outstanding_balance == Decimal("1600.00")
    assert third.total_purchases == Decimal("1600.00")


@pytest.mark.asyncio
async def test_supplier_khata_read_mutate_read_cycle(db_session: AsyncSession):
    from app.models import Supplier
    from app.services.purchases import PurchaseItemInput, create_purchase

    shop, variant = await _make_shop_with_catalog(db_session)
    supplier = Supplier(shop_id=shop.id, name="Al-Madina")
    db_session.add(supplier)
    await db_session.flush()

    first = await cached_reads.get_cached_supplier_summary(
        db_session, shop_id=shop.id, supplier_id=supplier.id
    )
    assert first.outstanding_balance == Decimal("0.00")
    second = await cached_reads.get_cached_supplier_summary(
        db_session, shop_id=shop.id, supplier_id=supplier.id
    )
    assert second.outstanding_balance == Decimal("0.00")
    assert cache_service.get_metrics()["hits"] >= 1

    await create_purchase(
        db_session,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=variant.id,
                quantity=Decimal("5.000"),
                unit_cost=Decimal("500.00"),
            )
        ],
    )
    await db_session.flush()
    await cache_service.after_purchase_committed(
        shop.id, supplier_id=supplier.id, variant_ids=[variant.id]
    )
    third = await cached_reads.get_cached_supplier_summary(
        db_session, shop_id=shop.id, supplier_id=supplier.id
    )
    assert third.outstanding_balance == Decimal("2500.00")


@pytest.mark.asyncio
async def test_inventory_and_dashboard_cycles(db_session: AsyncSession):
    from app.cache import keys as cache_keys
    from app.models.inventory import InventoryMovementType
    from app.services import inventory as inventory_service

    shop, variant = await _make_shop_with_catalog(db_session)

    # Dashboard: cold then cached.
    first = await cached_reads.get_cached_dashboard(db_session, shop_id=shop.id)
    second = await cached_reads.get_cached_dashboard(db_session, shop_id=shop.id)
    assert first.today_sales == second.today_sales
    assert cache_service.get_metrics()["hits"] >= 1

    # Inventory variant payload cached, then adjust invalidates it.
    payload = {
        "variant_id": str(variant.id),
        "quantity": "0.000",
    }
    key = cache_keys.inventory_variant_key(shop.id, variant.id)
    await cache_service.set_json(key, payload, cache_service.TTL_INVENTORY_SECONDS)
    assert await cache_service.get_json(key) == payload
    await inventory_service.add_stock(
        db_session,
        shop_id=shop.id,
        variant_id=variant.id,
        quantity=Decimal("3.000"),
        movement_type=InventoryMovementType.PURCHASE,
        unit_cost=Decimal("500.00"),
    )
    await db_session.flush()
    await cache_service.after_inventory_adjust_committed(shop.id, variant.id)
    assert await cache_service.get_json(key) is None

    # Dashboard reflects the mutation only after invalidation above.
    await cache_service.after_expense_committed(shop.id)
    assert await cache_service.get_json(cache_keys.dashboard_key(shop.id)) is None


@pytest.mark.asyncio
async def test_ai_customer_tool_benefits_from_route_cache(
    db_session: AsyncSession,
):
    """The AI Khata tool hits the entry the REST path cached (one cache)."""
    from app.ai.tools.business_reads import build_read_tools
    from app.models import Customer

    shop, _variant = await _make_shop_with_catalog(db_session)
    customer = Customer(shop_id=shop.id, name="Ahmed")
    db_session.add(customer)
    await db_session.flush()

    tenant = TenantContext(shop_id=shop.id, user_id=uuid.uuid4(), clerk_user_id="t")
    # REST-equivalent read populates the cache (miss).
    await cached_reads.get_cached_customer_summary(
        db_session, shop_id=shop.id, customer_id=customer.id
    )
    hits_before = cache_service.get_metrics()["hits"]

    tools = {t.name: t for t in build_read_tools(db_session, tenant)}
    out = await tools["get_customer_account_summary"].ainvoke(
        {"customer_name": "Ahmed"}
    )
    assert out["status"] == "ok"
    assert out["outstanding_balance"] == "0.00"
    assert cache_service.get_metrics()["hits"] > hits_before


@pytest.mark.asyncio
async def test_not_found_results_are_not_negatively_cached(
    db_session: AsyncSession,
):
    """V1 never caches 'not found' (spec 19)."""
    shop, _variant = await _make_shop_with_catalog(db_session)
    from app.services import receivables as receivables_service

    with pytest.raises(receivables_service.CustomerNotFoundError):
        await cached_reads.get_cached_customer_summary(
            db_session, shop_id=shop.id, customer_id=uuid.uuid4()
        )
    # Nothing stored under that (unknown) id's key.
    assert cache_service.get_metrics()["hits"] == 0


# ---------------------------------------------------------------------------
# HTTP route coverage with the cache enabled (wiring proof)
# ---------------------------------------------------------------------------


async def _make_user_for_shop(db_session: AsyncSession, shop_id: uuid.UUID):
    from app.models import User
    from app.models.user import UserRole

    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=shop_id,
        name="Mock User",
        email="mock@example.com",
        role=UserRole.OWNER,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_dashboard_route_hit_and_sale_invalidation(
    mocked_api_client, api_session: AsyncSession
):
    """GET /reports/dashboard is cached; POST /sales evicts it post-commit."""
    from httpx import AsyncClient

    from app.models.inventory import InventoryMovementType
    from app.services import inventory as inventory_service

    client: AsyncClient = mocked_api_client
    shop, variant = await _make_shop_with_catalog(api_session)
    await _make_user_for_shop(api_session, shop.id)
    await inventory_service.add_stock(
        api_session,
        shop_id=shop.id,
        variant_id=variant.id,
        quantity=Decimal("10.000"),
        movement_type=InventoryMovementType.PURCHASE,
        unit_cost=Decimal("500.00"),
    )

    first = await client.get("/reports/dashboard")
    assert first.status_code == 200
    second = await client.get("/reports/dashboard")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert cache_service.get_metrics()["hits"] >= 1

    sale = await client.post(
        "/sales",
        json={
            "items": [
                {
                    "variant_id": str(variant.id),
                    "quantity": 2.0,
                    "unit_price": 800.0,
                    "discount": 0,
                }
            ],
            "payments": [{"amount": 1600.0, "method": "cash"}],
        },
    )
    assert sale.status_code == 201
    assert cache_service.get_metrics()["invalidations"] >= 1

    third = await client.get("/reports/dashboard")
    assert third.status_code == 200
    assert Decimal(str(third.json()["today_sales"])) == Decimal("1600.00")


@pytest.mark.asyncio
async def test_customer_summary_route_cached(
    mocked_api_client, api_session: AsyncSession
):
    """GET /customers/{id}/summary hits the shared customer cache."""
    from httpx import AsyncClient

    from app.models import Customer

    client: AsyncClient = mocked_api_client
    shop, _variant = await _make_shop_with_catalog(api_session)
    await _make_user_for_shop(api_session, shop.id)
    customer = Customer(shop_id=shop.id, name="Ahmed")
    api_session.add(customer)
    await api_session.flush()

    first = await client.get(f"/customers/{customer.id}/summary")
    assert first.status_code == 200
    second = await client.get(f"/customers/{customer.id}/summary")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert cache_service.get_metrics()["hits"] >= 1

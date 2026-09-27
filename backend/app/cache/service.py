"""Small reusable read-cache abstraction (Step 10).

Only what V1 needs: ``get_json`` / ``set_json`` / ``delete`` /
``delete_many`` / ``delete_pattern`` plus ``cached_json`` and
tenant-scoped invalidation helpers. JSON is the only serialization
format — ORM objects are never pickled; callers cache Pydantic
``mode="json"`` dicts/lists and re-validate on hit.

Fail-open everywhere: every function catches Redis errors, logs them
without sensitive data, updates the error counters, and returns a safe
fallback (miss / ``False`` / ``0``) so PostgreSQL serves the request.

Backend selection:

* ``REDIS_URL`` set + reachable → shared async Redis client.
* otherwise → process-local in-memory dict with TTL (same key/TTL/
  invalidation semantics; safe for local dev and tests without a server).

The cache is never authoritative: invalidation runs **after** the DB
commit (routes own the transaction), so a rolled-back mutation only
over-evicts (a harmless extra miss), and a failed invalidation only
leaves a short-TTL stale entry behind.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.cache import keys as cache_keys
from app.cache.redis import get_redis
from app.config import get_settings

logger = logging.getLogger("app.cache")

# ---------------------------------------------------------------------------
# TTL strategy (seconds) — centralized so tuning never touches business logic.
# Very-frequently-changing reads are short; catalog data lives longer.
# ---------------------------------------------------------------------------

TTL_DASHBOARD_SECONDS = 20
TTL_CUSTOMER_SECONDS = 20
TTL_SUPPLIER_SECONDS = 20
TTL_INVENTORY_SECONDS = 20
TTL_STATEMENT_SECONDS = 20
TTL_SALES_SECONDS = 45
TTL_PURCHASE_SECONDS = 45
TTL_EXPENSE_SECONDS = 45
TTL_FINANCIAL_SECONDS = 30
TTL_PRODUCT_SECONDS = 600  # 10 minutes (catalog changes slowly)

# ---------------------------------------------------------------------------
# Metrics (process-local counters; lightweight observability for V1).
# ---------------------------------------------------------------------------

_metrics: dict[str, int] = {
    "hits": 0,
    "misses": 0,
    "sets": 0,
    "get_errors": 0,
    "set_errors": 0,
    "deletes": 0,
    "delete_errors": 0,
    "invalidations": 0,
}
_metrics_lock = asyncio.Lock()
# Memory backend is synchronous state guarded by a threading-free approach:
# all access happens in async functions; a plain dict + asyncio lock is enough.
_memory_store: dict[str, tuple[str, float | None]] = {}
_memory_lock = asyncio.Lock()


def get_metrics() -> dict[str, int]:
    """Snapshot of cache counters (hits/misses/errors/invalidations)."""
    return dict(_metrics)


def reset_metrics() -> None:
    """Reset counters (tests)."""
    for key in _metrics:
        _metrics[key] = 0


async def _bump(counter: str, amount: int = 1) -> None:
    _metrics[counter] = _metrics.get(counter, 0) + amount


def _cache_disabled() -> bool:
    try:
        settings = get_settings()
    except Exception:  # noqa: BLE001 — fail open
        return True
    return not bool(getattr(settings, "cache_enabled", True))


def _log_key(key: str) -> str:
    """Key prefix for logs (never log values — they hold business data)."""
    return key[:80]


def _verbose() -> bool:
    """True when CACHE_LOG_HITS is on (visible HIT/MISS lines in terminal)."""
    try:
        return bool(getattr(get_settings(), "cache_log_hits", False))
    except Exception:  # noqa: BLE001 — fail open
        return False


def _log_hit(key: str) -> None:
    if _verbose():
        logger.info(
            "cache HIT key=%s (served from Redis/memory, DB skipped)", _log_key(key)
        )
    else:
        logger.debug("cache hit key=%s", _log_key(key))


def _log_miss(key: str, reason: str = "miss") -> None:
    if _verbose():
        logger.info(
            "cache MISS key=%s (%s; querying PostgreSQL)", _log_key(key), reason
        )
    else:
        logger.debug("cache miss key=%s", _log_key(key))


def _log_set(key: str, ttl: int) -> None:
    if _verbose():
        logger.info(
            "cache SET key=%s ttl=%ss (stored after DB read)", _log_key(key), ttl
        )
    else:
        logger.debug("cache set key=%s ttl=%s", _log_key(key), ttl)


def _get_client() -> Any | None:
    """Shared Redis client, or ``None`` when unavailable. Never raises."""
    try:
        return get_redis()
    except Exception:  # noqa: BLE001 — fail open
        return None


# ---------------------------------------------------------------------------
# Core primitives
# ---------------------------------------------------------------------------


async def get_json(key: str) -> Any | None:
    """Return the cached JSON value, or ``None`` on miss/error/disabled."""
    if _cache_disabled():
        return None
    client = _get_client()
    if client is not None:
        try:
            raw = await client.get(key)
        except Exception as exc:  # noqa: BLE001 — fail open
            await _bump("get_errors")
            logger.warning(
                "cache GET failed key=%s err=%s; falling back to DB",
                _log_key(key),
                type(exc).__name__,
            )
            return None
        if raw is None:
            await _bump("misses")
            _log_miss(key)
            return None
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            await _bump("misses")
            _log_miss(key, reason="unparseable payload")
            return None
        await _bump("hits")
        _log_hit(key)
        return value
    # In-memory fallback (no Redis configured).
    async with _memory_lock:
        entry = _memory_store.get(key)
        if entry is None:
            await _bump("misses")
            _log_miss(key)
            return None
        raw_value, expires_at = entry
        if expires_at is not None and time.monotonic() >= expires_at:
            _memory_store.pop(key, None)
            await _bump("misses")
            _log_miss(key, reason="expired")
            return None
        try:
            value = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError, ValueError):
            _memory_store.pop(key, None)
            await _bump("misses")
            _log_miss(key, reason="unparseable payload")
            return None
    await _bump("hits")
    _log_hit(key)
    return value


async def set_json(key: str, value: Any, ttl: int) -> bool:
    """Cache a JSON-serializable value. ``False`` on error/disabled."""
    if _cache_disabled():
        return False
    try:
        raw = json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:
        await _bump("set_errors")
        logger.warning(
            "cache SET skipped (unserializable) key=%s err=%s",
            _log_key(key),
            type(exc).__name__,
        )
        return False
    client = _get_client()
    if client is not None:
        try:
            await client.set(key, raw, ex=max(1, int(ttl)))
        except Exception as exc:  # noqa: BLE001 — fail open
            await _bump("set_errors")
            logger.warning(
                "cache SET failed key=%s err=%s; response already served",
                _log_key(key),
                type(exc).__name__,
            )
            return False
        await _bump("sets")
        _log_set(key, ttl)
        return True
    expires_at = time.monotonic() + max(1, int(ttl)) if ttl else None
    async with _memory_lock:
        _memory_store[key] = (raw, expires_at)
    await _bump("sets")
    _log_set(key, ttl)
    return True


async def delete(key: str) -> bool:
    """Delete one key. Always fail-open; ``False`` on error/disabled."""
    if _cache_disabled():
        return False
    client = _get_client()
    if client is not None:
        try:
            await client.delete(key)
        except Exception as exc:  # noqa: BLE001 — fail open
            await _bump("delete_errors")
            logger.warning(
                "cache DELETE failed key=%s err=%s",
                _log_key(key),
                type(exc).__name__,
            )
            return False
        await _bump("deletes")
        await _bump("invalidations")
        return True
    async with _memory_lock:
        _memory_store.pop(key, None)
    await _bump("deletes")
    await _bump("invalidations")
    return True


async def delete_many(keys_list: list[str]) -> int:
    """Delete exact keys (no patterns). Returns keys actually requested."""
    if _cache_disabled() or not keys_list:
        return 0
    client = _get_client()
    if client is not None:
        try:
            await client.delete(*keys_list)
        except Exception as exc:  # noqa: BLE001 — fail open
            await _bump("delete_errors")
            logger.warning(
                "cache DELETE many failed n=%d err=%s",
                len(keys_list),
                type(exc).__name__,
            )
            return 0
        await _bump("deletes", len(keys_list))
        await _bump("invalidations", len(keys_list))
        return len(keys_list)
    async with _memory_lock:
        for key in keys_list:
            _memory_store.pop(key, None)
    await _bump("deletes", len(keys_list))
    await _bump("invalidations", len(keys_list))
    return len(keys_list)


async def delete_pattern(pattern: str) -> int:
    """Delete keys matching a tenant-scoped pattern (e.g. ``shop:{id}:…*``).

    Refuses non-tenant patterns (anything not starting with ``shop:``) so
    a bug can never trigger a global flush. Never raises.
    """
    if _cache_disabled():
        return 0
    if not pattern.startswith("shop:") or "*" not in pattern:
        logger.warning("cache pattern delete refused pattern=%s", pattern[:60])
        return 0
    client = _get_client()
    if client is not None:
        try:
            removed = 0
            cursor = 0
            while True:
                cursor, found = await client.scan(
                    cursor=cursor, match=pattern, count=200
                )
                if found:
                    await client.delete(*found)
                    removed += len(found)
                if cursor == 0:
                    break
        except Exception as exc:  # noqa: BLE001 — fail open
            await _bump("delete_errors")
            logger.warning(
                "cache pattern delete failed pattern=%s err=%s",
                pattern[:60],
                type(exc).__name__,
            )
            return 0
        if removed:
            await _bump("deletes", removed)
            await _bump("invalidations", removed)
        if _verbose():
            logger.info(
                "cache INVALIDATE pattern=%s n=%d (post-commit eviction)",
                pattern[:60],
                removed,
            )
        else:
            logger.debug("cache invalidated pattern=%s n=%d", pattern[:60], removed)
        return removed
    # Memory backend: fnmatch over keys (patterns here only use trailing "*").
    import fnmatch

    async with _memory_lock:
        matched = [k for k in _memory_store if fnmatch.fnmatch(k, pattern)]
        for k in matched:
            _memory_store.pop(k, None)
    if matched:
        await _bump("deletes", len(matched))
        await _bump("invalidations", len(matched))
    return len(matched)


async def cached_json(
    key: str,
    ttl: int,
    loader: Callable[[], Awaitable[Any]],
) -> tuple[Any, bool]:
    """Read-through helper: ``(value, hit)`` with fail-open semantics.

    On hit the loader is never called (DB not queried). On miss/error the
    loader runs against PostgreSQL and its result is stored best-effort.
    """

    cached = await get_json(key)
    if cached is not None:
        return cached, True
    value = await loader()
    await set_json(key, value, ttl)
    return value, False


async def clear_memory_cache() -> None:
    """Empty the in-memory fallback (tests). Never touches Redis."""
    async with _memory_lock:
        _memory_store.clear()


# ---------------------------------------------------------------------------
# Tenant-scoped invalidation helpers (call AFTER db.commit).
# ---------------------------------------------------------------------------


async def invalidate_dashboard(shop_id: Any) -> int:
    n = await delete(cache_keys.dashboard_key(shop_id))
    return int(n)


async def invalidate_financial(shop_id: Any) -> int:
    prefix = f"{cache_keys.shop_prefix(shop_id)}:reports:financial:"
    return await delete_pattern(prefix + "*")


async def invalidate_customer(shop_id: Any, customer_id: Any) -> int:
    removed = await delete_many(
        [
            cache_keys.customer_summary_key(shop_id, customer_id),
            cache_keys.customer_balance_key(shop_id, customer_id),
        ]
    )
    removed += await delete_pattern(
        f"{cache_keys.shop_prefix(shop_id)}:customer:{customer_id}:statement:*"
    )
    return removed


async def invalidate_supplier(shop_id: Any, supplier_id: Any) -> int:
    removed = await delete_many(
        [
            cache_keys.supplier_summary_key(shop_id, supplier_id),
            cache_keys.supplier_balance_key(shop_id, supplier_id),
        ]
    )
    removed += await delete_pattern(
        f"{cache_keys.shop_prefix(shop_id)}:supplier:{supplier_id}:statement:*"
    )
    return removed


async def invalidate_inventory_variant(
    shop_id: Any, variant_id: Any | None = None
) -> int:
    removed = 0
    if variant_id is not None:
        removed += int(
            await delete(cache_keys.inventory_variant_key(shop_id, variant_id))
        )
        removed += await delete_pattern(
            f"{cache_keys.shop_prefix(shop_id)}:inventory:status:*"
        )
    removed += await delete_pattern(
        f"{cache_keys.shop_prefix(shop_id)}:inventory:list:*"
    )
    return removed


async def invalidate_product(
    shop_id: Any,
    product_id: Any | None = None,
    variant_id: Any | None = None,
) -> int:
    removed = 0
    if product_id is not None:
        removed += int(await delete(cache_keys.product_detail_key(shop_id, product_id)))
    if variant_id is not None:
        removed += int(await delete(cache_keys.variant_detail_key(shop_id, variant_id)))
    removed += await delete_pattern(
        f"{cache_keys.shop_prefix(shop_id)}:products:list:*"
    )
    removed += await delete_pattern(
        f"{cache_keys.shop_prefix(shop_id)}:variants:list:*"
    )
    removed += await delete_pattern(f"{cache_keys.shop_prefix(shop_id)}:catalog:info:*")
    return removed


async def invalidate_sales(shop_id: Any) -> int:
    prefix = cache_keys.shop_prefix(shop_id)
    removed = await delete_pattern(f"{prefix}:sales:*")
    return removed


async def invalidate_purchases(shop_id: Any) -> int:
    prefix = cache_keys.shop_prefix(shop_id)
    return await delete_pattern(f"{prefix}:purchases:*")


async def invalidate_expenses(shop_id: Any) -> int:
    prefix = cache_keys.shop_prefix(shop_id)
    return await delete_pattern(f"{prefix}:expenses:*")


# -- Composite mutation mappings (mutation → affected read caches) ----------


async def after_sale_committed(
    shop_id: Any,
    customer_id: Any | None = None,
    variant_ids: list[Any] | None = None,
) -> int:
    """Sale created/updated/deleted → customer + inventory + summaries."""
    removed = 0
    if customer_id is not None:
        removed += await invalidate_customer(shop_id, customer_id)
    for vid in variant_ids or []:
        removed += int(await delete(cache_keys.inventory_variant_key(shop_id, vid)))
    if variant_ids:
        removed += await delete_pattern(
            f"{cache_keys.shop_prefix(shop_id)}:inventory:list:*"
        )
        removed += await delete_pattern(
            f"{cache_keys.shop_prefix(shop_id)}:inventory:status:*"
        )
    removed += await invalidate_dashboard(shop_id)
    removed += await invalidate_financial(shop_id)
    removed += await invalidate_sales(shop_id)
    return removed


async def after_customer_payment_committed(shop_id: Any, customer_id: Any) -> int:
    removed = await invalidate_customer(shop_id, customer_id)
    removed += await invalidate_dashboard(shop_id)
    removed += await invalidate_financial(shop_id)
    removed += await invalidate_sales(shop_id)
    return removed


async def after_supplier_payment_committed(shop_id: Any, supplier_id: Any) -> int:
    removed = await invalidate_supplier(shop_id, supplier_id)
    removed += await invalidate_dashboard(shop_id)
    removed += await invalidate_financial(shop_id)
    removed += await invalidate_purchases(shop_id)
    return removed


async def after_purchase_committed(
    shop_id: Any,
    supplier_id: Any | None = None,
    variant_ids: list[Any] | None = None,
) -> int:
    removed = 0
    if supplier_id is not None:
        removed += await invalidate_supplier(shop_id, supplier_id)
    for vid in variant_ids or []:
        removed += int(await delete(cache_keys.inventory_variant_key(shop_id, vid)))
    if variant_ids:
        removed += await delete_pattern(
            f"{cache_keys.shop_prefix(shop_id)}:inventory:list:*"
        )
        removed += await delete_pattern(
            f"{cache_keys.shop_prefix(shop_id)}:inventory:status:*"
        )
    removed += await invalidate_dashboard(shop_id)
    removed += await invalidate_financial(shop_id)
    removed += await invalidate_purchases(shop_id)
    return removed


async def after_expense_committed(shop_id: Any) -> int:
    removed = await invalidate_expenses(shop_id)
    removed += await invalidate_dashboard(shop_id)
    removed += await invalidate_financial(shop_id)
    return removed


async def after_customer_return_committed(
    shop_id: Any,
    customer_id: Any | None = None,
    variant_ids: list[Any] | None = None,
) -> int:
    # Same footprint as a sale: Khata + stock + sales reads + dashboard.
    return await after_sale_committed(
        shop_id, customer_id=customer_id, variant_ids=variant_ids
    )


async def after_supplier_return_committed(
    shop_id: Any,
    supplier_id: Any | None = None,
    variant_ids: list[Any] | None = None,
) -> int:
    return await after_purchase_committed(
        shop_id, supplier_id=supplier_id, variant_ids=variant_ids
    )


async def after_inventory_adjust_committed(shop_id: Any, variant_id: Any) -> int:
    removed = await invalidate_inventory_variant(shop_id, variant_id)
    removed += await invalidate_dashboard(shop_id)
    removed += await invalidate_financial(shop_id)
    return removed


async def after_payment_void_committed(
    shop_id: Any,
    customer_id: Any | None = None,
    supplier_id: Any | None = None,
) -> int:
    removed = 0
    if customer_id is not None:
        removed += await invalidate_customer(shop_id, customer_id)
        removed += await invalidate_sales(shop_id)
    if supplier_id is not None:
        removed += await invalidate_supplier(shop_id, supplier_id)
        removed += await invalidate_purchases(shop_id)
    removed += await invalidate_dashboard(shop_id)
    removed += await invalidate_financial(shop_id)
    return removed


async def invalidate_shop_reads(shop_id: Any) -> int:
    """Tenant-scoped fallback for AI-approved writes ( afectados unknown).

    Deletes only this shop's read-cache prefixes — never other tenants,
    never a global flush. AI writes are infrequent, so breadth here is
    safer than risking a stale Khata/dashboard.
    """
    prefix = cache_keys.shop_prefix(shop_id)
    removed = 0
    for sub in (
        "dashboard:*",
        "reports:*",
        "customer:*",
        "supplier:*",
        "inventory:*",
        "product:*",
        "products:*",
        "variant:*",
        "variants:*",
        "catalog:*",
        "sales:*",
        "purchases:*",
        "expenses:*",
    ):
        removed += await delete_pattern(f"{prefix}:{sub}")
    return removed


__all__ = [
    "TTL_CUSTOMER_SECONDS",
    "TTL_DASHBOARD_SECONDS",
    "TTL_EXPENSE_SECONDS",
    "TTL_FINANCIAL_SECONDS",
    "TTL_INVENTORY_SECONDS",
    "TTL_PRODUCT_SECONDS",
    "TTL_PURCHASE_SECONDS",
    "TTL_SALES_SECONDS",
    "TTL_STATEMENT_SECONDS",
    "TTL_SUPPLIER_SECONDS",
    "after_customer_payment_committed",
    "after_customer_return_committed",
    "after_expense_committed",
    "after_inventory_adjust_committed",
    "after_payment_void_committed",
    "after_purchase_committed",
    "after_sale_committed",
    "after_supplier_payment_committed",
    "after_supplier_return_committed",
    "cached_json",
    "clear_memory_cache",
    "delete",
    "delete_many",
    "delete_pattern",
    "get_json",
    "get_metrics",
    "invalidate_customer",
    "invalidate_dashboard",
    "invalidate_expenses",
    "invalidate_financial",
    "invalidate_inventory_variant",
    "invalidate_product",
    "invalidate_purchases",
    "invalidate_sales",
    "invalidate_shop_reads",
    "invalidate_supplier",
    "reset_metrics",
    "set_json",
]

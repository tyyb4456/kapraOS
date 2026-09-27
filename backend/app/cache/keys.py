"""Central cache-key construction (Step 10).

Every tenant-specific key starts with ``shop:{shop_id}:`` where ``shop_id``
is the server-side tenant identity (``TenantContext.shop_id`` /
``get_current_shop_id``) — never a client-supplied value. A key collision
across tenants would be a critical security bug, so no helper here accepts
a bare ``customer:123`` style key.

Keys include every parameter that changes the response: paginated/filtered
reads hash their full parameter set deterministically (sorted JSON →
SHA-256), so the same parameter set always maps to the same key regardless
of dict ordering, while different ranges/pages never share a key.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any


def shop_prefix(shop_id: uuid.UUID | str) -> str:
    return f"shop:{shop_id}"


def dashboard_key(shop_id: uuid.UUID | str) -> str:
    return f"{shop_prefix(shop_id)}:dashboard:summary"


def financial_summary_key(
    shop_id: uuid.UUID | str,
    *,
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> str:
    digest = filters_hash({"period": period, "start": start, "end": end})
    return f"{shop_prefix(shop_id)}:reports:financial:{digest}"


def customer_summary_key(shop_id: uuid.UUID | str, customer_id: uuid.UUID | str) -> str:
    return f"{shop_prefix(shop_id)}:customer:{customer_id}:summary"


def customer_balance_key(shop_id: uuid.UUID | str, customer_id: uuid.UUID | str) -> str:
    return f"{shop_prefix(shop_id)}:customer:{customer_id}:balance"


def customer_statement_key(
    shop_id: uuid.UUID | str,
    customer_id: uuid.UUID | str,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> str:
    digest = filters_hash(
        {"start": start, "end": end, "limit": limit, "offset": offset}
    )
    return f"{shop_prefix(shop_id)}:customer:{customer_id}:statement:{digest}"


def supplier_summary_key(shop_id: uuid.UUID | str, supplier_id: uuid.UUID | str) -> str:
    return f"{shop_prefix(shop_id)}:supplier:{supplier_id}:summary"


def supplier_balance_key(shop_id: uuid.UUID | str, supplier_id: uuid.UUID | str) -> str:
    return f"{shop_prefix(shop_id)}:supplier:{supplier_id}:balance"


def supplier_statement_key(
    shop_id: uuid.UUID | str,
    supplier_id: uuid.UUID | str,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> str:
    digest = filters_hash(
        {"start": start, "end": end, "limit": limit, "offset": offset}
    )
    return f"{shop_prefix(shop_id)}:supplier:{supplier_id}:statement:{digest}"


def inventory_variant_key(shop_id: uuid.UUID | str, variant_id: uuid.UUID | str) -> str:
    return f"{shop_prefix(shop_id)}:inventory:{variant_id}"


def inventory_list_key(
    shop_id: uuid.UUID | str,
    *,
    variant_id: str | None = None,
    low_stock: bool = False,
    search: str | None = None,
) -> str:
    digest = filters_hash(
        {"variant_id": variant_id, "low_stock": low_stock, "search": search}
    )
    return f"{shop_prefix(shop_id)}:inventory:list:{digest}"


def product_detail_key(shop_id: uuid.UUID | str, product_id: uuid.UUID | str) -> str:
    return f"{shop_prefix(shop_id)}:product:{product_id}"


def product_list_key(
    shop_id: uuid.UUID | str,
    *,
    category_id: str | None = None,
    brand_id: str | None = None,
    product_type: str | None = None,
    search: str | None = None,
) -> str:
    digest = filters_hash(
        {
            "category_id": category_id,
            "brand_id": brand_id,
            "product_type": product_type,
            "search": search,
        }
    )
    return f"{shop_prefix(shop_id)}:products:list:{digest}"


def variant_detail_key(shop_id: uuid.UUID | str, variant_id: uuid.UUID | str) -> str:
    return f"{shop_prefix(shop_id)}:variant:{variant_id}"


def variant_list_key(
    shop_id: uuid.UUID | str,
    *,
    product_id: str | None = None,
    sku: str | None = None,
) -> str:
    digest = filters_hash({"product_id": product_id, "sku": sku})
    return f"{shop_prefix(shop_id)}:variants:list:{digest}"


def sales_summary_today_key(shop_id: uuid.UUID | str) -> str:
    return f"{shop_prefix(shop_id)}:sales:summary:today"


def sales_aggregate_key(
    shop_id: uuid.UUID | str,
    *,
    start: str | None = None,
    end: str | None = None,
) -> str:
    digest = filters_hash({"start": start, "end": end})
    return f"{shop_prefix(shop_id)}:sales:agg:{digest}"


def sales_list_key(
    shop_id: uuid.UUID | str,
    *,
    customer_id: str | None = None,
    status: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    digest = filters_hash(
        {
            "customer_id": customer_id,
            "status": status,
            "start": start,
            "end": end,
            "limit": limit,
            "offset": offset,
        }
    )
    return f"{shop_prefix(shop_id)}:sales:list:{digest}"


def purchase_aggregate_key(
    shop_id: uuid.UUID | str,
    *,
    start: str | None = None,
    end: str | None = None,
) -> str:
    digest = filters_hash({"start": start, "end": end})
    return f"{shop_prefix(shop_id)}:purchases:agg:{digest}"


def purchase_list_key(
    shop_id: uuid.UUID | str,
    *,
    supplier_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    digest = filters_hash(
        {
            "supplier_id": supplier_id,
            "start": start,
            "end": end,
            "limit": limit,
            "offset": offset,
        }
    )
    return f"{shop_prefix(shop_id)}:purchases:list:{digest}"


def expense_aggregate_key(
    shop_id: uuid.UUID | str,
    *,
    start: str | None = None,
    end: str | None = None,
    category: str | None = None,
) -> str:
    digest = filters_hash({"start": start, "end": end, "category": category})
    return f"{shop_prefix(shop_id)}:expenses:agg:{digest}"


def expense_list_key(
    shop_id: uuid.UUID | str,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    digest = filters_hash(
        {"start": start, "end": end, "limit": limit, "offset": offset}
    )
    return f"{shop_prefix(shop_id)}:expenses:list:{digest}"


def inventory_status_key(
    shop_id: uuid.UUID | str,
    *,
    product_name: str | None = None,
    category: str | None = None,
    low_stock_only: bool = False,
    limit: int = 20,
) -> str:
    digest = filters_hash(
        {
            "product_name": (product_name or "").strip().lower(),
            "category": (category or "").strip().lower(),
            "low_stock_only": low_stock_only,
            "limit": limit,
        }
    )
    return f"{shop_prefix(shop_id)}:inventory:status:{digest}"


def product_info_key(shop_id: uuid.UUID | str, *, product_name: str) -> str:
    digest = filters_hash({"product_name": (product_name or "").strip().lower()})
    return f"{shop_prefix(shop_id)}:catalog:info:{digest}"


def filters_hash(params: dict[str, Any]) -> str:
    """Deterministic short hash of a parameter set.

    ``json.dumps(..., sort_keys=True, default=str)`` makes the same
    parameter set produce the same key regardless of dict ordering, and
    normalises UUID/datetime/Decimal values via ``str``.
    """

    def _normalise(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): _normalise(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_normalise(v) for v in value]
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    canonical = json.dumps(_normalise(params), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "customer_balance_key",
    "customer_statement_key",
    "customer_summary_key",
    "dashboard_key",
    "expense_aggregate_key",
    "expense_list_key",
    "filters_hash",
    "financial_summary_key",
    "inventory_list_key",
    "inventory_status_key",
    "inventory_variant_key",
    "product_detail_key",
    "product_info_key",
    "product_list_key",
    "purchase_aggregate_key",
    "purchase_list_key",
    "sales_aggregate_key",
    "sales_list_key",
    "sales_summary_today_key",
    "shop_prefix",
    "supplier_balance_key",
    "supplier_statement_key",
    "supplier_summary_key",
    "variant_detail_key",
    "variant_list_key",
]

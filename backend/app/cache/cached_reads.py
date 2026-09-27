"""Cached read services (Step 10).

Single home for every cached read, shared by REST routes and AI tools::

    AI read tool / REST route
        -> cached_* wrapper (this module)
            -> app.cache.service (Redis / memory, fail-open)
                -> authoritative service / SQL (PostgreSQL, truth)

There is no separate AI cache: ``business_reads`` tools call these same
wrappers. Mutation validation never uses this module — sale/purchase/
return/payment creation always queries PostgreSQL directly.

All wrappers store Pydantic ``mode="json"`` dicts (or plain JSON-safe
dicts for the AI aggregate shapes) and re-validate on hit, so a stale or
incompatible entry degrades to a miss instead of a wrong response.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.cache import keys as cache_keys
from app.cache import service as cache

logger = logging.getLogger("app.cache.reads")

_MAX_INVENTORY_LIMIT = 50
_MAX_MATCHES = 5


def _money_str(value: Decimal | int | None) -> str:
    if value is None:
        return "0.00"
    return str(Decimal(value).quantize(Decimal("0.01")))


def _qty_str(value: Decimal | int | None) -> str:
    if value is None:
        return "0.000"
    return str(Decimal(value).quantize(Decimal("0.001")))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# ---------------------------------------------------------------------------
# Dashboard + financial summary (reporting service is authoritative)
# ---------------------------------------------------------------------------


async def get_cached_dashboard(session: AsyncSession, *, shop_id: uuid.UUID):
    """Today's business overview (20s TTL)."""
    from app.schemas.reporting import DashboardResponse
    from app.services import reporting as reporting_service

    key = cache_keys.dashboard_key(shop_id)
    cached = await cache.get_json(key)
    if cached is not None:
        try:
            return DashboardResponse.model_validate(cached)
        except ValidationError:
            logger.debug("cache dashboard invalid payload; reloading from DB")

    report = await reporting_service.get_dashboard_summary(session, shop_id=shop_id)
    response = DashboardResponse.model_validate(report)
    await cache.set_json(
        key, response.model_dump(mode="json"), cache.TTL_DASHBOARD_SECONDS
    )
    return response


async def get_cached_financial_summary(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    period: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
):
    """Period P&L + Khata balances (30s TTL, key includes all params)."""
    from app.schemas.reporting import FinancialSummaryResponse
    from app.services import reporting as reporting_service

    key = cache_keys.financial_summary_key(
        shop_id, period=period, start=_iso(start_date), end=_iso(end_date)
    )
    cached = await cache.get_json(key)
    if cached is not None:
        try:
            return FinancialSummaryResponse.model_validate(cached)
        except ValidationError:
            logger.debug("cache financial invalid payload; reloading from DB")

    report = await reporting_service.get_financial_summary(
        session,
        shop_id=shop_id,
        period=period,
        start_date=start_date,
        end_date=end_date,
    )
    response = FinancialSummaryResponse.model_validate(report)
    await cache.set_json(
        key, response.model_dump(mode="json"), cache.TTL_FINANCIAL_SECONDS
    )
    return response


# ---------------------------------------------------------------------------
# Customer Khata (receivables service is authoritative)
# ---------------------------------------------------------------------------


async def get_cached_customer_summary(
    session: AsyncSession, *, shop_id: uuid.UUID, customer_id: uuid.UUID
):
    from app.schemas.receivables import CustomerSummaryResponse
    from app.services import receivables as receivables_service

    key = cache_keys.customer_summary_key(shop_id, customer_id)
    cached = await cache.get_json(key)
    if cached is not None:
        try:
            return CustomerSummaryResponse.model_validate(cached)
        except ValidationError:
            logger.debug("cache customer summary invalid; reloading from DB")

    summary = await receivables_service.get_customer_summary(
        session, shop_id=shop_id, customer_id=customer_id
    )
    response = CustomerSummaryResponse.model_validate(summary)
    await cache.set_json(
        key, response.model_dump(mode="json"), cache.TTL_CUSTOMER_SECONDS
    )
    return response


async def get_cached_customer_balance(
    session: AsyncSession, *, shop_id: uuid.UUID, customer_id: uuid.UUID
):
    from app.schemas.receivables import CustomerBalanceResponse
    from app.services import receivables as receivables_service

    key = cache_keys.customer_balance_key(shop_id, customer_id)
    cached = await cache.get_json(key)
    if cached is not None:
        try:
            return CustomerBalanceResponse.model_validate(cached)
        except ValidationError:
            logger.debug("cache customer balance invalid; reloading from DB")

    balance = await receivables_service.get_customer_balance(
        session, shop_id=shop_id, customer_id=customer_id
    )
    response = CustomerBalanceResponse.model_validate(balance)
    await cache.set_json(
        key, response.model_dump(mode="json"), cache.TTL_CUSTOMER_SECONDS
    )
    return response


async def get_cached_customer_statement(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    customer_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int | None = None,
    offset: int = 0,
):
    from app.schemas.receivables import CustomerStatementResponse
    from app.services import receivables as receivables_service

    key = cache_keys.customer_statement_key(
        shop_id,
        customer_id,
        start=_iso(start_date),
        end=_iso(end_date),
        limit=limit,
        offset=offset,
    )
    cached = await cache.get_json(key)
    if cached is not None:
        try:
            return CustomerStatementResponse.model_validate(cached)
        except ValidationError:
            logger.debug("cache customer statement invalid; reloading")

    statement = await receivables_service.get_customer_statement(
        session,
        shop_id=shop_id,
        customer_id=customer_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    response = CustomerStatementResponse.model_validate(statement)
    await cache.set_json(
        key, response.model_dump(mode="json"), cache.TTL_STATEMENT_SECONDS
    )
    return response


# ---------------------------------------------------------------------------
# Supplier Khata (payables service is authoritative)
# ---------------------------------------------------------------------------


async def get_cached_supplier_summary(
    session: AsyncSession, *, shop_id: uuid.UUID, supplier_id: uuid.UUID
):
    from app.schemas.payables import SupplierSummaryResponse
    from app.services import payables as payables_service

    key = cache_keys.supplier_summary_key(shop_id, supplier_id)
    cached = await cache.get_json(key)
    if cached is not None:
        try:
            return SupplierSummaryResponse.model_validate(cached)
        except ValidationError:
            logger.debug("cache supplier summary invalid; reloading from DB")

    summary = await payables_service.get_supplier_summary(
        session, shop_id=shop_id, supplier_id=supplier_id
    )
    response = SupplierSummaryResponse.model_validate(summary)
    await cache.set_json(
        key, response.model_dump(mode="json"), cache.TTL_SUPPLIER_SECONDS
    )
    return response


async def get_cached_supplier_balance(
    session: AsyncSession, *, shop_id: uuid.UUID, supplier_id: uuid.UUID
):
    from app.schemas.payables import SupplierBalanceResponse
    from app.services import payables as payables_service

    key = cache_keys.supplier_balance_key(shop_id, supplier_id)
    cached = await cache.get_json(key)
    if cached is not None:
        try:
            return SupplierBalanceResponse.model_validate(cached)
        except ValidationError:
            logger.debug("cache supplier balance invalid; reloading from DB")

    balance = await payables_service.get_supplier_balance(
        session, shop_id=shop_id, supplier_id=supplier_id
    )
    response = SupplierBalanceResponse.model_validate(balance)
    await cache.set_json(
        key, response.model_dump(mode="json"), cache.TTL_SUPPLIER_SECONDS
    )
    return response


async def get_cached_supplier_statement(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    supplier_id: uuid.UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int | None = None,
    offset: int = 0,
):
    from app.schemas.payables import SupplierStatementResponse
    from app.services import payables as payables_service

    key = cache_keys.supplier_statement_key(
        shop_id,
        supplier_id,
        start=_iso(start_date),
        end=_iso(end_date),
        limit=limit,
        offset=offset,
    )
    cached = await cache.get_json(key)
    if cached is not None:
        try:
            return SupplierStatementResponse.model_validate(cached)
        except ValidationError:
            logger.debug("cache supplier statement invalid; reloading")

    statement = await payables_service.get_supplier_statement(
        session,
        shop_id=shop_id,
        supplier_id=supplier_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    response = SupplierStatementResponse.model_validate(statement)
    await cache.set_json(
        key, response.model_dump(mode="json"), cache.TTL_STATEMENT_SECONDS
    )
    return response


# ---------------------------------------------------------------------------
# Shared date-range parsing (YYYY-MM-DD, UTC) for the aggregate shapes.
# ---------------------------------------------------------------------------


def _parse_day(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        day = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid {field} {value!r}: expected YYYY-MM-DD.") from exc
    return datetime.combine(day, time.min, tzinfo=UTC)


def _parse_range(
    start: str | None, end: str | None
) -> tuple[datetime | None, datetime | None]:
    start_dt = _parse_day(start, "start_date")
    end_dt: datetime | None = None
    if end is not None and end.strip():
        try:
            end_day = date.fromisoformat(end.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid end_date {end!r}: expected YYYY-MM-DD.") from exc
        end_dt = datetime.combine(end_day, time.max, tzinfo=UTC)
    if start_dt is not None and end_dt is not None and start_dt > end_dt:
        raise ValueError("Invalid date range: start_date is after end_date.")
    return start_dt, end_dt


def _norm_iso(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


# ---------------------------------------------------------------------------
# Sales / purchase / expense aggregates (shared by AI tools).
# ---------------------------------------------------------------------------


async def get_cached_sales_aggregate(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Sales totals for a date range (45s TTL). Raises ValueError on bad input."""
    from app.models.sale import Sale
    from app.services.receivables import QUALIFYING_SALE_STATUSES

    start_norm, end_norm = _norm_iso(start_date), _norm_iso(end_date)
    key = cache_keys.sales_aggregate_key(shop_id, start=start_norm, end=end_norm)
    cached = await cache.get_json(key)
    if (
        isinstance(cached, dict)
        and cached.get("status") == "ok"
        and "total_sales_amount" in cached
    ):
        return cached

    start_dt, end_dt = _parse_range(start_norm, end_norm)
    stmt = select(
        func.coalesce(func.sum(Sale.total), 0),
        func.count(Sale.id),
        func.coalesce(func.sum(Sale.paid_amount), 0),
    ).where(
        Sale.shop_id == shop_id,
        Sale.status.in_(QUALIFYING_SALE_STATUSES),
    )
    if start_dt is not None:
        stmt = stmt.where(Sale.created_at >= start_dt)
    if end_dt is not None:
        stmt = stmt.where(Sale.created_at <= end_dt)
    row = (await session.execute(stmt)).one()
    total = Decimal(row[0])
    paid = Decimal(row[2])
    payload = {
        "status": "ok",
        "start_date": start_date,
        "end_date": end_date,
        "total_sales_amount": _money_str(total),
        "sale_count": int(row[1]),
        "paid_amount": _money_str(paid),
        "due_amount": _money_str(total - paid),
    }
    await cache.set_json(key, payload, cache.TTL_SALES_SECONDS)
    return payload


async def get_cached_purchase_aggregate(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Purchase totals for a date range (45s TTL)."""
    from app.models.purchase import Purchase

    start_norm, end_norm = _norm_iso(start_date), _norm_iso(end_date)
    key = cache_keys.purchase_aggregate_key(shop_id, start=start_norm, end=end_norm)
    cached = await cache.get_json(key)
    if (
        isinstance(cached, dict)
        and cached.get("status") == "ok"
        and "total_purchase_amount" in cached
    ):
        return cached

    start_dt, end_dt = _parse_range(start_norm, end_norm)
    stmt = select(
        func.coalesce(func.sum(Purchase.total), 0),
        func.count(Purchase.id),
        func.coalesce(func.sum(Purchase.paid_amount), 0),
    ).where(Purchase.shop_id == shop_id)
    if start_dt is not None:
        stmt = stmt.where(Purchase.created_at >= start_dt)
    if end_dt is not None:
        stmt = stmt.where(Purchase.created_at <= end_dt)
    row = (await session.execute(stmt)).one()
    total = Decimal(row[0])
    paid = Decimal(row[2])
    payload = {
        "status": "ok",
        "start_date": start_date,
        "end_date": end_date,
        "total_purchase_amount": _money_str(total),
        "purchase_count": int(row[1]),
        "paid_amount": _money_str(paid),
        "due_amount": _money_str(total - paid),
    }
    await cache.set_json(key, payload, cache.TTL_PURCHASE_SECONDS)
    return payload


async def get_cached_expense_aggregate(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Expense totals for a date range + optional category (45s TTL)."""
    from app.models.expense import Expense, ExpenseCategory

    start_norm, end_norm = _norm_iso(start_date), _norm_iso(end_date)
    cat_norm = category.strip().lower() if category and category.strip() else None
    key = cache_keys.expense_aggregate_key(
        shop_id, start=start_norm, end=end_norm, category=cat_norm
    )
    cached = await cache.get_json(key)
    if (
        isinstance(cached, dict)
        and cached.get("status") == "ok"
        and "total_expenses" in cached
    ):
        return cached

    start_dt, end_dt = _parse_range(start_norm, end_norm)
    cat: ExpenseCategory | None = None
    if cat_norm is not None:
        try:
            cat = ExpenseCategory(cat_norm)
        except ValueError as exc:
            raise ValueError(f"Unknown expense category {category!r}.") from exc

    stmt = select(
        func.coalesce(func.sum(Expense.amount), 0),
        func.count(Expense.id),
    ).where(Expense.shop_id == shop_id)
    if start_dt is not None:
        stmt = stmt.where(Expense.expense_date >= start_dt)
    if end_dt is not None:
        stmt = stmt.where(Expense.expense_date <= end_dt)
    if cat is not None:
        stmt = stmt.where(Expense.category == cat)
    row = (await session.execute(stmt)).one()
    result: dict[str, Any] = {
        "status": "ok",
        "start_date": start_date,
        "end_date": end_date,
        "total_expenses": _money_str(Decimal(row[0])),
        "expense_count": int(row[1]),
    }
    if cat is not None:
        result["category"] = cat.value
    else:
        by_cat_stmt = select(
            Expense.category, func.coalesce(func.sum(Expense.amount), 0)
        ).where(Expense.shop_id == shop_id)
        if start_dt is not None:
            by_cat_stmt = by_cat_stmt.where(Expense.expense_date >= start_dt)
        if end_dt is not None:
            by_cat_stmt = by_cat_stmt.where(Expense.expense_date <= end_dt)
        by_cat_stmt = by_cat_stmt.group_by(Expense.category)
        by_cat = (await session.execute(by_cat_stmt)).all()
        result["by_category"] = {
            str(r[0].value if hasattr(r[0], "value") else r[0]): _money_str(
                Decimal(r[1])
            )
            for r in by_cat
        }
    await cache.set_json(key, result, cache.TTL_EXPENSE_SECONDS)
    return result


# ---------------------------------------------------------------------------
# Inventory status + catalog info (shared by AI tools).
# ---------------------------------------------------------------------------


async def get_cached_inventory_status(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    product_name: str | None = None,
    category: str | None = None,
    low_stock_only: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Stock overview for products/variants (20s TTL)."""
    from app.models.category import Category
    from app.models.inventory import Inventory
    from app.models.product import Product, ProductVariant

    try:
        safe_limit = max(1, min(int(limit), _MAX_INVENTORY_LIMIT))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid limit.") from exc

    name_norm = (product_name or "").strip()
    cat_norm = (category or "").strip()
    key = cache_keys.inventory_status_key(
        shop_id,
        product_name=name_norm or None,
        category=cat_norm or None,
        low_stock_only=bool(low_stock_only),
        limit=safe_limit,
    )
    cached = await cache.get_json(key)
    if isinstance(cached, dict) and cached.get("status") in (
        "ok",
        "not_found",
        "ambiguous",
    ):
        return cached

    stmt = (
        select(ProductVariant, Inventory, Product)
        .join(Product, Product.id == ProductVariant.product_id)
        .outerjoin(Inventory, Inventory.variant_id == ProductVariant.id)
        .where(ProductVariant.shop_id == shop_id)
        .order_by(Product.name.asc(), ProductVariant.sku.asc())
        .limit(safe_limit * 4)
    )
    if name_norm:
        like = f"%{name_norm}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(like),
                ProductVariant.sku.ilike(like),
            )
        )
    if cat_norm:
        cat_like = f"%{cat_norm}%"
        stmt = stmt.join(Category, Category.id == Product.category_id).where(
            Category.name.ilike(cat_like)
        )
    rows = (await session.execute(stmt)).all()
    if not rows:
        payload: dict[str, Any] = {
            "status": "not_found",
            "message": "No matching inventory found.",
        }
        await cache.set_json(key, payload, cache.TTL_INVENTORY_SECONDS)
        return payload

    if name_norm:
        product_ids = {str(r[2].id) for r in rows}
        if len(product_ids) > 1:
            by_product: dict[str, dict] = {}
            for _, _, prod in rows:
                pkey = str(prod.id)
                if pkey not in by_product:
                    by_product[pkey] = {
                        "product_name": prod.name,
                        "product_type": str(prod.product_type.value)
                        if hasattr(prod.product_type, "value")
                        else str(prod.product_type),
                    }
            payload = {
                "status": "ambiguous",
                "message": (
                    f"Multiple products match {name_norm!r}. "
                    "Ask which one the shopkeeper means."
                ),
                "matches": list(by_product.values())[:_MAX_MATCHES],
            }
            await cache.set_json(key, payload, cache.TTL_INVENTORY_SECONDS)
            return payload

    items = []
    for variant, inv, prod in rows:
        qty = inv.quantity if inv else Decimal(0)
        reserved = inv.reserved_quantity if inv else Decimal(0)
        reorder = inv.reorder_level if inv else Decimal(0)
        unit_val = (
            variant.unit.value if hasattr(variant.unit, "value") else str(variant.unit)
        )
        low = bool(qty <= reorder)
        if low_stock_only and not low:
            continue
        items.append(
            {
                "product": prod.name,
                "variant_sku": variant.sku,
                "quantity": _qty_str(qty),
                "available_quantity": _qty_str(qty - reserved),
                "unit": unit_val,
                "reorder_level": _qty_str(reorder),
                "low_stock": low,
            }
        )
        if len(items) >= safe_limit:
            break
    if not items:
        if low_stock_only:
            payload = {
                "status": "ok",
                "items": [],
                "count": 0,
                "message": "No low-stock products.",
            }
        else:
            payload = {
                "status": "not_found",
                "message": "No matching inventory found.",
            }
        await cache.set_json(key, payload, cache.TTL_INVENTORY_SECONDS)
        return payload
    payload = {"status": "ok", "items": items, "count": len(items)}
    await cache.set_json(key, payload, cache.TTL_INVENTORY_SECONDS)
    return payload


async def get_cached_product_info(
    session: AsyncSession, *, shop_id: uuid.UUID, product_name: str
) -> dict[str, Any]:
    """Product + variants lookup (10-minute TTL; catalog changes slowly)."""
    from app.models.brand import Brand
    from app.models.product import Product

    name = (product_name or "").strip()
    if not name:
        raise ValueError("Product name is required.")
    if len(name) > 200:
        raise ValueError("Product name is too long.")

    key = cache_keys.product_info_key(shop_id, product_name=name)
    cached = await cache.get_json(key)
    if isinstance(cached, dict) and cached.get("status") in (
        "ok",
        "not_found",
        "ambiguous",
    ):
        return cached

    like = f"%{name}%"
    products = (
        (
            await session.execute(
                select(Product)
                .options(
                    selectinload(Product.variants),
                    selectinload(Product.category),
                )
                .where(Product.shop_id == shop_id, Product.name.ilike(like))
                .order_by(Product.name.asc())
                .limit(_MAX_MATCHES + 1)
            )
        )
        .scalars()
        .all()
    )
    if not products:
        payload: dict[str, Any] = {
            "status": "not_found",
            "message": "Product not found.",
        }
        await cache.set_json(key, payload, cache.TTL_PRODUCT_SECONDS)
        return payload
    if len(products) > 1:
        payload = {
            "status": "ambiguous",
            "message": (
                f"Multiple products match {name!r}. Ask which one the shopkeeper means."
            ),
            "matches": [
                {
                    "product_name": p.name,
                    "category": p.category.name if p.category else None,
                    "variant_count": len(p.variants or []),
                }
                for p in products[:_MAX_MATCHES]
            ],
        }
        await cache.set_json(key, payload, cache.TTL_PRODUCT_SECONDS)
        return payload
    product = products[0]
    brand_name: str | None = None
    if product.brand_id is not None:
        brand = await session.get(Brand, product.brand_id)
        if brand is not None and brand.shop_id == shop_id:
            brand_name = brand.name
    variants = []
    for v in (product.variants or [])[:10]:
        unit_val = v.unit.value if hasattr(v.unit, "value") else str(v.unit)
        variants.append(
            {
                "sku": v.sku,
                "selling_price": _money_str(v.selling_price),
                "unit": unit_val,
                "is_active": bool(v.is_active),
            }
        )
    payload = {
        "status": "ok",
        "product_name": product.name,
        "category": product.category.name if product.category else None,
        "brand": brand_name,
        "product_type": str(product.product_type.value)
        if hasattr(product.product_type, "value")
        else str(product.product_type),
        "variants": variants,
    }
    await cache.set_json(key, payload, cache.TTL_PRODUCT_SECONDS)
    return payload


__all__ = [
    "get_cached_customer_balance",
    "get_cached_customer_statement",
    "get_cached_customer_summary",
    "get_cached_dashboard",
    "get_cached_expense_aggregate",
    "get_cached_financial_summary",
    "get_cached_inventory_status",
    "get_cached_product_info",
    "get_cached_purchase_aggregate",
    "get_cached_sales_aggregate",
    "get_cached_supplier_balance",
    "get_cached_supplier_statement",
    "get_cached_supplier_summary",
]

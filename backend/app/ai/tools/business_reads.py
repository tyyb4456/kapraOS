"""Business-level read-only tools (Step 2).

Small, typed capabilities over the shop's REAL data — never low-level CRUD,
never raw SQL, never a ``shop_id`` argument. Each tool closes over the
authenticated :class:`TenantContext` and the request's ``AsyncSession``,
so the model can only read the authenticated shop.

Service reuse (adapter, not second business logic):

* ``get_sales_summary`` — direct ``Sale`` aggregates (no range-summary
  service exists; statuses reuse ``receivables.QUALIFYING_SALE_STATUSES``).
* ``get_inventory_status`` — direct ``ProductVariant``/``Inventory``/
  ``Product`` read (same shape as ``GET /inventory``; mutation logic is
  never touched).
* ``get_customer_account_summary`` — ``receivables.get_customer_summary``.
* ``get_supplier_account_summary`` — ``payables.get_supplier_summary``.
* ``get_purchase_summary`` — direct ``Purchase`` aggregates.
* ``get_expense_summary`` — direct ``Expense`` aggregates.
* ``get_product_or_catalog_info`` — direct catalog read.
* ``get_dashboard_summary`` — ``reporting.get_dashboard_summary``.

All money is Decimal-safe (returned as strings, never floats).
All quantities are NUMERIC(14,3) strings. Dates are ``YYYY-MM-DD`` in UTC —
the project has no shop timezone, so UTC is respected, not reinvented.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.state import TenantContext
from app.models.brand import Brand
from app.models.category import Category
from app.models.customer import Customer
from app.models.expense import Expense, ExpenseCategory
from app.models.inventory import Inventory
from app.models.product import Product, ProductVariant
from app.models.purchase import Purchase
from app.models.sale import Sale
from app.models.supplier import Supplier
from app.services import payables as payables_service
from app.services import receivables as receivables_service
from app.services import reporting as reporting_service
from app.services.receivables import QUALIFYING_SALE_STATUSES

READ_TOOL_NAMES: tuple[str, ...] = (
    "get_sales_summary",
    "get_inventory_status",
    "get_customer_account_summary",
    "get_supplier_account_summary",
    "get_purchase_summary",
    "get_expense_summary",
    "get_product_or_catalog_info",
    "get_dashboard_summary",
)

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


def _parse_day(value: str | None, field: str) -> tuple[datetime | None, dict | None]:
    """Parse ``YYYY-MM-DD`` into a UTC day bound; error dict on bad input."""
    if value is None:
        return None, None
    text = value.strip()
    if not text:
        return None, None
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None, {
            "status": "error",
            "message": f"Invalid {field} {value!r}: expected YYYY-MM-DD.",
        }
    return datetime.combine(day, time.min, tzinfo=UTC), None


def _range_bounds(
    start_date: str | None, end_date: str | None
) -> tuple[datetime | None, datetime | None, dict | None]:
    start_dt, err = _parse_day(start_date, "start_date")
    if err is not None:
        return None, None, err
    end_day: date | None = None
    if end_date is not None and end_date.strip():
        try:
            end_day = date.fromisoformat(end_date.strip())
        except ValueError:
            return None, None, {
                "status": "error",
                "message": f"Invalid end_date {end_date!r}: expected YYYY-MM-DD.",
            }
    end_dt = (
        datetime.combine(end_day, time.max, tzinfo=UTC) if end_day is not None else None
    )
    # start_dt from _parse_day is midnight; keep it as-is.
    if start_dt is not None and end_dt is not None and start_dt > end_dt:
        return None, None, {
            "status": "error",
            "message": "Invalid date range: start_date is after end_date.",
        }
    return start_dt, end_dt, None


def _clean_name(value: str | None) -> str:
    return (value or "").strip()


def build_read_tools(session: AsyncSession, tenant: TenantContext) -> list[BaseTool]:
    """Build tenant-bound read tools for one AI request.

    ``shop_id`` is captured from ``tenant`` — it never appears in any tool
    schema, so the model cannot choose another shop.
    """
    shop_id = tenant.shop_id

    @tool
    async def get_sales_summary(
        start_date: str | None = None, end_date: str | None = None
    ) -> dict:
        """Summarize sales for a date range (UTC, YYYY-MM-DD).

        Use for questions like 'Aaj kitni sale hui?', 'Aaj ki total sale
        kitni hai?', 'Is month ki sales kitni hain?', 'Kal kitni sale hui?'.
        Returns total_sales_amount, sale_count, paid_amount, due_amount.
        """
        start_dt, end_dt, err = _range_bounds(start_date, end_date)
        if err is not None:
            return err
        try:
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
            return {
                "status": "ok",
                "start_date": start_date,
                "end_date": end_date,
                "total_sales_amount": _money_str(total),
                "sale_count": int(row[1]),
                "paid_amount": _money_str(paid),
                "due_amount": _money_str(total - paid),
            }
        except Exception:  # noqa: BLE001 — tool boundary hides internals
            return {
                "status": "error",
                "message": "Could not load sales summary.",
            }

    @tool
    async def get_inventory_status(
        product_name: str | None = None,
        category: str | None = None,
        low_stock_only: bool = False,
        limit: int = 20,
    ) -> dict:
        """Check stock for products/variants in this shop.

        Use for 'Black lawn ka kitna stock hai?', 'Inventory mein kya pada
        hai?', 'Kaunse products low stock hain?'. Returns product, variant
        sku, quantity, available_quantity, unit, reorder_level. Ambiguous
        names return matches for clarification instead of guessing.
        """
        try:
            safe_limit = max(1, min(int(limit), _MAX_INVENTORY_LIMIT))
        except (TypeError, ValueError):
            return {"status": "error", "message": "Invalid limit."}
        try:
            stmt = (
                select(ProductVariant, Inventory, Product)
                .join(Product, Product.id == ProductVariant.product_id)
                .outerjoin(Inventory, Inventory.variant_id == ProductVariant.id)
                .where(ProductVariant.shop_id == shop_id)
                .order_by(Product.name.asc(), ProductVariant.sku.asc())
                .limit(safe_limit * 4)
            )
            name = _clean_name(product_name)
            if name:
                like = f"%{name}%"
                stmt = stmt.where(
                    or_(
                        Product.name.ilike(like),
                        ProductVariant.sku.ilike(like),
                    )
                )
            if _clean_name(category):
                cat_like = f"%{_clean_name(category)}%"
                stmt = stmt.join(Category, Category.id == Product.category_id).where(
                    Category.name.ilike(cat_like)
                )
            rows = (await session.execute(stmt)).all()
            if not rows:
                return {"status": "not_found", "message": "No matching inventory found."}

            if name:
                product_ids = {str(r[2].id) for r in rows}
                if len(product_ids) > 1:
                    by_product: dict[str, dict] = {}
                    for _, _, prod in rows:
                        key = str(prod.id)
                        if key not in by_product:
                            by_product[key] = {
                                "product_name": prod.name,
                                "product_type": str(prod.product_type.value)
                                if hasattr(prod.product_type, "value")
                                else str(prod.product_type),
                            }
                    matches = list(by_product.values())[:_MAX_MATCHES]
                    return {
                        "status": "ambiguous",
                        "message": (
                            f"Multiple products match {name!r}. "
                            "Ask which one the shopkeeper means."
                        ),
                        "matches": matches,
                    }

            items = []
            for variant, inv, prod in rows:
                qty = inv.quantity if inv else Decimal(0)
                reserved = inv.reserved_quantity if inv else Decimal(0)
                reorder = inv.reorder_level if inv else Decimal(0)
                unit_val = (
                    variant.unit.value
                    if hasattr(variant.unit, "value")
                    else str(variant.unit)
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
                    return {
                        "status": "ok",
                        "items": [],
                        "count": 0,
                        "message": "No low-stock products.",
                    }
                return {"status": "not_found", "message": "No matching inventory found."}
            return {"status": "ok", "items": items, "count": len(items)}
        except Exception:  # noqa: BLE001 — tool boundary hides internals
            return {"status": "error", "message": "Could not load inventory status."}

    @tool
    async def get_customer_account_summary(customer_name: str) -> dict:
        """Show a customer's khata/udhaar balance (receivable).

        Use for 'Ahmed ka khata kitna hai?', 'Ali se kitna lena hai?'.
        Uses the authoritative receivables service. Ambiguous names return
        matches for clarification.
        """
        name = _clean_name(customer_name)
        if not name:
            return {"status": "error", "message": "Customer name is required."}
        if len(name) > 150:
            return {"status": "error", "message": "Customer name is too long."}
        try:
            like = f"%{name}%"
            found = (
                await session.execute(
                    select(Customer)
                    .where(Customer.shop_id == shop_id, Customer.name.ilike(like))
                    .order_by(Customer.name.asc())
                    .limit(_MAX_MATCHES + 1)
                )
            ).scalars().all()
            if not found:
                return {"status": "not_found", "message": "Customer not found."}
            if len(found) > 1:
                return {
                    "status": "ambiguous",
                    "message": (
                        f"Multiple customers match {name!r}. "
                        "Ask which one the shopkeeper means."
                    ),
                    "matches": [
                        {"name": c.name, "phone": c.phone} for c in found[:_MAX_MATCHES]
                    ],
                }
            customer = found[0]
            try:
                summary = await receivables_service.get_customer_summary(
                    session, shop_id=shop_id, customer_id=customer.id
                )
            except receivables_service.CustomerNotFoundError:
                return {"status": "not_found", "message": "Customer not found."}
            return {
                "status": "ok",
                "customer_name": summary.name,
                "phone": summary.phone,
                "total_purchases": _money_str(summary.total_purchases),
                "total_paid": _money_str(summary.total_paid),
                "outstanding_balance": _money_str(summary.outstanding_balance),
            }
        except Exception:  # noqa: BLE001 — tool boundary hides internals
            return {"status": "error", "message": "Could not load customer account."}

    @tool
    async def get_supplier_account_summary(supplier_name: str) -> dict:
        """Show a supplier's khata balance (payable — kitna dena hai).

        Use for 'Ahmed supplier ko kitna dena hai?', 'Suppliers ka
        outstanding kitna hai?'. Uses the authoritative payables service.
        """
        name = _clean_name(supplier_name)
        if not name:
            return {"status": "error", "message": "Supplier name is required."}
        if len(name) > 150:
            return {"status": "error", "message": "Supplier name is too long."}
        try:
            like = f"%{name}%"
            found = (
                await session.execute(
                    select(Supplier)
                    .where(Supplier.shop_id == shop_id, Supplier.name.ilike(like))
                    .order_by(Supplier.name.asc())
                    .limit(_MAX_MATCHES + 1)
                )
            ).scalars().all()
            if not found:
                return {"status": "not_found", "message": "Supplier not found."}
            if len(found) > 1:
                return {
                    "status": "ambiguous",
                    "message": (
                        f"Multiple suppliers match {name!r}. "
                        "Ask which one the shopkeeper means."
                    ),
                    "matches": [
                        {"name": s.name, "phone": s.phone} for s in found[:_MAX_MATCHES]
                    ],
                }
            supplier = found[0]
            try:
                summary = await payables_service.get_supplier_summary(
                    session, shop_id=shop_id, supplier_id=supplier.id
                )
            except payables_service.SupplierNotFoundError:
                return {"status": "not_found", "message": "Supplier not found."}
            return {
                "status": "ok",
                "supplier_name": summary.name,
                "phone": summary.phone,
                "total_purchases": _money_str(summary.total_purchases),
                "total_paid": _money_str(summary.total_paid),
                "outstanding_balance": _money_str(summary.outstanding_balance),
            }
        except Exception:  # noqa: BLE001 — tool boundary hides internals
            return {"status": "error", "message": "Could not load supplier account."}

    @tool
    async def get_purchase_summary(
        start_date: str | None = None, end_date: str | None = None
    ) -> dict:
        """Summarize supplier purchases for a date range (UTC, YYYY-MM-DD).

        Use for 'Is month kitni purchases hui hain?', 'Aaj kitni purchase
        hui?'. Returns total_purchase_amount, purchase_count, paid_amount,
        due_amount.
        """
        start_dt, end_dt, err = _range_bounds(start_date, end_date)
        if err is not None:
            return err
        try:
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
            return {
                "status": "ok",
                "start_date": start_date,
                "end_date": end_date,
                "total_purchase_amount": _money_str(total),
                "purchase_count": int(row[1]),
                "paid_amount": _money_str(paid),
                "due_amount": _money_str(total - paid),
            }
        except Exception:  # noqa: BLE001 — tool boundary hides internals
            return {"status": "error", "message": "Could not load purchase summary."}

    @tool
    async def get_expense_summary(
        start_date: str | None = None,
        end_date: str | None = None,
        category: str | None = None,
    ) -> dict:
        """Summarize operating expenses for a date range (UTC, YYYY-MM-DD).

        Use for 'Is month ke expenses kitne hain?', 'Aaj kitna expense hua?',
        'Rent par kitna kharch hua?'. Optional category filter (rent, salary,
        utilities, transport, marketing, maintenance, supplies, other).
        """
        start_dt, end_dt, err = _range_bounds(start_date, end_date)
        if err is not None:
            return err
        cat: ExpenseCategory | None = None
        if category is not None and str(category).strip():
            try:
                cat = ExpenseCategory(str(category).strip().lower())
            except ValueError:
                return {
                    "status": "error",
                    "message": f"Unknown expense category {category!r}.",
                }
        try:
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
            return result
        except Exception:  # noqa: BLE001 — tool boundary hides internals
            return {"status": "error", "message": "Could not load expense summary."}

    @tool
    async def get_product_or_catalog_info(product_name: str) -> dict:
        """Look up a product, its category, and its variants.

        Use for 'Black lawn ka product hai?', 'Black lawn kis category mein
        hai?', 'Is product ke variants kya hain?'. Ambiguous names return
        matches for clarification.
        """
        name = _clean_name(product_name)
        if not name:
            return {"status": "error", "message": "Product name is required."}
        if len(name) > 200:
            return {"status": "error", "message": "Product name is too long."}
        try:
            like = f"%{name}%"
            products = (
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
            ).scalars().all()
            if not products:
                return {"status": "not_found", "message": "Product not found."}
            if len(products) > 1:
                return {
                    "status": "ambiguous",
                    "message": (
                        f"Multiple products match {name!r}. "
                        "Ask which one the shopkeeper means."
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
            return {
                "status": "ok",
                "product_name": product.name,
                "category": product.category.name if product.category else None,
                "brand": brand_name,
                "product_type": str(product.product_type.value)
                if hasattr(product.product_type, "value")
                else str(product.product_type),
                "variants": variants,
            }
        except Exception:  # noqa: BLE001 — tool boundary hides internals
            return {"status": "error", "message": "Could not load product info."}

    @tool
    async def get_dashboard_summary() -> dict:
        """Summarize today's business (UTC): sales, purchases, khata totals.

        Use for 'Aaj business ka kya haal hai?', 'Aaj ka summary do.'.
        Reuses the reporting dashboard service.
        """
        try:
            dashboard = await reporting_service.get_dashboard_summary(
                session, shop_id=shop_id
            )
            return {
                "status": "ok",
                "today_sales": _money_str(dashboard.today_sales),
                "today_sales_count": int(dashboard.today_sales_count),
                "today_purchases": _money_str(dashboard.today_purchases),
                "today_purchase_count": int(dashboard.today_purchase_count),
                "today_expenses": _money_str(dashboard.today_expenses),
                "receivables_outstanding": _money_str(
                    dashboard.receivables_outstanding
                ),
                "payables_outstanding": _money_str(dashboard.payables_outstanding),
                "inventory_quantity": _qty_str(dashboard.inventory_quantity),
                "inventory_estimated_value": _money_str(
                    dashboard.inventory_estimated_value
                ),
            }
        except Exception:  # noqa: BLE001 — tool boundary hides internals
            return {"status": "error", "message": "Could not load dashboard summary."}

    return [
        get_sales_summary,  # type: ignore[list-item]
        get_inventory_status,  # type: ignore[list-item]
        get_customer_account_summary,  # type: ignore[list-item]
        get_supplier_account_summary,  # type: ignore[list-item]
        get_purchase_summary,  # type: ignore[list-item]
        get_expense_summary,  # type: ignore[list-item]
        get_product_or_catalog_info,  # type: ignore[list-item]
        get_dashboard_summary,  # type: ignore[list-item]
    ]


def get_read_tool_by_name(
    session: AsyncSession, tenant: TenantContext, name: str
) -> BaseTool | None:
    """Return one bound read tool by name (tests/convenience)."""
    for t in build_read_tools(session, tenant):
        if t.name == name:
            return t
    return None


__all__ = [
    "READ_TOOL_NAMES",
    "build_read_tools",
    "get_read_tool_by_name",
]

"""Business-level read-only tools (Step 2, cached in Step 10).

Small, typed capabilities over the shop's REAL data — never low-level CRUD,
never raw SQL, never a ``shop_id`` argument. Each tool closes over the
authenticated :class:`TenantContext` and the request's ``AsyncSession``,
so the model can only read the authenticated shop.

Step 10 caching: every tool delegates to the shared cached read services
in ``app.cache.cached_reads`` — the exact same wrappers the REST routes
use — so there is one cache implementation, one key scheme and one TTL
policy for both surfaces. The authoritative queries still live in the
domain services / cached-read loaders over PostgreSQL; Redis is only a
read-through cache and is never trusted for mutations.
"""

from __future__ import annotations

from decimal import Decimal

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.state import TenantContext
from app.cache import cached_reads
from app.models.customer import Customer
from app.models.supplier import Supplier
from app.services import payables as payables_service
from app.services import receivables as receivables_service

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

_MAX_MATCHES = 5


def _money_str(value: Decimal | int | None) -> str:
    if value is None:
        return "0.00"
    return str(Decimal(value).quantize(Decimal("0.01")))


def _qty_str(value: Decimal | int | None) -> str:
    if value is None:
        return "0.000"
    return str(Decimal(value).quantize(Decimal("0.001")))


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
        try:
            return await cached_reads.get_cached_sales_aggregate(
                session,
                shop_id=shop_id,
                start_date=start_date,
                end_date=end_date,
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
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
            return await cached_reads.get_cached_inventory_status(
                session,
                shop_id=shop_id,
                product_name=product_name,
                category=category,
                low_stock_only=bool(low_stock_only),
                limit=limit,
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
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
                summary = await cached_reads.get_cached_customer_summary(
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
                summary = await cached_reads.get_cached_supplier_summary(
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
        try:
            return await cached_reads.get_cached_purchase_aggregate(
                session,
                shop_id=shop_id,
                start_date=start_date,
                end_date=end_date,
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
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
        try:
            return await cached_reads.get_cached_expense_aggregate(
                session,
                shop_id=shop_id,
                start_date=start_date,
                end_date=end_date,
                category=category,
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:  # noqa: BLE001 — tool boundary hides internals
            return {"status": "error", "message": "Could not load expense summary."}

    @tool
    async def get_product_or_catalog_info(product_name: str) -> dict:
        """Look up a product, its category, and its variants.

        Use for 'Black lawn ka product hai?', 'Black lawn kis category mein
        hai?', 'Is product ke variants kya hain?'. Ambiguous names return
        matches for clarification.
        """
        try:
            return await cached_reads.get_cached_product_info(
                session, shop_id=shop_id, product_name=product_name
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:  # noqa: BLE001 — tool boundary hides internals
            return {"status": "error", "message": "Could not load product info."}

    @tool
    async def get_dashboard_summary() -> dict:
        """Summarize today's business (UTC): sales, purchases, khata totals.

        Use for 'Aaj business ka kya haal hai?', 'Aaj ka summary do.'.
        Reuses the reporting dashboard service.
        """
        try:
            dashboard = await cached_reads.get_cached_dashboard(
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

"""Reporting & financial statements tests (Step 9).

Covers the read-only reporting layer over the existing operational and
accounting data: trial balance, profit & loss (ledger revenue vs historical
`SaleItem.cost_price` COGS), balance sheet, dashboard, cross-report
consistency with the Step 6/7 Khatas, tenant isolation, and the read-only
`/reports/*` API.

The reporting layer must never mutate anything - a test asserts that too.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import NamedTuple

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    Category,
    Customer,
    LedgerEntry,
    Payment,
    PaymentMethod,
    Product,
    ProductType,
    ProductVariant,
    Purchase,
    Sale,
    SaleItem,
    SaleStatus,
    Shop,
    Supplier,
    Unit,
)
from app.services.accounting import (
    CASH,
    INVENTORY,
    SALES_REVENUE,
    ensure_system_accounts,
)
from app.services.payables import get_total_outstanding as get_total_payables
from app.services.purchases import PurchaseItemInput, create_purchase
from app.services.receivables import (
    get_total_outstanding as get_total_receivables,
)
from app.services.reporting import (
    InvalidReportRangeError,
    get_balance_sheet,
    get_dashboard_summary,
    get_profit_and_loss,
    get_trial_balance,
)
from app.services.sales import PaymentInput, SaleItemInput, create_sale

BASE_DATE = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _day(day: int) -> datetime:
    return BASE_DATE.replace(day=day)


ShopFixture = NamedTuple(
    "ShopFixture",
    [
        ("shop", Shop),
        ("product", Product),
        ("variant", ProductVariant),
        ("supplier", Supplier),
    ],
)


async def _make_shop(
    db_session: AsyncSession,
    *,
    shop_name: str = "Ahmed Fabrics",
    sku: str = "LINEN-WHT-001",
) -> ShopFixture:
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
        purchase_price=Decimal("100.00"),
        selling_price=Decimal("1000.00"),
        unit=Unit.METER,
    )
    supplier = Supplier(shop_id=shop.id, name="Al-Madina Textile")
    db_session.add_all([variant, supplier])
    await db_session.flush()

    return ShopFixture(shop=shop, product=product, variant=variant, supplier=supplier)


async def _stock(
    db_session: AsyncSession, fixture: ShopFixture, *, quantity: str = "1000"
) -> Purchase:
    return await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal(quantity),
                unit_cost=Decimal("100.00"),
            )
        ],
    )


async def _sale(
    db_session: AsyncSession,
    fixture: ShopFixture,
    *,
    quantity: str = "1",
    unit_price: str = "1000",
    payments: list[PaymentInput] | None = None,
    customer_id: uuid.UUID | None = None,
) -> Sale:
    return await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer_id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal(quantity),
                unit_price=Decimal(unit_price),
            )
        ],
        payments=payments,
    )


async def _make_customer(db_session: AsyncSession, shop_id: uuid.UUID) -> Customer:
    customer = Customer(shop_id=shop_id, name="Ahmed")
    db_session.add(customer)
    await db_session.flush()
    return customer


async def _accounts(
    db_session: AsyncSession, shop_id: uuid.UUID
) -> dict[str, Account]:
    return await ensure_system_accounts(db_session, shop_id=shop_id)


async def _set_sale_date(
    db_session: AsyncSession, shop_id: uuid.UUID, sale: Sale, when: datetime
) -> None:
    """Move a sale and its ledger entries to `when` (test-only time travel)."""

    sale.created_at = when
    entries = (
        await db_session.execute(
            sa.select(LedgerEntry).where(
                LedgerEntry.shop_id == shop_id,
                LedgerEntry.reference_id == sale.id,
            )
        )
    ).scalars().all()
    for entry in entries:
        entry.created_at = when
    await db_session.flush()


async def _cancel(db_session: AsyncSession, sale: Sale) -> None:
    sale.status = SaleStatus.CANCELLED
    await db_session.flush()


def _headers(shop_id: uuid.UUID) -> dict[str, str]:
    return {"X-Shop-Id": str(shop_id)}


# --------------------------------------------------------------------------
# Trial balance
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trial_balance_empty_shop(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)

    report = await get_trial_balance(db_session, shop_id=fixture.shop.id)

    assert report.rows == ()
    assert report.total_debits == Decimal("0.00")
    assert report.total_credits == Decimal("0.00")
    assert report.is_balanced is True


@pytest.mark.asyncio
async def test_trial_balance_lists_system_accounts_with_zero_activity(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _accounts(db_session, fixture.shop.id)

    report = await get_trial_balance(db_session, shop_id=fixture.shop.id)

    codes = {row.code for row in report.rows}
    assert CASH in codes
    assert SALES_REVENUE in codes
    assert all(row.debit_total == Decimal("0.00") for row in report.rows)
    assert all(row.credit_total == Decimal("0.00") for row in report.rows)


@pytest.mark.asyncio
async def test_trial_balance_is_balanced_after_sale_and_purchase(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    await _sale(
        db_session,
        fixture,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    report = await get_trial_balance(db_session, shop_id=fixture.shop.id)

    assert report.total_debits == report.total_credits
    assert report.is_balanced is True
    assert report.total_debits == Decimal("101000.00")


@pytest.mark.asyncio
async def test_trial_balance_account_balances_use_normal_direction(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    await _sale(
        db_session,
        fixture,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )
    accounts = await _accounts(db_session, fixture.shop.id)

    report = await get_trial_balance(db_session, shop_id=fixture.shop.id)
    by_code = {row.code: row for row in report.rows}

    assert by_code[CASH].balance == Decimal("1000.00")
    assert by_code[SALES_REVENUE].balance == Decimal("1000.00")
    assert by_code[INVENTORY].balance == Decimal("100000.00")
    assert by_code[INVENTORY].account_id == accounts[INVENTORY].id


@pytest.mark.asyncio
async def test_trial_balance_date_filtering(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    first = await _sale(
        db_session,
        fixture,
        payments=[PaymentInput(amount=Decimal("100"), method=PaymentMethod.CASH)],
    )
    second = await _sale(
        db_session,
        fixture,
        payments=[PaymentInput(amount=Decimal("200"), method=PaymentMethod.CASH)],
    )
    await _set_sale_date(db_session, fixture.shop.id, first, _day(1))
    await _set_sale_date(db_session, fixture.shop.id, second, _day(5))

    windowed = await get_trial_balance(
        db_session,
        shop_id=fixture.shop.id,
        start_date=_day(4),
        end_date=_day(10),
    )

    # Only the second sale's posting falls in the window: cash 200 + AR 800,
    # against revenue 1000.
    assert windowed.total_debits == Decimal("1000.00")
    assert windowed.total_credits == Decimal("1000.00")
    by_window_code = {row.code: row for row in windowed.rows}
    assert by_window_code[CASH].balance == Decimal("200.00")

    closing = await get_trial_balance(
        db_session, shop_id=fixture.shop.id, end_date=_day(10)
    )
    by_code = {row.code: row for row in closing.rows}
    assert by_code[CASH].closing_balance == Decimal("300.00")
    assert by_code[CASH].balance == Decimal("300.00")


@pytest.mark.asyncio
async def test_trial_balance_rejects_inverted_range(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    with pytest.raises(InvalidReportRangeError):
        await get_trial_balance(
            db_session,
            shop_id=fixture.shop.id,
            start_date=_day(10),
            end_date=_day(1),
        )


@pytest.mark.asyncio
async def test_trial_balance_is_tenant_isolated(db_session: AsyncSession) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    await _stock(db_session, shop_b)
    await _sale(
        db_session,
        shop_b,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    report = await get_trial_balance(db_session, shop_id=shop_a.shop.id)

    assert report.total_debits == Decimal("0.00")
    assert report.rows == ()


# --------------------------------------------------------------------------
# Profit & loss
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profit_and_loss_revenue_minus_cogs(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    await _sale(
        db_session,
        fixture,
        quantity="10",
        payments=[PaymentInput(amount=Decimal("10000"), method=PaymentMethod.CASH)],
    )

    report = await get_profit_and_loss(db_session, shop_id=fixture.shop.id)

    assert report.revenue == Decimal("10000.00")
    assert report.cogs == Decimal("1000.00")
    assert report.gross_profit == Decimal("9000.00")


@pytest.mark.asyncio
async def test_cogs_uses_historical_sale_item_cost_not_current_inventory_cost(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture, quantity="1000")
    await _sale(
        db_session,
        fixture,
        quantity="10",
        payments=[PaymentInput(amount=Decimal("10000"), method=PaymentMethod.CASH)],
    )

    # A later, much more expensive receipt changes the *current* weighted
    # average - historical COGS must not move.
    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("1000"),
                unit_cost=Decimal("900.00"),
            )
        ],
    )

    report = await get_profit_and_loss(db_session, shop_id=fixture.shop.id)

    assert report.cogs == Decimal("1000.00")


@pytest.mark.asyncio
async def test_profit_and_loss_sums_multiple_sales_with_decimal_precision(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    await _sale(db_session, fixture, quantity="3.500", unit_price="1000")
    await _sale(db_session, fixture, quantity="1.250", unit_price="1000")

    report = await get_profit_and_loss(db_session, shop_id=fixture.shop.id)

    assert report.revenue == Decimal("4750.00")
    assert report.cogs == Decimal("475.00")
    assert report.gross_profit == Decimal("4275.00")


@pytest.mark.asyncio
async def test_profit_and_loss_excludes_cancelled_sales_from_cogs(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    await _sale(
        db_session,
        fixture,
        quantity="10",
        payments=[PaymentInput(amount=Decimal("10000"), method=PaymentMethod.CASH)],
    )
    cancelled = await _sale(db_session, fixture, quantity="5")
    await _cancel(db_session, cancelled)

    report = await get_profit_and_loss(db_session, shop_id=fixture.shop.id)

    assert report.cogs == Decimal("1000.00")


@pytest.mark.asyncio
async def test_profit_and_loss_date_filtering(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    old = await _sale(
        db_session,
        fixture,
        quantity="1",
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )
    recent = await _sale(
        db_session,
        fixture,
        quantity="2",
        payments=[PaymentInput(amount=Decimal("2000"), method=PaymentMethod.CASH)],
    )
    await _set_sale_date(db_session, fixture.shop.id, old, _day(1))
    await _set_sale_date(db_session, fixture.shop.id, recent, _day(5))

    report = await get_profit_and_loss(
        db_session,
        shop_id=fixture.shop.id,
        start_date=_day(4),
        end_date=_day(10),
    )

    assert report.revenue == Decimal("2000.00")
    assert report.cogs == Decimal("200.00")
    assert report.gross_profit == Decimal("1800.00")


@pytest.mark.asyncio
async def test_profit_and_loss_reports_expense_limitation(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    report = await get_profit_and_loss(db_session, shop_id=fixture.shop.id)

    assert report.expense_reporting_available is False
    assert report.expenses is None
    assert report.net_profit is None


@pytest.mark.asyncio
async def test_profit_and_loss_is_tenant_isolated(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    await _stock(db_session, shop_b)
    await _sale(
        db_session,
        shop_b,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    report = await get_profit_and_loss(db_session, shop_id=shop_a.shop.id)

    assert report.revenue == Decimal("0.00")
    assert report.cogs == Decimal("0.00")
    assert report.gross_profit == Decimal("0.00")


# --------------------------------------------------------------------------
# Balance sheet
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_balance_sheet_classifies_accounts_by_type(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)

    report = await get_balance_sheet(db_session, shop_id=fixture.shop.id)

    assert report.total_assets == Decimal("100000.00")
    assert report.total_liabilities == Decimal("100000.00")
    assert report.total_equity == Decimal("0.00")
    assert report.difference == Decimal("0.00")


@pytest.mark.asyncio
async def test_balance_sheet_surfaces_an_imbalance_rather_than_hiding_it(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    await _sale(
        db_session,
        fixture,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    report = await get_balance_sheet(db_session, shop_id=fixture.shop.id)

    assert report.total_assets == Decimal("101000.00")
    assert report.total_liabilities_and_equity == Decimal("100000.00")
    assert report.difference == Decimal("1000.00")


@pytest.mark.asyncio
async def test_balance_sheet_excludes_revenue_and_expense_accounts(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)

    report = await get_balance_sheet(db_session, shop_id=fixture.shop.id)

    assert all(
        row.account_type.value in {"asset", "liability", "equity"}
        for row in report.rows
    )


@pytest.mark.asyncio
async def test_balance_sheet_is_tenant_isolated(db_session: AsyncSession) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    await _stock(db_session, shop_b)

    report = await get_balance_sheet(db_session, shop_id=shop_a.shop.id)

    assert report.total_assets == Decimal("0.00")
    assert report.total_liabilities == Decimal("0.00")
    assert report.rows == ()


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_totals_for_today(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture, quantity="100")
    await _sale(
        db_session,
        fixture,
        quantity="2",
        payments=[PaymentInput(amount=Decimal("2000"), method=PaymentMethod.CASH)],
    )

    report = await get_dashboard_summary(db_session, shop_id=fixture.shop.id)

    assert report.today_sales == Decimal("2000.00")
    assert report.today_sales_count == 1
    assert report.today_payments_received == Decimal("2000.00")
    assert report.today_cogs == Decimal("200.00")
    assert report.today_gross_profit == Decimal("1800.00")
    assert report.today_purchases == Decimal("10000.00")
    assert report.today_purchase_count == 1


@pytest.mark.asyncio
async def test_dashboard_inventory_metrics(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture, quantity="100")

    report = await get_dashboard_summary(db_session, shop_id=fixture.shop.id)

    assert report.inventory_quantity == Decimal("100.000")
    assert report.inventory_estimated_value == Decimal("10000.00")
    assert report.low_stock_available is False
    assert report.low_stock_variant_count is None


@pytest.mark.asyncio
async def test_dashboard_receivables_and_payables_match_khata_aggregates(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture, quantity="100")
    customer = await _make_customer(db_session, fixture.shop.id)
    await _sale(
        db_session,
        fixture,
        quantity="3",
        customer_id=customer.id,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    report = await get_dashboard_summary(db_session, shop_id=fixture.shop.id)

    assert report.receivables_outstanding == await get_total_receivables(
        db_session, shop_id=fixture.shop.id
    )
    assert report.payables_outstanding == await get_total_payables(
        db_session, shop_id=fixture.shop.id
    )
    assert report.receivables_outstanding == Decimal("2000.00")
    assert report.payables_outstanding == Decimal("10000.00")


@pytest.mark.asyncio
async def test_dashboard_excludes_cancelled_sales(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    sale = await _sale(db_session, fixture, quantity="1")
    await _cancel(db_session, sale)

    report = await get_dashboard_summary(db_session, shop_id=fixture.shop.id)

    assert report.today_sales == Decimal("0.00")
    assert report.today_sales_count == 0


@pytest.mark.asyncio
async def test_dashboard_is_tenant_isolated(db_session: AsyncSession) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    await _stock(db_session, shop_b)
    await _sale(
        db_session,
        shop_b,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    report = await get_dashboard_summary(db_session, shop_id=shop_a.shop.id)

    assert report.today_sales == Decimal("0.00")
    assert report.today_purchases == Decimal("0.00")
    assert report.inventory_quantity == Decimal("0.000")


# --------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reporting_never_mutates_state(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture, quantity="100")
    await _sale(
        db_session,
        fixture,
        quantity="2",
        payments=[PaymentInput(amount=Decimal("2000"), method=PaymentMethod.CASH)],
    )

    async def _counts() -> tuple[int, int, int, int, int, int]:
        async def count(model) -> int:
            return int(
                (
                    await db_session.execute(
                        sa.select(sa.func.count()).select_from(model)
                    )
                ).scalar_one()
            )

        return (
            await count(Sale),
            await count(SaleItem),
            await count(Purchase),
            await count(Payment),
            await count(Account),
            await count(LedgerEntry),
        )

    before = await _counts()

    await get_trial_balance(db_session, shop_id=fixture.shop.id)
    await get_profit_and_loss(db_session, shop_id=fixture.shop.id)
    await get_balance_sheet(db_session, shop_id=fixture.shop.id)
    await get_dashboard_summary(db_session, shop_id=fixture.shop.id)

    assert await _counts() == before


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trial_balance_endpoint(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _stock(api_session, fixture)
    await _sale(
        api_session,
        fixture,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    response = await api_client.get(
        "/reports/trial-balance", headers=_headers(fixture.shop.id)
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_balanced"] is True
    assert Decimal(str(payload["total_debits"])) == Decimal("101000.00")
    assert any(row["code"] == SALES_REVENUE for row in payload["rows"])


@pytest.mark.asyncio
async def test_profit_and_loss_endpoint(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _stock(api_session, fixture)
    await _sale(
        api_session,
        fixture,
        quantity="10",
        payments=[PaymentInput(amount=Decimal("10000"), method=PaymentMethod.CASH)],
    )

    response = await api_client.get(
        "/reports/profit-and-loss", headers=_headers(fixture.shop.id)
    )

    assert response.status_code == 200
    payload = response.json()
    assert Decimal(str(payload["revenue"])) == Decimal("10000.00")
    assert Decimal(str(payload["cogs"])) == Decimal("1000.00")
    assert Decimal(str(payload["gross_profit"])) == Decimal("9000.00")
    assert payload["expense_reporting_available"] is False


@pytest.mark.asyncio
async def test_balance_sheet_endpoint(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _stock(api_session, fixture)

    response = await api_client.get(
        "/reports/balance-sheet", headers=_headers(fixture.shop.id)
    )

    assert response.status_code == 200
    payload = response.json()
    assert Decimal(str(payload["total_assets"])) == Decimal("100000.00")
    assert Decimal(str(payload["difference"])) == Decimal("0.00")


@pytest.mark.asyncio
async def test_dashboard_endpoint(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _stock(api_session, fixture, quantity="100")
    await _sale(
        api_session,
        fixture,
        quantity="2",
        payments=[PaymentInput(amount=Decimal("2000"), method=PaymentMethod.CASH)],
    )

    response = await api_client.get(
        "/reports/dashboard", headers=_headers(fixture.shop.id)
    )

    assert response.status_code == 200
    payload = response.json()
    assert Decimal(str(payload["today_sales"])) == Decimal("2000.00")
    assert payload["today_sales_count"] == 1
    assert payload["low_stock_available"] is False


@pytest.mark.asyncio
async def test_report_endpoints_reject_invalid_date_range(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)

    response = await api_client.get(
        "/reports/trial-balance",
        headers=_headers(fixture.shop.id),
        params={
            "start_date": "2026-09-10T00:00:00Z",
            "end_date": "2026-09-01T00:00:00Z",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_report_endpoints_are_tenant_isolated(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    shop_a = await _make_shop(api_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(api_session, shop_name="Shop B", sku="B-1")
    await _stock(api_session, shop_b)
    await _sale(
        api_session,
        shop_b,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    response = await api_client.get(
        "/reports/dashboard", headers=_headers(shop_a.shop.id)
    )

    assert response.status_code == 200
    assert Decimal(str(response.json()["today_sales"])) == Decimal("0.00")


@pytest.mark.asyncio
async def test_report_endpoints_require_a_shop(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    response = await api_client.get("/reports/dashboard")

    assert response.status_code == 401
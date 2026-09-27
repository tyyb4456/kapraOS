"""Core customer & supplier return mechanism tests.

Covers the deterministic backend return workflows: traceability to the
original sale/purchase item, remaining-quantity enforcement, authoritative
pricing (never client-supplied), inventory via the existing service with
CUSTOMER_RETURN / SUPPLIER_RETURN movements, AR-first receivable impact and
AP payable impact, balanced ledger postings, tenant isolation, atomic
rollback, multi-line atomicity and concurrent-return protection.
"""

import asyncio
import uuid
from decimal import Decimal
from typing import NamedTuple

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

import app.database.session as session_module
from app.models import (
    Category,
    Customer,
    InventoryMovement,
    InventoryMovementType,
    LedgerEntry,
    PaymentMethod,
    Product,
    ProductType,
    ProductVariant,
    Purchase,
    Sale,
    SaleStatus,
    Shop,
    Supplier,
    Unit,
)
from app.services import inventory as inventory_service
from app.services import returns as returns_service
from app.services.accounting import (
    ACCOUNTS_PAYABLE,
    ACCOUNTS_RECEIVABLE,
    CASH,
    COST_OF_GOODS_SOLD,
    INVENTORY as INV_ACCOUNT,
    SALES_REVENUE,
    ensure_system_accounts,
    get_account_balance,
)
from app.services.payables import get_supplier_balance
from app.services.purchases import PurchaseItemInput, create_purchase
from app.services.receivables import get_customer_balance
from app.services.returns import (
    EmptyReturnError,
    ExceedsRemainingQuantityError,
    InvalidReturnQuantityError,
    PurchaseItemNotFoundError,
    PurchaseNotFoundError,
    PurchaseReturnLineInput,
    SaleItemNotFoundError,
    SaleNotFoundError,
    SaleReturnLineInput,
)
from app.services.sales import PaymentInput, SaleItemInput, create_sale

ShopFixture = NamedTuple(
    "ShopFixture",
    [
        ("shop", Shop),
        ("category", Category),
        ("product", Product),
        ("variant", ProductVariant),
        ("supplier", Supplier),
    ],
)


async def _make_shop_with_variant(
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
        purchase_price=Decimal("800.00"),
        selling_price=Decimal("1000.00"),
        unit=Unit.METER,
    )
    supplier = Supplier(shop_id=shop.id, name="Al-Madina Textile")
    db_session.add_all([variant, supplier])
    await db_session.flush()
    return ShopFixture(
        shop=shop, category=category, product=product, variant=variant, supplier=supplier
    )


async def _make_customer(db_session: AsyncSession, shop_id: uuid.UUID) -> Customer:
    customer = Customer(shop_id=shop_id, name="Ahmed", phone="0300-1234567")
    db_session.add(customer)
    await db_session.flush()
    return customer


async def _seed(db_session: AsyncSession, fixture: ShopFixture, qty: str, cost: str) -> None:
    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal(qty),
                unit_cost=Decimal(cost),
            )
        ],
    )


async def _qty(db_session: AsyncSession, shop_id: uuid.UUID, variant_id: uuid.UUID) -> Decimal:
    inv = await inventory_service.get_or_create_inventory(
        db_session, shop_id=shop_id, variant_id=variant_id
    )
    return inv.quantity


async def _movements(db_session: AsyncSession, variant_id: uuid.UUID) -> list[InventoryMovement]:
    result = await db_session.execute(
        sa.select(InventoryMovement)
        .where(InventoryMovement.variant_id == variant_id)
        .order_by(InventoryMovement.created_at)
    )
    return list(result.scalars())


async def _sale_by_id(db_session: AsyncSession, sale_id: uuid.UUID) -> Sale:
    result = await db_session.execute(sa.select(Sale).where(Sale.id == sale_id))
    return result.scalar_one()


async def _ledger_for(
    db_session: AsyncSession, shop_id: uuid.UUID, reference_id: uuid.UUID
) -> list[LedgerEntry]:
    rows = (
        await db_session.execute(
            sa.select(LedgerEntry).where(
                LedgerEntry.shop_id == shop_id,
                LedgerEntry.reference_id == reference_id,
            )
        )
    ).scalars().all()
    return list(rows)


def _balanced(entries: list[LedgerEntry]) -> bool:
    debit = sum((Decimal(e.debit) for e in entries), start=Decimal("0"))
    credit = sum((Decimal(e.credit) for e in entries), start=Decimal("0"))
    return debit == credit and debit > 0


# --------------------------------------------------------------------------
# Customer returns
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_customer_return(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    sale_item_id = sale.items[0].id

    ret = await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[SaleReturnLineInput(sale_item_id=sale_item_id, quantity=Decimal("5"))],
    )
    assert ret.total_amount == Decimal("5000.00")
    assert ret.ar_amount == Decimal("5000.00")
    assert ret.cash_refund == Decimal("0.00")
    assert ret.cogs_amount == Decimal("4000.00")

    assert await _qty(db_session, fixture.shop.id, fixture.variant.id) == Decimal("10.000")
    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=customer.id
    )
    assert balance.outstanding_balance == Decimal("0.00")
    assert balance.total_returns == Decimal("5000.00")

    entries = await _ledger_for(db_session, fixture.shop.id, ret.id)
    assert _balanced(entries)
    types = {e.reference_type for e in entries}
    assert types == {"SALE_RETURN"}


@pytest.mark.asyncio
async def test_partial_customer_return(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    ret = await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[SaleReturnLineInput(sale_item_id=sale.items[0].id, quantity=Decimal("2"))],
    )
    assert ret.total_amount == Decimal("2000.00")
    assert await _qty(db_session, fixture.shop.id, fixture.variant.id) == Decimal("7.000")
    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=customer.id
    )
    assert balance.outstanding_balance == Decimal("3000.00")


@pytest.mark.asyncio
async def test_multiple_partial_customer_returns(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("10"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    item_id = sale.items[0].id
    await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[SaleReturnLineInput(sale_item_id=item_id, quantity=Decimal("3"))],
    )
    await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[SaleReturnLineInput(sale_item_id=item_id, quantity=Decimal("2"))],
    )
    remaining = await returns_service.get_remaining_sale_quantities(
        db_session, shop_id=fixture.shop.id, sale_id=sale.id
    )
    assert remaining[item_id] == Decimal("5.000")
    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=customer.id
    )
    assert balance.outstanding_balance == Decimal("5000.00")
    assert balance.total_returns == Decimal("5000.00")


@pytest.mark.asyncio
async def test_customer_return_exceeding_original_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    with pytest.raises(ExceedsRemainingQuantityError):
        await returns_service.create_sale_return(
            db_session,
            shop_id=fixture.shop.id,
            sale_id=sale.id,
            lines=[
                SaleReturnLineInput(sale_item_id=sale.items[0].id, quantity=Decimal("6"))
            ],
        )


@pytest.mark.asyncio
async def test_customer_return_exceeding_remaining_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    item_id = sale.items[0].id
    await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[SaleReturnLineInput(sale_item_id=item_id, quantity=Decimal("2"))],
    )
    with pytest.raises(ExceedsRemainingQuantityError):
        await returns_service.create_sale_return(
            db_session,
            shop_id=fixture.shop.id,
            sale_id=sale.id,
            lines=[SaleReturnLineInput(sale_item_id=item_id, quantity=Decimal("4"))],
        )


@pytest.mark.asyncio
async def test_customer_return_invalid_sale(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    with pytest.raises(SaleNotFoundError):
        await returns_service.create_sale_return(
            db_session,
            shop_id=fixture.shop.id,
            sale_id=uuid.uuid4(),
            lines=[
                SaleReturnLineInput(sale_item_id=uuid.uuid4(), quantity=Decimal("1"))
            ],
        )


@pytest.mark.asyncio
async def test_customer_return_invalid_item(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("2"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    with pytest.raises(SaleItemNotFoundError):
        await returns_service.create_sale_return(
            db_session,
            shop_id=fixture.shop.id,
            sale_id=sale.id,
            lines=[SaleReturnLineInput(sale_item_id=uuid.uuid4(), quantity=Decimal("1"))],
        )


@pytest.mark.asyncio
async def test_customer_return_foreign_tenant_sale(db_session: AsyncSession) -> None:
    shop_a = await _make_shop_with_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(db_session, shop_name="Shop B", sku="B-1")
    customer_b = await _make_customer(db_session, shop_b.shop.id)
    await _seed(db_session, shop_b, "10", "800")
    sale_b = await create_sale(
        db_session,
        shop_id=shop_b.shop.id,
        customer_id=customer_b.id,
        items=[
            SaleItemInput(
                variant_id=shop_b.variant.id,
                quantity=Decimal("2"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    with pytest.raises(SaleNotFoundError):
        await returns_service.create_sale_return(
            db_session,
            shop_id=shop_a.shop.id,
            sale_id=sale_b.id,
            lines=[
                SaleReturnLineInput(
                    sale_item_id=sale_b.items[0].id, quantity=Decimal("1")
                )
            ],
        )


@pytest.mark.asyncio
async def test_customer_return_pricing_authoritative(db_session: AsyncSession) -> None:
    """Return value is qty × original unit price, never client-supplied."""

    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    ret = await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[SaleReturnLineInput(sale_item_id=sale.items[0].id, quantity=Decimal("2"))],
    )
    assert ret.items[0].unit_price == Decimal("1000.00")
    assert ret.items[0].total == Decimal("2000.00")
    assert ret.total_amount == Decimal("2000.00")


@pytest.mark.asyncio
async def test_customer_return_creates_correct_movement(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("4"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    ret = await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[SaleReturnLineInput(sale_item_id=sale.items[0].id, quantity=Decimal("1.5"))],
    )
    movements = await _movements(db_session, fixture.variant.id)
    returns = [m for m in movements if m.movement_type is InventoryMovementType.CUSTOMER_RETURN]
    assert len(returns) == 1
    assert returns[0].quantity == Decimal("1.500")
    assert returns[0].reference_type == "SALE_RETURN"
    assert returns[0].reference_id == ret.id
    assert returns[0].unit_cost == Decimal("800.00")


@pytest.mark.asyncio
async def test_customer_return_accounting_balanced(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "20", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_price=Decimal("1000"),
            )
        ],
        payments=[PaymentInput(amount=Decimal("2000"), method=PaymentMethod.CASH)],
    )
    # Paid 2000, due 3000. Return 4000 → AR 3000, cash 1000 (AR-first).
    ret = await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[SaleReturnLineInput(sale_item_id=sale.items[0].id, quantity=Decimal("4"))],
    )
    assert ret.total_amount == Decimal("4000.00")
    assert ret.ar_amount == Decimal("3000.00")
    assert ret.cash_refund == Decimal("1000.00")

    entries = await _ledger_for(db_session, fixture.shop.id, ret.id)
    assert _balanced(entries)
    accounts = await ensure_system_accounts(db_session, shop_id=fixture.shop.id)
    by_account = {e.account_id: e for e in entries}
    assert by_account[accounts[SALES_REVENUE].id].debit == Decimal("4000.00")
    assert by_account[accounts[ACCOUNTS_RECEIVABLE].id].credit == Decimal("3000.00")
    assert by_account[accounts[CASH].id].credit == Decimal("1000.00")

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=customer.id
    )
    # 5000 - 2000 - 3000 = 0
    assert balance.outstanding_balance == Decimal("0.00")


@pytest.mark.asyncio
async def test_customer_return_failure_rolls_back(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    real_add = inventory_service.add_stock

    async def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("inventory exploded")

    monkeypatch.setattr("app.services.returns.inventory_service.add_stock", _boom)
    savepoint = await db_session.begin_nested()
    with pytest.raises(RuntimeError):
        await returns_service.create_sale_return(
            db_session,
            shop_id=fixture.shop.id,
            sale_id=sale.id,
            lines=[
                SaleReturnLineInput(sale_item_id=sale.items[0].id, quantity=Decimal("1"))
            ],
        )
    await savepoint.rollback()
    assert real_add is not None

    from app.models.returns import SaleReturn as SaleReturnModel

    rows = (await db_session.execute(sa.select(SaleReturnModel))).scalars().all()
    assert rows == []
    assert await _qty(db_session, fixture.shop.id, fixture.variant.id) == Decimal("5.000")
    movements = await _movements(db_session, fixture.variant.id)
    assert all(m.movement_type is not InventoryMovementType.CUSTOMER_RETURN for m in movements)


@pytest.mark.asyncio
async def test_multi_line_customer_return_atomic(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    second = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="SUIT-002",
        purchase_price=Decimal("2000.00"),
        selling_price=Decimal("3500.00"),
        unit=Unit.SET,
    )
    db_session.add(second)
    await db_session.flush()
    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[PurchaseItemInput(variant_id=second.id, quantity=Decimal("5"), unit_cost=Decimal("2000"))],
    )
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("4"), unit_price=Decimal("1000")
            ),
            SaleItemInput(
                variant_id=second.id, quantity=Decimal("2"), unit_price=Decimal("3500")
            ),
        ],
    )
    by_variant = {i.variant_id: i for i in sale.items}
    ret = await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[
            SaleReturnLineInput(
                sale_item_id=by_variant[fixture.variant.id].id, quantity=Decimal("1")
            ),
            SaleReturnLineInput(
                sale_item_id=by_variant[second.id].id, quantity=Decimal("1")
            ),
        ],
    )
    assert ret.total_amount == Decimal("4500.00")
    assert len(ret.items) == 2

    # One failing line rejects the whole return.
    with pytest.raises(ExceedsRemainingQuantityError):
        await returns_service.create_sale_return(
            db_session,
            shop_id=fixture.shop.id,
            sale_id=sale.id,
            lines=[
                SaleReturnLineInput(
                    sale_item_id=by_variant[fixture.variant.id].id, quantity=Decimal("1")
                ),
                SaleReturnLineInput(
                    sale_item_id=by_variant[second.id].id, quantity=Decimal("99")
                ),
            ],
        )
    from app.models.returns import SaleReturn as SaleReturnModel

    count = (
        await db_session.execute(sa.select(sa.func.count()).select_from(SaleReturnModel))
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_concurrent_customer_returns_cannot_over_return() -> None:
    async with session_module.AsyncSessionLocal() as setup:
        shop = Shop(name="Concurrency Return Shop")
        category = Category(shop=shop, name="Open Fabric")
        setup.add_all([shop, category])
        await setup.flush()
        product = Product(
            shop_id=shop.id,
            category_id=category.id,
            name="Fabric",
            product_type=ProductType.OPEN_FABRIC,
        )
        setup.add(product)
        await setup.flush()
        variant = ProductVariant(
            shop_id=shop.id,
            product_id=product.id,
            sku="CONC-RET-001",
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("200.00"),
            unit=Unit.METER,
        )
        supplier = Supplier(shop_id=shop.id, name="Supplier")
        customer = Customer(shop_id=shop.id, name="Ahmed")
        setup.add_all([variant, supplier, customer])
        await setup.flush()
        await create_purchase(
            setup,
            shop_id=shop.id,
            supplier_id=supplier.id,
            items=[
                PurchaseItemInput(
                    variant_id=variant.id, quantity=Decimal("10"), unit_cost=Decimal("100")
                )
            ],
        )
        sale = await create_sale(
            setup,
            shop_id=shop.id,
            customer_id=customer.id,
            items=[
                SaleItemInput(
                    variant_id=variant.id, quantity=Decimal("5"), unit_price=Decimal("200")
                )
            ],
        )
        await setup.commit()
        shop_id, sale_id, item_id = shop.id, sale.id, sale.items[0].id

    async def _return() -> bool:
        async with session_module.AsyncSessionLocal() as worker:
            try:
                await returns_service.create_sale_return(
                    worker,
                    shop_id=shop_id,
                    sale_id=sale_id,
                    lines=[SaleReturnLineInput(sale_item_id=item_id, quantity=Decimal("4"))],
                )
                await worker.commit()
                return True
            except ExceedsRemainingQuantityError:
                await worker.rollback()
                return False

    try:
        results = await asyncio.gather(_return(), _return())
        assert results.count(True) == 1
        assert results.count(False) == 1
    finally:
        async with session_module.AsyncSessionLocal() as cleanup:
            shop_to_delete = await cleanup.get(Shop, shop_id)
            if shop_to_delete is not None:
                await cleanup.delete(shop_to_delete)
                await cleanup.commit()


@pytest.mark.asyncio
async def test_unrelated_inventory_unchanged_on_customer_return(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    other = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="OTHER-001",
        purchase_price=Decimal("50.00"),
        selling_price=Decimal("100.00"),
        unit=Unit.METER,
    )
    db_session.add(other)
    await db_session.flush()
    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[PurchaseItemInput(variant_id=other.id, quantity=Decimal("7"), unit_cost=Decimal("50"))],
    )
    await _seed(db_session, fixture, "10", "800")
    customer = await _make_customer(db_session, fixture.shop.id)
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("5"), unit_price=Decimal("1000")
            )
        ],
    )
    await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[SaleReturnLineInput(sale_item_id=sale.items[0].id, quantity=Decimal("2"))],
    )
    assert await _qty(db_session, fixture.shop.id, other.id) == Decimal("7.000")


@pytest.mark.asyncio
async def test_customer_return_zero_quantity_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("2"), unit_price=Decimal("1000")
            )
        ],
    )
    with pytest.raises(InvalidReturnQuantityError):
        await returns_service.create_sale_return(
            db_session,
            shop_id=fixture.shop.id,
            sale_id=sale.id,
            lines=[SaleReturnLineInput(sale_item_id=sale.items[0].id, quantity=Decimal("0"))],
        )


# --------------------------------------------------------------------------
# Supplier returns
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_supplier_return(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("10"), unit_cost=Decimal("2500")
            )
        ],
    )
    item_id = (
        await db_session.execute(
            sa.select(Purchase).where(Purchase.id == purchase.id)
        )
    ).scalar_one()
    purchase_items = (
        await db_session.execute(
            sa.select(sa.text("id")).select_from(sa.text("purchase_items"))
        )
    )
    _ = (item_id, purchase_items)
    from app.models import PurchaseItem

    pi = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    ret = await returns_service.create_purchase_return(
        db_session,
        shop_id=fixture.shop.id,
        purchase_id=purchase.id,
        lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("10"))],
    )
    assert ret.total_amount == Decimal("25000.00")
    assert await _qty(db_session, fixture.shop.id, fixture.variant.id) == Decimal("0.000")
    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=fixture.supplier.id
    )
    assert balance.outstanding_balance == Decimal("0.00")
    entries = await _ledger_for(db_session, fixture.shop.id, ret.id)
    assert _balanced(entries)


@pytest.mark.asyncio
async def test_partial_supplier_return(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("10"), unit_cost=Decimal("2500")
            )
        ],
    )
    from app.models import PurchaseItem

    pi = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    ret = await returns_service.create_purchase_return(
        db_session,
        shop_id=fixture.shop.id,
        purchase_id=purchase.id,
        lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("2"))],
    )
    assert ret.total_amount == Decimal("5000.00")
    assert await _qty(db_session, fixture.shop.id, fixture.variant.id) == Decimal("8.000")
    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=fixture.supplier.id
    )
    assert balance.outstanding_balance == Decimal("20000.00")


@pytest.mark.asyncio
async def test_multiple_partial_supplier_returns(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("10"), unit_cost=Decimal("100")
            )
        ],
    )
    from app.models import PurchaseItem

    pi = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    await returns_service.create_purchase_return(
        db_session,
        shop_id=fixture.shop.id,
        purchase_id=purchase.id,
        lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("3"))],
    )
    await returns_service.create_purchase_return(
        db_session,
        shop_id=fixture.shop.id,
        purchase_id=purchase.id,
        lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("2"))],
    )
    remaining = await returns_service.get_remaining_purchase_quantities(
        db_session, shop_id=fixture.shop.id, purchase_id=purchase.id
    )
    assert remaining[pi.id] == Decimal("5.000")


@pytest.mark.asyncio
async def test_supplier_return_exceeding_original_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("10"), unit_cost=Decimal("100")
            )
        ],
    )
    from app.models import PurchaseItem

    pi = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    with pytest.raises(ExceedsRemainingQuantityError):
        await returns_service.create_purchase_return(
            db_session,
            shop_id=fixture.shop.id,
            purchase_id=purchase.id,
            lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("11"))],
        )


@pytest.mark.asyncio
async def test_supplier_return_exceeding_remaining_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("10"), unit_cost=Decimal("100")
            )
        ],
    )
    from app.models import PurchaseItem

    pi = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    await returns_service.create_purchase_return(
        db_session,
        shop_id=fixture.shop.id,
        purchase_id=purchase.id,
        lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("3"))],
    )
    with pytest.raises(ExceedsRemainingQuantityError):
        await returns_service.create_purchase_return(
            db_session,
            shop_id=fixture.shop.id,
            purchase_id=purchase.id,
            lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("8"))],
        )


@pytest.mark.asyncio
async def test_supplier_return_exceeding_stock_rejected(db_session: AsyncSession) -> None:
    from app.services.inventory import InsufficientStockError

    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("10"), unit_cost=Decimal("100")
            )
        ],
    )
    # Sell 7 so only 3 remain; returning 7 must fail on stock, not on remaining.
    await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("7"), unit_price=Decimal("200")
            )
        ],
    )
    from app.models import PurchaseItem

    pi = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    with pytest.raises(InsufficientStockError):
        await returns_service.create_purchase_return(
            db_session,
            shop_id=fixture.shop.id,
            purchase_id=purchase.id,
            lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("7"))],
        )


@pytest.mark.asyncio
async def test_supplier_return_invalid_purchase(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    with pytest.raises(PurchaseNotFoundError):
        await returns_service.create_purchase_return(
            db_session,
            shop_id=fixture.shop.id,
            purchase_id=uuid.uuid4(),
            lines=[
                PurchaseReturnLineInput(
                    purchase_item_id=uuid.uuid4(), quantity=Decimal("1")
                )
            ],
        )


@pytest.mark.asyncio
async def test_supplier_return_invalid_item(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("5"), unit_cost=Decimal("100")
            )
        ],
    )
    with pytest.raises(PurchaseItemNotFoundError):
        await returns_service.create_purchase_return(
            db_session,
            shop_id=fixture.shop.id,
            purchase_id=purchase.id,
            lines=[
                PurchaseReturnLineInput(
                    purchase_item_id=uuid.uuid4(), quantity=Decimal("1")
                )
            ],
        )


@pytest.mark.asyncio
async def test_supplier_return_foreign_tenant(db_session: AsyncSession) -> None:
    shop_a = await _make_shop_with_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(db_session, shop_name="Shop B", sku="B-1")
    purchase_b = await create_purchase(
        db_session,
        shop_id=shop_b.shop.id,
        supplier_id=shop_b.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=shop_b.variant.id, quantity=Decimal("5"), unit_cost=Decimal("100")
            )
        ],
    )
    from app.models import PurchaseItem

    pi = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase_b.id)
        )
    ).scalar_one()
    with pytest.raises(PurchaseNotFoundError):
        await returns_service.create_purchase_return(
            db_session,
            shop_id=shop_a.shop.id,
            purchase_id=purchase_b.id,
            lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("1"))],
        )


@pytest.mark.asyncio
async def test_supplier_return_pricing_authoritative(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("10"), unit_cost=Decimal("2500")
            )
        ],
    )
    from app.models import PurchaseItem

    pi = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    ret = await returns_service.create_purchase_return(
        db_session,
        shop_id=fixture.shop.id,
        purchase_id=purchase.id,
        lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("2"))],
    )
    assert ret.items[0].unit_cost == Decimal("2500.00")
    assert ret.items[0].total == Decimal("5000.00")


@pytest.mark.asyncio
async def test_supplier_return_movement_and_accounting(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("10"), unit_cost=Decimal("100")
            )
        ],
    )
    from app.models import PurchaseItem

    pi = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    ret = await returns_service.create_purchase_return(
        db_session,
        shop_id=fixture.shop.id,
        purchase_id=purchase.id,
        lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("4"))],
    )
    movements = await _movements(db_session, fixture.variant.id)
    supplier_returns = [
        m for m in movements if m.movement_type is InventoryMovementType.SUPPLIER_RETURN
    ]
    assert len(supplier_returns) == 1
    assert supplier_returns[0].quantity == Decimal("-4.000")
    assert supplier_returns[0].reference_id == ret.id

    entries = await _ledger_for(db_session, fixture.shop.id, ret.id)
    assert _balanced(entries)
    accounts = await ensure_system_accounts(db_session, shop_id=fixture.shop.id)
    by_account = {e.account_id: e for e in entries}
    assert by_account[accounts[ACCOUNTS_PAYABLE].id].debit == Decimal("400.00")
    assert by_account[accounts[INV_ACCOUNT].id].credit == Decimal("400.00")


@pytest.mark.asyncio
async def test_supplier_return_failure_rolls_back(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("5"), unit_cost=Decimal("100")
            )
        ],
    )
    from app.models import PurchaseItem

    pi = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    savepoint = await db_session.begin_nested()
    with pytest.raises(ExceedsRemainingQuantityError):
        await returns_service.create_purchase_return(
            db_session,
            shop_id=fixture.shop.id,
            purchase_id=purchase.id,
            lines=[PurchaseReturnLineInput(purchase_item_id=pi.id, quantity=Decimal("99"))],
        )
    await savepoint.rollback()
    from app.models.returns import PurchaseReturn as PurchaseReturnModel

    rows = (await db_session.execute(sa.select(PurchaseReturnModel))).scalars().all()
    assert rows == []
    assert await _qty(db_session, fixture.shop.id, fixture.variant.id) == Decimal("5.000")


@pytest.mark.asyncio
async def test_multi_line_supplier_return_atomic(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    second = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="SUIT-SUP-002",
        purchase_price=Decimal("2000.00"),
        selling_price=Decimal("3000.00"),
        unit=Unit.SET,
    )
    db_session.add(second)
    await db_session.flush()
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("10"), unit_cost=Decimal("100")
            ),
            PurchaseItemInput(
                variant_id=second.id, quantity=Decimal("5"), unit_cost=Decimal("2000")
            ),
        ],
    )
    from app.models import PurchaseItem

    items = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalars().all()
    by_variant = {i.variant_id: i for i in items}
    ret = await returns_service.create_purchase_return(
        db_session,
        shop_id=fixture.shop.id,
        purchase_id=purchase.id,
        lines=[
            PurchaseReturnLineInput(
                purchase_item_id=by_variant[fixture.variant.id].id, quantity=Decimal("2")
            ),
            PurchaseReturnLineInput(
                purchase_item_id=by_variant[second.id].id, quantity=Decimal("1")
            ),
        ],
    )
    assert ret.total_amount == Decimal("2200.00")

    with pytest.raises(ExceedsRemainingQuantityError):
        await returns_service.create_purchase_return(
            db_session,
            shop_id=fixture.shop.id,
            purchase_id=purchase.id,
            lines=[
                PurchaseReturnLineInput(
                    purchase_item_id=by_variant[fixture.variant.id].id,
                    quantity=Decimal("1"),
                ),
                PurchaseReturnLineInput(
                    purchase_item_id=by_variant[second.id].id, quantity=Decimal("99")
                ),
            ],
        )


@pytest.mark.asyncio
async def test_concurrent_supplier_returns_cannot_over_return() -> None:
    async with session_module.AsyncSessionLocal() as setup:
        shop = Shop(name="Concurrency Supplier Return Shop")
        category = Category(shop=shop, name="Open Fabric")
        setup.add_all([shop, category])
        await setup.flush()
        product = Product(
            shop_id=shop.id,
            category_id=category.id,
            name="Fabric",
            product_type=ProductType.OPEN_FABRIC,
        )
        setup.add(product)
        await setup.flush()
        variant = ProductVariant(
            shop_id=shop.id,
            product_id=product.id,
            sku="CONC-SUP-RET-001",
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("200.00"),
            unit=Unit.METER,
        )
        supplier = Supplier(shop_id=shop.id, name="Supplier")
        setup.add_all([variant, supplier])
        await setup.flush()
        purchase = await create_purchase(
            setup,
            shop_id=shop.id,
            supplier_id=supplier.id,
            items=[
                PurchaseItemInput(
                    variant_id=variant.id, quantity=Decimal("5"), unit_cost=Decimal("100")
                )
            ],
        )
        await setup.commit()
        shop_id, purchase_id = shop.id, purchase.id
        from app.models import PurchaseItem

        async with session_module.AsyncSessionLocal() as reader:
            pi = (
                await reader.execute(
                    sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase_id)
                )
            ).scalar_one()
            item_id = pi.id

    async def _return() -> bool:
        async with session_module.AsyncSessionLocal() as worker:
            try:
                await returns_service.create_purchase_return(
                    worker,
                    shop_id=shop_id,
                    purchase_id=purchase_id,
                    lines=[
                        PurchaseReturnLineInput(
                            purchase_item_id=item_id, quantity=Decimal("4")
                        )
                    ],
                )
                await worker.commit()
                return True
            except ExceedsRemainingQuantityError:
                await worker.rollback()
                return False

    try:
        results = await asyncio.gather(_return(), _return())
        assert results.count(True) == 1
        assert results.count(False) == 1
    finally:
        async with session_module.AsyncSessionLocal() as cleanup:
            shop_to_delete = await cleanup.get(Shop, shop_id)
            if shop_to_delete is not None:
                await cleanup.delete(shop_to_delete)
                await cleanup.commit()


@pytest.mark.asyncio
async def test_empty_return_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("2"), unit_price=Decimal("1000")
            )
        ],
    )
    with pytest.raises(EmptyReturnError):
        await returns_service.create_sale_return(
            db_session, shop_id=fixture.shop.id, sale_id=sale.id, lines=[]
        )


@pytest.mark.asyncio
async def test_sale_with_returns_cannot_be_edited_or_voided(
    db_session: AsyncSession,
) -> None:
    from app.services import sales as sales_service
    from app.services.returns import SaleHasReturnsError

    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, fixture.shop.id)
    await _seed(db_session, fixture, "10", "800")
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("5"), unit_price=Decimal("1000")
            )
        ],
    )
    await returns_service.create_sale_return(
        db_session,
        shop_id=fixture.shop.id,
        sale_id=sale.id,
        lines=[SaleReturnLineInput(sale_item_id=sale.items[0].id, quantity=Decimal("1"))],
    )
    with pytest.raises(SaleHasReturnsError):
        await sales_service.delete_sale(
            db_session, shop_id=fixture.shop.id, sale_id=sale.id
        )


@pytest.mark.asyncio
async def test_sale_return_statuses_and_ledgers() -> None:
    """Sale stays COMPLETED/PARTIAL; its original postings are untouched."""

    async with session_module.AsyncSessionLocal() as setup:
        shop = Shop(name="Return Status Shop")
        category = Category(shop=shop, name="Open Fabric")
        setup.add_all([shop, category])
        await setup.flush()
        product = Product(
            shop_id=shop.id,
            category_id=category.id,
            name="Fabric",
            product_type=ProductType.OPEN_FABRIC,
        )
        setup.add(product)
        await setup.flush()
        variant = ProductVariant(
            shop_id=shop.id,
            product_id=product.id,
            sku="RET-STATUS-001",
            purchase_price=Decimal("800.00"),
            selling_price=Decimal("1000.00"),
            unit=Unit.METER,
        )
        supplier = Supplier(shop_id=shop.id, name="Supplier")
        customer = Customer(shop_id=shop.id, name="Ahmed")
        setup.add_all([variant, supplier, customer])
        await setup.flush()
        await create_purchase(
            setup,
            shop_id=shop.id,
            supplier_id=supplier.id,
            items=[
                PurchaseItemInput(
                    variant_id=variant.id, quantity=Decimal("10"), unit_cost=Decimal("800")
                )
            ],
        )
        sale = await create_sale(
            setup,
            shop_id=shop.id,
            customer_id=customer.id,
            items=[
                SaleItemInput(
                    variant_id=variant.id, quantity=Decimal("5"), unit_price=Decimal("1000")
                )
            ],
        )
        sale_id, item_id = sale.id, sale.items[0].id
        shop_id, customer_id = shop.id, customer.id
        await setup.commit()

        try:
            async with session_module.AsyncSessionLocal() as worker:
                await returns_service.create_sale_return(
                    worker,
                    shop_id=shop_id,
                    sale_id=sale_id,
                    lines=[SaleReturnLineInput(sale_item_id=item_id, quantity=Decimal("5"))],
                )
                await worker.commit()

            async with session_module.AsyncSessionLocal() as check:
                refreshed = await _sale_by_id(check, sale_id)
                assert refreshed.status in (SaleStatus.COMPLETED, SaleStatus.PARTIAL)
                sale_entries = await _ledger_for(check, shop_id, sale_id)
                assert any(e.reference_type == "SALE" for e in sale_entries)
                assert any(e.reference_type == "SALE_COGS" for e in sale_entries)
        finally:
            async with session_module.AsyncSessionLocal() as cleanup:
                shop_to_delete = await cleanup.get(Shop, shop_id)
                if shop_to_delete is not None:
                    await cleanup.delete(shop_to_delete)
                    await cleanup.commit()


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


@pytest.mark.asyncio
async def test_sale_return_api_end_to_end(api_session: AsyncSession, mocked_api_client) -> None:  # type: ignore[no-untyped-def]
    from app.models import User
    from app.models.user import UserRole

    fixture = await _make_shop_with_variant(api_session)
    customer = await _make_customer(api_session, fixture.shop.id)
    await _seed(api_session, fixture, "10", "800")
    sale = await create_sale(
        api_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("5"), unit_price=Decimal("1000")
            )
        ],
    )
    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=fixture.shop.id,
        name="Mock User",
        email="mock@example.com",
        role=UserRole.OWNER,
    )
    api_session.add(user)
    await api_session.flush()

    response = await mocked_api_client.post(
        f"/sales/{sale.id}/returns",
        headers=_headers(),
        json={"lines": [{"sale_item_id": str(sale.items[0].id), "quantity": "2"}]},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert Decimal(str(payload["total_return_amount"])) == Decimal("2000.00")
    assert payload["sale_id"] == str(sale.id)

    # Exceeding remaining → 422.
    response = await mocked_api_client.post(
        f"/sales/{sale.id}/returns",
        headers=_headers(),
        json={"lines": [{"sale_item_id": str(sale.items[0].id), "quantity": "4"}]},
    )
    assert response.status_code == 422

    # Unknown sale → 404 without leaking.
    response = await mocked_api_client.post(
        f"/sales/{uuid.uuid4()}/returns",
        headers=_headers(),
        json={"lines": [{"sale_item_id": str(sale.items[0].id), "quantity": "1"}]},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_purchase_return_api_end_to_end(api_session: AsyncSession, mocked_api_client) -> None:  # type: ignore[no-untyped-def]
    from app.models import PurchaseItem, User
    from app.models.user import UserRole

    fixture = await _make_shop_with_variant(api_session)
    purchase = await create_purchase(
        api_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id, quantity=Decimal("10"), unit_cost=Decimal("100")
            )
        ],
    )
    pi = (
        await api_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=fixture.shop.id,
        name="Mock User",
        email="mock@example.com",
        role=UserRole.OWNER,
    )
    api_session.add(user)
    await api_session.flush()

    response = await mocked_api_client.post(
        f"/purchases/{purchase.id}/returns",
        headers=_headers(),
        json={"lines": [{"purchase_item_id": str(pi.id), "quantity": "3"}]},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert Decimal(str(payload["total_return_amount"])) == Decimal("300.00")

    response = await mocked_api_client.get(
        f"/purchases/{purchase.id}/returns", headers=_headers()
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_return_apis_do_not_leak_foreign_tenant(
    api_session: AsyncSession, mocked_api_client  # type: ignore[no-untyped-def]
) -> None:
    from app.models import PurchaseItem, User
    from app.models.user import UserRole

    shop_a = await _make_shop_with_variant(api_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(api_session, shop_name="Shop B", sku="B-1")
    customer_b = await _make_customer(api_session, shop_b.shop.id)
    await _seed(api_session, shop_b, "10", "800")
    sale_b = await create_sale(
        api_session,
        shop_id=shop_b.shop.id,
        customer_id=customer_b.id,
        items=[
            SaleItemInput(
                variant_id=shop_b.variant.id, quantity=Decimal("2"), unit_price=Decimal("1000")
            )
        ],
    )
    purchase_b = await create_purchase(
        api_session,
        shop_id=shop_b.shop.id,
        supplier_id=shop_b.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=shop_b.variant.id, quantity=Decimal("5"), unit_cost=Decimal("100")
            )
        ],
    )
    pi_b = (
        await api_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase_b.id)
        )
    ).scalar_one()
    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=shop_a.shop.id,
        name="Mock User",
        email="mock@example.com",
        role=UserRole.OWNER,
    )
    api_session.add(user)
    await api_session.flush()

    response = await mocked_api_client.post(
        f"/sales/{sale_b.id}/returns",
        headers=_headers(),
        json={"lines": [{"sale_item_id": str(sale_b.items[0].id), "quantity": "1"}]},
    )
    assert response.status_code == 404

    response = await mocked_api_client.post(
        f"/purchases/{purchase_b.id}/returns",
        headers=_headers(),
        json={"lines": [{"purchase_item_id": str(pi_b.id), "quantity": "1"}]},
    )
    assert response.status_code == 404

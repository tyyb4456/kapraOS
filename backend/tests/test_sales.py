"""Sales / POS domain tests: the Customer, Sale, SaleItem and Payment models,
the sales service's server-side totals, its weighted-average cost snapshot, its
tenant guards, and its atomic integration with the inventory ledger.
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
    Payment,
    PaymentMethod,
    Product,
    ProductType,
    ProductVariant,
    Sale,
    SaleItem,
    SaleStatus,
    Shop,
    Supplier,
    Unit,
)
from app.services import inventory as inventory_service
from app.services.inventory import InsufficientStockError
from app.services.purchases import PurchaseItemInput, create_purchase
from app.services.sales import (
    CustomerNotFoundError,
    EmptySaleError,
    InvalidSaleItemError,
    InvalidSaleTotalsError,
    PaymentInput,
    SaleItemInput,
    ShopNotFoundError,
    VariantNotFoundError,
    create_sale,
)

# A shop with one variant (plus a supplier so costed stock can be seeded via a
# real purchase) is the minimum most sale tests need.
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
    unit: Unit = Unit.METER,
    selling_price: str = "1000.00",
) -> ShopFixture:
    """Create shop -> category -> product -> variant, plus a supplier."""

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
        selling_price=Decimal(selling_price),
        unit=unit,
    )
    supplier = Supplier(shop_id=shop.id, name="Al-Madina Textile")
    db_session.add_all([variant, supplier])
    await db_session.flush()

    return ShopFixture(
        shop=shop,
        category=category,
        product=product,
        variant=variant,
        supplier=supplier,
    )


async def _make_customer(
    db_session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    name: str = "Ahmed",
    phone: str | None = "0300-1234567",
) -> Customer:
    customer = Customer(shop_id=shop_id, name=name, phone=phone)
    db_session.add(customer)
    await db_session.flush()
    return customer


async def _seed_stock(
    db_session: AsyncSession,
    fixture: ShopFixture,
    *,
    quantity: str,
    unit_cost: str,
) -> None:
    """Seed stock at a known cost (via a real purchase, so the average is set)."""

    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal(quantity),
                unit_cost=Decimal(unit_cost),
            )
        ],
    )


async def _inventory_quantity(
    db_session: AsyncSession, shop_id: uuid.UUID, variant_id: uuid.UUID
) -> Decimal:
    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=shop_id, variant_id=variant_id
    )
    return inventory.quantity


async def _weighted_average_cost(
    db_session: AsyncSession, shop_id: uuid.UUID, variant_id: uuid.UUID
) -> Decimal:
    return await inventory_service.get_weighted_average_cost(
        db_session, shop_id=shop_id, variant_id=variant_id
    )


async def _movements(
    db_session: AsyncSession, variant_id: uuid.UUID
) -> list[InventoryMovement]:
    result = await db_session.execute(
        sa.select(InventoryMovement)
        .where(InventoryMovement.variant_id == variant_id)
        .order_by(InventoryMovement.created_at)
    )
    return list(result.scalars())


async def _sale_items(db_session: AsyncSession, sale_id: uuid.UUID) -> list[SaleItem]:
    result = await db_session.execute(
        sa.select(SaleItem).where(SaleItem.sale_id == sale_id)
    )
    return list(result.scalars())


# --------------------------------------------------------------------------
# 1. Customer creation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_can_be_created_for_a_shop(db_session: AsyncSession) -> None:
    shop = Shop(name="Ahmed Fabrics")
    db_session.add(shop)
    await db_session.flush()

    customer = Customer(
        shop_id=shop.id,
        name="Ahmed",
        phone="0300-1234567",
        address="Lahore",
    )
    db_session.add(customer)
    await db_session.flush()
    await db_session.refresh(customer)

    assert customer.shop_id == shop.id
    assert customer.name == "Ahmed"
    assert customer.phone == "0300-1234567"
    assert customer.address == "Lahore"
    assert customer.notes is None


@pytest.mark.asyncio
async def test_customer_can_be_created_with_only_a_name(
    db_session: AsyncSession,
) -> None:
    shop = Shop(name="Walk-in Shop")
    db_session.add(shop)
    await db_session.flush()

    customer = Customer(shop_id=shop.id, name="Bilal")
    db_session.add(customer)
    await db_session.flush()

    assert customer.phone is None
    assert customer.address is None
    assert customer.notes is None


# --------------------------------------------------------------------------
# 2. Walk-in sale (no customer)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_walk_in_sale_succeeds_without_a_customer(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("2"),
                unit_price=Decimal("1000"),
            )
        ],
        payments=[
            PaymentInput(amount=Decimal("2000"), method=PaymentMethod.CASH)
        ],
    )

    assert sale.customer_id is None
    assert sale.subtotal == Decimal("2000.00")
    assert sale.paid_amount == Decimal("2000.00")
    assert sale.due_amount == Decimal("0.00")
    assert sale.status is SaleStatus.COMPLETED


# --------------------------------------------------------------------------
# 3. Sale with customer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_with_a_customer_links_them(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    customer = await _make_customer(db_session, shop_id=fixture.shop.id)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("3.5"),
                unit_price=Decimal("1000"),
            )
        ],
        payments=[
            PaymentInput(amount=Decimal("3500"), method=PaymentMethod.CASH)
        ],
    )

    assert sale.customer_id == customer.id
    await db_session.refresh(sale, attribute_names=["customer"])
    assert sale.customer is not None
    assert sale.customer.name == "Ahmed"

    payment = (
        await db_session.execute(
            sa.select(Payment).where(Payment.sale_id == sale.id)
        )
    ).scalar_one()
    assert payment.customer_id == customer.id


# --------------------------------------------------------------------------
# 4. One-item sale updates inventory
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_with_one_item_creates_sale_item_and_reduces_stock(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="20", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        invoice_number="INV-2026-00125",
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("3.500"),
                unit_price=Decimal("1000"),
            )
        ],
    )

    assert sale.invoice_number == "INV-2026-00125"
    assert sale.subtotal == Decimal("3500.00")
    assert sale.total == Decimal("3500.00")

    items = await _sale_items(db_session, sale.id)
    assert len(items) == 1
    assert items[0].variant_id == fixture.variant.id
    assert items[0].quantity == Decimal("3.500")
    assert items[0].unit_price == Decimal("1000.00")
    assert items[0].total == Decimal("3500.00")

    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("16.500")


# --------------------------------------------------------------------------
# 5. Sale creates SALE inventory movement
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_creates_a_sale_movement_referencing_the_sale(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="20", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("3.500"),
                unit_price=Decimal("1000"),
            )
        ],
    )

    movements = await _movements(db_session, fixture.variant.id)
    sale_movements = [
        m for m in movements if m.movement_type is InventoryMovementType.SALE
    ]
    assert len(sale_movements) == 1
    movement = sale_movements[0]
    assert movement.quantity == Decimal("-3.500")
    assert movement.reference_type == "SALE"
    assert movement.reference_id == sale.id
    assert movement.shop_id == fixture.shop.id
    assert movement.unit_cost == Decimal("800.00")


# --------------------------------------------------------------------------
# 6. Insufficient stock
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_fails_when_stock_is_insufficient(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="2", unit_cost="800")

    # A savepoint stands in for the surrounding request transaction so the
    # flushed-but-failed sale can be rolled back while the fixture rows stay.
    savepoint = await db_session.begin_nested()
    with pytest.raises(InsufficientStockError):
        await create_sale(
            db_session,
            shop_id=fixture.shop.id,
            items=[
                SaleItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("3"),
                    unit_price=Decimal("1000"),
                )
            ],
            payments=[
                PaymentInput(amount=Decimal("3000"), method=PaymentMethod.CASH)
            ],
        )
    await savepoint.rollback()

    # No sale, no items, no payment, stock unchanged, no SALE movement.
    result = await db_session.execute(
        sa.select(Sale).where(Sale.shop_id == fixture.shop.id)
    )
    assert result.scalars().all() == []
    result = await db_session.execute(sa.select(SaleItem))
    assert result.scalars().all() == []
    result = await db_session.execute(sa.select(Payment))
    assert result.scalars().all() == []

    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("2.000")

    movements = await _movements(db_session, fixture.variant.id)
    assert all(
        m.movement_type is not InventoryMovementType.SALE for m in movements
    )


# --------------------------------------------------------------------------
# 7. Weighted-average cost snapshot
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_item_captures_weighted_average_cost(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    # 10 @ 800 then 20 @ 900 -> (10*800 + 20*900) / 30 = 866.6666...
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")
    await _seed_stock(db_session, fixture, quantity="20", unit_cost="900")

    current = await _weighted_average_cost(
        db_session, fixture.shop.id, fixture.variant.id
    )
    assert current == Decimal("866.6667")  # 4 dp, half-up

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_price=Decimal("1200"),
            )
        ],
    )

    item = (await _sale_items(db_session, sale.id))[0]
    # Snapshot is the average rounded to money precision (2 dp, half-up).
    assert item.cost_price == Decimal("866.67")
    assert isinstance(item.cost_price, Decimal)

    # The sale movement also carries the captured cost.
    movement = [
        m
        for m in await _movements(db_session, fixture.variant.id)
        if m.movement_type is InventoryMovementType.SALE
    ][0]
    assert movement.unit_cost == Decimal("866.67")


# --------------------------------------------------------------------------
# 8. Historical cost snapshot does not change
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_historical_cost_price_is_not_rewritten_by_later_purchases(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")
    await _seed_stock(db_session, fixture, quantity="20", unit_cost="900")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_price=Decimal("1200"),
            )
        ],
    )
    item = (await _sale_items(db_session, sale.id))[0]
    original_cost = item.cost_price
    assert original_cost == Decimal("866.67")

    # A later, more expensive purchase moves the current average.
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="1500")
    new_average = await _weighted_average_cost(
        db_session, fixture.shop.id, fixture.variant.id
    )
    assert new_average != original_cost.quantize(Decimal("0.0001"))

    await db_session.refresh(item)
    assert item.cost_price == original_cost


@pytest.mark.asyncio
async def test_sale_item_keeps_its_own_unit_price_snapshot(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("1"),
                unit_price=Decimal("1000"),
            )
        ],
    )
    item = (await _sale_items(db_session, sale.id))[0]

    # Catalog price changes later; the historical sale must not.
    fixture.variant.selling_price = Decimal("2000.00")
    await db_session.flush()
    await db_session.refresh(item)
    assert item.unit_price == Decimal("1000.00")


# --------------------------------------------------------------------------
# 9. Sale total calculation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_totals_are_calculated_by_the_backend(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="20", unit_cost="800")

    suit = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="SUIT-003",
        purchase_price=Decimal("2000.00"),
        selling_price=Decimal("3500.00"),
        unit=Unit.SET,
    )
    db_session.add(suit)
    await db_session.flush()
    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=suit.id,
                quantity=Decimal("5"),
                unit_cost=Decimal("2000"),
            )
        ],
    )

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        discount=Decimal("500"),
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("3.5"),
                unit_price=Decimal("1000"),
            ),
            SaleItemInput(
                variant_id=suit.id,
                quantity=Decimal("2"),
                unit_price=Decimal("3500"),
            ),
        ],
        payments=[
            PaymentInput(amount=Decimal("7000"), method=PaymentMethod.CASH)
        ],
    )

    assert sale.subtotal == Decimal("10500.00")  # 3500 + 7000
    assert sale.discount == Decimal("500.00")
    assert sale.total == Decimal("10000.00")
    assert sale.paid_amount == Decimal("7000.00")
    assert sale.due_amount == Decimal("3000.00")
    assert sale.status is SaleStatus.PARTIAL


@pytest.mark.asyncio
async def test_header_discount_cannot_exceed_subtotal(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    with pytest.raises(InvalidSaleTotalsError):
        await create_sale(
            db_session,
            shop_id=fixture.shop.id,
            discount=Decimal("99999"),
            items=[
                SaleItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("1"),
                    unit_price=Decimal("100"),
                )
            ],
        )


@pytest.mark.asyncio
async def test_line_discount_is_applied(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("3.5"),
                unit_price=Decimal("1000"),
                discount=Decimal("200"),
            )
        ],
    )

    item = (await _sale_items(db_session, sale.id))[0]
    assert item.total == Decimal("3300.00")  # 3500 - 200
    assert sale.subtotal == Decimal("3300.00")


# --------------------------------------------------------------------------
# 10. Payment
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_payment_records_amounts_and_status(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="20", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("10"),
                unit_price=Decimal("1000"),
            )
        ],
        payments=[
            PaymentInput(amount=Decimal("7000"), method=PaymentMethod.CASH)
        ],
    )

    assert sale.total == Decimal("10000.00")
    assert sale.paid_amount == Decimal("7000.00")
    assert sale.due_amount == Decimal("3000.00")
    assert sale.status is SaleStatus.PARTIAL

    payment = (
        await db_session.execute(
            sa.select(Payment).where(Payment.sale_id == sale.id)
        )
    ).scalar_one()
    assert payment.amount == Decimal("7000.00")
    assert payment.method is PaymentMethod.CASH
    assert payment.shop_id == fixture.shop.id
    assert payment.purchase_id is None


# --------------------------------------------------------------------------
# 11. Multiple payments
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_can_have_multiple_payments(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="20", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("10"),
                unit_price=Decimal("1000"),
            )
        ],
        payments=[
            PaymentInput(amount=Decimal("5000"), method=PaymentMethod.CASH),
            PaymentInput(
                amount=Decimal("5000"),
                method=PaymentMethod.JAZZCASH,
                reference="JC-12345",
            ),
        ],
    )

    assert sale.paid_amount == Decimal("10000.00")
    assert sale.due_amount == Decimal("0.00")
    assert sale.status is SaleStatus.COMPLETED

    payments = list(
        (
            await db_session.execute(
                sa.select(Payment)
                .where(Payment.sale_id == sale.id)
                .order_by(Payment.amount.desc())
            )
        ).scalars()
    )
    methods = {p.method for p in payments}
    assert methods == {PaymentMethod.CASH, PaymentMethod.JAZZCASH}
    assert sum(p.amount for p in payments) == Decimal("10000.00")


# --------------------------------------------------------------------------
# 12. Payment cannot exceed total
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_cannot_exceed_the_sale_total(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="20", unit_cost="800")

    with pytest.raises(InvalidSaleTotalsError):
        await create_sale(
            db_session,
            shop_id=fixture.shop.id,
            items=[
                SaleItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("10"),
                    unit_price=Decimal("1000"),
                )
            ],
            payments=[
                PaymentInput(amount=Decimal("12000"), method=PaymentMethod.CASH)
            ],
        )

    # Nothing leaked through (failure happens before any write).
    result = await db_session.execute(
        sa.select(Sale).where(Sale.shop_id == fixture.shop.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_non_positive_payment_is_rejected(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    with pytest.raises(InvalidSaleTotalsError):
        await create_sale(
            db_session,
            shop_id=fixture.shop.id,
            items=[
                SaleItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000"),
                )
            ],
            payments=[
                PaymentInput(amount=Decimal("0"), method=PaymentMethod.CASH)
            ],
        )


# --------------------------------------------------------------------------
# 13. Decimal precision
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decimal_arithmetic_is_exact(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("3.500"),
                unit_price=Decimal("875.50"),
            )
        ],
    )

    # 3.500 x 875.50 = 3064.250 -> 3064.25 (2 dp), no float error.
    assert sale.subtotal == Decimal("3064.25")
    item = (await _sale_items(db_session, sale.id))[0]
    assert item.total == Decimal("3064.25")
    assert isinstance(item.total, Decimal)
    assert isinstance(item.quantity, Decimal)


@pytest.mark.asyncio
async def test_fractional_quantities_are_preserved(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="20", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("12.250"),
                unit_price=Decimal("100.00"),
            )
        ],
    )

    item = (await _sale_items(db_session, sale.id))[0]
    assert item.quantity == Decimal("12.250")
    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("7.750")


# --------------------------------------------------------------------------
# 14. Tenant isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_rejects_variant_from_another_shop(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop_with_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(db_session, shop_name="Shop B", sku="B-1")

    with pytest.raises(VariantNotFoundError):
        await create_sale(
            db_session,
            shop_id=shop_a.shop.id,
            items=[
                SaleItemInput(
                    variant_id=shop_b.variant.id,  # belongs to Shop B
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000"),
                )
            ],
        )

    result = await db_session.execute(
        sa.select(Sale).where(Sale.shop_id == shop_a.shop.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_sale_rejects_customer_from_another_shop(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop_with_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(db_session, shop_name="Shop B", sku="B-1")
    await _seed_stock(db_session, shop_a, quantity="10", unit_cost="800")
    customer_b = await _make_customer(db_session, shop_id=shop_b.shop.id)

    with pytest.raises(CustomerNotFoundError):
        await create_sale(
            db_session,
            shop_id=shop_a.shop.id,
            customer_id=customer_b.id,  # belongs to Shop B
            items=[
                SaleItemInput(
                    variant_id=shop_a.variant.id,
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000"),
                )
            ],
        )


@pytest.mark.asyncio
async def test_database_rejects_cross_shop_payment_reference(
    db_session: AsyncSession,
) -> None:
    """The composite FK is the database-level half of the payment guard."""

    shop_a = await _make_shop_with_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(db_session, shop_name="Shop B", sku="B-1")
    await _seed_stock(db_session, shop_b, quantity="10", unit_cost="800")

    sale_b = await create_sale(
        db_session,
        shop_id=shop_b.shop.id,
        items=[
            SaleItemInput(
                variant_id=shop_b.variant.id,
                quantity=Decimal("1"),
                unit_price=Decimal("1000"),
            )
        ],
    )

    db_session.add(
        Payment(
            shop_id=shop_a.shop.id,  # Shop A
            sale_id=sale_b.id,  # but Shop B's sale
            amount=Decimal("1000.00"),
            method=PaymentMethod.CASH,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_sale_rejects_unknown_shop(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    with pytest.raises(ShopNotFoundError):
        await create_sale(
            db_session,
            shop_id=uuid.uuid4(),
            items=[
                SaleItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000"),
                )
            ],
        )


# --------------------------------------------------------------------------
# 15. Atomic rollback
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_mid_transaction_rolls_back_everything(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If stock removal fails on a later line, the whole sale rolls back.

    The first line's SaleItem, stock deduction and SALE movement have already
    been written when the second `remove_stock` fails - recreating the real
    mid-transaction failure a database error would cause - and the savepoint
    rollback (standing in for `get_db`) must undo all of it.
    """

    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="20", unit_cost="800")

    suit = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="SUIT-003",
        purchase_price=Decimal("2000.00"),
        selling_price=Decimal("3500.00"),
        unit=Unit.SET,
    )
    db_session.add(suit)
    await db_session.flush()
    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=suit.id,
                quantity=Decimal("5"),
                unit_cost=Decimal("2000"),
            )
        ],
    )

    real_remove_stock = inventory_service.remove_stock
    call_count = {"n": 0}

    async def flaky_remove_stock(*args: object, **kwargs: object) -> object:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated inventory failure")
        return await real_remove_stock(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "app.services.sales.inventory_service.remove_stock", flaky_remove_stock
    )

    savepoint = await db_session.begin_nested()
    with pytest.raises(RuntimeError):
        await create_sale(
            db_session,
            shop_id=fixture.shop.id,
            items=[
                SaleItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("3"),
                    unit_price=Decimal("1000"),
                ),
                SaleItemInput(
                    variant_id=suit.id,
                    quantity=Decimal("2"),
                    unit_price=Decimal("3500"),
                ),
            ],
            payments=[
                PaymentInput(amount=Decimal("5000"), method=PaymentMethod.CASH)
            ],
        )
    await savepoint.rollback()

    result = await db_session.execute(
        sa.select(Sale).where(Sale.shop_id == fixture.shop.id)
    )
    assert result.scalars().all() == []
    result = await db_session.execute(sa.select(SaleItem))
    assert result.scalars().all() == []
    result = await db_session.execute(sa.select(Payment))
    assert result.scalars().all() == []

    for variant_id, expected in ((fixture.variant.id, "20.000"), (suit.id, "5.000")):
        assert await _inventory_quantity(
            db_session, fixture.shop.id, variant_id
        ) == Decimal(expected)
        movements = await _movements(db_session, variant_id)
        assert all(
            m.movement_type is not InventoryMovementType.SALE for m in movements
        )


# --------------------------------------------------------------------------
# 16. Concurrency
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_sales_cannot_oversell() -> None:
    """Two simultaneous sales of the last unit: only one may succeed.

    This is a real database-level concurrency test. It runs two independent
    sessions (each with its own asyncpg connection) that both try to sell 1
    unit of a variant with 1 in stock. Without `SELECT ... FOR UPDATE` in the
    inventory service both could read 1 and succeed, leaving stock at 0 while
    2 units were sold. With row locking, one transaction waits for the other
    and then correctly rejects the second sale, leaving no partial sale behind.
    """

    async with session_module.AsyncSessionLocal() as setup:
        shop = Shop(name="Concurrency Sales Shop")
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
            sku="CONCURRENCY-SALE-001",
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
            quantity=Decimal("1"),
            movement_type=InventoryMovementType.PURCHASE,
            unit_cost=Decimal("100.00"),
        )
        await setup.commit()
        shop_id, variant_id = shop.id, variant.id

    async def sell() -> bool:
        async with session_module.AsyncSessionLocal() as worker:
            try:
                await create_sale(
                    worker,
                    shop_id=shop_id,
                    items=[
                        SaleItemInput(
                            variant_id=variant_id,
                            quantity=Decimal("1"),
                            unit_price=Decimal("200"),
                        )
                    ],
                    payments=[
                        PaymentInput(
                            amount=Decimal("200"), method=PaymentMethod.CASH
                        )
                    ],
                )
                await worker.commit()
                return True
            except InsufficientStockError:
                await worker.rollback()
                return False

    try:
        results = await asyncio.gather(sell(), sell())
        assert results.count(True) == 1
        assert results.count(False) == 1

        async with session_module.AsyncSessionLocal() as check:
            inventory = await inventory_service.get_or_create_inventory(
                check, shop_id=shop_id, variant_id=variant_id
            )
            assert inventory.quantity == Decimal("0.000")

            sales = (
                await check.execute(
                    sa.select(Sale).where(Sale.shop_id == shop_id)
                )
            ).scalars().all()
            assert len(sales) == 1

            movements = await _movements(check, variant_id)
            sale_movements = [
                m
                for m in movements
                if m.movement_type is InventoryMovementType.SALE
            ]
            assert len(sale_movements) == 1
            assert sale_movements[0].quantity == Decimal("-1.000")
    finally:
        async with session_module.AsyncSessionLocal() as cleanup:
            shop_to_delete = await cleanup.get(Shop, shop_id)
            if shop_to_delete is not None:
                await cleanup.delete(shop_to_delete)
                await cleanup.commit()


# --------------------------------------------------------------------------
# Other behaviour: invoice, duplicates, empty sale, movement traceability
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoice_number_is_unique_per_shop_only(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop_with_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(db_session, shop_name="Shop B", sku="B-1")
    await _seed_stock(db_session, shop_a, quantity="10", unit_cost="800")
    await _seed_stock(db_session, shop_b, quantity="10", unit_cost="800")

    async def _sell(shop_fixture: ShopFixture) -> Sale:
        return await create_sale(
            db_session,
            shop_id=shop_fixture.shop.id,
            invoice_number="INV-100",
            items=[
                SaleItemInput(
                    variant_id=shop_fixture.variant.id,
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000"),
                )
            ],
        )

    await _sell(shop_a)
    # Same invoice number in another shop is fine.
    sale_b = await _sell(shop_b)
    assert sale_b.invoice_number == "INV-100"

    # A duplicate within the same shop is rejected by the partial unique index.
    with pytest.raises(sa.exc.IntegrityError):
        await _sell(shop_a)


@pytest.mark.asyncio
async def test_sale_without_invoice_number_can_repeat(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    for _ in range(2):
        sale = await create_sale(
            db_session,
            shop_id=fixture.shop.id,
            items=[
                SaleItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000"),
                )
            ],
        )
        assert sale.invoice_number is None


@pytest.mark.asyncio
async def test_empty_sale_is_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)

    with pytest.raises(EmptySaleError):
        await create_sale(db_session, shop_id=fixture.shop.id, items=[])


@pytest.mark.asyncio
async def test_duplicate_variant_lines_are_combined(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("2"),
                unit_price=Decimal("1000"),
            ),
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("1"),
                unit_price=Decimal("1000"),
            ),
        ],
    )

    items = await _sale_items(db_session, sale.id)
    assert len(items) == 1
    assert items[0].quantity == Decimal("3.000")
    assert sale.subtotal == Decimal("3000.00")
    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("7.000")


@pytest.mark.asyncio
async def test_non_positive_quantity_is_rejected(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    for bad_quantity in (Decimal("0"), Decimal("-5")):
        with pytest.raises(InvalidSaleItemError):
            await create_sale(
                db_session,
                shop_id=fixture.shop.id,
                items=[
                    SaleItemInput(
                        variant_id=fixture.variant.id,
                        quantity=bad_quantity,
                        unit_price=Decimal("1000"),
                    )
                ],
            )


@pytest.mark.asyncio
async def test_database_rejects_inconsistent_line_total(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    await _seed_stock(db_session, fixture, quantity="10", unit_cost="800")

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("1"),
                unit_price=Decimal("1000"),
            )
        ],
    )

    db_session.add(
        SaleItem(
            sale_id=sale.id,
            variant_id=fixture.variant.id,
            quantity=Decimal("2"),
            unit_price=Decimal("1000"),
            cost_price=Decimal("800"),
            total=Decimal("999.00"),  # should be 2000.00
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_database_rejects_inconsistent_header_total(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    db_session.add(
        Sale(
            shop_id=fixture.shop.id,
            subtotal=Decimal("100.00"),
            discount=Decimal("10.00"),
            total=Decimal("999.00"),  # should be 90.00
            status=SaleStatus.COMPLETED,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()
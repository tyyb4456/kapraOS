"""Supplier / Purchase domain tests: the Supplier, Purchase and PurchaseItem
models, the purchase service's server-side totals, its tenant guards, and -
most importantly - its atomic integration with the inventory ledger.
"""

import uuid
from decimal import Decimal
from typing import NamedTuple

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Category,
    InventoryMovement,
    InventoryMovementType,
    Product,
    ProductType,
    ProductVariant,
    Purchase,
    PurchaseItem,
    Shop,
    Supplier,
    Unit,
)
from app.services import inventory as inventory_service
from app.services.purchases import (
    DuplicatePurchaseItemError,
    InvalidPurchaseItemError,
    InvalidPurchaseTotalsError,
    PurchaseItemInput,
    SupplierNotFoundError,
    VariantNotFoundError,
    create_purchase,
)

# A shop with one variant (and a supplier) is the minimum every purchase test
# needs; this bundles the pieces those helpers build.
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
    supplier_name: str = "Al-Madina Textile",
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
        selling_price=Decimal("1200.00"),
        unit=unit,
    )
    supplier = Supplier(shop_id=shop.id, name=supplier_name)
    db_session.add_all([variant, supplier])
    await db_session.flush()

    return ShopFixture(
        shop=shop,
        category=category,
        product=product,
        variant=variant,
        supplier=supplier,
    )


async def _inventory_quantity(
    db_session: AsyncSession, shop_id: uuid.UUID, variant_id: uuid.UUID
) -> Decimal:
    inventory = await inventory_service.get_or_create_inventory(
        db_session, shop_id=shop_id, variant_id=variant_id
    )
    return inventory.quantity


async def _movements(
    db_session: AsyncSession, variant_id: uuid.UUID
) -> list[InventoryMovement]:
    result = await db_session.execute(
        sa.select(InventoryMovement)
        .where(InventoryMovement.variant_id == variant_id)
        .order_by(InventoryMovement.created_at)
    )
    return list(result.scalars())


# --------------------------------------------------------------------------
# 1. Supplier creation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_can_be_created_for_a_shop(db_session: AsyncSession) -> None:
    shop = Shop(name="Ahmed Fabrics")
    db_session.add(shop)
    await db_session.flush()

    supplier = Supplier(
        shop_id=shop.id,
        name="Al-Madina Textile",
        phone="0300-1234567",
    )
    db_session.add(supplier)
    await db_session.flush()
    await db_session.refresh(supplier)

    assert supplier.shop_id == shop.id
    assert supplier.name == "Al-Madina Textile"
    assert supplier.phone == "0300-1234567"
    assert supplier.address is None
    assert supplier.notes is None


@pytest.mark.asyncio
async def test_supplier_can_be_created_with_only_a_name(
    db_session: AsyncSession,
) -> None:
    shop = Shop(name="Local Wholesale")
    db_session.add(shop)
    await db_session.flush()

    supplier = Supplier(shop_id=shop.id, name="Local Wholesale Market Supplier")
    db_session.add(supplier)
    await db_session.flush()

    assert supplier.phone is None
    assert supplier.address is None


# --------------------------------------------------------------------------
# 2. Create purchase
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_with_one_item_is_created(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)

    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        invoice_number="INV-1042",
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("50"),
                unit_cost=Decimal("850"),
            )
        ],
    )

    assert purchase.id is not None
    assert purchase.supplier_id == fixture.supplier.id
    assert purchase.invoice_number == "INV-1042"
    assert purchase.subtotal == Decimal("42500.00")
    assert purchase.discount == Decimal("0.00")
    assert purchase.total == Decimal("42500.00")
    assert purchase.paid_amount == Decimal("0.00")
    assert purchase.due_amount == Decimal("42500.00")

    result = await db_session.execute(
        sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
    )
    items = list(result.scalars())
    assert len(items) == 1
    assert items[0].variant_id == fixture.variant.id
    assert items[0].quantity == Decimal("50.000")
    assert items[0].unit_cost == Decimal("850.00")
    assert items[0].total == Decimal("42500.00")


# --------------------------------------------------------------------------
# 3. Purchase increases inventory
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_increases_inventory_and_records_movement(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    # Seed an existing 10m so we prove the purchase *adds*, not replaces.
    await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=fixture.variant.id,
        quantity=Decimal("10"),
        movement_type=InventoryMovementType.ADJUSTMENT,
    )

    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("50"),
                unit_cost=Decimal("850"),
            )
        ],
    )

    quantity = await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    )
    assert quantity == Decimal("60.000")

    movements = await _movements(db_session, fixture.variant.id)
    purchase_movements = [
        m for m in movements if m.movement_type is InventoryMovementType.PURCHASE
    ]
    assert len(purchase_movements) == 1
    movement = purchase_movements[0]
    assert movement.quantity == Decimal("50.000")
    assert movement.unit_cost == Decimal("850.00")
    assert movement.reference_type == "PURCHASE"
    assert movement.reference_id == purchase.id


# --------------------------------------------------------------------------
# 4. Multiple purchase items
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_items_update_each_variant_and_subtotal(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    suit = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="LAWN-BLK-003",
        purchase_price=Decimal("2000.00"),
        selling_price=Decimal("3000.00"),
        unit=Unit.SET,
    )
    db_session.add(suit)
    await db_session.flush()

    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("50"),
                unit_cost=Decimal("850"),
            ),
            PurchaseItemInput(
                variant_id=suit.id,
                quantity=Decimal("20"),
                unit_cost=Decimal("2400"),
            ),
        ],
    )

    assert purchase.subtotal == Decimal("90500.00")
    assert purchase.total == Decimal("90500.00")

    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("50.000")
    assert await _inventory_quantity(db_session, fixture.shop.id, suit.id) == Decimal(
        "20.000"
    )


# --------------------------------------------------------------------------
# 5. Discount calculation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discount_is_applied_by_the_backend(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        discount=Decimal("5000"),
        paid_amount=Decimal("30000"),
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("125"),
                unit_cost=Decimal("800"),
            )
        ],
    )

    assert purchase.subtotal == Decimal("100000.00")
    assert purchase.discount == Decimal("5000.00")
    assert purchase.total == Decimal("95000.00")
    assert purchase.paid_amount == Decimal("30000.00")
    assert purchase.due_amount == Decimal("65000.00")


@pytest.mark.asyncio
async def test_discount_cannot_exceed_subtotal(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)

    with pytest.raises(InvalidPurchaseTotalsError):
        await create_purchase(
            db_session,
            shop_id=fixture.shop.id,
            supplier_id=fixture.supplier.id,
            discount=Decimal("2000"),
            items=[
                PurchaseItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("1"),
                    unit_cost=Decimal("100"),
                )
            ],
        )


@pytest.mark.asyncio
async def test_paid_amount_cannot_exceed_total(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)

    with pytest.raises(InvalidPurchaseTotalsError):
        await create_purchase(
            db_session,
            shop_id=fixture.shop.id,
            supplier_id=fixture.supplier.id,
            paid_amount=Decimal("500"),
            items=[
                PurchaseItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("1"),
                    unit_cost=Decimal("100"),
                )
            ],
        )


# --------------------------------------------------------------------------
# 6. Decimal precision
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decimal_arithmetic_is_exact(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)

    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("3.500"),
                unit_cost=Decimal("875.50"),
            )
        ],
    )

    # 3.500 x 875.50 = 3064.250 -> 3064.25 (2dp), no float error.
    assert purchase.subtotal == Decimal("3064.25")
    item = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    assert item.total == Decimal("3064.25")
    assert isinstance(item.total, Decimal)


@pytest.mark.asyncio
async def test_fractional_quantities_are_preserved(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)

    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("12.250"),
                unit_cost=Decimal("100.00"),
            )
        ],
    )

    item = (
        await db_session.execute(
            sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
        )
    ).scalar_one()
    assert item.quantity == Decimal("12.250")
    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("12.250")


# --------------------------------------------------------------------------
# 7. Optional invoice
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_without_invoice_number_succeeds(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_cost=Decimal("100"),
            )
        ],
    )

    assert purchase.invoice_number is None


@pytest.mark.asyncio
async def test_invoice_number_is_unique_per_shop_only(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop_with_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(db_session, shop_name="Shop B", sku="B-1")

    await create_purchase(
        db_session,
        shop_id=shop_a.shop.id,
        supplier_id=shop_a.supplier.id,
        invoice_number="INV-100",
        items=[
            PurchaseItemInput(
                variant_id=shop_a.variant.id,
                quantity=Decimal("1"),
                unit_cost=Decimal("10"),
            )
        ],
    )

    # Same invoice number in another shop is fine.
    purchase_b = await create_purchase(
        db_session,
        shop_id=shop_b.shop.id,
        supplier_id=shop_b.supplier.id,
        invoice_number="INV-100",
        items=[
            PurchaseItemInput(
                variant_id=shop_b.variant.id,
                quantity=Decimal("1"),
                unit_cost=Decimal("10"),
            )
        ],
    )
    assert purchase_b.invoice_number == "INV-100"

    # A duplicate within the same shop is rejected by the partial unique index.
    with pytest.raises(sa.exc.IntegrityError):
        await create_purchase(
            db_session,
            shop_id=shop_a.shop.id,
            supplier_id=shop_a.supplier.id,
            invoice_number="INV-100",
            items=[
                PurchaseItemInput(
                    variant_id=shop_a.variant.id,
                    quantity=Decimal("2"),
                    unit_cost=Decimal("10"),
                )
            ],
        )


@pytest.mark.asyncio
async def test_null_invoice_numbers_can_repeat(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    for _ in range(2):
        purchase = await create_purchase(
            db_session,
            shop_id=fixture.shop.id,
            supplier_id=fixture.supplier.id,
            items=[
                PurchaseItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("1"),
                    unit_cost=Decimal("10"),
                )
            ],
        )
        assert purchase.invoice_number is None


# --------------------------------------------------------------------------
# 8. Supplier isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_rejects_supplier_from_another_shop(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop_with_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(db_session, shop_name="Shop B", sku="B-1")

    with pytest.raises(SupplierNotFoundError):
        await create_purchase(
            db_session,
            shop_id=shop_a.shop.id,
            supplier_id=shop_b.supplier.id,  # belongs to Shop B
            items=[
                PurchaseItemInput(
                    variant_id=shop_a.variant.id,
                    quantity=Decimal("1"),
                    unit_cost=Decimal("10"),
                )
            ],
        )


@pytest.mark.asyncio
async def test_database_rejects_cross_shop_supplier_reference(
    db_session: AsyncSession,
) -> None:
    """The composite FK is the database-level half of the supplier guard."""

    shop_a = await _make_shop_with_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(db_session, shop_name="Shop B", sku="B-1")

    db_session.add(
        Purchase(
            shop_id=shop_a.shop.id,  # Shop A
            supplier_id=shop_b.supplier.id,  # but Shop B's supplier
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


# --------------------------------------------------------------------------
# 9. Variant isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_rejects_variant_from_another_shop(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop_with_variant(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop_with_variant(db_session, shop_name="Shop B", sku="B-1")

    with pytest.raises(VariantNotFoundError):
        await create_purchase(
            db_session,
            shop_id=shop_a.shop.id,
            supplier_id=shop_a.supplier.id,
            items=[
                PurchaseItemInput(
                    variant_id=shop_b.variant.id,  # belongs to Shop B
                    quantity=Decimal("1"),
                    unit_cost=Decimal("10"),
                )
            ],
        )

    # No purchase leaked through.
    result = await db_session.execute(
        sa.select(Purchase).where(Purchase.shop_id == shop_a.shop.id)
    )
    assert result.scalars().all() == []


# --------------------------------------------------------------------------
# 10. Invalid quantity
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_positive_quantity_is_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)

    for bad_quantity in (Decimal("0"), Decimal("-5")):
        with pytest.raises(InvalidPurchaseItemError):
            await create_purchase(
                db_session,
                shop_id=fixture.shop.id,
                supplier_id=fixture.supplier.id,
                items=[
                    PurchaseItemInput(
                        variant_id=fixture.variant.id,
                        quantity=bad_quantity,
                        unit_cost=Decimal("100"),
                    )
                ],
            )


# --------------------------------------------------------------------------
# 11. Invalid unit cost
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_negative_unit_cost_is_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop_with_variant(db_session)

    with pytest.raises(InvalidPurchaseItemError):
        await create_purchase(
            db_session,
            shop_id=fixture.shop.id,
            supplier_id=fixture.supplier.id,
            items=[
                PurchaseItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("1"),
                    unit_cost=Decimal("-100"),
                )
            ],
        )


# --------------------------------------------------------------------------
# 12. Atomic rollback
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_item_leaves_nothing_behind(db_session: AsyncSession) -> None:
    """A bad line must not create a purchase, items, stock or movements."""

    fixture = await _make_shop_with_variant(db_session)

    with pytest.raises(InvalidPurchaseTotalsError):
        await create_purchase(
            db_session,
            shop_id=fixture.shop.id,
            supplier_id=fixture.supplier.id,
            discount=Decimal("999999"),  # > subtotal, fails after items build
            items=[
                PurchaseItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("50"),
                    unit_cost=Decimal("850"),
                )
            ],
        )

    result = await db_session.execute(
        sa.select(Purchase).where(Purchase.shop_id == fixture.shop.id)
    )
    assert result.scalars().all() == []

    result = await db_session.execute(
        sa.select(PurchaseItem).where(PurchaseItem.variant_id == fixture.variant.id)
    )
    assert result.scalars().all() == []

    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("0.000")
    assert await _movements(db_session, fixture.variant.id) == []


@pytest.mark.asyncio
async def test_failure_mid_transaction_rolls_back_everything(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If stock addition fails on a later line, the whole purchase rolls back.

    `_combine_items` rejects bad input *before* any write, so to exercise the
    real mid-transaction path we make the inventory service fail on the second
    `add_stock` call - the same way a real database error would - and assert
    that the already-written Purchase, its first PurchaseItem, the first
    variant's stock increase and both movements are all undone.
    """

    fixture = await _make_shop_with_variant(db_session)
    suit = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="LAWN-BLK-003",
        purchase_price=Decimal("2000.00"),
        selling_price=Decimal("3000.00"),
        unit=Unit.SET,
    )
    db_session.add(suit)
    await db_session.flush()

    real_add_stock = inventory_service.add_stock
    call_count = {"n": 0}

    async def flaky_add_stock(*args: object, **kwargs: object) -> object:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated inventory failure")
        return await real_add_stock(*args, **kwargs)  # type: ignore[arg-type]

    # The purchases service imported the module, so patching the module
    # attribute is enough to intercept its call.
    monkeypatch.setattr(
        "app.services.purchases.inventory_service.add_stock", flaky_add_stock
    )

    # A savepoint stands in for the surrounding request transaction: the test
    # fixture rows (shop/variants) were only flushed, not committed, so a full
    # session rollback would erase them too. Rolling back to this savepoint
    # reproduces exactly what `get_db` does to the purchase transaction.
    savepoint = await db_session.begin_nested()
    with pytest.raises(RuntimeError):
        await create_purchase(
            db_session,
            shop_id=fixture.shop.id,
            supplier_id=fixture.supplier.id,
            items=[
                PurchaseItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("50"),
                    unit_cost=Decimal("850"),
                ),
                PurchaseItemInput(
                    variant_id=suit.id,
                    quantity=Decimal("20"),
                    unit_cost=Decimal("2400"),
                ),
            ],
        )
    await savepoint.rollback()

    result = await db_session.execute(
        sa.select(Purchase).where(Purchase.shop_id == fixture.shop.id)
    )
    assert result.scalars().all() == []

    result = await db_session.execute(sa.select(PurchaseItem))
    assert result.scalars().all() == []

    for variant_id in (fixture.variant.id, suit.id):
        assert await _inventory_quantity(
            db_session, fixture.shop.id, variant_id
        ) == Decimal("0.000")
        assert await _movements(db_session, variant_id) == []


# --------------------------------------------------------------------------
# 13. Duplicate variant in one purchase
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_variant_lines_are_combined(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("10"),
                unit_cost=Decimal("850"),
            ),
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("5"),
                unit_cost=Decimal("850"),
            ),
        ],
    )

    result = await db_session.execute(
        sa.select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)
    )
    items = list(result.scalars())
    assert len(items) == 1
    assert items[0].quantity == Decimal("15.000")
    assert items[0].total == Decimal("12750.00")
    assert purchase.subtotal == Decimal("12750.00")

    assert await _inventory_quantity(
        db_session, fixture.shop.id, fixture.variant.id
    ) == Decimal("15.000")

    movements = await _movements(db_session, fixture.variant.id)
    assert len(movements) == 1
    assert movements[0].quantity == Decimal("15.000")


@pytest.mark.asyncio
async def test_same_variant_at_different_costs_is_rejected(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    with pytest.raises(DuplicatePurchaseItemError):
        await create_purchase(
            db_session,
            shop_id=fixture.shop.id,
            supplier_id=fixture.supplier.id,
            items=[
                PurchaseItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("10"),
                    unit_cost=Decimal("800"),
                ),
                PurchaseItemInput(
                    variant_id=fixture.variant.id,
                    quantity=Decimal("5"),
                    unit_cost=Decimal("900"),
                ),
            ],
        )


# --------------------------------------------------------------------------
# Historical cost / totals integrity
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_historical_costs_are_independent_per_purchase(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    first = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("10"),
                unit_cost=Decimal("800"),
            )
        ],
    )
    second = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("10"),
                unit_cost=Decimal("900"),
            )
        ],
    )

    result = await db_session.execute(
        sa.select(PurchaseItem)
        .where(PurchaseItem.purchase_id.in_([first.id, second.id]))
        .order_by(PurchaseItem.unit_cost)
    )
    costs = [item.unit_cost for item in result.scalars()]
    assert costs == [Decimal("800.00"), Decimal("900.00")]


@pytest.mark.asyncio
async def test_database_rejects_inconsistent_line_total(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("1"),
                unit_cost=Decimal("100"),
            )
        ],
    )

    db_session.add(
        PurchaseItem(
            purchase_id=purchase.id,
            variant_id=fixture.variant.id,
            quantity=Decimal("2"),
            unit_cost=Decimal("100"),
            total=Decimal("999.00"),  # should be 200.00
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
        Purchase(
            shop_id=fixture.shop.id,
            supplier_id=fixture.supplier.id,
            subtotal=Decimal("100.00"),
            discount=Decimal("10.00"),
            total=Decimal("999.00"),  # should be 90.00
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_purchase_movement_is_traceable_to_the_purchase(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop_with_variant(db_session)

    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("7"),
                unit_cost=Decimal("123.45"),
            )
        ],
    )

    movement = (
        await db_session.execute(
            sa.select(InventoryMovement).where(
                InventoryMovement.reference_type == "PURCHASE",
                InventoryMovement.reference_id == purchase.id,
            )
        )
    ).scalar_one()
    assert movement.variant_id == fixture.variant.id
    assert movement.shop_id == fixture.shop.id
    assert movement.quantity == Decimal("7.000")
    assert movement.unit_cost == Decimal("123.45")
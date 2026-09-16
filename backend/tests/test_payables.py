"""Supplier Khata / payables tests.

Covers the payable balance aggregate, the statement read model (ordering,
running balances, date filtering, pagination), the settlement operation and -
most importantly - that a balance can only ever be built from one shop's and
one supplier's own `Purchase` / `Payment` rows.

A note on timestamps: `created_at` comes from `now()`, which in Postgres is the
*transaction* timestamp, so every row a test writes inside `db_session` shares
it. Tests that assert on ordering therefore stamp explicit dates with
`_stamp()`, the way real rows written on different days would look.
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
    Category,
    Payment,
    PaymentMethod,
    Product,
    ProductType,
    ProductVariant,
    Purchase,
    Shop,
    Supplier,
    Unit,
)
from app.services.payables import (
    InvalidPaymentAmountError,
    InvalidPaymentMethodError,
    InvalidStatementRangeError,
    PaymentExceedsOutstandingError,
    PaymentExceedsPurchaseDueError,
    PurchaseNotFoundError,
    PurchaseSupplierMismatchError,
    StatementEntryType,
    SupplierNotFoundError,
    get_supplier_balance,
    get_supplier_statement,
    get_supplier_summary,
    record_supplier_payment,
)
from app.services.purchases import PurchaseItemInput, create_purchase

BASE_DATE = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _day(day: int) -> datetime:
    """A fixed September 2026 timestamp, for deterministic ordering tests."""

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
        purchase_price=Decimal("100.00"),
        selling_price=Decimal("1000.00"),
        unit=Unit.METER,
    )
    supplier = Supplier(shop_id=shop.id, name=supplier_name)
    db_session.add_all([variant, supplier])
    await db_session.flush()

    return ShopFixture(shop=shop, product=product, variant=variant, supplier=supplier)


async def _make_supplier(
    db_session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    name: str = "Second Supplier",
) -> Supplier:
    supplier = Supplier(shop_id=shop_id, name=name)
    db_session.add(supplier)
    await db_session.flush()
    return supplier


async def _stamp(db_session: AsyncSession, when: datetime, *rows: object) -> None:
    """Backdate rows so ordering/date-filter assertions are meaningful."""

    for row in rows:
        row.created_at = when  # type: ignore[attr-defined]
    await db_session.flush()


async def _buy(
    db_session: AsyncSession,
    fixture: ShopFixture,
    supplier: Supplier,
    *,
    amount: str,
    invoice: str | None = None,
    paid: str | None = None,
    at: datetime | None = None,
) -> Purchase:
    """Buy one metre at `amount` cost, so the purchase total is exactly `amount`."""

    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=supplier.id,
        invoice_number=invoice,
        paid_amount=Decimal(paid) if paid is not None else Decimal("0"),
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("1"),
                unit_cost=Decimal(amount),
            )
        ],
    )
    if at is not None:
        await _stamp(db_session, at, purchase)
    return purchase


async def _pay(
    db_session: AsyncSession,
    fixture: ShopFixture,
    supplier: Supplier,
    amount: str,
    *,
    purchase_id: uuid.UUID | None = None,
    reference: str | None = None,
    at: datetime | None = None,
) -> Payment:
    payment = await record_supplier_payment(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=supplier.id,
        amount=Decimal(amount),
        method=PaymentMethod.CASH,
        purchase_id=purchase_id,
        reference=reference,
    )
    if at is not None:
        await _stamp(db_session, at, payment)
    return payment


async def _example_khata(
    db_session: AsyncSession,
) -> tuple[ShopFixture, Supplier]:
    """The worked example from the spec:

        Sep 01  Purchase  INV-1001   +50,000  ->  50,000
        Sep 02  Payment   CASH-001   -20,000  ->  30,000
        Sep 05  Purchase  INV-1008   +10,000  ->  40,000
        Sep 07  Payment   CASH-002   -10,000  ->  30,000
    """

    fixture = await _make_shop(db_session)
    supplier = fixture.supplier

    await _buy(db_session, fixture, supplier, amount="50000", invoice="INV-1001", at=_day(1))
    await _pay(db_session, fixture, supplier, "20000", reference="CASH-001", at=_day(2))
    await _buy(db_session, fixture, supplier, amount="10000", invoice="INV-1008", at=_day(5))
    await _pay(db_session, fixture, supplier, "10000", reference="CASH-002", at=_day(7))

    return fixture, supplier


async def _payment_count(
    db_session: AsyncSession, supplier_id: uuid.UUID
) -> int:
    result = await db_session.execute(
        sa.select(sa.func.count())
        .select_from(Payment)
        .where(Payment.supplier_id == supplier_id)
    )
    return int(result.scalar_one())


# --------------------------------------------------------------------------
# 1. Supplier with no transactions
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_with_no_transactions_owes_nothing(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=fixture.supplier.id
    )

    assert balance.total_purchases == Decimal("0.00")
    assert balance.total_payments == Decimal("0.00")
    assert balance.outstanding_balance == Decimal("0.00")
    assert balance.number_of_purchases == 0
    assert balance.number_of_payments == 0
    assert balance.last_purchase_at is None
    assert balance.last_payment_at is None


# --------------------------------------------------------------------------
# 2. Unpaid purchase
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpaid_purchase_is_fully_outstanding(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _buy(db_session, fixture, fixture.supplier, amount="10000")

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=fixture.supplier.id
    )

    assert balance.total_purchases == Decimal("10000.00")
    assert balance.total_payments == Decimal("0.00")
    assert balance.outstanding_balance == Decimal("10000.00")


# --------------------------------------------------------------------------
# 3. Partially paid purchase (paid at purchase time)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_time_paid_amount_does_not_count_as_a_payment(
    db_session: AsyncSession,
) -> None:
    """`Purchase.paid_amount` is a cache, not a `Payment` row.

    Step 7's balance is derived from `Purchase` + `Payment`, so a purchase
    created with an up-front `paid_amount` still shows its full total as
    payable until a real supplier `Payment` row is recorded.
    """

    fixture = await _make_shop(db_session)
    purchase = await _buy(
        db_session, fixture, fixture.supplier, amount="10000", paid="7000"
    )

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=fixture.supplier.id
    )

    assert purchase.paid_amount == Decimal("7000.00")
    assert balance.total_purchases == Decimal("10000.00")
    assert balance.total_payments == Decimal("0.00")
    assert balance.outstanding_balance == Decimal("10000.00")


# --------------------------------------------------------------------------
# 4. Multiple purchases and payments
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_purchases_and_payments_aggregate(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    supplier = fixture.supplier

    await _buy(db_session, fixture, supplier, amount="10000")
    await _buy(db_session, fixture, supplier, amount="5000")
    await _pay(db_session, fixture, supplier, "4000")
    await _pay(db_session, fixture, supplier, "3000")

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )

    assert balance.total_purchases == Decimal("15000.00")
    assert balance.total_payments == Decimal("7000.00")
    assert balance.outstanding_balance == Decimal("8000.00")
    assert balance.number_of_purchases == 2
    assert balance.number_of_payments == 2


@pytest.mark.asyncio
async def test_balance_reports_counts_and_last_activity(
    db_session: AsyncSession,
) -> None:
    fixture, supplier = await _example_khata(db_session)

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )

    assert balance.number_of_purchases == 2
    assert balance.number_of_payments == 2
    assert balance.last_purchase_at == _day(5)
    assert balance.last_payment_at == _day(7)


# --------------------------------------------------------------------------
# 5. Supplier statement
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_statement_is_chronological_with_running_balances(
    db_session: AsyncSession,
) -> None:
    fixture, supplier = await _example_khata(db_session)

    statement = await get_supplier_statement(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )

    assert statement.total_entries == 4
    assert statement.opening_balance == Decimal("0.00")
    assert [entry.entry_type for entry in statement.entries] == [
        StatementEntryType.PURCHASE,
        StatementEntryType.PAYMENT,
        StatementEntryType.PURCHASE,
        StatementEntryType.PAYMENT,
    ]
    assert [entry.running_balance for entry in statement.entries] == [
        Decimal("50000.00"),
        Decimal("30000.00"),
        Decimal("40000.00"),
        Decimal("30000.00"),
    ]
    assert [entry.date for entry in statement.entries] == [
        _day(1),
        _day(2),
        _day(5),
        _day(7),
    ]
    assert statement.closing_balance == Decimal("30000.00")

    first_purchase, first_payment = statement.entries[0], statement.entries[1]
    assert first_purchase.debit == Decimal("50000.00")
    assert first_purchase.credit == Decimal("0.00")
    assert first_purchase.reference == "INV-1001"
    assert first_purchase.invoice_number == "INV-1001"
    assert first_purchase.payment_id is None
    assert first_purchase.purchase_id is not None

    assert first_payment.credit == Decimal("20000.00")
    assert first_payment.debit == Decimal("0.00")
    assert first_payment.reference == "CASH-001"
    assert first_payment.payment_method is PaymentMethod.CASH
    assert first_payment.payment_id is not None


@pytest.mark.asyncio
async def test_statement_lists_a_purchase_before_the_payment_at_the_same_instant(
    db_session: AsyncSession,
) -> None:
    """A purchase and its same-transaction payment share a timestamp exactly.

    The purchase must still be listed first, otherwise the running balance
    would dip negative before the purchase that justifies it appears.
    """

    fixture = await _make_shop(db_session)
    supplier = fixture.supplier
    await _buy(db_session, fixture, supplier, amount="10000")
    await _pay(db_session, fixture, supplier, "7000")

    statement = await get_supplier_statement(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )

    assert [entry.entry_type for entry in statement.entries] == [
        StatementEntryType.PURCHASE,
        StatementEntryType.PAYMENT,
    ]
    assert statement.entries[0].date == statement.entries[1].date
    assert [entry.running_balance for entry in statement.entries] == [
        Decimal("10000.00"),
        Decimal("3000.00"),
    ]


# --------------------------------------------------------------------------
# 6. Another supplier's purchase never belongs to this Khata
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_another_suppliers_purchase_is_not_in_this_khata(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    other = await _make_supplier(db_session, fixture.shop.id, name="Other Supplier")

    await _buy(db_session, fixture, other, amount="4000")
    await _buy(db_session, fixture, fixture.supplier, amount="1000")

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=fixture.supplier.id
    )
    statement = await get_supplier_statement(
        db_session, shop_id=fixture.shop.id, supplier_id=fixture.supplier.id
    )

    assert balance.total_purchases == Decimal("1000.00")
    assert balance.outstanding_balance == Decimal("1000.00")
    assert statement.total_entries == 1


# --------------------------------------------------------------------------
# 7. A payment cannot cross from one supplier to another
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settlement_rejects_a_purchase_belonging_to_another_supplier(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    other = await _make_supplier(db_session, fixture.shop.id, name="Other Supplier")

    await _buy(db_session, fixture, fixture.supplier, amount="10000")
    other_purchase = await _buy(db_session, fixture, other, amount="5000")

    with pytest.raises(PurchaseSupplierMismatchError):
        await _pay(
            db_session,
            fixture,
            fixture.supplier,
            "1000",
            purchase_id=other_purchase.id,
        )

    assert await _payment_count(db_session, fixture.supplier.id) == 0


@pytest.mark.asyncio
async def test_database_rejects_a_payment_against_another_suppliers_purchase(
    db_session: AsyncSession,
) -> None:
    """The composite FK is the database-level half of the Khata guard."""

    fixture = await _make_shop(db_session)
    other = await _make_supplier(db_session, fixture.shop.id, name="Other Supplier")
    other_purchase = await _buy(db_session, fixture, other, amount="5000")

    db_session.add(
        Payment(
            shop_id=fixture.shop.id,
            supplier_id=fixture.supplier.id,  # Supplier A...
            purchase_id=other_purchase.id,  # ...but Supplier B's purchase
            amount=Decimal("1000.00"),
            method=PaymentMethod.CASH,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


# --------------------------------------------------------------------------
# 8/9. Tenant isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_from_another_shop_is_not_found(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")

    for call in (get_supplier_balance, get_supplier_summary, get_supplier_statement):
        with pytest.raises(SupplierNotFoundError):
            await call(
                db_session, shop_id=shop_a.shop.id, supplier_id=shop_b.supplier.id
            )

    with pytest.raises(SupplierNotFoundError):
        await record_supplier_payment(
            db_session,
            shop_id=shop_a.shop.id,
            supplier_id=shop_b.supplier.id,
            amount=Decimal("100"),
            method=PaymentMethod.CASH,
        )


@pytest.mark.asyncio
async def test_another_shop_cannot_pay_into_this_suppliers_khata(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    await _buy(db_session, shop_a, shop_a.supplier, amount="10000")

    balance = await get_supplier_balance(
        db_session, shop_id=shop_a.shop.id, supplier_id=shop_a.supplier.id
    )
    assert balance.outstanding_balance == Decimal("10000.00")

    # Shop B cannot even persist a payment naming Shop A's supplier.
    db_session.add(
        Payment(
            shop_id=shop_b.shop.id,
            supplier_id=shop_a.supplier.id,
            amount=Decimal("10000.00"),
            method=PaymentMethod.CASH,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_same_supplier_name_in_two_shops_stays_isolated(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(
        db_session, shop_name="Shop A", sku="A-1", supplier_name="Al-Madina"
    )
    shop_b = await _make_shop(
        db_session, shop_name="Shop B", sku="B-1", supplier_name="Al-Madina"
    )

    await _buy(db_session, shop_a, shop_a.supplier, amount="10000", paid="0")
    await _buy(db_session, shop_b, shop_b.supplier, amount="2000")

    balance_a = await get_supplier_balance(
        db_session, shop_id=shop_a.shop.id, supplier_id=shop_a.supplier.id
    )
    balance_b = await get_supplier_balance(
        db_session, shop_id=shop_b.shop.id, supplier_id=shop_b.supplier.id
    )

    assert balance_a.total_purchases == Decimal("10000.00")
    assert balance_a.outstanding_balance == Decimal("10000.00")
    assert balance_b.total_purchases == Decimal("2000.00")
    assert balance_b.outstanding_balance == Decimal("2000.00")


# --------------------------------------------------------------------------
# 10. Unallocated supplier payment
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unallocated_payment_reduces_the_balance_without_touching_purchases(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    supplier = fixture.supplier
    purchase = await _buy(db_session, fixture, supplier, amount="10000")

    payment = await _pay(db_session, fixture, supplier, "5000")

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )
    await db_session.refresh(purchase)

    assert payment.purchase_id is None
    assert balance.outstanding_balance == Decimal("5000.00")
    # No purchase-level allocation was invented for it.
    assert purchase.paid_amount == Decimal("0.00")
    assert purchase.due_amount == Decimal("10000.00")

    statement = await get_supplier_statement(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )
    unallocated = statement.entries[-1]
    assert unallocated.entry_type is StatementEntryType.PAYMENT
    assert unallocated.purchase_id is None
    assert unallocated.credit == Decimal("5000.00")


# --------------------------------------------------------------------------
# 11. Settlement
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settlement_creates_exactly_one_payment_row(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    supplier = fixture.supplier
    await _buy(db_session, fixture, supplier, amount="10000")

    payment = await _pay(db_session, fixture, supplier, "4000", reference="CASH-77")

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )

    assert balance.outstanding_balance == Decimal("6000.00")
    assert await _payment_count(db_session, supplier.id) == 1
    assert payment.amount == Decimal("4000.00")
    assert payment.method is PaymentMethod.CASH
    assert payment.reference == "CASH-77"
    assert payment.shop_id == fixture.shop.id
    assert payment.purchase_id is None
    assert payment.customer_id is None
    assert payment.sale_id is None


@pytest.mark.asyncio
async def test_settlement_allocated_to_a_purchase_updates_that_purchase(
    db_session: AsyncSession,
) -> None:
    """Allocating keeps Step 5's `Purchase.paid_amount` cache truthful."""

    fixture = await _make_shop(db_session)
    supplier = fixture.supplier
    purchase = await _buy(db_session, fixture, supplier, amount="10000")

    await _pay(db_session, fixture, supplier, "4000", purchase_id=purchase.id)
    await db_session.refresh(purchase)
    assert purchase.paid_amount == Decimal("4000.00")
    assert purchase.due_amount == Decimal("6000.00")

    await _pay(db_session, fixture, supplier, "6000", purchase_id=purchase.id)
    await db_session.refresh(purchase)
    assert purchase.paid_amount == Decimal("10000.00")
    assert purchase.due_amount == Decimal("0.00")

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )
    assert balance.outstanding_balance == Decimal("0.00")


# --------------------------------------------------------------------------
# 12/13. Settlement validation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settlement_cannot_exceed_the_outstanding_balance(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    supplier = fixture.supplier
    await _buy(db_session, fixture, supplier, amount="10000")

    with pytest.raises(PaymentExceedsOutstandingError):
        await _pay(db_session, fixture, supplier, "12000")

    assert await _payment_count(db_session, supplier.id) == 0


@pytest.mark.asyncio
async def test_settlement_cannot_exceed_the_named_purchases_due(
    db_session: AsyncSession,
) -> None:
    """Enough is owed overall, but not on the purchase being settled."""

    fixture = await _make_shop(db_session)
    supplier = fixture.supplier
    first = await _buy(db_session, fixture, supplier, amount="10000")
    await _buy(db_session, fixture, supplier, amount="10000")

    with pytest.raises(PaymentExceedsPurchaseDueError):
        await _pay(db_session, fixture, supplier, "15000", purchase_id=first.id)

    assert await _payment_count(db_session, supplier.id) == 0


@pytest.mark.asyncio
async def test_zero_and_negative_settlements_are_rejected(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    supplier = fixture.supplier
    await _buy(db_session, fixture, supplier, amount="10000")

    for bad_amount in ("0", "-100"):
        with pytest.raises(InvalidPaymentAmountError):
            await _pay(db_session, fixture, supplier, bad_amount)

    assert await _payment_count(db_session, supplier.id) == 0


@pytest.mark.asyncio
async def test_unknown_payment_method_is_rejected(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    supplier = fixture.supplier
    await _buy(db_session, fixture, supplier, amount="10000")

    with pytest.raises(InvalidPaymentMethodError):
        await record_supplier_payment(
            db_session,
            shop_id=fixture.shop.id,
            supplier_id=supplier.id,
            amount=Decimal("100"),
            method="cheque",  # type: ignore[arg-type]
        )

    assert await _payment_count(db_session, supplier.id) == 0


@pytest.mark.asyncio
async def test_settlement_against_another_shops_purchase_is_not_found(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    purchase_b = await _buy(db_session, shop_b, shop_b.supplier, amount="5000")
    await _buy(db_session, shop_a, shop_a.supplier, amount="10000")

    with pytest.raises(PurchaseNotFoundError):
        await _pay(
            db_session,
            shop_a,
            shop_a.supplier,
            "1000",
            purchase_id=purchase_b.id,
        )

    assert await _payment_count(db_session, shop_a.supplier.id) == 0


# --------------------------------------------------------------------------
# 14. Decimal correctness
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decimal_arithmetic_is_exact(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    supplier = fixture.supplier
    await _buy(db_session, fixture, supplier, amount="10500.50")
    await _pay(db_session, fixture, supplier, "7250.25")

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )

    assert balance.total_purchases == Decimal("10500.50")
    assert balance.total_payments == Decimal("7250.25")
    assert balance.outstanding_balance == Decimal("3250.25")
    assert isinstance(balance.outstanding_balance, Decimal)

    statement = await get_supplier_statement(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )
    assert statement.closing_balance == Decimal("3250.25")


# --------------------------------------------------------------------------
# 15. History stays correct as new business arrives
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_stays_correct_as_new_purchases_arrive(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    supplier = fixture.supplier

    await _buy(db_session, fixture, supplier, amount="10000", at=_day(1))
    await _pay(db_session, fixture, supplier, "4000", at=_day(2))
    await _buy(db_session, fixture, supplier, amount="5000", at=_day(9))

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )
    statement = await get_supplier_statement(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )

    assert balance.outstanding_balance == Decimal("11000.00")
    assert [entry.running_balance for entry in statement.entries] == [
        Decimal("10000.00"),
        Decimal("6000.00"),
        Decimal("11000.00"),
    ]


# --------------------------------------------------------------------------
# 16. Negative balances are surfaced
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_negative_balance_is_surfaced_not_hidden(
    db_session: AsyncSession,
) -> None:
    """The settlement operation refuses overpayments, but if historical rows
    produce a negative balance the caller must be able to see it."""

    fixture = await _make_shop(db_session)
    supplier = fixture.supplier
    await _buy(db_session, fixture, supplier, amount="1000")

    db_session.add(
        Payment(
            shop_id=fixture.shop.id,
            supplier_id=supplier.id,
            amount=Decimal("3000.00"),
            method=PaymentMethod.CASH,
        )
    )
    await db_session.flush()

    balance = await get_supplier_balance(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )

    assert balance.outstanding_balance == Decimal("-2000.00")


# --------------------------------------------------------------------------
# 17. Date filtering and pagination
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_statement_can_be_filtered_by_date(
    db_session: AsyncSession,
) -> None:
    fixture, supplier = await _example_khata(db_session)

    statement = await get_supplier_statement(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=supplier.id,
        start_date=_day(3),
        end_date=_day(6),
    )

    assert statement.total_entries == 1
    # Everything before the window is carried in as the opening balance.
    assert statement.opening_balance == Decimal("30000.00")
    assert [entry.invoice_number for entry in statement.entries] == ["INV-1008"]
    assert statement.entries[0].running_balance == Decimal("40000.00")
    assert statement.closing_balance == Decimal("40000.00")


@pytest.mark.asyncio
async def test_statement_range_must_not_be_inverted(
    db_session: AsyncSession,
) -> None:
    fixture, supplier = await _example_khata(db_session)

    with pytest.raises(InvalidStatementRangeError):
        await get_supplier_statement(
            db_session,
            shop_id=fixture.shop.id,
            supplier_id=supplier.id,
            start_date=_day(7),
            end_date=_day(1),
        )


@pytest.mark.asyncio
async def test_statement_pagination_carries_the_running_balance(
    db_session: AsyncSession,
) -> None:
    fixture, supplier = await _example_khata(db_session)

    first_page = await get_supplier_statement(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id, limit=2
    )
    second_page = await get_supplier_statement(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=supplier.id,
        limit=2,
        offset=2,
    )

    assert first_page.total_entries == 4
    assert len(first_page.entries) == 2
    assert first_page.closing_balance == Decimal("30000.00")

    assert second_page.total_entries == 4
    assert len(second_page.entries) == 2
    # The skipped entries are folded into the opening balance, so page two's
    # running balances continue exactly where page one stopped.
    assert second_page.opening_balance == Decimal("30000.00")
    assert [entry.running_balance for entry in second_page.entries] == [
        Decimal("40000.00"),
        Decimal("30000.00"),
    ]
    assert second_page.closing_balance == Decimal("30000.00")


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_reports_purchases_paid_and_outstanding(
    db_session: AsyncSession,
) -> None:
    fixture, supplier = await _example_khata(db_session)

    summary = await get_supplier_summary(
        db_session, shop_id=fixture.shop.id, supplier_id=supplier.id
    )

    assert summary.name == "Al-Madina Textile"
    assert summary.total_purchases == Decimal("60000.00")
    assert summary.total_paid == Decimal("30000.00")
    assert summary.outstanding_balance == Decimal("30000.00")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def _headers(shop_id: uuid.UUID) -> dict[str, str]:
    return {"X-Shop-Id": str(shop_id)}


@pytest.mark.asyncio
async def test_balance_endpoint_reports_the_outstanding_payable(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _buy(api_session, fixture, fixture.supplier, amount="10000", paid="7000")

    response = await api_client.get(
        f"/suppliers/{fixture.supplier.id}/balance",
        headers=_headers(fixture.shop.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert Decimal(str(payload["outstanding_balance"])) == Decimal("10000.00")
    assert Decimal(str(payload["total_purchases"])) == Decimal("10000.00")
    assert payload["number_of_purchases"] == 1


@pytest.mark.asyncio
async def test_statement_endpoint_returns_entries_in_order(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _buy(
        api_session, fixture, fixture.supplier, amount="10000", invoice="INV-1"
    )
    await _pay(api_session, fixture, fixture.supplier, "7000")

    response = await api_client.get(
        f"/suppliers/{fixture.supplier.id}/statement",
        headers=_headers(fixture.shop.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_entries"] == 2
    assert [entry["entry_type"] for entry in payload["entries"]] == [
        "PURCHASE",
        "PAYMENT",
    ]
    assert Decimal(str(payload["closing_balance"])) == Decimal("3000.00")


@pytest.mark.asyncio
async def test_summary_endpoint_returns_the_dashboard_figures(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _buy(api_session, fixture, fixture.supplier, amount="10000")
    await _pay(api_session, fixture, fixture.supplier, "7000")

    response = await api_client.get(
        f"/suppliers/{fixture.supplier.id}/summary",
        headers=_headers(fixture.shop.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Al-Madina Textile"
    assert Decimal(str(payload["total_purchases"])) == Decimal("10000.00")
    assert Decimal(str(payload["total_paid"])) == Decimal("7000.00")
    assert Decimal(str(payload["outstanding_balance"])) == Decimal("3000.00")


@pytest.mark.asyncio
async def test_payment_endpoint_records_a_settlement(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _buy(api_session, fixture, fixture.supplier, amount="10000")

    response = await api_client.post(
        f"/suppliers/{fixture.supplier.id}/payments",
        headers=_headers(fixture.shop.id),
        json={"amount": "4000.00", "method": "cash", "reference": "CASH-9"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert Decimal(str(payload["payment"]["amount"])) == Decimal("4000.00")
    assert payload["payment"]["method"] == "cash"
    assert payload["payment"]["purchase_id"] is None
    assert Decimal(str(payload["balance"]["outstanding_balance"])) == Decimal(
        "6000.00"
    )
    assert await _payment_count(api_session, fixture.supplier.id) == 1


@pytest.mark.asyncio
async def test_payment_endpoint_rejects_an_overpayment(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _buy(api_session, fixture, fixture.supplier, amount="10000")

    response = await api_client.post(
        f"/suppliers/{fixture.supplier.id}/payments",
        headers=_headers(fixture.shop.id),
        json={"amount": "12000.00", "method": "cash"},
    )

    assert response.status_code == 422
    assert await _payment_count(api_session, fixture.supplier.id) == 0


@pytest.mark.asyncio
async def test_endpoints_do_not_expose_another_shops_supplier(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    shop_a = await _make_shop(api_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(api_session, shop_name="Shop B", sku="B-1")
    await _buy(api_session, shop_b, shop_b.supplier, amount="10000")

    response = await api_client.get(
        f"/suppliers/{shop_b.supplier.id}/balance",
        headers=_headers(shop_a.shop.id),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_endpoints_require_a_shop(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)

    response = await api_client.get(f"/suppliers/{fixture.supplier.id}/balance")

    assert response.status_code == 401
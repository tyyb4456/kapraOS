"""Customer Khata / receivables tests.

Covers the balance aggregate, the statement read model (ordering, running
balances, date filtering, pagination), the settlement operation and - most
importantly - that a balance can only ever be built from one shop's and one
customer's own `Sale` / `Payment` rows.

A note on timestamps: `created_at` comes from `now()`, which in Postgres is the
*transaction* timestamp, so every row a test writes inside `db_session` shares
it. Tests that assert on ordering therefore stamp explicit dates with
`_stamp()`, the way real rows written on different days would look. The one
exception is
`test_statement_lists_a_sale_before_the_payment_taken_at_the_same_instant`,
which leaves the shared timestamp in place on purpose.
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
    Customer,
    Payment,
    PaymentMethod,
    Product,
    ProductType,
    ProductVariant,
    Sale,
    SaleStatus,
    Shop,
    Supplier,
    Unit,
    User,
)
from app.models.user import UserRole
from app.services.purchases import PurchaseItemInput, create_purchase
from app.services.receivables import (
    CustomerNotFoundError,
    InvalidPaymentAmountError,
    InvalidPaymentMethodError,
    InvalidStatementRangeError,
    PaymentExceedsOutstandingError,
    PaymentExceedsSaleDueError,
    SaleCustomerMismatchError,
    SaleNotSettleableError,
    StatementEntryType,
    get_customer_balance,
    get_customer_statement,
    get_customer_summary,
    record_customer_payment,
)
from app.services.sales import PaymentInput, SaleItemInput, create_sale

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
) -> ShopFixture:
    """Create shop -> category -> product -> variant, with stock to sell.

    Stock is seeded through a real purchase so the weighted-average cost is set
    and sales never fail on availability - this suite is about receivables, not
    inventory.
    """

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

    await create_purchase(
        db_session,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=variant.id,
                quantity=Decimal("100000"),
                unit_cost=Decimal("100"),
            )
        ],
    )

    return ShopFixture(shop=shop, product=product, variant=variant, supplier=supplier)


async def _make_customer(
    db_session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    name: str = "Ahmed",
    phone: str | None = "0300-1234567",
) -> Customer:
    customer = Customer(shop_id=shop_id, name=name, phone=phone)
    db_session.add(customer)
    await db_session.flush()
    return customer


async def _stamp(db_session: AsyncSession, when: datetime, *rows: object) -> None:
    """Backdate rows so ordering/date-filter assertions are meaningful."""

    for row in rows:
        row.created_at = when  # type: ignore[attr-defined]
    await db_session.flush()


async def _sell(
    db_session: AsyncSession,
    fixture: ShopFixture,
    customer: Customer | None,
    *,
    amount: str,
    invoice: str | None = None,
    paid: str | None = None,
    at: datetime | None = None,
) -> Sale:
    """Sell one metre at `amount`, so the sale total is exactly `amount`."""

    payments = (
        [PaymentInput(amount=Decimal(paid), method=PaymentMethod.CASH)]
        if paid is not None
        else []
    )
    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id if customer is not None else None,
        invoice_number=invoice,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("1"),
                unit_price=Decimal(amount),
            )
        ],
        payments=payments,
    )
    if at is not None:
        await _stamp(db_session, at, sale, *sale.payments)
    return sale


async def _settle(
    db_session: AsyncSession,
    fixture: ShopFixture,
    customer: Customer,
    amount: str,
    *,
    sale_id: uuid.UUID | None = None,
    reference: str | None = None,
    at: datetime | None = None,
) -> Payment:
    payment = await record_customer_payment(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        amount=Decimal(amount),
        method=PaymentMethod.CASH,
        sale_id=sale_id,
        reference=reference,
    )
    if at is not None:
        await _stamp(db_session, at, payment)
    return payment


async def _example_khata(
    db_session: AsyncSession,
) -> tuple[ShopFixture, Customer]:
    """The worked example from the spec:

        Sep 01  Sale     INV-1001   +10,000  ->  10,000
        Sep 02  Payment  CASH-001    -7,000  ->   3,000
        Sep 05  Sale     INV-1008    +5,000  ->   8,000
        Sep 07  Payment  CASH-002    -3,000  ->   5,000
    """

    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)

    await _sell(db_session, fixture, ahmed, amount="10000", invoice="INV-1001", at=_day(1))
    await _settle(db_session, fixture, ahmed, "7000", reference="CASH-001", at=_day(2))
    await _sell(db_session, fixture, ahmed, amount="5000", invoice="INV-1008", at=_day(5))
    await _settle(db_session, fixture, ahmed, "3000", reference="CASH-002", at=_day(7))

    return fixture, ahmed


async def _payment_count(
    db_session: AsyncSession, customer_id: uuid.UUID
) -> int:
    result = await db_session.execute(
        sa.select(sa.func.count())
        .select_from(Payment)
        .where(Payment.customer_id == customer_id)
    )
    return int(result.scalar_one())


# --------------------------------------------------------------------------
# 1. Customer with no transactions
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_with_no_transactions_owes_nothing(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.total_sales == Decimal("0.00")
    assert balance.total_payments == Decimal("0.00")
    assert balance.outstanding_balance == Decimal("0.00")
    assert balance.number_of_sales == 0
    assert balance.number_of_payments == 0
    assert balance.last_sale_at is None
    assert balance.last_payment_at is None


# --------------------------------------------------------------------------
# 2. Unpaid sale
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpaid_sale_is_fully_outstanding(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    await _sell(db_session, fixture, ahmed, amount="10000")

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.total_sales == Decimal("10000.00")
    assert balance.total_payments == Decimal("0.00")
    assert balance.outstanding_balance == Decimal("10000.00")


# --------------------------------------------------------------------------
# 3. Partial payment
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_payment_reduces_the_outstanding_balance(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    sale = await _sell(db_session, fixture, ahmed, amount="10000", paid="7000")

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert sale.status is SaleStatus.PARTIAL
    assert balance.outstanding_balance == Decimal("3000.00")


# --------------------------------------------------------------------------
# 4. Multiple sales and payments
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_sales_and_payments_aggregate(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)

    await _sell(db_session, fixture, ahmed, amount="10000", paid="4000")
    await _sell(db_session, fixture, ahmed, amount="5000", paid="3000")

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.total_sales == Decimal("15000.00")
    assert balance.total_payments == Decimal("7000.00")
    assert balance.outstanding_balance == Decimal("8000.00")
    assert balance.number_of_sales == 2
    assert balance.number_of_payments == 2


@pytest.mark.asyncio
async def test_balance_reports_counts_and_last_activity(
    db_session: AsyncSession,
) -> None:
    fixture, ahmed = await _example_khata(db_session)

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.number_of_sales == 2
    assert balance.number_of_payments == 2
    assert balance.last_sale_at == _day(5)
    assert balance.last_payment_at == _day(7)


# --------------------------------------------------------------------------
# 5. Customer statement
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_statement_is_chronological_with_running_balances(
    db_session: AsyncSession,
) -> None:
    fixture, ahmed = await _example_khata(db_session)

    statement = await get_customer_statement(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert statement.total_entries == 4
    assert statement.opening_balance == Decimal("0.00")
    assert [entry.entry_type for entry in statement.entries] == [
        StatementEntryType.SALE,
        StatementEntryType.PAYMENT,
        StatementEntryType.SALE,
        StatementEntryType.PAYMENT,
    ]
    assert [entry.running_balance for entry in statement.entries] == [
        Decimal("10000.00"),
        Decimal("3000.00"),
        Decimal("8000.00"),
        Decimal("5000.00"),
    ]
    assert [entry.date for entry in statement.entries] == [
        _day(1),
        _day(2),
        _day(5),
        _day(7),
    ]
    assert statement.closing_balance == Decimal("5000.00")

    first_sale, first_payment = statement.entries[0], statement.entries[1]
    assert first_sale.debit == Decimal("10000.00")
    assert first_sale.credit == Decimal("0.00")
    assert first_sale.reference == "INV-1001"
    assert first_sale.invoice_number == "INV-1001"
    assert first_sale.payment_id is None
    assert first_sale.sale_id is not None

    assert first_payment.credit == Decimal("7000.00")
    assert first_payment.debit == Decimal("0.00")
    assert first_payment.reference == "CASH-001"
    assert first_payment.payment_method is PaymentMethod.CASH
    assert first_payment.payment_id is not None


@pytest.mark.asyncio
async def test_statement_lists_a_sale_before_the_payment_taken_at_the_same_instant(
    db_session: AsyncSession,
) -> None:
    """A till payment shares its sale's transaction timestamp exactly.

    The invoice must still be listed first, otherwise the running balance would
    dip negative before the sale that justifies it appears.
    """

    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    await _sell(db_session, fixture, ahmed, amount="10000", paid="7000")

    statement = await get_customer_statement(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert [entry.entry_type for entry in statement.entries] == [
        StatementEntryType.SALE,
        StatementEntryType.PAYMENT,
    ]
    assert statement.entries[0].date == statement.entries[1].date
    assert [entry.running_balance for entry in statement.entries] == [
        Decimal("10000.00"),
        Decimal("3000.00"),
    ]


# --------------------------------------------------------------------------
# 6. Walk-in sales never belong to a Khata
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_walk_in_sale_is_not_in_any_customer_khata(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)

    walk_in = await _sell(db_session, fixture, None, amount="4000")
    await _sell(db_session, fixture, ahmed, amount="1000")

    assert walk_in.customer_id is None

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )
    statement = await get_customer_statement(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.total_sales == Decimal("1000.00")
    assert balance.outstanding_balance == Decimal("1000.00")
    assert statement.total_entries == 1
    assert walk_in.id not in {entry.sale_id for entry in statement.entries}


# --------------------------------------------------------------------------
# 7. A payment cannot cross from one customer to another
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settlement_rejects_a_sale_belonging_to_another_customer(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id, name="Ahmed")
    bilal = await _make_customer(db_session, fixture.shop.id, name="Bilal")

    await _sell(db_session, fixture, ahmed, amount="10000")
    bilal_sale = await _sell(db_session, fixture, bilal, amount="5000")

    with pytest.raises(SaleCustomerMismatchError):
        await _settle(db_session, fixture, ahmed, "1000", sale_id=bilal_sale.id)

    assert await _payment_count(db_session, ahmed.id) == 0


@pytest.mark.asyncio
async def test_database_rejects_a_payment_against_another_customers_sale(
    db_session: AsyncSession,
) -> None:
    """The composite FK is the database-level half of the Khata guard."""

    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id, name="Ahmed")
    bilal = await _make_customer(db_session, fixture.shop.id, name="Bilal")
    bilal_sale = await _sell(db_session, fixture, bilal, amount="5000")

    db_session.add(
        Payment(
            shop_id=fixture.shop.id,
            customer_id=ahmed.id,  # Ahmed...
            sale_id=bilal_sale.id,  # ...but Bilal's invoice
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
async def test_customer_from_another_shop_is_not_found(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    customer_b = await _make_customer(db_session, shop_b.shop.id, name="Ahmed")

    for call in (get_customer_balance, get_customer_summary, get_customer_statement):
        with pytest.raises(CustomerNotFoundError):
            await call(
                db_session, shop_id=shop_a.shop.id, customer_id=customer_b.id
            )

    with pytest.raises(CustomerNotFoundError):
        await record_customer_payment(
            db_session,
            shop_id=shop_a.shop.id,
            customer_id=customer_b.id,
            amount=Decimal("100"),
            method=PaymentMethod.CASH,
        )


@pytest.mark.asyncio
async def test_another_shop_cannot_pay_into_this_customers_khata(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    ahmed = await _make_customer(db_session, shop_a.shop.id, name="Ahmed")
    await _sell(db_session, shop_a, ahmed, amount="10000")

    balance = await get_customer_balance(
        db_session, shop_id=shop_a.shop.id, customer_id=ahmed.id
    )
    assert balance.outstanding_balance == Decimal("10000.00")

    # Shop B cannot even persist a payment naming Shop A's customer.
    db_session.add(
        Payment(
            shop_id=shop_b.shop.id,
            customer_id=ahmed.id,
            amount=Decimal("10000.00"),
            method=PaymentMethod.CASH,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_same_customer_name_in_two_shops_stays_isolated(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    ahmed_a = await _make_customer(
        db_session, shop_a.shop.id, name="Ahmed", phone="0300-1111111"
    )
    ahmed_b = await _make_customer(
        db_session, shop_b.shop.id, name="Ahmed", phone="0300-1111111"
    )

    await _sell(db_session, shop_a, ahmed_a, amount="10000", paid="4000")
    await _sell(db_session, shop_b, ahmed_b, amount="2000")

    balance_a = await get_customer_balance(
        db_session, shop_id=shop_a.shop.id, customer_id=ahmed_a.id
    )
    balance_b = await get_customer_balance(
        db_session, shop_id=shop_b.shop.id, customer_id=ahmed_b.id
    )

    assert balance_a.total_sales == Decimal("10000.00")
    assert balance_a.outstanding_balance == Decimal("6000.00")
    assert balance_b.total_sales == Decimal("2000.00")
    assert balance_b.outstanding_balance == Decimal("2000.00")


# --------------------------------------------------------------------------
# 10. Unallocated customer payment
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unallocated_payment_reduces_the_balance_without_touching_sales(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    sale = await _sell(db_session, fixture, ahmed, amount="10000")

    payment = await _settle(db_session, fixture, ahmed, "5000")

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )
    await db_session.refresh(sale)

    assert payment.sale_id is None
    assert balance.outstanding_balance == Decimal("5000.00")
    # No invoice-level allocation was invented for it.
    assert sale.paid_amount == Decimal("0.00")
    assert sale.due_amount == Decimal("10000.00")
    assert sale.status is SaleStatus.PARTIAL

    statement = await get_customer_statement(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )
    unallocated = statement.entries[-1]
    assert unallocated.entry_type is StatementEntryType.PAYMENT
    assert unallocated.sale_id is None
    assert unallocated.credit == Decimal("5000.00")


# --------------------------------------------------------------------------
# 11. Settlement
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settlement_creates_exactly_one_payment_row(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    await _sell(db_session, fixture, ahmed, amount="10000")

    payment = await _settle(db_session, fixture, ahmed, "4000", reference="CASH-77")

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.outstanding_balance == Decimal("6000.00")
    assert await _payment_count(db_session, ahmed.id) == 1
    assert payment.amount == Decimal("4000.00")
    assert payment.method is PaymentMethod.CASH
    assert payment.reference == "CASH-77"
    assert payment.shop_id == fixture.shop.id
    assert payment.purchase_id is None


@pytest.mark.asyncio
async def test_settlement_allocated_to_a_sale_updates_that_sale(
    db_session: AsyncSession,
) -> None:
    """Allocating keeps Step 5's `Sale.paid_amount` cache truthful."""

    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    sale = await _sell(db_session, fixture, ahmed, amount="10000")

    await _settle(db_session, fixture, ahmed, "4000", sale_id=sale.id)
    await db_session.refresh(sale)
    assert sale.paid_amount == Decimal("4000.00")
    assert sale.due_amount == Decimal("6000.00")
    assert sale.status is SaleStatus.PARTIAL

    await _settle(db_session, fixture, ahmed, "6000", sale_id=sale.id)
    await db_session.refresh(sale)
    assert sale.paid_amount == Decimal("10000.00")
    assert sale.due_amount == Decimal("0.00")
    assert sale.status is SaleStatus.COMPLETED

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
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
    ahmed = await _make_customer(db_session, fixture.shop.id)
    await _sell(db_session, fixture, ahmed, amount="10000")

    with pytest.raises(PaymentExceedsOutstandingError):
        await _settle(db_session, fixture, ahmed, "12000")

    assert await _payment_count(db_session, ahmed.id) == 0


@pytest.mark.asyncio
async def test_settlement_cannot_exceed_the_named_sales_due(
    db_session: AsyncSession,
) -> None:
    """Enough is owed overall, but not on the invoice being settled."""

    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    first = await _sell(db_session, fixture, ahmed, amount="10000")
    await _sell(db_session, fixture, ahmed, amount="10000")

    with pytest.raises(PaymentExceedsSaleDueError):
        await _settle(db_session, fixture, ahmed, "15000", sale_id=first.id)

    assert await _payment_count(db_session, ahmed.id) == 0


@pytest.mark.asyncio
async def test_zero_and_negative_settlements_are_rejected(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    await _sell(db_session, fixture, ahmed, amount="10000")

    for bad_amount in ("0", "-100"):
        with pytest.raises(InvalidPaymentAmountError):
            await _settle(db_session, fixture, ahmed, bad_amount)

    assert await _payment_count(db_session, ahmed.id) == 0


@pytest.mark.asyncio
async def test_unknown_payment_method_is_rejected(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    await _sell(db_session, fixture, ahmed, amount="10000")

    with pytest.raises(InvalidPaymentMethodError):
        await record_customer_payment(
            db_session,
            shop_id=fixture.shop.id,
            customer_id=ahmed.id,
            amount=Decimal("100"),
            method="cheque",  # type: ignore[arg-type]
        )

    assert await _payment_count(db_session, ahmed.id) == 0


@pytest.mark.asyncio
async def test_settlement_against_a_cancelled_sale_is_rejected(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    await _sell(db_session, fixture, ahmed, amount="10000")

    cancelled = Sale(
        shop_id=fixture.shop.id,
        customer_id=ahmed.id,
        subtotal=Decimal("5000.00"),
        discount=Decimal("0.00"),
        total=Decimal("5000.00"),
        paid_amount=Decimal("0.00"),
        status=SaleStatus.CANCELLED,
    )
    db_session.add(cancelled)
    await db_session.flush()

    with pytest.raises(SaleNotSettleableError):
        await _settle(db_session, fixture, ahmed, "1000", sale_id=cancelled.id)

    assert await _payment_count(db_session, ahmed.id) == 0


# --------------------------------------------------------------------------
# 14. Decimal correctness
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decimal_arithmetic_is_exact(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    await _sell(db_session, fixture, ahmed, amount="10500.50")
    await _settle(db_session, fixture, ahmed, "7250.25")

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.total_sales == Decimal("10500.50")
    assert balance.total_payments == Decimal("7250.25")
    assert balance.outstanding_balance == Decimal("3250.25")
    assert isinstance(balance.outstanding_balance, Decimal)

    statement = await get_customer_statement(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )
    assert statement.closing_balance == Decimal("3250.25")


# --------------------------------------------------------------------------
# 15. Non-qualifying sales
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_sale_does_not_increase_the_receivable(
    db_session: AsyncSession,
) -> None:
    """A voided invoice - and any payment booked against it - is excluded.

    Both halves are excluded deliberately: crediting a payment whose invoice no
    longer counts would leave the customer in a phantom negative balance.
    """

    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)

    cancelled = Sale(
        shop_id=fixture.shop.id,
        customer_id=ahmed.id,
        subtotal=Decimal("5000.00"),
        discount=Decimal("0.00"),
        total=Decimal("5000.00"),
        paid_amount=Decimal("1000.00"),
        status=SaleStatus.CANCELLED,
    )
    db_session.add(cancelled)
    await db_session.flush()
    db_session.add(
        Payment(
            shop_id=fixture.shop.id,
            customer_id=ahmed.id,
            sale_id=cancelled.id,
            amount=Decimal("1000.00"),
            method=PaymentMethod.CASH,
        )
    )
    await db_session.flush()

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )
    statement = await get_customer_statement(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.total_sales == Decimal("0.00")
    assert balance.total_payments == Decimal("0.00")
    assert balance.outstanding_balance == Decimal("0.00")
    assert statement.total_entries == 0


@pytest.mark.asyncio
async def test_returned_sale_does_not_increase_the_receivable(
    db_session: AsyncSession,
) -> None:
    """RETURNED is excluded too - there is no returns workflow yet to reverse
    it, and returned goods are not money the customer still owes."""

    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    await _sell(db_session, fixture, ahmed, amount="2000")

    returned = Sale(
        shop_id=fixture.shop.id,
        customer_id=ahmed.id,
        subtotal=Decimal("5000.00"),
        discount=Decimal("0.00"),
        total=Decimal("5000.00"),
        paid_amount=Decimal("0.00"),
        status=SaleStatus.RETURNED,
    )
    db_session.add(returned)
    await db_session.flush()

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.total_sales == Decimal("2000.00")
    assert balance.outstanding_balance == Decimal("2000.00")
    assert balance.number_of_sales == 1


# --------------------------------------------------------------------------
# 16. History stays correct as new business arrives
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_stays_correct_as_new_sales_arrive(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)

    await _sell(db_session, fixture, ahmed, amount="10000", at=_day(1))
    await _settle(db_session, fixture, ahmed, "4000", at=_day(2))
    await _sell(db_session, fixture, ahmed, amount="5000", at=_day(9))

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )
    statement = await get_customer_statement(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.outstanding_balance == Decimal("11000.00")
    assert [entry.running_balance for entry in statement.entries] == [
        Decimal("10000.00"),
        Decimal("6000.00"),
        Decimal("11000.00"),
    ]


# --------------------------------------------------------------------------
# 17. Negative balances are surfaced
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_negative_balance_is_surfaced_not_hidden(
    db_session: AsyncSession,
) -> None:
    """The settlement operation refuses overpayments, but if historical rows
    produce a negative balance the caller must be able to see it."""

    fixture = await _make_shop(db_session)
    ahmed = await _make_customer(db_session, fixture.shop.id)
    await _sell(db_session, fixture, ahmed, amount="1000")

    db_session.add(
        Payment(
            shop_id=fixture.shop.id,
            customer_id=ahmed.id,
            amount=Decimal("3000.00"),
            method=PaymentMethod.CASH,
        )
    )
    await db_session.flush()

    balance = await get_customer_balance(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert balance.outstanding_balance == Decimal("-2000.00")


# --------------------------------------------------------------------------
# 18. Date filtering and pagination
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_statement_can_be_filtered_by_date(
    db_session: AsyncSession,
) -> None:
    fixture, ahmed = await _example_khata(db_session)

    statement = await get_customer_statement(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=ahmed.id,
        start_date=_day(3),
        end_date=_day(6),
    )

    assert statement.total_entries == 1
    # Everything before the window is carried in as the opening balance.
    assert statement.opening_balance == Decimal("3000.00")
    assert [entry.invoice_number for entry in statement.entries] == ["INV-1008"]
    assert statement.entries[0].running_balance == Decimal("8000.00")
    assert statement.closing_balance == Decimal("8000.00")


@pytest.mark.asyncio
async def test_statement_range_must_not_be_inverted(
    db_session: AsyncSession,
) -> None:
    fixture, ahmed = await _example_khata(db_session)

    with pytest.raises(InvalidStatementRangeError):
        await get_customer_statement(
            db_session,
            shop_id=fixture.shop.id,
            customer_id=ahmed.id,
            start_date=_day(7),
            end_date=_day(1),
        )


@pytest.mark.asyncio
async def test_statement_pagination_carries_the_running_balance(
    db_session: AsyncSession,
) -> None:
    fixture, ahmed = await _example_khata(db_session)

    first_page = await get_customer_statement(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id, limit=2
    )
    second_page = await get_customer_statement(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=ahmed.id,
        limit=2,
        offset=2,
    )

    assert first_page.total_entries == 4
    assert len(first_page.entries) == 2
    assert first_page.closing_balance == Decimal("3000.00")

    assert second_page.total_entries == 4
    assert len(second_page.entries) == 2
    # The skipped entries are folded into the opening balance, so page two's
    # running balances continue exactly where page one stopped.
    assert second_page.opening_balance == Decimal("3000.00")
    assert [entry.running_balance for entry in second_page.entries] == [
        Decimal("8000.00"),
        Decimal("5000.00"),
    ]
    assert second_page.closing_balance == Decimal("5000.00")


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_reports_purchases_paid_and_outstanding(
    db_session: AsyncSession,
) -> None:
    fixture, ahmed = await _example_khata(db_session)

    summary = await get_customer_summary(
        db_session, shop_id=fixture.shop.id, customer_id=ahmed.id
    )

    assert summary.name == "Ahmed"
    assert summary.phone == "0300-1234567"
    assert summary.total_purchases == Decimal("15000.00")
    assert summary.total_paid == Decimal("10000.00")
    assert summary.outstanding_balance == Decimal("5000.00")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def _headers(shop_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


@pytest.mark.asyncio
async def test_balance_endpoint_reports_the_outstanding_balance(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    ahmed = await _make_customer(api_session, fixture.shop.id)
    await _sell(api_session, fixture, ahmed, amount="10000", paid="7000")
    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=fixture.shop.id,
        name="Mock User",
        email="mock@example.com",
        role=UserRole.OWNER,
    )
    api_session.add(user)
    await api_session.flush()

    response = await mocked_api_client.get(
        f"/customers/{ahmed.id}/balance", headers=_headers(fixture.shop.id)
    )

    assert response.status_code == 200
    payload = response.json()
    assert Decimal(str(payload["outstanding_balance"])) == Decimal("3000.00")
    assert Decimal(str(payload["total_sales"])) == Decimal("10000.00")
    assert payload["number_of_sales"] == 1


@pytest.mark.asyncio
async def test_statement_endpoint_returns_entries_in_order(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    ahmed = await _make_customer(api_session, fixture.shop.id)
    await _sell(api_session, fixture, ahmed, amount="10000", paid="7000", invoice="INV-1")
    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=fixture.shop.id,
        name="Mock User",
        email="mock@example.com",
        role=UserRole.OWNER,
    )
    api_session.add(user)
    await api_session.flush()

    response = await mocked_api_client.get(
        f"/customers/{ahmed.id}/statement", headers=_headers(fixture.shop.id)
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_entries"] == 2
    assert [entry["entry_type"] for entry in payload["entries"]] == ["SALE", "PAYMENT"]
    assert Decimal(str(payload["closing_balance"])) == Decimal("3000.00")


@pytest.mark.asyncio
async def test_summary_endpoint_returns_the_dashboard_figures(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    ahmed = await _make_customer(api_session, fixture.shop.id)
    await _sell(api_session, fixture, ahmed, amount="10000", paid="7000")
    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=fixture.shop.id,
        name="Mock User",
        email="mock@example.com",
        role=UserRole.OWNER,
    )
    api_session.add(user)
    await api_session.flush()

    response = await mocked_api_client.get(
        f"/customers/{ahmed.id}/summary", headers=_headers(fixture.shop.id)
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Ahmed"
    assert Decimal(str(payload["total_purchases"])) == Decimal("10000.00")
    assert Decimal(str(payload["total_paid"])) == Decimal("7000.00")
    assert Decimal(str(payload["outstanding_balance"])) == Decimal("3000.00")


@pytest.mark.asyncio
async def test_payment_endpoint_records_a_settlement(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    ahmed = await _make_customer(api_session, fixture.shop.id)
    await _sell(api_session, fixture, ahmed, amount="10000")
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
        f"/customers/{ahmed.id}/payments",
        headers=_headers(fixture.shop.id),
        json={"amount": "4000.00", "method": "cash", "reference": "CASH-9"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert Decimal(str(payload["payment"]["amount"])) == Decimal("4000.00")
    assert payload["payment"]["method"] == "cash"
    assert payload["payment"]["sale_id"] is None
    assert Decimal(str(payload["balance"]["outstanding_balance"])) == Decimal("6000.00")
    assert await _payment_count(api_session, ahmed.id) == 1


@pytest.mark.asyncio
async def test_payment_endpoint_rejects_an_overpayment(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    ahmed = await _make_customer(api_session, fixture.shop.id)
    await _sell(api_session, fixture, ahmed, amount="10000")
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
        f"/customers/{ahmed.id}/payments",
        headers=_headers(fixture.shop.id),
        json={"amount": "12000.00", "method": "cash"},
    )

    assert response.status_code == 422
    assert await _payment_count(api_session, ahmed.id) == 0


@pytest.mark.asyncio
async def test_endpoints_do_not_expose_another_shops_customer(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    shop_a = await _make_shop(api_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(api_session, shop_name="Shop B", sku="B-1")
    customer_b = await _make_customer(api_session, shop_b.shop.id, name="Ahmed")
    await _sell(api_session, shop_b, customer_b, amount="10000")
    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=shop_a.shop.id,
        name="Mock User",
        email="mock@example.com",
        role=UserRole.OWNER,
    )
    api_session.add(user)
    await api_session.flush()

    response = await mocked_api_client.get(
        f"/customers/{customer_b.id}/balance", headers=_headers(shop_a.shop.id)
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_endpoints_require_a_shop(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    ahmed = await _make_customer(api_session, fixture.shop.id)

    response = await mocked_api_client.get(f"/customers/{ahmed.id}/balance")

    assert response.status_code == 401
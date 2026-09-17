"""General ledger / double-entry accounting tests (Step 8).

Covers the chart of accounts, the double-entry invariant, the four posting
operations (sale, purchase, customer payment, supplier payment), duplicate-post
protection, tenant isolation, the derived balance and the account ledger read
model, plus the read-only accounting API.

The invariant under test everywhere is `SUM(debits) == SUM(credits)` for every
posting group. A `payment method -> account` mapping test also exists so a
change in `_METHOD_ACCOUNT_CODES` is caught here.
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
    AccountType,
    Category,
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
from app.services.accounting import (
    ACCOUNTS_PAYABLE,
    ACCOUNTS_RECEIVABLE,
    BANK,
    CASH,
    COST_OF_GOODS_SOLD,
    INVENTORY,
    MAINTENANCE_EXPENSE,
    MARKETING_EXPENSE,
    OTHER_EXPENSE,
    RENT_EXPENSE,
    SALARIES_EXPENSE,
    SALES_REVENUE,
    SUPPLIES_EXPENSE,
    TRANSPORT_EXPENSE,
    UTILITIES_EXPENSE,
    AccountNotFoundError,
    UnbalancedPostingError,
    ensure_system_accounts,
    get_account_balance,
    get_account_ledger,
    list_accounts,
    post_cogs,
    post_customer_payment,
    post_purchase,
    post_sale,
    post_supplier_payment,
)
from app.services.payables import record_supplier_payment
from app.services.purchases import PurchaseItemInput, create_purchase
from app.services.receivables import record_customer_payment
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
) -> None:
    await create_purchase(
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


async def _accounts(
    db_session: AsyncSession, shop_id: uuid.UUID
) -> dict[str, Account]:
    return await ensure_system_accounts(db_session, shop_id=shop_id)


async def _count_entries(
    db_session: AsyncSession, shop_id: uuid.UUID, reference_id: uuid.UUID
) -> int:
    result = await db_session.execute(
        sa.select(sa.func.count())
        .select_from(sa.text("ledger_entries"))
        .where(
            sa.text("shop_id = :shop_id AND reference_id = :reference_id")
        )
        .params(shop_id=shop_id, reference_id=reference_id)
    )
    return int(result.scalar_one())


def _totals(entries) -> tuple[Decimal, Decimal]:
    debit = sum((_dec(e.debit) for e in entries), start=Decimal("0.00"))
    credit = sum((_dec(e.credit) for e in entries), start=Decimal("0.00"))
    return debit, credit


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(value).quantize(Decimal("0.01"))


def _entry(
    shop_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    debit: str,
    credit: str,
):
    from app.models import LedgerEntry

    return LedgerEntry(
        shop_id=shop_id,
        account_id=account_id,
        debit=Decimal(debit),
        credit=Decimal(credit),
        reference_type="TEST",
        reference_id=uuid.uuid4(),
    )


async def _make_customer(db_session: AsyncSession, shop_id: uuid.UUID):
    from app.models import Customer

    customer = Customer(shop_id=shop_id, name="Ahmed")
    db_session.add(customer)
    await db_session.flush()
    return customer


async def _entries_for(
    db_session: AsyncSession,
    shop_id: uuid.UUID,
    reference_id: uuid.UUID,
    reference_type: str | None = None,
):
    from app.models import LedgerEntry

    statement = sa.select(LedgerEntry).where(
        LedgerEntry.shop_id == shop_id,
        LedgerEntry.reference_id == reference_id,
    )
    if reference_type is not None:
        statement = statement.where(LedgerEntry.reference_type == reference_type)

    return list((await db_session.execute(statement)).scalars())


async def _ledger_count(db_session: AsyncSession, shop_id: uuid.UUID) -> int:
    from app.models import LedgerEntry

    return int(
        (
            await db_session.execute(
                sa.select(sa.func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.shop_id == shop_id)
            )
        ).scalar_one()
    )


async def _post_unbalanced(
    db_session: AsyncSession,
    shop_id: uuid.UUID,
    accounts: dict[str, Account],
):
    from app.services.accounting import (
        REFERENCE_SALE,
        _post_group,
    )

    return await _post_group(
        db_session,
        shop_id=shop_id,
        reference_type=REFERENCE_SALE,
        reference_id=uuid.uuid4(),
        lines=[
            (accounts[CASH].id, Decimal("100"), Decimal("0"), None),
            (accounts[SALES_REVENUE].id, Decimal("0"), Decimal("90"), None),
        ],
    )


# --------------------------------------------------------------------------
# Chart of accounts
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_accounts_are_created(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)

    accounts = await _accounts(db_session, fixture.shop.id)

    assert set(accounts) == {
        CASH,
        BANK,
        ACCOUNTS_RECEIVABLE,
        INVENTORY,
        ACCOUNTS_PAYABLE,
        "3000",
        SALES_REVENUE,
        COST_OF_GOODS_SOLD,
        RENT_EXPENSE,
        UTILITIES_EXPENSE,
        SALARIES_EXPENSE,
        MARKETING_EXPENSE,
        TRANSPORT_EXPENSE,
        MAINTENANCE_EXPENSE,
        SUPPLIES_EXPENSE,
        OTHER_EXPENSE,
    }
    assert accounts[CASH].account_type is AccountType.ASSET
    assert accounts[ACCOUNTS_PAYABLE].account_type is AccountType.LIABILITY
    assert accounts[SALES_REVENUE].account_type is AccountType.REVENUE
    assert accounts[COST_OF_GOODS_SOLD].account_type is AccountType.EXPENSE
    assert accounts[RENT_EXPENSE].account_type is AccountType.EXPENSE
    assert all(account.is_system for account in accounts.values())


@pytest.mark.asyncio
async def test_system_account_initialization_is_idempotent(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    first = await _accounts(db_session, fixture.shop.id)
    second = await _accounts(db_session, fixture.shop.id)

    assert set(first) == set(second)
    listed = await list_accounts(db_session, shop_id=fixture.shop.id)
    assert len(listed) == len(first)


@pytest.mark.asyncio
async def test_account_code_is_unique_within_a_shop(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _accounts(db_session, fixture.shop.id)

    db_session.add(
        Account(
            shop_id=fixture.shop.id,
            code=CASH,
            name="Duplicate Cash",
            account_type=AccountType.ASSET,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_same_code_is_allowed_across_shops(db_session: AsyncSession) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")

    accounts_a = await _accounts(db_session, shop_a.shop.id)
    accounts_b = await _accounts(db_session, shop_b.shop.id)

    assert accounts_a[CASH].id != accounts_b[CASH].id
    assert accounts_a[CASH].code == accounts_b[CASH].code == CASH


@pytest.mark.asyncio
async def test_account_from_another_shop_is_not_found(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    accounts_b = await _accounts(db_session, shop_b.shop.id)

    with pytest.raises(AccountNotFoundError):
        await get_account_balance(
            db_session, shop_id=shop_a.shop.id, account_id=accounts_b[CASH].id
        )

    with pytest.raises(AccountNotFoundError):
        await get_account_ledger(
            db_session, shop_id=shop_a.shop.id, account_id=accounts_b[CASH].id
        )


@pytest.mark.asyncio
async def test_account_type_is_validated_by_the_database(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    with pytest.raises(sa.exc.DBAPIError):
        await db_session.execute(
            sa.text(
                "INSERT INTO accounts (id, shop_id, code, name, account_type, "
                "is_system, is_active) VALUES (:id, :shop_id, :code, :name, "
                ":type, false, true)"
            ).params(
                id=uuid.uuid4(),
                shop_id=fixture.shop.id,
                code="9999",
                name="Bogus",
                type="not_a_type",
            )
        )


# --------------------------------------------------------------------------
# Ledger entry integrity
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debit_and_credit_on_one_line_is_rejected(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    accounts = await _accounts(db_session, fixture.shop.id)

    db_session.add(
        _entry(fixture.shop.id, accounts[CASH].id, debit="10", credit="10")
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_zero_line_is_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    accounts = await _accounts(db_session, fixture.shop.id)

    db_session.add(
        _entry(fixture.shop.id, accounts[CASH].id, debit="0", credit="0")
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_negative_amount_is_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    accounts = await _accounts(db_session, fixture.shop.id)

    db_session.add(
        _entry(fixture.shop.id, accounts[CASH].id, debit="-5", credit="0")
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_unbalanced_posting_is_rejected(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    accounts = await _accounts(db_session, fixture.shop.id)

    with pytest.raises(UnbalancedPostingError):
        await _post_unbalanced(db_session, fixture.shop.id, accounts)

    count = await _ledger_count(db_session, fixture.shop.id)
    assert count == 0


@pytest.mark.asyncio
async def test_cross_shop_ledger_entry_is_rejected(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    accounts_b = await _accounts(db_session, shop_b.shop.id)

    # Shop A's ledger row pointing at Shop B's account.
    db_session.add(
        _entry(shop_a.shop.id, accounts_b[CASH].id, debit="10", credit="0")
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


# --------------------------------------------------------------------------
# Sale posting
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cash_sale_posts_cash_and_revenue(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    sale = await _sale(
        db_session,
        fixture,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    accounts = await _accounts(db_session, fixture.shop.id)
    balance = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[SALES_REVENUE].id
    )
    assert balance.balance == Decimal("1000.00")

    cash = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[CASH].id
    )
    assert cash.balance == Decimal("1000.00")

    ar = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[ACCOUNTS_RECEIVABLE].id
    )
    assert ar.balance == Decimal("0.00")

    entries = await _entries_for(db_session, fixture.shop.id, sale.id, "SALE")
    debit, credit = _totals(entries)
    assert debit == credit == Decimal("1000.00")


@pytest.mark.asyncio
async def test_credit_sale_posts_receivable_and_revenue(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    customer = await _make_customer(db_session, fixture.shop.id)
    sale = await _sale(db_session, fixture, customer_id=customer.id)

    accounts = await _accounts(db_session, fixture.shop.id)
    ar = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[ACCOUNTS_RECEIVABLE].id
    )
    assert ar.balance == Decimal("1000.00")

    entries = await _entries_for(db_session, fixture.shop.id, sale.id, "SALE")
    debit, credit = _totals(entries)
    assert debit == credit == Decimal("1000.00")


@pytest.mark.asyncio
async def test_partially_paid_sale_splits_cash_and_receivable(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    customer = await _make_customer(db_session, fixture.shop.id)
    sale = await _sale(
        db_session,
        fixture,
        customer_id=customer.id,
        payments=[PaymentInput(amount=Decimal("300"), method=PaymentMethod.CASH)],
    )

    accounts = await _accounts(db_session, fixture.shop.id)
    cash = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[CASH].id
    )
    ar = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[ACCOUNTS_RECEIVABLE].id
    )
    revenue = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[SALES_REVENUE].id
    )

    assert cash.balance == Decimal("300.00")
    assert ar.balance == Decimal("700.00")
    assert revenue.balance == Decimal("1000.00")

    entries = await _entries_for(db_session, fixture.shop.id, sale.id, "SALE")
    debit, credit = _totals(entries)
    assert debit == credit == Decimal("1000.00")


@pytest.mark.asyncio
async def test_sale_posting_is_idempotent(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    sale = await _sale(
        db_session,
        fixture,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    before = await _ledger_count(db_session, fixture.shop.id)
    await post_sale(db_session, sale=sale)
    after = await _ledger_count(db_session, fixture.shop.id)

    assert before == after


# --------------------------------------------------------------------------
# Sale COGS posting (Step 10)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_posts_cogs_and_reduces_inventory(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture, quantity="1000")
    sale = await _sale(
        db_session,
        fixture,
        quantity="10",
        payments=[PaymentInput(amount=Decimal("10000"), method=PaymentMethod.CASH)],
    )

    accounts = await _accounts(db_session, fixture.shop.id)
    cogs = await get_account_balance(
        db_session,
        shop_id=fixture.shop.id,
        account_id=accounts[COST_OF_GOODS_SOLD].id,
    )
    inventory = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[INVENTORY].id
    )

    assert cogs.balance == Decimal("1000.00")
    assert inventory.balance == Decimal("99000.00")

    entries = await _entries_for(db_session, fixture.shop.id, sale.id, "SALE_COGS")
    debit, credit = _totals(entries)
    assert debit == credit == Decimal("1000.00")

    cogs_line = next(e for e in entries if e.account_id == accounts[COST_OF_GOODS_SOLD].id)
    inventory_line = next(e for e in entries if e.account_id == accounts[INVENTORY].id)
    assert _dec(cogs_line.debit) == Decimal("1000.00")
    assert _dec(inventory_line.credit) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_multi_item_sale_cogs_sums_every_line(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    second = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="LINEN-WHT-002",
        purchase_price=Decimal("100.00"),
        selling_price=Decimal("1000.00"),
        unit=Unit.METER,
    )
    db_session.add(second)
    await db_session.flush()

    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("100"),
                unit_cost=Decimal("500.00"),
            ),
            PurchaseItemInput(
                variant_id=second.id,
                quantity=Decimal("100"),
                unit_cost=Decimal("700.00"),
            ),
        ],
    )

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("2"),
                unit_price=Decimal("900.00"),
            ),
            SaleItemInput(
                variant_id=second.id,
                quantity=Decimal("3"),
                unit_price=Decimal("1200.00"),
            ),
        ],
    )

    accounts = await _accounts(db_session, fixture.shop.id)
    cogs = await get_account_balance(
        db_session,
        shop_id=fixture.shop.id,
        account_id=accounts[COST_OF_GOODS_SOLD].id,
    )
    # 2 x 500 + 3 x 700 = 1000 + 2100
    assert cogs.balance == Decimal("3100.00")

    entries = await _entries_for(db_session, fixture.shop.id, sale.id, "SALE_COGS")
    debit, credit = _totals(entries)
    assert debit == credit == Decimal("3100.00")


@pytest.mark.asyncio
async def test_decimal_quantity_and_cost_cogs(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("1000"),
                unit_cost=Decimal("433.33"),
            )
        ],
    )
    await _sale(db_session, fixture, quantity="3.500", unit_price="1000")

    accounts = await _accounts(db_session, fixture.shop.id)
    cogs = await get_account_balance(
        db_session,
        shop_id=fixture.shop.id,
        account_id=accounts[COST_OF_GOODS_SOLD].id,
    )
    assert cogs.balance == Decimal("1516.66")


@pytest.mark.asyncio
async def test_cogs_posting_is_idempotent(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    sale = await _sale(db_session, fixture, quantity="10")

    before = await _ledger_count(db_session, fixture.shop.id)
    await post_cogs(db_session, sale=sale)
    after = await _ledger_count(db_session, fixture.shop.id)

    assert before == after


@pytest.mark.asyncio
async def test_cancelled_sale_receives_no_cogs_posting(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    sale = await _sale(db_session, fixture, quantity="10")

    sale.status = SaleStatus.CANCELLED
    await db_session.flush()

    accounts = await _accounts(db_session, fixture.shop.id)
    before = await _ledger_count(db_session, fixture.shop.id)
    entries = await post_cogs(db_session, sale=sale)
    after = await _ledger_count(db_session, fixture.shop.id)

    assert entries == []
    assert before == after
    cogs = await get_account_balance(
        db_session,
        shop_id=fixture.shop.id,
        account_id=accounts[COST_OF_GOODS_SOLD].id,
    )
    assert cogs.balance == Decimal("1000.00")


@pytest.mark.asyncio
async def test_zero_cost_sale_skips_cogs_without_invalid_entries(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture, quantity="10")

    # Force the snapshot cost to zero through the inventory service path used
    # by sales: an uncosted receipt leaves the weighted average at 0.
    zero_variant = ProductVariant(
        shop_id=fixture.shop.id,
        product_id=fixture.product.id,
        sku="ZERO-COST-001",
        purchase_price=Decimal("0.00"),
        selling_price=Decimal("500.00"),
        unit=Unit.METER,
    )
    db_session.add(zero_variant)
    await db_session.flush()

    from app.models import InventoryMovementType
    from app.services import inventory as inventory_service

    await inventory_service.add_stock(
        db_session,
        shop_id=fixture.shop.id,
        variant_id=zero_variant.id,
        quantity=Decimal("5"),
        movement_type=InventoryMovementType.ADJUSTMENT,
    )

    sale = await create_sale(
        db_session,
        shop_id=fixture.shop.id,
        items=[
            SaleItemInput(
                variant_id=zero_variant.id,
                quantity=Decimal("2"),
                unit_price=Decimal("500.00"),
            )
        ],
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )

    entries = await _entries_for(db_session, fixture.shop.id, sale.id, "SALE_COGS")
    assert entries == []

    # The revenue posting is unaffected.
    revenue_entries = await _entries_for(
        db_session, fixture.shop.id, sale.id, "SALE"
    )
    debit, credit = _totals(revenue_entries)
    assert debit == credit == Decimal("1000.00")


@pytest.mark.asyncio
async def test_cogs_failure_rolls_the_sale_back(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture, quantity="100")

    async def _boom(*args, **kwargs):
        raise RuntimeError("cogs posting failed")

    monkeypatch.setattr(
        "app.services.accounting.post_cogs", _boom, raising=True
    )

    with pytest.raises(RuntimeError):
        await _sale(db_session, fixture, quantity="10")

    await db_session.rollback()

    counts = (
        await db_session.execute(sa.select(sa.func.count()).select_from(Sale))
    ).scalar_one()
    inventory = (
        await db_session.execute(sa.select(sa.func.count()).select_from(Account))
    ).scalar_one()
    assert counts == 0
    assert inventory == 0


@pytest.mark.asyncio
async def test_cogs_is_tenant_isolated(db_session: AsyncSession) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    await _stock(db_session, shop_b, quantity="100")
    await _sale(db_session, shop_b, quantity="10")

    accounts_a = await _accounts(db_session, shop_a.shop.id)
    cogs_a = await get_account_balance(
        db_session,
        shop_id=shop_a.shop.id,
        account_id=accounts_a[COST_OF_GOODS_SOLD].id,
    )

    assert cogs_a.balance == Decimal("0.00")


# --------------------------------------------------------------------------
# Purchase posting
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_posts_inventory_and_payable(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("1"),
                unit_cost=Decimal("5000"),
            )
        ],
    )

    accounts = await _accounts(db_session, fixture.shop.id)
    inventory = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[INVENTORY].id
    )
    payable = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[ACCOUNTS_PAYABLE].id
    )

    assert inventory.balance == Decimal("5000.00")
    assert payable.balance == Decimal("5000.00")

    entries = await _entries_for(db_session, fixture.shop.id, purchase.id, "PURCHASE")
    debit, credit = _totals(entries)
    assert debit == credit == Decimal("5000.00")


# --------------------------------------------------------------------------
# Customer payment posting
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_payment_settles_receivable(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    customer = await _make_customer(db_session, fixture.shop.id)
    sale = await _sale(
        db_session,
        fixture,
        quantity="10",
        customer_id=customer.id,
        payments=[PaymentInput(amount=Decimal("4000"), method=PaymentMethod.CASH)],
    )

    payment = await record_customer_payment(
        db_session,
        shop_id=fixture.shop.id,
        customer_id=customer.id,
        amount=Decimal("6000"),
        method=PaymentMethod.CASH,
        sale_id=sale.id,
    )

    accounts = await _accounts(db_session, fixture.shop.id)
    ar = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[ACCOUNTS_RECEIVABLE].id
    )
    cash = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[CASH].id
    )
    assert ar.balance == Decimal("0.00")
    assert cash.balance == Decimal("10000.00")

    entries = await _entries_for(
        db_session, fixture.shop.id, payment.id, "CUSTOMER_PAYMENT"
    )
    debit, credit = _totals(entries)
    assert debit == credit == Decimal("6000.00")


@pytest.mark.asyncio
async def test_customer_payment_methods_map_to_expected_accounts(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    await _stock(db_session, fixture)
    accounts = await _accounts(db_session, fixture.shop.id)

    expected = {
        PaymentMethod.CASH: CASH,
        PaymentMethod.CARD: BANK,
        PaymentMethod.BANK: BANK,
        PaymentMethod.JAZZCASH: BANK,
        PaymentMethod.EASYPAISA: BANK,
        PaymentMethod.OTHER: CASH,
    }

    for method, code in expected.items():
        payment = Payment(
            shop_id=fixture.shop.id,
            amount=Decimal("10.00"),
            method=method,
        )
        db_session.add(payment)
        await db_session.flush()

        before = await _ledger_count(db_session, fixture.shop.id)
        entries = await post_customer_payment(db_session, payment=payment)
        after = await _ledger_count(db_session, fixture.shop.id)

        assert after - before == 2
        debit_line = next(e for e in entries if _dec(e.debit) > 0)
        assert debit_line.account_id == accounts[code].id


# --------------------------------------------------------------------------
# Supplier payment posting
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_payment_settles_payable(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    await create_purchase(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("1"),
                unit_cost=Decimal("30000"),
            )
        ],
    )

    payment = await record_supplier_payment(
        db_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        amount=Decimal("30000"),
        method=PaymentMethod.CASH,
    )

    accounts = await _accounts(db_session, fixture.shop.id)
    payable = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[ACCOUNTS_PAYABLE].id
    )
    cash = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[CASH].id
    )
    assert payable.balance == Decimal("0.00")
    assert cash.balance == Decimal("-30000.00")

    entries = await _entries_for(
        db_session, fixture.shop.id, payment.id, "SUPPLIER_PAYMENT"
    )
    debit, credit = _totals(entries)
    assert debit == credit == Decimal("30000.00")


# --------------------------------------------------------------------------
# Duplicate posting / tenant isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_payment_posted_twice_writes_nothing_new(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    payment = Payment(
        shop_id=fixture.shop.id,
        amount=Decimal("100.00"),
        method=PaymentMethod.CASH,
    )
    db_session.add(payment)
    await db_session.flush()

    await post_customer_payment(db_session, payment=payment)
    first = await _ledger_count(db_session, fixture.shop.id)
    await post_customer_payment(db_session, payment=payment)
    second = await _ledger_count(db_session, fixture.shop.id)

    assert first == second == 2


@pytest.mark.asyncio
async def test_shop_a_cannot_post_against_shop_b_accounts(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    accounts_b = await _accounts(db_session, shop_b.shop.id)

    db_session.add(
        _entry(shop_a.shop.id, accounts_b[CASH].id, debit="5", credit="0")
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


# --------------------------------------------------------------------------
# Account ledger read model
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_ledger_orders_and_runs_a_balance(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    accounts = await _accounts(db_session, fixture.shop.id)
    cash = accounts[CASH]

    first = Payment(
        shop_id=fixture.shop.id, amount=Decimal("100.00"), method=PaymentMethod.CASH
    )
    second = Payment(
        shop_id=fixture.shop.id, amount=Decimal("50.00"), method=PaymentMethod.CASH
    )
    db_session.add_all([first, second])
    await db_session.flush()

    e1 = await post_customer_payment(db_session, payment=first)
    e2 = await post_customer_payment(db_session, payment=second)
    debit_line_1 = next(e for e in e1 if _dec(e.debit) > 0)
    debit_line_2 = next(e for e in e2 if _dec(e.debit) > 0)
    debit_line_1.created_at = _day(1)
    debit_line_2.created_at = _day(2)
    await db_session.flush()

    ledger = await get_account_ledger(
        db_session, shop_id=fixture.shop.id, account_id=cash.id
    )

    assert ledger.total_entries == 2
    assert [entry.running_balance for entry in ledger.entries] == [
        Decimal("100.00"),
        Decimal("150.00"),
    ]
    assert ledger.closing_balance == Decimal("150.00")


@pytest.mark.asyncio
async def test_account_ledger_supports_date_filter_and_pagination(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    accounts = await _accounts(db_session, fixture.shop.id)
    cash = accounts[CASH]

    for day, amount in ((1, "10"), (2, "20"), (3, "30")):
        payment = Payment(
            shop_id=fixture.shop.id,
            amount=Decimal(amount),
            method=PaymentMethod.CASH,
        )
        db_session.add(payment)
        await db_session.flush()
        entries = await post_customer_payment(db_session, payment=payment)
        line = next(e for e in entries if _dec(e.debit) > 0)
        line.created_at = _day(day)
    await db_session.flush()

    windowed = await get_account_ledger(
        db_session,
        shop_id=fixture.shop.id,
        account_id=cash.id,
        start_date=_day(2),
        end_date=_day(3),
    )
    assert windowed.total_entries == 2
    assert windowed.opening_balance == Decimal("10.00")
    assert windowed.closing_balance == Decimal("60.00")

    page = await get_account_ledger(
        db_session,
        shop_id=fixture.shop.id,
        account_id=cash.id,
        limit=2,
        offset=1,
    )
    assert len(page.entries) == 2
    assert page.opening_balance == Decimal("10.00")
    assert page.entries[0].running_balance == Decimal("30.00")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def _headers(shop_id: uuid.UUID) -> dict[str, str]:
    return {"X-Shop-Id": str(shop_id)}


@pytest.mark.asyncio
async def test_accounts_endpoint_lists_the_chart(
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
        "/accounts", headers=_headers(fixture.shop.id)
    )

    assert response.status_code == 200
    codes = {row["code"] for row in response.json()}
    assert CASH in codes
    assert SALES_REVENUE in codes


@pytest.mark.asyncio
async def test_balance_endpoint_reports_a_derived_balance(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _stock(api_session, fixture)
    await _sale(
        api_session,
        fixture,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )
    accounts = await _accounts(api_session, fixture.shop.id)

    response = await api_client.get(
        f"/accounts/{accounts[SALES_REVENUE].id}/balance",
        headers=_headers(fixture.shop.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert Decimal(str(payload["balance"])) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_ledger_endpoint_returns_entries(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _stock(api_session, fixture)
    await _sale(
        api_session,
        fixture,
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )
    accounts = await _accounts(api_session, fixture.shop.id)

    response = await api_client.get(
        f"/accounts/{accounts[CASH].id}/ledger",
        headers=_headers(fixture.shop.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_entries"] == 1
    assert Decimal(str(payload["closing_balance"])) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_endpoints_do_not_expose_another_shops_account(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    shop_a = await _make_shop(api_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(api_session, shop_name="Shop B", sku="B-1")
    accounts_b = await _accounts(api_session, shop_b.shop.id)

    response = await api_client.get(
        f"/accounts/{accounts_b[CASH].id}/balance",
        headers=_headers(shop_a.shop.id),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_accounts_endpoints_require_a_shop(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    accounts = await _accounts(api_session, fixture.shop.id)

    response = await api_client.get(f"/accounts/{accounts[CASH].id}/balance")

    assert response.status_code == 401
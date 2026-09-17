"""Expense domain tests (Step 10).

Covers the expense model and its immediate-payment accounting: creation and
validation, the category -> expense-account mapping, the Cash/Bank credit,
double-entry balance, atomicity, tenant isolation, the read model and the
`/expenses` API. A posted expense is immutable by design - there is no update
or delete route to test.

The invariant under test is the same one Step 8 established:
`SUM(debits) == SUM(credits)` for every posting group, with the expense side
always landing on the category's EXPENSE account and the asset side always
following the existing `PaymentMethod -> account` mapping.
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
    Expense,
    ExpenseCategory,
    LedgerEntry,
    PaymentMethod,
    Product,
    ProductType,
    ProductVariant,
    Shop,
    Supplier,
    Unit,
)
from app.services.accounting import (
    BANK,
    CASH,
    MAINTENANCE_EXPENSE,
    MARKETING_EXPENSE,
    OTHER_EXPENSE,
    RENT_EXPENSE,
    SALARIES_EXPENSE,
    SUPPLIES_EXPENSE,
    TRANSPORT_EXPENSE,
    UTILITIES_EXPENSE,
    ensure_system_accounts,
    get_account_balance,
)
from app.services.expenses import (
    ExpenseNotFoundError,
    InvalidExpenseAmountError,
    InvalidExpenseCategoryError,
    InvalidExpensePaymentMethodError,
    InvalidPaginationError,
    InvalidStatementRangeError,
    ShopNotFoundError,
    create_expense,
    get_expense,
    list_expenses,
)

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


async def _accounts(
    db_session: AsyncSession, shop_id: uuid.UUID
) -> dict[str, Account]:
    return await ensure_system_accounts(db_session, shop_id=shop_id)


async def _expense(
    db_session: AsyncSession,
    fixture: ShopFixture,
    *,
    category: ExpenseCategory = ExpenseCategory.RENT,
    amount: str = "10000",
    method: PaymentMethod = PaymentMethod.CASH,
    description: str | None = None,
) -> Expense:
    return await create_expense(
        db_session,
        shop_id=fixture.shop.id,
        category=category,
        amount=Decimal(amount),
        payment_method=method,
        description=description,
    )


async def _entries_for(
    db_session: AsyncSession, shop_id: uuid.UUID, reference_id: uuid.UUID
) -> list[LedgerEntry]:
    return list(
        (
            await db_session.execute(
                sa.select(LedgerEntry).where(
                    LedgerEntry.shop_id == shop_id,
                    LedgerEntry.reference_id == reference_id,
                )
            )
        ).scalars()
    )


def _totals(entries) -> tuple[Decimal, Decimal]:
    debit = sum((Decimal(e.debit) for e in entries), start=Decimal("0.00"))
    credit = sum((Decimal(e.credit) for e in entries), start=Decimal("0.00"))
    return debit, credit


def _headers(shop_id: uuid.UUID) -> dict[str, str]:
    return {"X-Shop-Id": str(shop_id)}


# --------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_expense_persists_the_row(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)

    expense = await _expense(
        db_session,
        fixture,
        category=ExpenseCategory.SALARY,
        amount="25000.50",
        description="September salaries",
    )

    stored = await get_expense(
        db_session, shop_id=fixture.shop.id, expense_id=expense.id
    )
    assert stored.shop_id == fixture.shop.id
    assert stored.category is ExpenseCategory.SALARY
    assert stored.amount == Decimal("25000.50")
    assert stored.payment_method is PaymentMethod.CASH
    assert stored.description == "September salaries"


@pytest.mark.asyncio
async def test_expense_amount_is_decimal_and_normalised(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    expense = await _expense(db_session, fixture, amount="100.005")

    assert expense.amount == Decimal("100.01")
    assert isinstance(expense.amount, Decimal)


@pytest.mark.asyncio
async def test_expense_amount_must_be_positive(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)

    with pytest.raises(InvalidExpenseAmountError):
        await _expense(db_session, fixture, amount="0")

    with pytest.raises(InvalidExpenseAmountError):
        await _expense(db_session, fixture, amount="-5")


@pytest.mark.asyncio
async def test_expense_category_is_validated(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)

    with pytest.raises(InvalidExpenseCategoryError):
        await _expense(db_session, fixture, category="not_a_category")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_expense_payment_method_is_validated(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    with pytest.raises(InvalidExpensePaymentMethodError):
        await _expense(db_session, fixture, method="bitcoin")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_expense_requires_an_existing_shop(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(ShopNotFoundError):
        await create_expense(
            db_session,
            shop_id=uuid.uuid4(),
            category=ExpenseCategory.RENT,
            amount=Decimal("100"),
            payment_method=PaymentMethod.CASH,
        )


@pytest.mark.asyncio
async def test_expense_amount_constraint_is_enforced_by_the_database(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    db_session.add(
        Expense(
            shop_id=fixture.shop.id,
            category=ExpenseCategory.OTHER,
            amount=Decimal("0.00"),
            payment_method=PaymentMethod.CASH,
            expense_date=BASE_DATE,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_expense_category_is_enforced_by_the_database(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    with pytest.raises(sa.exc.DBAPIError):
        await db_session.execute(
            sa.text(
                "INSERT INTO expenses (id, shop_id, category, amount, "
                "payment_method, expense_date) VALUES (:id, :shop_id, "
                ":category, :amount, :method, now())"
            ).params(
                id=uuid.uuid4(),
                shop_id=fixture.shop.id,
                category="not_a_category",
                amount=Decimal("10.00"),
                method="cash",
            )
        )


# --------------------------------------------------------------------------
# Accounting
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cash_expense_debits_expense_and_credits_cash(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    expense = await _expense(db_session, fixture, amount="10000")

    accounts = await _accounts(db_session, fixture.shop.id)
    rent = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[RENT_EXPENSE].id
    )
    cash = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[CASH].id
    )

    assert rent.balance == Decimal("10000.00")
    assert cash.balance == Decimal("-10000.00")

    entries = await _entries_for(db_session, fixture.shop.id, expense.id)
    debit, credit = _totals(entries)
    assert debit == credit == Decimal("10000.00")

    debit_line = next(e for e in entries if Decimal(e.debit) > 0)
    credit_line = next(e for e in entries if Decimal(e.credit) > 0)
    assert debit_line.account_id == accounts[RENT_EXPENSE].id
    assert credit_line.account_id == accounts[CASH].id


@pytest.mark.asyncio
async def test_bank_expense_credits_bank(db_session: AsyncSession) -> None:
    fixture = await _make_shop(db_session)
    expense = await _expense(
        db_session,
        fixture,
        category=ExpenseCategory.UTILITIES,
        amount="7500",
        method=PaymentMethod.BANK,
    )

    accounts = await _accounts(db_session, fixture.shop.id)
    utilities = await get_account_balance(
        db_session,
        shop_id=fixture.shop.id,
        account_id=accounts[UTILITIES_EXPENSE].id,
    )
    bank = await get_account_balance(
        db_session, shop_id=fixture.shop.id, account_id=accounts[BANK].id
    )

    assert utilities.balance == Decimal("7500.00")
    assert bank.balance == Decimal("-7500.00")

    entries = await _entries_for(db_session, fixture.shop.id, expense.id)
    credit_line = next(e for e in entries if Decimal(e.credit) > 0)
    assert credit_line.account_id == accounts[BANK].id


@pytest.mark.asyncio
async def test_each_category_maps_to_its_expense_account(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    accounts = await _accounts(db_session, fixture.shop.id)

    expected = {
        ExpenseCategory.RENT: RENT_EXPENSE,
        ExpenseCategory.SALARY: SALARIES_EXPENSE,
        ExpenseCategory.UTILITIES: UTILITIES_EXPENSE,
        ExpenseCategory.TRANSPORT: TRANSPORT_EXPENSE,
        ExpenseCategory.MARKETING: MARKETING_EXPENSE,
        ExpenseCategory.MAINTENANCE: MAINTENANCE_EXPENSE,
        ExpenseCategory.SUPPLIES: SUPPLIES_EXPENSE,
        ExpenseCategory.OTHER: OTHER_EXPENSE,
    }

    for category, code in expected.items():
        expense = await _expense(
            db_session, fixture, category=category, amount="100"
        )
        entries = await _entries_for(db_session, fixture.shop.id, expense.id)
        debit_line = next(e for e in entries if Decimal(e.debit) > 0)
        assert debit_line.account_id == accounts[code].id


@pytest.mark.asyncio
async def test_expense_is_idempotent_when_posted_twice(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)
    expense = await _expense(db_session, fixture, amount="1000")

    before = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.shop_id == fixture.shop.id)
        )
    ).scalar_one()

    from app.services import accounting as accounting_service

    await accounting_service.post_expense(db_session, expense=expense)

    after = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.shop_id == fixture.shop.id)
        )
    ).scalar_one()

    assert before == after == 2


# --------------------------------------------------------------------------
# Atomicity
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expense_rolls_back_when_posting_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = await _make_shop(db_session)

    async def _boom(*args, **kwargs):
        raise RuntimeError("expense posting failed")

    monkeypatch.setattr("app.services.accounting.post_expense", _boom, raising=True)

    with pytest.raises(RuntimeError):
        await _expense(db_session, fixture, amount="500")

    await db_session.rollback()

    expenses = (
        await db_session.execute(
            sa.select(sa.func.count()).select_from(Expense)
        )
    ).scalar_one()
    accounts = (
        await db_session.execute(
            sa.select(sa.func.count()).select_from(Account)
        )
    ).scalar_one()
    assert expenses == 0
    assert accounts == 0


# --------------------------------------------------------------------------
# Tenant isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shop_a_cannot_read_shop_b_expense(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    expense_b = await _expense(db_session, shop_b, amount="100")

    with pytest.raises(ExpenseNotFoundError):
        await get_expense(
            db_session, shop_id=shop_a.shop.id, expense_id=expense_b.id
        )


@pytest.mark.asyncio
async def test_expense_listing_is_tenant_scoped(db_session: AsyncSession) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    await _expense(db_session, shop_b, amount="100")

    page = await list_expenses(db_session, shop_id=shop_a.shop.id)

    assert page.total == 0
    assert page.expenses == ()


@pytest.mark.asyncio
async def test_expense_ledger_posting_is_tenant_isolated(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(db_session, shop_name="Shop B", sku="B-1")
    await _expense(db_session, shop_b, amount="100")

    accounts_a = await _accounts(db_session, shop_a.shop.id)
    rent_a = await get_account_balance(
        db_session,
        shop_id=shop_a.shop.id,
        account_id=accounts_a[RENT_EXPENSE].id,
    )
    cash_a = await get_account_balance(
        db_session, shop_id=shop_a.shop.id, account_id=accounts_a[CASH].id
    )

    assert rent_a.balance == Decimal("0.00")
    assert cash_a.balance == Decimal("0.00")


# --------------------------------------------------------------------------
# Read model
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_expenses_is_newest_first_and_paged(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    for day, amount in ((1, "100"), (2, "200"), (3, "300")):
        expense = await _expense(db_session, fixture, amount=amount)
        expense.expense_date = _day(day)
    await db_session.flush()

    page = await list_expenses(
        db_session, shop_id=fixture.shop.id, limit=2, offset=0
    )

    assert page.total == 3
    assert [e.amount for e in page.expenses] == [
        Decimal("300.00"),
        Decimal("200.00"),
    ]

    windowed = await list_expenses(
        db_session,
        shop_id=fixture.shop.id,
        start_date=_day(2),
        end_date=_day(3),
    )
    assert windowed.total == 2


@pytest.mark.asyncio
async def test_list_expenses_validates_arguments(
    db_session: AsyncSession,
) -> None:
    fixture = await _make_shop(db_session)

    with pytest.raises(InvalidStatementRangeError):
        await list_expenses(
            db_session,
            shop_id=fixture.shop.id,
            start_date=_day(10),
            end_date=_day(1),
        )

    with pytest.raises(InvalidPaginationError):
        await list_expenses(db_session, shop_id=fixture.shop.id, offset=-1)

    with pytest.raises(InvalidPaginationError):
        await list_expenses(db_session, shop_id=fixture.shop.id, limit=0)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_expense_endpoint(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)

    response = await api_client.post(
        "/expenses",
        headers=_headers(fixture.shop.id),
        json={
            "category": "rent",
            "amount": "30000.00",
            "payment_method": "cash",
            "description": "September rent",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["category"] == "rent"
    assert Decimal(str(payload["amount"])) == Decimal("30000.00")
    assert payload["shop_id"] == str(fixture.shop.id)


@pytest.mark.asyncio
async def test_create_expense_endpoint_rejects_bad_amount(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)

    response = await api_client.post(
        "/expenses",
        headers=_headers(fixture.shop.id),
        json={
            "category": "rent",
            "amount": "0",
            "payment_method": "cash",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_and_read_expense_endpoints(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    expense = await _expense(api_session, fixture, amount="5000")

    listing = await api_client.get(
        "/expenses", headers=_headers(fixture.shop.id)
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    read = await api_client.get(
        f"/expenses/{expense.id}", headers=_headers(fixture.shop.id)
    )
    assert read.status_code == 200
    assert Decimal(str(read.json()["amount"])) == Decimal("5000.00")


@pytest.mark.asyncio
async def test_expense_endpoints_do_not_expose_another_shops_expense(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    shop_a = await _make_shop(api_session, shop_name="Shop A", sku="A-1")
    shop_b = await _make_shop(api_session, shop_name="Shop B", sku="B-1")
    expense_b = await _expense(api_session, shop_b, amount="100")

    response = await api_client.get(
        f"/expenses/{expense_b.id}", headers=_headers(shop_a.shop.id)
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_expense_endpoints_require_a_shop(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    response = await api_client.get("/expenses")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_expenses_have_no_delete_route(
    api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    expense = await _expense(api_session, fixture, amount="100")

    response = await api_client.delete(
        f"/expenses/{expense.id}", headers=_headers(fixture.shop.id)
    )

    assert response.status_code == 405
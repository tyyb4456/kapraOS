"""Step 3: AI-assisted sale creation — one HITL-gated mutation.

Covers the brief's minimum without any real LLM (deterministic fake
models, direct tool calls — no network, no tokens):

* preparation: customer/product resolution, ambiguity, invalid inputs
* HITL: pause before mutation, approve executes, reject is a no-op,
  edit executes the corrected args
* transaction: success commits atomically, failure leaves no partial
  state (savepoint-scoped, never a full-session rollback)
* idempotency: same operation key twice (in-session and post-commit)
  creates exactly one sale; a failed attempt does not poison its key
* tenant isolation: foreign-shop IDs/names are rejected
* service reuse: the AI path calls ``SaleService.create_sale()``
* HTTP: the mutation stays behind ``/ai/chat`` + ``/ai/chat/resume``
"""

import inspect
import uuid
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import (
    SALE_HITL_INTERRUPT_CONFIG,
    SALE_SYSTEM_ADDENDUM,
    WRITE_MASTER_TOOL_NAMES,
    approve_decision,
    build_master_agent,
    hitl_resume_payload,
    reject_decision,
)
from app.ai.state import TenantContext
from app.ai.tools.sales_write import (
    CREATE_SALE_TOOL_NAME,
    WRITE_TOOL_NAMES,
    SalePrepAmbiguousError,
    SalePrepError,
    SalePrepNotFoundError,
    assert_sale_write_registry_is_minimal,
    build_sale_preview,
    build_sale_write_tools,
    parse_payment_kind,
    parse_payment_method,
    parse_quantity,
    resolve_customer,
    resolve_variant,
    validate_idempotency_key,
)
from app.models import (
    AISaleReceipt,
    Category,
    Customer,
    LedgerEntry,
    Payment,
    PaymentMethod,
    Product,
    ProductType,
    ProductVariant,
    Sale,
    Shop,
    Supplier,
    Unit,
)
from app.services import receivables as receivables_service
from app.services import sales as sales_service
from app.services.inventory import get_or_create_inventory
from app.services.purchases import PurchaseItemInput, create_purchase


# --- Fixtures ------------------------------------------------------------
class SaleFixture:
    def __init__(
        self,
        shop: Shop,
        variant: ProductVariant,
        supplier: Supplier,
        customer: Customer,
    ) -> None:
        self.shop = shop
        self.variant = variant
        self.supplier = supplier
        self.customer = customer


async def _make_sale_shop(
    db: AsyncSession,
    name: str = "AI Sale Shop",
    *,
    customer_name: str = "Ali",
    stock: str = "20",
) -> SaleFixture:
    shop = Shop(name=f"{name} {uuid.uuid4().hex[:6]}")
    db.add(shop)
    await db.flush()
    category = Category(shop_id=shop.id, shop=shop, name="Lawn")
    db.add(category)
    await db.flush()
    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Black Lawn",
        product_type=ProductType.OPEN_FABRIC,
    )
    db.add(product)
    await db.flush()
    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku=f"BL-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("500.00"),
        selling_price=Decimal("800.00"),
        unit=Unit.METER,
    )
    supplier = Supplier(shop_id=shop.id, name="Al-Madina")
    customer = Customer(shop_id=shop.id, name=customer_name)
    db.add_all([variant, supplier, customer])
    await db.flush()
    await create_purchase(
        db,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=variant.id,
                quantity=Decimal(stock),
                unit_cost=Decimal(500),
            )
        ],
    )
    return SaleFixture(shop, variant, supplier, customer)


def _ctx(fix: SaleFixture) -> TenantContext:
    return TenantContext(shop_id=fix.shop.id, user_id=uuid.uuid4())


def _write_tool(db: AsyncSession, fix: SaleFixture) -> Any:
    tools = {t.name: t for t in build_sale_write_tools(db, _ctx(fix))}
    return tools[CREATE_SALE_TOOL_NAME]


def _key() -> str:
    return uuid.uuid4().hex


async def _counts(db: AsyncSession, fix: SaleFixture) -> dict[str, Any]:
    sales = (
        await db.execute(select(func.count(Sale.id)).where(Sale.shop_id == fix.shop.id))
    ).scalar_one()
    receipts = (
        await db.execute(
            select(func.count(AISaleReceipt.id)).where(
                AISaleReceipt.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    ledger = (
        await db.execute(
            select(func.count(LedgerEntry.id)).where(LedgerEntry.shop_id == fix.shop.id)
        )
    ).scalar_one()
    payments = (
        await db.execute(
            select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
        )
    ).scalar_one()
    inv = await get_or_create_inventory(
        db, shop_id=fix.shop.id, variant_id=fix.variant.id
    )
    return {
        "sales": sales,
        "receipts": receipts,
        "ledger": ledger,
        "payments": payments,
        "stock": inv.quantity,
    }


async def _outstanding(
    db: AsyncSession, fix: SaleFixture, customer: Customer | None = None
) -> Decimal:
    customer = customer or fix.customer
    summary = await receivables_service.get_customer_summary(
        db, shop_id=fix.shop.id, customer_id=customer.id
    )
    return summary.outstanding_balance


# --- Registry boundary: exactly one mutation -----------------------------


def test_write_registry_is_exactly_one_tool() -> None:
    assert WRITE_TOOL_NAMES == ("create_sale",)
    assert WRITE_MASTER_TOOL_NAMES == ("create_sale",)
    assert SALE_HITL_INTERRUPT_CONFIG == {"create_sale": True}


def test_sale_prompt_forces_same_turn_tool_call() -> None:
    """The prompt must order preview + tool call in ONE turn.

    Regression guard for the live bug where the model wrote a text
    preview ("Kya yeh theek hai?") and ended its turn WITHOUT calling
    `create_sale` — so no interrupt fired and no approval card appeared.
    The card only exists when the tool is actually called.
    """
    lowered = SALE_SYSTEM_ADDENDUM.lower()
    assert "same turn" in lowered
    assert "without calling the tool" in lowered or "without calling" in lowered
    assert "approval card" in lowered
    # Confirmations of a previous preview must go straight to the tool.
    assert "haan" in lowered and "directly" in lowered


@pytest.mark.asyncio
async def test_write_tool_takes_no_tenant_or_ledger_args(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session)
    tools = build_sale_write_tools(db_session, _ctx(fix))
    assert_sale_write_registry_is_minimal(tools)
    assert len(tools) == 1
    func_attr = getattr(tools[0], "func", None) or getattr(tools[0], "coroutine", None)
    assert func_attr is not None
    params = inspect.signature(func_attr).parameters
    for forbidden in (
        "shop_id",
        "shop",
        "user_id",
        "account_id",
        "inventory_id",
        "ledger_entry_id",
    ):
        assert forbidden not in params, f"create_sale must not take {forbidden}"


def test_write_registry_rejects_a_second_mutation() -> None:
    from app.ai.tools.registry import demo_prepare_operation

    with pytest.raises(AssertionError, match="one mutation only"):
        assert_sale_write_registry_is_minimal(
            [demo_prepare_operation]  # type: ignore[list-item]
        )


@pytest.mark.asyncio
async def test_write_tools_require_a_checkpointer(
    db_session: AsyncSession,
) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    fix = await _make_sale_shop(db_session)
    with pytest.raises(ValueError, match="checkpointer"):
        build_master_agent(
            model=GenericFakeChatModel(messages=iter(["x"])),
            checkpointer=None,
            include_hitl_demo=False,
            write_tools=build_sale_write_tools(db_session, _ctx(fix)),
        )


# --- Pure preparation helpers --------------------------------------------


@pytest.mark.asyncio
async def test_resolve_customer_unique_walkin_and_by_id(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session)
    shop_id = fix.shop.id
    assert await resolve_customer(db_session, shop_id) is None  # walk-in
    one = await resolve_customer(db_session, shop_id, customer_name="Ali")
    assert one is not None and one.id == fix.customer.id
    by_id = await resolve_customer(
        db_session, shop_id, customer_id=str(fix.customer.id)
    )
    assert by_id is not None and by_id.id == fix.customer.id
    with pytest.raises(SalePrepNotFoundError):
        await resolve_customer(db_session, shop_id, customer_name="Nobody")
    with pytest.raises(SalePrepError):
        await resolve_customer(
            db_session,
            shop_id,
            customer_id=str(fix.customer.id),
            customer_name="Ali",
        )


@pytest.mark.asyncio
async def test_resolve_customer_ambiguous_never_guesses(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session)
    db_session.add(Customer(shop_id=fix.shop.id, name="Ali Raza"))
    await db_session.flush()
    with pytest.raises(SalePrepAmbiguousError) as exc_info:
        await resolve_customer(db_session, fix.shop.id, customer_name="Ali")
    assert len(exc_info.value.matches) == 2
    assert "shop_id" not in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_resolve_variant_paths(db_session: AsyncSession) -> None:
    fix = await _make_sale_shop(db_session)
    shop_id = fix.shop.id
    by_name = await resolve_variant(db_session, shop_id, product_name="Black Lawn")
    assert by_name.variant.id == fix.variant.id
    assert by_name.product_name == "Black Lawn"
    by_sku = await resolve_variant(db_session, shop_id, variant_sku=fix.variant.sku)
    assert by_sku.variant.id == fix.variant.id
    by_id = await resolve_variant(db_session, shop_id, variant_id=str(fix.variant.id))
    assert by_id.variant.id == fix.variant.id
    with pytest.raises(SalePrepNotFoundError):
        await resolve_variant(db_session, shop_id, product_name="Nope")
    with pytest.raises(SalePrepError):
        await resolve_variant(db_session, shop_id)  # nothing to resolve
    with pytest.raises(SalePrepError):
        await resolve_variant(
            db_session,
            shop_id,
            variant_id=str(fix.variant.id),
            product_name="Black Lawn",
        )


@pytest.mark.asyncio
async def test_resolve_variant_ambiguous_and_inactive(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session)
    category_id = (
        (
            await db_session.execute(
                select(Category).where(Category.shop_id == fix.shop.id).limit(1)
            )
        )
        .scalar_one()
        .id
    )
    other = Product(
        shop_id=fix.shop.id,
        category_id=category_id,
        name="Black Lawn Premium",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(other)
    await db_session.flush()
    with pytest.raises(SalePrepAmbiguousError):
        await resolve_variant(db_session, fix.shop.id, product_name="Black Lawn")

    fix.variant.is_active = False
    await db_session.flush()
    with pytest.raises(SalePrepError, match="inactive"):
        await resolve_variant(db_session, fix.shop.id, variant_id=str(fix.variant.id))


def test_parse_helpers() -> None:
    assert parse_quantity("3") == Decimal("3.000")
    assert parse_quantity(2) == Decimal("2.000")
    for bad in ("0", "-1", "abc", "", "nan", "inf"):
        with pytest.raises(SalePrepError):
            parse_quantity(bad)
    assert parse_payment_kind("cash") == "cash"
    assert parse_payment_kind("Nagad") == "cash"
    assert parse_payment_kind("udhaar") == "credit"
    assert parse_payment_kind("KHATA") == "credit"
    assert parse_payment_kind("baqi") == "credit"
    with pytest.raises(SalePrepError):
        parse_payment_kind("jazzcash-udhaar")
    assert parse_payment_method("cash") == PaymentMethod.CASH
    assert parse_payment_method("JazzCash") == PaymentMethod.JAZZCASH
    with pytest.raises(SalePrepError):
        parse_payment_method("bitcoin")
    assert validate_idempotency_key(uuid.uuid4().hex) is not None
    bad_key: Any
    for bad_key in ("", "short", "has space", "semi;colon", None, "x" * 65):
        with pytest.raises(SalePrepError):
            validate_idempotency_key(bad_key)


def test_sale_preview_text() -> None:
    text = build_sale_preview(
        customer_name="Ali",
        product_name="Black Lawn",
        variant_sku="BL-1",
        unit="meter",
        quantity=Decimal(3),
        unit_price=Decimal(1800),
        payment_kind="credit",
        paid_amount=Decimal(0),
        due_amount=Decimal(5400),
    )
    assert "Ali" in text and "Black Lawn" in text
    assert "3.000" in text and "5400.00" in text and "Udhaar" in text
    assert "Do you want me to record this sale?" in text


# --- Direct tool execution ------------------------------------------------


@pytest.mark.asyncio
async def test_tool_credit_sale_uses_catalog_price(db_session: AsyncSession) -> None:
    fix = await _make_sale_shop(db_session)
    before = await _counts(db_session, fix)
    out = await _write_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "product_name": "Black Lawn",
            "quantity": "3",
            "payment_kind": "udhaar",
        }
    )
    assert out["status"] == "completed"
    assert out["duplicate"] is False
    assert out["customer_name"] == "Ali"
    assert out["total"] == "2400.00"  # 3 x catalog 800
    assert out["paid_amount"] == "0.00"
    assert out["due_amount"] == "2400.00"
    assert out["payment_status"] == "partial"
    assert out["invoice_number"] and out["invoice_number"].startswith("INV-")
    after = await _counts(db_session, fix)
    assert after["sales"] == before["sales"] + 1
    assert after["receipts"] == before["receipts"] + 1
    assert after["stock"] == before["stock"] - Decimal(3)
    assert after["payments"] == before["payments"]  # credit: no payment rows
    assert after["ledger"] > before["ledger"]  # SALE + COGS postings
    assert await _outstanding(db_session, fix) == Decimal("2400.00")


@pytest.mark.asyncio
async def test_tool_cash_walkin_sale(db_session: AsyncSession) -> None:
    fix = await _make_sale_shop(db_session)
    out = await _write_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "product_name": "Black Lawn",
            "quantity": "2",
            "payment_kind": "cash",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert out["customer_name"] is None  # walk-in
    assert out["total"] == "1600.00"
    assert out["paid_amount"] == "1600.00"
    assert out["due_amount"] == "0.00"
    assert out["payment_status"] == "completed"
    sale = await db_session.get(Sale, uuid.UUID(out["sale_id"]))
    assert sale is not None and sale.customer_id is None


@pytest.mark.asyncio
async def test_tool_explicit_price_and_partial_payment(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session)
    out = await _write_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "product_name": "Black Lawn",
            "quantity": "2",
            "unit_price": "1000",
            "payment_kind": "cash",
            "payment_method": "jazzcash",
            "paid_amount": "500",
        }
    )
    assert out["status"] == "completed"
    assert out["unit_price"] == "1000.00"
    assert out["total"] == "2000.00"
    assert out["paid_amount"] == "500.00"
    assert out["due_amount"] == "1500.00"


@pytest.mark.asyncio
async def test_tool_invalid_inputs_change_nothing(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session)
    before = await _counts(db_session, fix)
    tool = _write_tool(db_session, fix)
    bad_calls = [
        {"quantity": "0"},
        {"quantity": "-2"},
        {"quantity": "lots"},
        {"payment_kind": "bitcoin"},
        {"payment_method": "bitcoin"},
        {"payment_kind": "credit", "paid_amount": "100"},
        {"payment_kind": "cash", "paid_amount": "999999"},
        {"idempotency_key": "short"},
        {"customer_name": "Nobody Here"},
        {"product_name": "No Such Fabric"},
        {
            "customer_name": "Ali",
            "customer_id": str(fix.customer.id),
        },
        {
            "product_name": "Black Lawn",
            "variant_id": str(fix.variant.id),
        },
        {"unit_price": "-5"},
    ]
    for extra in bad_calls:
        args: dict[str, Any] = {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "product_name": "Black Lawn",
            "quantity": "1",
            "payment_kind": "credit",
        }
        args.update(extra)
        out = await tool.ainvoke(args)
        assert out["status"] in ("error", "ambiguous", "not_found"), args
        assert "traceback" not in out["message"].lower()
        assert "shop_id" not in out["message"]
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_tool_ambiguous_names_never_execute(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session)
    db_session.add(Customer(shop_id=fix.shop.id, name="Ali Raza"))
    await db_session.flush()
    before = await _counts(db_session, fix)
    out = await _write_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "product_name": "Black Lawn",
            "quantity": "1",
            "payment_kind": "credit",
        }
    )
    assert out["status"] == "ambiguous"
    assert len(out["matches"]) == 2
    assert await _counts(db_session, fix) == before


# --- Stock failure: no partial state --------------------------------------


@pytest.mark.asyncio
async def test_tool_insufficient_stock_rolls_back_cleanly(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session, stock="2")
    good = await _write_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "product_name": "Black Lawn",
            "quantity": "1",
            "payment_kind": "credit",
        }
    )
    assert good["status"] == "completed"
    before = await _counts(db_session, fix)
    assert before["stock"] == Decimal("1.000")
    out = await _write_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "product_name": "Black Lawn",
            "quantity": "5",
            "payment_kind": "credit",
        }
    )
    assert out["status"] == "error"
    assert out["code"] == "insufficient_stock"
    # No partial sale: the earlier good sale is intact, nothing new exists.
    assert await _counts(db_session, fix) == before
    assert await _outstanding(db_session, fix) == Decimal("800.00")


# --- Idempotency ------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_duplicate_key_executes_once(db_session: AsyncSession) -> None:
    fix = await _make_sale_shop(db_session)
    tool = _write_tool(db_session, fix)
    key = _key()
    args = {
        "idempotency_key": key,
        "customer_name": "Ali",
        "product_name": "Black Lawn",
        "quantity": "2",
        "payment_kind": "credit",
    }
    first = await tool.ainvoke(args)
    assert first["status"] == "completed" and first["duplicate"] is False
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["sale_id"] == first["sale_id"]
    assert second["total"] == first["total"]
    sales = (
        await db_session.execute(
            select(func.count(Sale.id)).where(Sale.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert sales == 1


@pytest.mark.asyncio
async def test_tool_duplicate_key_safe_after_commit(
    api_session: AsyncSession,
) -> None:
    # Uses `api_session` (outer-transaction fixture) on purpose: the
    # commit below stays inside the test's outer transaction, so the
    # teardown rollback discards it — a plain `db_session` commit would
    # leak rows into the shared test database and break other suites
    # that assert globally-empty tables.
    db_session = api_session
    fix = await _make_sale_shop(db_session)
    tool = _write_tool(db_session, fix)
    key = _key()
    args = {
        "idempotency_key": key,
        "product_name": "Black Lawn",
        "quantity": "1",
        "payment_kind": "cash",
    }
    first = await tool.ainvoke(args)
    assert first["status"] == "completed"
    await db_session.commit()  # simulate the resume-request commit
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["sale_id"] == first["sale_id"]
    sales = (
        await db_session.execute(
            select(func.count(Sale.id)).where(Sale.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert sales == 1
    # Distinct keys are distinct sales.
    third = await tool.ainvoke({**args, "idempotency_key": _key()})
    assert third["duplicate"] is False
    sales = (
        await db_session.execute(
            select(func.count(Sale.id)).where(Sale.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert sales == 2


@pytest.mark.asyncio
async def test_tool_failed_attempt_does_not_poison_key(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session, stock="1")
    tool = _write_tool(db_session, fix)
    key = _key()
    failed = await tool.ainvoke(
        {
            "idempotency_key": key,
            "product_name": "Black Lawn",
            "quantity": "9",
            "payment_kind": "credit",
        }
    )
    assert failed["status"] == "error"
    retry = await tool.ainvoke(
        {
            "idempotency_key": key,
            "product_name": "Black Lawn",
            "quantity": "1",
            "payment_kind": "credit",
        }
    )
    assert retry["status"] == "completed" and retry["duplicate"] is False
    sales = (
        await db_session.execute(
            select(func.count(Sale.id)).where(Sale.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert sales == 1


# --- Tenant isolation -------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_rejects_foreign_shop_ids(db_session: AsyncSession) -> None:
    shop_a = await _make_sale_shop(db_session, name="Shop A")
    shop_b = await _make_sale_shop(db_session, name="Shop B")
    before_a = await _counts(db_session, shop_a)
    before_b = await _counts(db_session, shop_b)
    out = await _write_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_id": str(shop_b.customer.id),
            "variant_id": str(shop_b.variant.id),
            "quantity": "1",
            "payment_kind": "credit",
        }
    )
    assert out["status"] in ("error", "not_found")
    assert await _counts(db_session, shop_a) == before_a
    assert await _counts(db_session, shop_b) == before_b


@pytest.mark.asyncio
async def test_tool_cannot_see_other_shop_names(db_session: AsyncSession) -> None:
    shop_a = await _make_sale_shop(db_session, name="Shop A")
    shop_b = await _make_sale_shop(db_session, name="Shop B", customer_name="Only In B")
    out = await _write_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Only In B",
            "product_name": "Black Lawn",
            "quantity": "1",
            "payment_kind": "credit",
        }
    )
    assert out["status"] == "not_found"
    assert (await _counts(db_session, shop_a))["sales"] == 0
    # Sanity: the name resolves fine inside its own shop.
    ok = await _write_tool(db_session, shop_b).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Only In B",
            "product_name": "Black Lawn",
            "quantity": "1",
            "payment_kind": "credit",
        }
    )
    assert ok["status"] == "completed"


# --- Authoritative service reuse -------------------------------------------


@pytest.mark.asyncio
async def test_ai_path_calls_sale_service_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fix = await _make_sale_shop(db_session)
    calls: list[dict[str, Any]] = []
    real_create = sales_service.create_sale

    async def _spy(session: AsyncSession, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await real_create(session, **kwargs)

    monkeypatch.setattr(sales_service, "create_sale", _spy)
    out = await _write_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "product_name": "Black Lawn",
            "quantity": "2",
            "payment_kind": "credit",
        }
    )
    assert out["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["shop_id"] == fix.shop.id
    assert calls[0]["customer_id"] == fix.customer.id
    assert len(calls[0]["items"]) == 1
    assert calls[0]["items"][0].quantity == Decimal("2.000")
    assert calls[0]["items"][0].unit_price == Decimal("800.00")
    assert calls[0]["payments"] == []


@pytest.mark.asyncio
async def test_ai_sale_posts_balanced_ledger_and_cogs(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session)
    out = await _write_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "product_name": "Black Lawn",
            "quantity": "2",
            "payment_kind": "credit",
        }
    )
    sale_id = uuid.UUID(out["sale_id"])
    refs = (
        (
            await db_session.execute(
                select(LedgerEntry.reference_type).where(
                    LedgerEntry.shop_id == fix.shop.id,
                    LedgerEntry.reference_id == sale_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert "SALE" in refs and "SALE_COGS" in refs
    rows = (
        await db_session.execute(
            select(LedgerEntry.debit, LedgerEntry.credit).where(
                LedgerEntry.shop_id == fix.shop.id,
                LedgerEntry.reference_id == sale_id,
            )
        )
    ).all()
    assert sum(r[0] for r in rows) == sum(r[1] for r in rows) > 0


# --- Agent-level HITL -------------------------------------------------------


def _sale_fake(args: dict[str, Any]) -> BaseChatModel:
    """Fake model: request ``create_sale`` once, then echo the result."""

    class _SaleToolFakeModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "sale-write-fake"

        def bind_tools(self, tools: Any, **kwargs: Any) -> "_SaleToolFakeModel":
            return self

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: CallbackManagerForLLMRun | None = None,
            **kwargs: Any,
        ) -> ChatResult:
            tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
            if tool_msgs:
                message = AIMessage(content=f"Sale recorded: {tool_msgs[-1].content}")
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_sale",
                            "args": dict(args),
                            "id": "call_sale_1",
                            "type": "tool_call",
                        }
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return _SaleToolFakeModel()


def _sale_args(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {
        "idempotency_key": _key(),
        "customer_name": "Ali",
        "product_name": "Black Lawn",
        "quantity": "3",
        "payment_kind": "credit",
    }
    if extra:
        args.update(extra)
    return args


@pytest.mark.asyncio
async def test_hitl_pause_then_approve_creates_one_sale(
    db_session: AsyncSession,
) -> None:
    fix = await _make_sale_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_sale_fake(_sale_args()),
        include_hitl_demo=False,
        write_tools=build_sale_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"sale-approve-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {
            "messages": [
                {"role": "user", "content": "Ali ko 3 meter black lawn udhaar de do"}
            ]
        },
        config=config,
        version="v2",
    )
    assert paused.interrupts
    action = paused.interrupts[0].value["action_requests"][0]
    assert action["name"] == "create_sale"
    assert action["args"]["customer_name"] == "Ali"
    # Paused BEFORE mutation: nothing written, nothing held.
    assert await _counts(db_session, fix) == before

    resumed = await agent.ainvoke(
        Command(resume=hitl_resume_payload([approve_decision()])),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    after = await _counts(db_session, fix)
    assert after["sales"] == before["sales"] + 1
    assert after["receipts"] == before["receipts"] + 1
    assert after["stock"] == before["stock"] - Decimal(3)
    assert await _outstanding(db_session, fix) == Decimal("2400.00")


@pytest.mark.asyncio
async def test_hitl_reject_creates_nothing(db_session: AsyncSession) -> None:
    fix = await _make_sale_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_sale_fake(_sale_args()),
        include_hitl_demo=False,
        write_tools=build_sale_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"sale-reject-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Ali ko sale de do"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    resumed = await agent.ainvoke(
        Command(
            resume=hitl_resume_payload(
                [reject_decision("User rejected the sale. Do not record anything.")]
            )
        ),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    # Rejection: zero mutation across sales, stock, khata, ledger.
    assert await _counts(db_session, fix) == before
    assert await _outstanding(db_session, fix) == Decimal("0.00")


@pytest.mark.asyncio
async def test_hitl_edit_executes_corrected_args(db_session: AsyncSession) -> None:
    fix = await _make_sale_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_sale_fake(_sale_args()),
        include_hitl_demo=False,
        write_tools=build_sale_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"sale-edit-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Ali ko sale de do"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    edited = _sale_args({"quantity": "2"})
    resumed = await agent.ainvoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {"name": "create_sale", "args": edited},
                    }
                ]
            }
        ),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    after = await _counts(db_session, fix)
    assert after["sales"] == before["sales"] + 1
    assert after["stock"] == before["stock"] - Decimal(2)
    assert await _outstanding(db_session, fix) == Decimal("1600.00")


# --- HTTP: mutation stays behind /ai/chat ----------------------------------


def _http_sale_fake() -> BaseChatModel:
    return _sale_fake(
        {
            "idempotency_key": f"httpsale{uuid.uuid4().hex[:24]}",
            "customer_name": "Ali",
            "product_name": "Black Lawn",
            "quantity": "1",
            "payment_kind": "credit",
        }
    )


@pytest.mark.asyncio
async def test_chat_endpoint_sale_pause_then_approve(
    mocked_api_client: Any, api_session: AsyncSession
) -> None:
    from app.api.ai import get_chat_model
    from app.main import app
    from app.models import Shop as ShopModel
    from app.models import User as UserModel
    from app.models.user import UserRole

    shop = ShopModel(name=f"HTTP Sale Shop {uuid.uuid4().hex[:6]}")
    api_session.add(shop)
    await api_session.flush()
    await api_session.refresh(shop)
    api_session.add(
        UserModel(
            clerk_user_id="mock_clerk_id",
            shop_id=shop.id,
            name="Seller",
            email=f"seller-{uuid.uuid4().hex[:6]}@example.com",
            role=UserRole.OWNER,
        )
    )
    await api_session.flush()
    category = Category(shop_id=shop.id, shop=shop, name="Lawn")
    api_session.add(category)
    await api_session.flush()
    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Black Lawn",
        product_type=ProductType.OPEN_FABRIC,
    )
    api_session.add(product)
    await api_session.flush()
    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku=f"HTTP-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("500.00"),
        selling_price=Decimal("800.00"),
        unit=Unit.METER,
    )
    supplier = Supplier(shop_id=shop.id, name="HTTP Supplier")
    customer = Customer(shop_id=shop.id, name="Ali")
    api_session.add_all([variant, supplier, customer])
    await api_session.flush()
    await create_purchase(
        api_session,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=variant.id,
                quantity=Decimal(10),
                unit_cost=Decimal(500),
            )
        ],
    )

    def _sale_count() -> Any:
        return api_session.execute(
            select(func.count(Sale.id)).where(Sale.shop_id == shop.id)
        )

    app.dependency_overrides[get_chat_model] = _http_sale_fake
    try:
        thread = uuid.uuid4().hex
        paused = await mocked_api_client.post(
            "/ai/chat",
            json={
                "message": "Ali ko 1 meter black lawn udhaar de do",
                "thread_id": thread,
            },
        )
        assert paused.status_code == 200
        body = paused.json()
        assert body["status"] == "paused"
        assert body["interrupts"][0]["name"] == "create_sale"
        assert (await _sale_count()).scalar_one() == 0

        resumed = await mocked_api_client.post(
            "/ai/chat/resume",
            json={"thread_id": thread, "decisions": [{"type": "approve"}]},
        )
        assert resumed.status_code == 200
        done = resumed.json()
        assert done["status"] == "done"
        assert (await _sale_count()).scalar_one() == 1
    finally:
        app.dependency_overrides.pop(get_chat_model, None)


@pytest.mark.asyncio
async def test_chat_endpoint_sale_edit_decision_contract(
    mocked_api_client: Any, api_session: AsyncSession
) -> None:
    """The exact payload shape the frontend edit-approval sends must work.

    The UI sends ``{"type": "edit", "edited_action": {name, full args}}``
    when the shopkeeper corrects quantity/price on the approval card;
    the backend must execute the corrected args (re-validated) exactly
    once.
    """
    from app.api.ai import get_chat_model
    from app.main import app
    from app.models import Shop as ShopModel
    from app.models import User as UserModel
    from app.models.user import UserRole

    shop = ShopModel(name=f"HTTP Edit Shop {uuid.uuid4().hex[:6]}")
    api_session.add(shop)
    await api_session.flush()
    await api_session.refresh(shop)
    api_session.add(
        UserModel(
            clerk_user_id="mock_clerk_id",
            shop_id=shop.id,
            name="Seller",
            email=f"seller-{uuid.uuid4().hex[:6]}@example.com",
            role=UserRole.OWNER,
        )
    )
    await api_session.flush()
    category = Category(shop_id=shop.id, shop=shop, name="Lawn")
    api_session.add(category)
    await api_session.flush()
    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Black Lawn",
        product_type=ProductType.OPEN_FABRIC,
    )
    api_session.add(product)
    await api_session.flush()
    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku=f"HTTP-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("500.00"),
        selling_price=Decimal("800.00"),
        unit=Unit.METER,
    )
    supplier = Supplier(shop_id=shop.id, name="HTTP Supplier")
    customer = Customer(shop_id=shop.id, name="Ali")
    api_session.add_all([variant, supplier, customer])
    await api_session.flush()
    await create_purchase(
        api_session,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=variant.id,
                quantity=Decimal(10),
                unit_cost=Decimal(500),
            )
        ],
    )

    app.dependency_overrides[get_chat_model] = _http_sale_fake
    try:
        thread = uuid.uuid4().hex
        paused = await mocked_api_client.post(
            "/ai/chat",
            json={"message": "Ali ko sale de do", "thread_id": thread},
        )
        assert paused.status_code == 200
        body = paused.json()
        assert body["status"] == "paused"
        original = body["interrupts"][0]
        assert original["name"] == "create_sale"

        # Frontend edit-approval payload: full replacement args with a
        # corrected quantity (the fake proposed quantity "1").
        edited_args = {**original["args"], "quantity": "2"}
        resumed = await mocked_api_client.post(
            "/ai/chat/resume",
            json={
                "thread_id": thread,
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {"name": "create_sale", "args": edited_args},
                    }
                ],
            },
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "done"
        sales = (
            (await api_session.execute(select(Sale).where(Sale.shop_id == shop.id)))
            .scalars()
            .all()
        )
        assert len(sales) == 1
        assert sales[0].total == Decimal("1600.00")  # corrected 2 x 800
        assert sales[0].due_amount == Decimal("1600.00")
    finally:
        app.dependency_overrides.pop(get_chat_model, None)

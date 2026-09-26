"""Step 2 read-only business tools: real shop data, tenant isolation, safety.

Covers the brief's minimum: sales / inventory / customers / suppliers /
purchases / expenses / catalog, plus explicit cross-tenant isolation,
mutation safety, clean errors, ambiguity, Decimal-safe money, and one
master-agent -> read-tool -> real-DB integration (fake model, no network).
"""

import inspect
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import READ_MASTER_TOOL_NAMES, build_master_agent
from app.ai.state import TenantContext
from app.ai.tools.business_reads import READ_TOOL_NAMES, build_read_tools
from app.ai.tools.registry import (
    assert_no_db_mutation_tools,
    get_master_tools,
    get_read_tool_names,
)
from app.models import (
    Category,
    Customer,
    Expense,
    ExpenseCategory,
    PaymentMethod,
    Product,
    ProductType,
    ProductVariant,
    Purchase,
    Sale,
    Shop,
    Supplier,
    Unit,
)
from app.services.expenses import create_expense
from app.services.purchases import PurchaseItemInput, create_purchase
from app.services.sales import PaymentInput, SaleItemInput, create_sale


def _today_str() -> str:
    return datetime.now(UTC).date().isoformat()


async def _make_shop(
    db: AsyncSession,
    name: str = "Read Shop",
) -> tuple[Shop, Category, Product, ProductVariant, Supplier]:
    shop = Shop(name=name)
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
    db.add_all([variant, supplier])
    await db.flush()
    return shop, category, product, variant, supplier


def _ctx(shop: Shop) -> TenantContext:
    return TenantContext(shop_id=shop.id, user_id=uuid.uuid4())


def _tools(db: AsyncSession, shop: Shop) -> dict[str, Any]:
    return {t.name: t for t in build_read_tools(db, _ctx(shop))}


# --- Registry boundary -----------------------------------------------------


def test_read_registry_is_small_explicit_and_safe() -> None:
    assert READ_TOOL_NAMES == (
        "get_sales_summary",
        "get_inventory_status",
        "get_customer_account_summary",
        "get_supplier_account_summary",
        "get_purchase_summary",
        "get_expense_summary",
        "get_product_or_catalog_info",
        "get_dashboard_summary",
    )
    assert len(READ_TOOL_NAMES) <= 8
    assert get_read_tool_names() == READ_TOOL_NAMES
    assert READ_MASTER_TOOL_NAMES == READ_TOOL_NAMES
    # Step 1 demo registry is untouched.
    assert sorted(t.name for t in get_master_tools()) == [
        "demo_prepare_operation",
        "demo_side_effect",
        "kapraos_demo_info",
    ]
    assert_no_db_mutation_tools(get_master_tools())


@pytest.mark.asyncio
async def test_read_tools_take_no_shop_id(db_session: AsyncSession) -> None:
    shop, *_ = await _make_shop(db_session)
    for tool in build_read_tools(db_session, _ctx(shop)):
        func = getattr(tool, "func", None) or getattr(tool, "coroutine", None)
        assert func is not None, f"{tool.name} must expose its function"
        params = inspect.signature(func).parameters
        assert "shop_id" not in params, f"{tool.name} must not take shop_id"
        assert "shop" not in params, f"{tool.name} must not take shop"
    assert_no_db_mutation_tools(build_read_tools(db_session, _ctx(shop)))


def test_read_tools_never_wrap_mutations() -> None:
    import pathlib

    src = pathlib.Path("app/ai/tools/business_reads.py").read_text(encoding="utf-8")
    for forbidden in (
        "session.add",
        "session.delete",
        "session.commit",
        "record_customer_payment",
        "record_supplier_payment",
        "create_sale(",
        "create_purchase(",
        "create_expense(",
        "add_stock(",
        "remove_stock(",
        "adjust_stock(",
        "execute_sql",
        "raw_sql",
        "sa_text",
        "text(",
    ):
        assert forbidden not in src, f"read tools must not contain {forbidden!r}"


# --- Sales -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_sales_summary_today(db_session: AsyncSession) -> None:
    shop, _, _, variant, _ = await _make_shop(db_session)
    await create_purchase(
        db_session,
        shop_id=shop.id,
        supplier_id=(await _supplier(db_session, shop)),
        items=[PurchaseItemInput(variant_id=variant.id, quantity=Decimal("20"), unit_cost=Decimal("500"))],
    )
    await create_sale(
        db_session,
        shop_id=shop.id,
        items=[SaleItemInput(variant_id=variant.id, quantity=Decimal("2"), unit_price=Decimal("800"))],
        payments=[PaymentInput(amount=Decimal("1000"), method=PaymentMethod.CASH)],
    )
    tools = _tools(db_session, shop)
    out = await tools["get_sales_summary"].ainvoke(
        {"start_date": _today_str(), "end_date": _today_str()}
    )
    assert out["status"] == "ok"
    assert out["sale_count"] == 1
    assert Decimal(out["total_sales_amount"]) == Decimal("1600.00")
    assert Decimal(out["paid_amount"]) == Decimal("1000.00")
    assert Decimal(out["due_amount"]) == Decimal("600.00")
    assert isinstance(out["total_sales_amount"], str)


@pytest.mark.asyncio
async def test_sales_summary_empty_and_bad_range(db_session: AsyncSession) -> None:
    shop, *_ = await _make_shop(db_session)
    tools = _tools(db_session, shop)
    out = await tools["get_sales_summary"].ainvoke(
        {"start_date": _today_str(), "end_date": _today_str()}
    )
    assert out["status"] == "ok"
    assert out["sale_count"] == 0
    assert out["total_sales_amount"] == "0.00"
    bad = await tools["get_sales_summary"].ainvoke(
        {"start_date": "2026-09-10", "end_date": "2026-09-01"}
    )
    assert bad["status"] == "error"
    assert "SELECT" not in bad["message"]


async def _supplier(db: AsyncSession, shop: Shop) -> uuid.UUID:
    row = (
        await db.execute(select(Supplier).where(Supplier.shop_id == shop.id).limit(1))
    ).scalar_one()
    return row.id


# --- Inventory -------------------------------------------------------------


@pytest.mark.asyncio
async def test_inventory_stock_low_and_unknown(db_session: AsyncSession) -> None:
    shop, _, product, variant, supplier = await _make_shop(db_session)
    await create_purchase(
        db_session,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[PurchaseItemInput(variant_id=variant.id, quantity=Decimal("42"), unit_cost=Decimal("500"))],
    )
    tools = _tools(db_session, shop)
    out = await tools["get_inventory_status"].ainvoke({"product_name": "Black Lawn"})
    assert out["status"] == "ok"
    assert out["items"][0]["product"] == product.name
    assert Decimal(out["items"][0]["quantity"]) == Decimal("42.000")

    low = await tools["get_inventory_status"].ainvoke({"low_stock_only": True})
    assert low["status"] == "ok"
    assert low["items"] == []

    missing = await tools["get_inventory_status"].ainvoke({"product_name": "Nope"})
    assert missing["status"] == "not_found"
    assert "SELECT" not in missing["message"]


@pytest.mark.asyncio
async def test_inventory_ambiguous(db_session: AsyncSession) -> None:
    shop, category, _, _, _ = await _make_shop(db_session)
    for name in ("Black Lawn 2 Piece", "Gul Ahmed Black Lawn"):
        p = Product(
            shop_id=shop.id, category_id=category.id, name=name, product_type=ProductType.OPEN_FABRIC
        )
        db_session.add(p)
        await db_session.flush()
        db_session.add(
            ProductVariant(
                shop_id=shop.id,
                product_id=p.id,
                sku=f"SKU-{uuid.uuid4().hex[:6]}",
                purchase_price=Decimal("100"),
                selling_price=Decimal("200"),
                unit=Unit.METER,
            )
        )
        await db_session.flush()
    tools = _tools(db_session, shop)
    out = await tools["get_inventory_status"].ainvoke({"product_name": "Black Lawn"})
    assert out["status"] == "ambiguous"
    assert len(out["matches"]) >= 2


# --- Customers / suppliers -------------------------------------------------


@pytest.mark.asyncio
async def test_customer_balance_and_unknown(db_session: AsyncSession) -> None:
    shop, _, _, variant, _ = await _make_shop(db_session)
    supplier_id = await _supplier(db_session, shop)
    await create_purchase(
        db_session,
        shop_id=shop.id,
        supplier_id=supplier_id,
        items=[PurchaseItemInput(variant_id=variant.id, quantity=Decimal("10"), unit_cost=Decimal("500"))],
    )
    customer = Customer(shop_id=shop.id, name="Ahmed")
    db_session.add(customer)
    await db_session.flush()
    await create_sale(
        db_session,
        shop_id=shop.id,
        customer_id=customer.id,
        items=[SaleItemInput(variant_id=variant.id, quantity=Decimal("1"), unit_price=Decimal("800"))],
        payments=[PaymentInput(amount=Decimal("300"), method=PaymentMethod.CASH)],
    )
    tools = _tools(db_session, shop)
    out = await tools["get_customer_account_summary"].ainvoke({"customer_name": "Ahmed"})
    assert out["status"] == "ok"
    assert out["customer_name"] == "Ahmed"
    assert Decimal(out["outstanding_balance"]) == Decimal("500.00")

    missing = await tools["get_customer_account_summary"].ainvoke({"customer_name": "Nobody"})
    assert missing["status"] == "not_found"
    assert "Traceback" not in missing["message"]


@pytest.mark.asyncio
async def test_supplier_balance_and_unknown(db_session: AsyncSession) -> None:
    shop, _, _, variant, supplier = await _make_shop(db_session)
    await create_purchase(
        db_session,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[PurchaseItemInput(variant_id=variant.id, quantity=Decimal("5"), unit_cost=Decimal("500"))],
    )
    tools = _tools(db_session, shop)
    out = await tools["get_supplier_account_summary"].ainvoke({"supplier_name": "Al-Madina"})
    assert out["status"] == "ok"
    assert Decimal(out["outstanding_balance"]) == Decimal("2500.00")
    missing = await tools["get_supplier_account_summary"].ainvoke({"supplier_name": "Nobody"})
    assert missing["status"] == "not_found"


@pytest.mark.asyncio
async def test_same_name_in_different_shops_is_isolated(db_session: AsyncSession) -> None:
    shop_a, _, _, variant_a, _ = await _make_shop(db_session, name="Shop A")
    shop_b, _, _, variant_b, _ = await _make_shop(db_session, name="Shop B")
    for shop, variant in ((shop_a, variant_a), (shop_b, variant_b)):
        db_session.add(Customer(shop_id=shop.id, name="Ahmed"))
        db_session.add(Supplier(shop_id=shop.id, name="Same Supplier"))
    await db_session.flush()
    # Distinct balances: A owes 800, B owes 1600.
    for shop, variant, qty in ((shop_a, variant_a, "1"), (shop_b, variant_b, "2")):
        await create_purchase(
            db_session,
            shop_id=shop.id,
            supplier_id=await _supplier(db_session, shop),
            items=[PurchaseItemInput(variant_id=variant.id, quantity=Decimal("10"), unit_cost=Decimal("100"))],
        )
        cust = (
            await db_session.execute(
                select(Customer).where(Customer.shop_id == shop.id, Customer.name == "Ahmed")
            )
        ).scalar_one()
        await create_sale(
            db_session,
            shop_id=shop.id,
            customer_id=cust.id,
            items=[SaleItemInput(variant_id=variant.id, quantity=Decimal(qty), unit_price=Decimal("800"))],
        )
    tools_a = _tools(db_session, shop_a)
    out_a = await tools_a["get_customer_account_summary"].ainvoke({"customer_name": "Ahmed"})
    assert out_a["status"] == "ok"
    assert Decimal(out_a["outstanding_balance"]) == Decimal("800.00")
    # Shop B data never leaks into Shop A answers.
    sales_b = await tools_a["get_sales_summary"].ainvoke({})
    assert sales_b["sale_count"] == 1
    assert Decimal(sales_b["total_sales_amount"]) == Decimal("800.00")


@pytest.mark.asyncio
async def test_cross_tenant_supplier_isolation(db_session: AsyncSession) -> None:
    shop_a, *_ = await _make_shop(db_session, name="Iso A")
    shop_b, *_ = await _make_shop(db_session, name="Iso B")
    tools_a = _tools(db_session, shop_a)
    out = await tools_a["get_supplier_account_summary"].ainvoke({"supplier_name": "Al-Madina"})
    # Shop A has its own Al-Madina; Shop B's supplier must not confuse it,
    # and a B-only name must not be visible from A.
    assert out["status"] in ("ok", "ambiguous")
    missing = await tools_a["get_customer_account_summary"].ainvoke(
        {"customer_name": "Only In B"}
    )
    db_session.add(Customer(shop_id=shop_b.id, name="Only In B"))
    await db_session.flush()
    missing = await tools_a["get_customer_account_summary"].ainvoke(
        {"customer_name": "Only In B"}
    )
    assert missing["status"] == "not_found"


# --- Purchases / expenses / catalog / dashboard ----------------------------


@pytest.mark.asyncio
async def test_purchase_summary_and_empty(db_session: AsyncSession) -> None:
    shop, _, _, variant, supplier = await _make_shop(db_session)
    tools = _tools(db_session, shop)
    empty = await tools["get_purchase_summary"].ainvoke({})
    assert empty["status"] == "ok"
    assert empty["purchase_count"] == 0
    await create_purchase(
        db_session,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[PurchaseItemInput(variant_id=variant.id, quantity=Decimal("3"), unit_cost=Decimal("500"))],
    )
    out = await tools["get_purchase_summary"].ainvoke(
        {"start_date": _today_str(), "end_date": _today_str()}
    )
    assert out["purchase_count"] == 1
    assert Decimal(out["total_purchase_amount"]) == Decimal("1500.00")


@pytest.mark.asyncio
async def test_expense_summary_and_category(db_session: AsyncSession) -> None:
    shop, *_ = await _make_shop(db_session)
    await create_expense(
        db_session,
        shop_id=shop.id,
        category=ExpenseCategory.RENT,
        amount=Decimal("10000"),
        payment_method=PaymentMethod.CASH,
    )
    await create_expense(
        db_session,
        shop_id=shop.id,
        category=ExpenseCategory.UTILITIES,
        amount=Decimal("2000"),
        payment_method=PaymentMethod.CASH,
    )
    tools = _tools(db_session, shop)
    out = await tools["get_expense_summary"].ainvoke(
        {"start_date": _today_str(), "end_date": _today_str()}
    )
    assert out["status"] == "ok"
    assert Decimal(out["total_expenses"]) == Decimal("12000.00")
    assert out["by_category"]["rent"] == "10000.00"
    rent = await tools["get_expense_summary"].ainvoke(
        {"start_date": _today_str(), "end_date": _today_str(), "category": "rent"}
    )
    assert Decimal(rent["total_expenses"]) == Decimal("10000.00")
    bad = await tools["get_expense_summary"].ainvoke({"category": "spaceship"})
    assert bad["status"] == "error"


@pytest.mark.asyncio
async def test_catalog_lookup_and_ambiguous(db_session: AsyncSession) -> None:
    shop, category, _, _, _ = await _make_shop(db_session)
    tools = _tools(db_session, shop)
    out = await tools["get_product_or_catalog_info"].ainvoke({"product_name": "Black Lawn"})
    assert out["status"] == "ok"
    assert out["product_name"] == "Black Lawn"
    assert out["category"] == "Lawn"
    assert out["variants"]
    missing = await tools["get_product_or_catalog_info"].ainvoke({"product_name": "Nope"})
    assert missing["status"] == "not_found"
    p = Product(
        shop_id=shop.id, category_id=category.id, name="Black Lawn Premium", product_type=ProductType.OPEN_FABRIC
    )
    db_session.add(p)
    await db_session.flush()
    amb = await tools["get_product_or_catalog_info"].ainvoke({"product_name": "Black Lawn"})
    assert amb["status"] == "ambiguous"


@pytest.mark.asyncio
async def test_dashboard_summary(db_session: AsyncSession) -> None:
    shop, *_ = await _make_shop(db_session)
    tools = _tools(db_session, shop)
    out = await tools["get_dashboard_summary"].ainvoke({})
    assert out["status"] == "ok"
    for key in (
        "today_sales",
        "today_purchases",
        "today_expenses",
        "receivables_outstanding",
        "payables_outstanding",
    ):
        Decimal(out[key])  # parses; strings, never floats
        assert isinstance(out[key], str)


# --- Read-only guarantee ---------------------------------------------------


@pytest.mark.asyncio
async def test_read_tools_do_not_mutate(db_session: AsyncSession) -> None:
    shop, _, _, variant, supplier = await _make_shop(db_session)
    await create_purchase(
        db_session,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[PurchaseItemInput(variant_id=variant.id, quantity=Decimal("5"), unit_cost=Decimal("100"))],
    )

    async def counts() -> tuple[int, ...]:
        out = []
        for model in (Sale, Purchase, Expense):
            out.append(
                (
                    await db_session.execute(
                        select(func.count(model.id)).where(model.shop_id == shop.id)
                    )
                ).scalar_one()
            )
        return tuple(out)  # type: ignore[return-value]

    before = await counts()
    tools = _tools(db_session, shop)
    await tools["get_sales_summary"].ainvoke({})
    await tools["get_inventory_status"].ainvoke({})
    await tools["get_customer_account_summary"].ainvoke({"customer_name": "Nobody"})
    await tools["get_supplier_account_summary"].ainvoke({"supplier_name": "Al-Madina"})
    await tools["get_purchase_summary"].ainvoke({})
    await tools["get_expense_summary"].ainvoke({})
    await tools["get_product_or_catalog_info"].ainvoke({"product_name": "Black Lawn"})
    await tools["get_dashboard_summary"].ainvoke({})
    assert await counts() == before


# --- Integration: agent -> read tool -> real DB ----------------------------


class _SalesToolFakeModel(BaseChatModel):
    """First call requests get_sales_summary; then answers directly."""

    @property
    def _llm_type(self) -> str:
        return "reads-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_SalesToolFakeModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if any(isinstance(m, ToolMessage) for m in messages):
            message = AIMessage(content="Aaj ki sale mil gayi.")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_sales_summary",
                        "args": {},
                        "id": "call_sales",
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.mark.asyncio
async def test_agent_uses_read_tool_on_real_data(db_session: AsyncSession) -> None:
    shop, _, _, variant, supplier = await _make_shop(db_session, name="Agent Shop")
    await create_purchase(
        db_session,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[PurchaseItemInput(variant_id=variant.id, quantity=Decimal("10"), unit_cost=Decimal("100"))],
    )
    await create_sale(
        db_session,
        shop_id=shop.id,
        items=[SaleItemInput(variant_id=variant.id, quantity=Decimal("1"), unit_price=Decimal("800"))],
    )
    agent = build_master_agent(
        model=_SalesToolFakeModel(),
        include_hitl_demo=False,
        extra_tools=build_read_tools(db_session, _ctx(shop)),
    )
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Aaj kitni sale hui?"}]},
        config={"configurable": {"thread_id": f"reads-{uuid.uuid4().hex}"}},
    )
    value = getattr(result, "value", result)
    messages = value["messages"] if isinstance(value, dict) else result["messages"]
    assert any(isinstance(m, ToolMessage) for m in messages)
    texts = [getattr(m, "content", "") for m in messages if isinstance(m, AIMessage)]
    assert any("sale" in str(t).lower() for t in texts)


class _EchoSalesFakeModel(BaseChatModel):
    """Echo the real tool result into the final answer (HTTP proof)."""

    @property
    def _llm_type(self) -> str:
        return "reads-echo-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_EchoSalesFakeModel":
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
            message = AIMessage(content=f"Tool result: {tool_msgs[-1].content}")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_sales_summary",
                        "args": {},
                        "id": "call_sales_echo",
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.mark.asyncio
async def test_chat_endpoint_serves_real_read_data(
    mocked_api_client: Any, api_session: AsyncSession
) -> None:
    """POST /ai/chat -> master agent -> read tool -> real DB -> reply."""
    from app.api.ai import get_chat_model
    from app.main import app
    from app.models import Shop as ShopModel
    from app.models import User as UserModel
    from app.models.user import UserRole

    shop = ShopModel(name="HTTP Read Shop")
    api_session.add(shop)
    await api_session.flush()
    await api_session.refresh(shop)
    api_session.add(
        UserModel(
            clerk_user_id="mock_clerk_id",
            shop_id=shop.id,
            name="Reader",
            email="reader@example.com",
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
    api_session.add_all([variant, supplier])
    await api_session.flush()
    await create_purchase(
        api_session,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[PurchaseItemInput(variant_id=variant.id, quantity=Decimal("10"), unit_cost=Decimal("500"))],
    )
    await create_sale(
        api_session,
        shop_id=shop.id,
        items=[SaleItemInput(variant_id=variant.id, quantity=Decimal("2"), unit_price=Decimal("800"))],
    )

    app.dependency_overrides[get_chat_model] = lambda: _EchoSalesFakeModel()
    try:
        response = await mocked_api_client.post(
            "/ai/chat", json={"message": "Aaj kitni sale hui?"}
        )
    finally:
        app.dependency_overrides.pop(get_chat_model, None)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "done"
    # The reply echoes the real tool payload (1600.00), proving the full path.
    assert "1600" in data["reply"]

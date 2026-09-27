"""Step 9: AI-assisted customer & supplier returns — two HITL-gated mutations.

Covers the brief's minimum without any real LLM (deterministic fake
models, direct tool calls — no network, no tokens):

* preparation: sale/purchase resolution (UUID/invoice/customer+sale,
  supplier+purchase), item resolution (name/SKU/variant/line ID),
  quantity validation (units ignored, hazar scales, no conversion),
  notes handling
* ambiguity: multiple sales/purchases, multiple items — never guessing
* HITL: pause before mutation, approve executes, reject is a no-op,
  edit executes the corrected args (re-validated on a fresh session)
* transaction: success commits atomically, failure leaves no partial
  state (savepoint-scoped, never a full-session rollback)
* idempotency: same operation key twice creates exactly one return;
  a failed attempt does not poison its key; concurrent race re-reads winner
* tenant isolation: shop-scoped, foreign sale/purchase reads as not_found
* service reuse: the AI path calls ``returns.create_sale_return()`` /
  ``returns.create_purchase_return()`` exactly once; totals/AR/cash are
  backend-authoritative and preserved verbatim
* races: over-return, fully-returned, insufficient stock surface cleanly
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
    CUSTOMER_RETURN_HITL_INTERRUPT_CONFIG,
    RETURN_HITL_INTERRUPT_CONFIG,
    RETURN_SYSTEM_ADDENDUM,
    SUPPLIER_RETURN_HITL_INTERRUPT_CONFIG,
    WRITE_MASTER_TOOL_NAMES,
    approve_decision,
    build_master_agent,
    hitl_resume_payload,
    reject_decision,
)
from app.ai.state import TenantContext
from app.ai.tools.returns_write import (
    CREATE_CUSTOMER_RETURN_TOOL_NAME,
    CREATE_SUPPLIER_RETURN_TOOL_NAME,
    WRITE_TOOL_NAMES,
    ReturnPrepAmbiguousError,
    ReturnPrepError,
    ReturnPrepNotFoundError,
    assert_return_write_registry_is_minimal,
    build_customer_return_preview,
    build_return_write_tools,
    build_supplier_return_preview,
    parse_notes,
    parse_quantity,
    parse_uuid_arg,
    resolve_customer_sale,
    resolve_supplier_purchase,
    validate_idempotency_key,
)
from app.models import (
    AICustomerReturnReceipt,
    AISupplierReturnReceipt,
    Category,
    Customer,
    Inventory,
    LedgerEntry,
    Product,
    ProductType,
    ProductVariant,
    Purchase,
    PurchaseItem,
    PurchaseReturn,
    Sale,
    SaleItem,
    SaleReturn,
    Shop,
    Supplier,
    Unit,
)
from app.services import returns as returns_service
from app.services.purchases import PurchaseItemInput, create_purchase
from app.services.sales import SaleItemInput, create_sale

# --- Fixtures ------------------------------------------------------------


class ReturnFixture:
    def __init__(
        self,
        shop: Shop,
        supplier: Supplier,
        customer: Customer,
        product: Product,
        variant: ProductVariant,
    ) -> None:
        self.shop = shop
        self.supplier = supplier
        self.customer = customer
        self.product = product
        self.variant = variant


async def _make_return_shop(
    db: AsyncSession,
    name: str = "AI Return Shop",
    *,
    supplier_name: str = "Ahmed Traders",
    customer_name: str = "Ali",
    product_name: str = "Black Lawn",
    sku: str | None = None,
) -> ReturnFixture:
    shop = Shop(name=f"{name} {uuid.uuid4().hex[:6]}")
    db.add(shop)
    await db.flush()
    category = Category(shop_id=shop.id, shop=shop, name="Lawn")
    db.add(category)
    await db.flush()
    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name=product_name,
        product_type=ProductType.OPEN_FABRIC,
    )
    db.add(product)
    await db.flush()
    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku=sku or f"RET-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("800.00"),
        selling_price=Decimal("1000.00"),
        unit=Unit.METER,
    )
    db.add(variant)
    supplier = Supplier(shop_id=shop.id, name=supplier_name)
    customer = Customer(shop_id=shop.id, name=customer_name)
    db.add_all([supplier, customer])
    await db.flush()
    return ReturnFixture(shop, supplier, customer, product, variant)


async def _seed_purchase_and_sale(
    db: AsyncSession,
    fix: ReturnFixture,
    *,
    purchase_qty: str = "20",
    sale_qty: str = "5",
    unit_cost: str = "800",
    unit_price: str = "1000",
    sale_invoice: str | None = None,
    purchase_invoice: str | None = None,
) -> tuple[Purchase, Sale]:
    purchase = await create_purchase(
        db,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(purchase_qty),
                unit_cost=Decimal(unit_cost),
            )
        ],
        invoice_number=purchase_invoice,
    )
    sale = await create_sale(
        db,
        shop_id=fix.shop.id,
        customer_id=fix.customer.id,
        items=[
            SaleItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(sale_qty),
                unit_price=Decimal(unit_price),
            )
        ],
        invoice_number=sale_invoice,
    )
    return purchase, sale


def _ctx(fix: ReturnFixture) -> TenantContext:
    return TenantContext(shop_id=fix.shop.id, user_id=uuid.uuid4())


def _customer_tool(db: AsyncSession, fix: ReturnFixture) -> Any:
    tools = {t.name: t for t in build_return_write_tools(db, _ctx(fix))}
    return tools[CREATE_CUSTOMER_RETURN_TOOL_NAME]


def _supplier_tool(db: AsyncSession, fix: ReturnFixture) -> Any:
    tools = {t.name: t for t in build_return_write_tools(db, _ctx(fix))}
    return tools[CREATE_SUPPLIER_RETURN_TOOL_NAME]


def _key() -> str:
    return uuid.uuid4().hex


def _customer_args(
    fix: ReturnFixture,
    sale: Sale,
    quantity: str = "2",
    **extra: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "idempotency_key": _key(),
        "sale_id": str(sale.id),
        "items": [
            {"product_name": fix.product.name, "quantity": quantity}
        ],
    }
    base.update(extra)
    return base


def _supplier_args(
    fix: ReturnFixture,
    purchase: Purchase,
    quantity: str = "2",
    **extra: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "idempotency_key": _key(),
        "purchase_id": str(purchase.id),
        "items": [
            {"product_name": fix.product.name, "quantity": quantity}
        ],
    }
    base.update(extra)
    return base


async def _return_counts(db: AsyncSession, fix: ReturnFixture) -> dict[str, Any]:
    sales_ret = (
        await db.execute(
            select(func.count(SaleReturn.id)).where(
                SaleReturn.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    purch_ret = (
        await db.execute(
            select(func.count(PurchaseReturn.id)).where(
                PurchaseReturn.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    cust_receipts = (
        await db.execute(
            select(func.count(AICustomerReturnReceipt.id)).where(
                AICustomerReturnReceipt.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    supp_receipts = (
        await db.execute(
            select(func.count(AISupplierReturnReceipt.id)).where(
                AISupplierReturnReceipt.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    return {
        "sale_returns": sales_ret,
        "purchase_returns": purch_ret,
        "cust_receipts": cust_receipts,
        "supp_receipts": supp_receipts,
    }


async def _inventory_qty(db: AsyncSession, fix: ReturnFixture) -> Decimal:
    row = (
        await db.execute(
            select(Inventory).where(Inventory.variant_id == fix.variant.id)
        )
    ).scalar_one_or_none()
    if row is None:
        return Decimal("0.000")
    return Decimal(row.quantity)


# --- Registry boundary: exactly two NEW mutations ------------------------


def test_return_registry_is_exactly_two_tools() -> None:
    assert WRITE_TOOL_NAMES == (
        "create_customer_return",
        "create_supplier_return",
    )
    assert CREATE_CUSTOMER_RETURN_TOOL_NAME in WRITE_MASTER_TOOL_NAMES
    assert CREATE_SUPPLIER_RETURN_TOOL_NAME in WRITE_MASTER_TOOL_NAMES
    assert set(WRITE_MASTER_TOOL_NAMES) == {
        "create_sale",
        "record_customer_payment",
        "record_supplier_payment",
        "record_expense",
        "create_purchase",
        "create_customer_return",
        "create_supplier_return",
    }
    assert CUSTOMER_RETURN_HITL_INTERRUPT_CONFIG == {
        "create_customer_return": True
    }
    assert SUPPLIER_RETURN_HITL_INTERRUPT_CONFIG == {
        "create_supplier_return": True
    }
    assert RETURN_HITL_INTERRUPT_CONFIG == {
        "create_customer_return": True,
        "create_supplier_return": True,
    }


def test_return_prompt_forces_same_turn_tool_call() -> None:
    lowered = RETURN_SYSTEM_ADDENDUM.lower()
    assert "same turn" in lowered
    assert "without calling the tool" in lowered or "without calling" in lowered
    assert "approval card" in lowered
    assert "haan" in lowered and "directly" in lowered


def test_return_prompt_covers_natural_examples() -> None:
    lowered = RETURN_SYSTEM_ADDENDUM.lower()
    assert "ali" in lowered
    assert "black lawn" in lowered
    assert "ahmed traders" in lowered or "ahmed" in lowered
    assert "blue cotton" in lowered
    assert "hazar" in lowered
    assert "never guess" in lowered or "clarification" in lowered
    assert "authoritative" in lowered
    assert "never invent prices" in lowered or "never invent" in lowered
    assert "never bypass hitl" in lowered
    assert "create_customer_return" in lowered
    assert "create_supplier_return" in lowered


@pytest.mark.asyncio
async def test_return_tools_take_no_tenant_or_ledger_args(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    tools = build_return_write_tools(db_session, _ctx(fix))
    assert_return_write_registry_is_minimal(tools)
    assert len(tools) == 2
    for t in tools:
        func_attr = getattr(t, "func", None) or getattr(t, "coroutine", None)
        assert func_attr is not None
        params = inspect.signature(func_attr).parameters
        for forbidden in (
            "shop_id",
            "shop",
            "user_id",
            "total",
            "unit_price",
            "unit_cost",
            "discount",
            "ar_amount",
            "cash_refund",
            "account_id",
            "ledger_account_id",
            "journal",
            "ledger",
            "sql",
            "query",
            "inventory_cost",
        ):
            assert forbidden not in params, f"{t.name} must not take {forbidden}"
        assert "idempotency_key" in params
        assert "items" in params
        assert "notes" in params


def test_return_registry_rejects_a_third_mutation() -> None:
    from app.ai.tools.registry import demo_prepare_operation

    with pytest.raises(AssertionError, match="two mutations only"):
        assert_return_write_registry_is_minimal(
            [demo_prepare_operation]  # type: ignore[list-item]
        )


# --- Idempotency key / notes / uuid ---------------------------------------


def test_validate_idempotency_key() -> None:
    key = uuid.uuid4().hex
    assert validate_idempotency_key(key) == key
    assert validate_idempotency_key("abc-DEF_12345") == "abc-DEF_12345"
    for bad in ["short", "", "x" * 65, "has space!", "semi;colon", None]:
        with pytest.raises(ReturnPrepError):
            validate_idempotency_key(bad)


def test_parse_uuid_arg() -> None:
    vid = uuid.uuid4()
    assert parse_uuid_arg(str(vid), "sale_id") == vid
    with pytest.raises(ReturnPrepError):
        parse_uuid_arg("not-a-uuid", "sale_id")


def test_parse_notes() -> None:
    assert parse_notes(None) is None
    assert parse_notes("") is None
    assert parse_notes("  damaged piece  ") == "damaged piece"
    assert parse_notes("wapas") == "wapas"
    with pytest.raises(ReturnPrepError):
        parse_notes("x" * 501)


# --- Quantity parsing ------------------------------------------------------


def test_parse_quantity_plain_and_units() -> None:
    assert parse_quantity("2") == Decimal("2.000")
    assert parse_quantity("2.5") == Decimal("2.500")
    assert parse_quantity(2) == Decimal("2.000")
    assert parse_quantity("2 meter") == Decimal("2.000")
    assert parse_quantity("2 meters") == Decimal("2.000")
    assert parse_quantity("2m") == Decimal("2.000")
    assert parse_quantity("3 suits") == Decimal("3.000")
    assert parse_quantity("5 pieces") == Decimal("5.000")
    assert parse_quantity("10 rolls") == Decimal("10.000")
    assert parse_quantity("2 METER") == Decimal("2.000")
    assert parse_quantity("30 meter fabric") == Decimal("30.000")
    # Scale words (Pakistani/common expressions).
    assert parse_quantity("5 hazar") == Decimal("5000.000")
    assert parse_quantity("2.5 hazar") == Decimal("2500.000")
    assert parse_quantity("2,500 meter") == Decimal("2500.000")
    for bad in ["0", "-5", "lots", "", "meter", "hazar", None]:
        with pytest.raises(ReturnPrepError):
            parse_quantity(bad)


def test_parse_quantity_uses_decimal_not_float() -> None:
    out = parse_quantity("2.5")
    assert isinstance(out, Decimal)
    assert out == Decimal("2.500")
    assert parse_quantity("0.1") + parse_quantity("0.2") == Decimal("0.300")


def test_quantity_unit_words_are_not_conversions() -> None:
    # "return 2 meter" means quantity 2 — no meter-to-yard math.
    assert parse_quantity("2 meter") == parse_quantity("2")
    assert parse_quantity("2m") == Decimal("2.000")


# --- Customer: valid return + authoritative preservation --------------------


@pytest.mark.asyncio
async def test_customer_valid_return(db_session: AsyncSession) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    out = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="2")
    )
    assert out["status"] == "completed"
    assert out["duplicate"] is False
    assert out["sale_id"] == str(sale.id)
    assert "return_id" in out
    assert out["items_count"] == 1
    # Authoritative figures are preserved verbatim from the backend row.
    stored = await db_session.get(SaleReturn, uuid.UUID(out["return_id"]))
    assert stored is not None
    assert out["total"] == str(stored.total_amount.quantize(Decimal("0.01")))
    assert out["ar_amount"] == str(stored.ar_amount.quantize(Decimal("0.01")))
    assert out["cash_refund"] == str(
        stored.cash_refund.quantize(Decimal("0.01"))
    )
    assert stored.total_amount == Decimal("2000.00")
    assert out["remaining_quantities"] is not None


@pytest.mark.asyncio
async def test_customer_backend_authoritative_total_with_discount(
    db_session: AsyncSession,
) -> None:
    # A discounted sale must surface the backend's net total, not qty*price.
    fix = await _make_return_shop(db_session)
    await create_purchase(
        db_session,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(10),
                unit_cost=Decimal(800),
            )
        ],
    )
    sale = await create_sale(
        db_session,
        shop_id=fix.shop.id,
        customer_id=fix.customer.id,
        items=[
            SaleItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(5),
                unit_price=Decimal(1000),
            )
        ],
        discount=Decimal(500),
    )
    assert sale.total == Decimal("4500.00")
    out = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="2")
    )
    assert out["status"] == "completed"
    stored = await db_session.get(SaleReturn, uuid.UUID(out["return_id"]))
    assert stored is not None
    # Full 5m would be 4500; 2m is the proportional net share (1800).
    assert stored.total_amount == Decimal("1800.00")
    assert out["total"] == "1800.00"


@pytest.mark.asyncio
async def test_customer_ai_path_calls_service_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    calls: list[dict[str, Any]] = []
    real_create = returns_service.create_sale_return

    async def _spy(session: AsyncSession, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await real_create(session, **kwargs)

    monkeypatch.setattr(returns_service, "create_sale_return", _spy)
    out = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="2")
    )
    assert out["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["shop_id"] == fix.shop.id
    assert calls[0]["sale_id"] == sale.id
    assert len(calls[0]["lines"]) == 1
    assert calls[0]["lines"][0].quantity == Decimal("2.000")
    # The AI layer never passes pricing — only item ids + quantities.
    assert not hasattr(calls[0]["lines"][0], "unit_price")


@pytest.mark.asyncio
async def test_customer_partial_and_remaining(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix, sale_qty="5")
    out = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="2")
    )
    assert out["status"] == "completed"
    assert out["total"] == "2000.00"
    remaining = out["remaining_quantities"]
    sale_item_id = out["items"][0]["sale_item_id"]
    assert remaining[sale_item_id] == "3.000"
    assert sale is not None


@pytest.mark.asyncio
async def test_customer_multi_line_return(db_session: AsyncSession) -> None:
    fix = await _make_return_shop(db_session, product_name="Black Lawn")
    cat = Category(shop_id=fix.shop.id, shop=fix.shop, name="Lawn White")
    db_session.add(cat)
    await db_session.flush()
    prod2 = Product(
        shop_id=fix.shop.id,
        category_id=cat.id,
        name="White Lawn",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(prod2)
    await db_session.flush()
    var2 = ProductVariant(
        shop_id=fix.shop.id,
        product_id=prod2.id,
        sku=f"WHT-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("700.00"),
        selling_price=Decimal("900.00"),
        unit=Unit.METER,
    )
    db_session.add(var2)
    await db_session.flush()
    await create_purchase(
        db_session,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(10),
                unit_cost=Decimal(800),
            ),
            PurchaseItemInput(
                variant_id=var2.id,
                quantity=Decimal(10),
                unit_cost=Decimal(700),
            ),
        ],
    )
    sale = await create_sale(
        db_session,
        shop_id=fix.shop.id,
        customer_id=fix.customer.id,
        items=[
            SaleItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(4),
                unit_price=Decimal(1000),
            ),
            SaleItemInput(
                variant_id=var2.id,
                quantity=Decimal(3),
                unit_price=Decimal(900),
            ),
        ],
    )
    out = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "sale_id": str(sale.id),
            "items": [
                {"product_name": "Black Lawn", "quantity": "2"},
                {"product_name": "White Lawn", "quantity": "1"},
            ],
        }
    )
    assert out["status"] == "completed"
    assert out["items_count"] == 2
    assert out["total"] == "2900.00"


@pytest.mark.asyncio
async def test_customer_fully_returned_then_over_return(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix, sale_qty="2")
    first = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="2")
    )
    assert first["status"] == "completed"
    before = await _return_counts(db_session, fix)
    second = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="1")
    )
    assert second["status"] == "error"
    assert second["code"] == "exceeds_remaining"
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_customer_over_return_surfaced(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix, sale_qty="2")
    before = await _return_counts(db_session, fix)
    out = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="5")
    )
    assert out["status"] == "error"
    assert out["code"] == "exceeds_remaining"
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_customer_domain_validation_errors(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    tool = _customer_tool(db_session, fix)
    before = await _return_counts(db_session, fix)
    for bad_qty in ["0", "-2", "lots"]:
        out = await tool.ainvoke(_customer_args(fix, sale, quantity=bad_qty))
        assert out["status"] == "error", bad_qty
    missing = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "sale_id": str(sale.id),
            "items": [],
        }
    )
    assert missing["status"] == "error"
    assert await _return_counts(db_session, fix) == before


# --- Customer: sale resolution ---------------------------------------------


@pytest.mark.asyncio
async def test_customer_sale_resolution_by_id(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    sale_obj = await resolve_customer_sale(
        db_session, fix.shop.id, sale_id=str(sale.id)
    )
    assert sale_obj.id == sale.id
    out = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="1")
    )
    assert out["status"] == "completed"


@pytest.mark.asyncio
async def test_customer_sale_resolution_by_invoice(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(
        db_session, fix, sale_invoice="INV-1023"
    )
    assert sale.invoice_number == "INV-1023"
    resolved = await resolve_customer_sale(
        db_session, fix.shop.id, sale_reference="INV-1023"
    )
    assert resolved.id == sale.id
    out = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "sale_reference": "INV-1023",
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "completed"
    assert out["sale_id"] == str(sale.id)
    assert out["invoice_number"] == "INV-1023"


@pytest.mark.asyncio
async def test_customer_sale_resolution_by_customer_name(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    resolved = await resolve_customer_sale(
        db_session, fix.shop.id, customer_name=fix.customer.name
    )
    assert resolved.id == sale.id
    out = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": fix.customer.name,
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "completed"


@pytest.mark.asyncio
async def test_customer_sale_ambiguity_multiple_sales(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    await _seed_purchase_and_sale(db_session, fix, sale_qty="2")
    await create_sale(
        db_session,
        shop_id=fix.shop.id,
        customer_id=fix.customer.id,
        items=[
            SaleItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(1),
                unit_price=Decimal(1000),
            )
        ],
    )
    with pytest.raises(ReturnPrepAmbiguousError) as exc:
        await resolve_customer_sale(
            db_session, fix.shop.id, customer_name=fix.customer.name
        )
    assert len(exc.value.matches) == 2
    before = await _return_counts(db_session, fix)
    out = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": fix.customer.name,
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "ambiguous"
    assert len(out["matches"]) == 2
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_customer_sale_ambiguity_invoice_fragment(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale1 = await _seed_purchase_and_sale(
        db_session, fix, sale_qty="1", sale_invoice="INV-1023-A"
    )
    await create_sale(
        db_session,
        shop_id=fix.shop.id,
        customer_id=fix.customer.id,
        items=[
            SaleItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(1),
                unit_price=Decimal(1000),
            )
        ],
        invoice_number="INV-1023-B",
    )
    assert sale1.invoice_number == "INV-1023-A"
    out = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "sale_reference": "1023",
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "ambiguous"


@pytest.mark.asyncio
async def test_customer_sale_not_found_no_creation(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    await _seed_purchase_and_sale(db_session, fix)
    before_sales = (
        await db_session.execute(
            select(func.count(Sale.id)).where(Sale.shop_id == fix.shop.id)
        )
    ).scalar_one()
    out = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "sale_reference": "INV-DOES-NOT-EXIST",
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "not_found"
    after_sales = (
        await db_session.execute(
            select(func.count(Sale.id)).where(Sale.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert after_sales == before_sales
    # Missing reference entirely is an error, never a guess.
    out2 = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out2["status"] == "error"


# --- Customer: item resolution ---------------------------------------------


@pytest.mark.asyncio
async def test_customer_item_by_ids_and_sku(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    sale_item_id = (
        await db_session.execute(
            select(SaleItem.id).where(SaleItem.sale_id == sale.id)
        )
    ).scalars().first()
    assert sale_item_id is not None
    sale_item_id = str(sale_item_id)
    for entry in (
        {"sale_item_id": sale_item_id, "quantity": "1"},
        {"variant_id": str(fix.variant.id), "quantity": "1"},
        {"variant_sku": fix.variant.sku, "quantity": "1"},
        {"product_name": fix.product.name, "quantity": "1"},
        {"sale_item_reference": fix.product.name, "quantity": "1"},
    ):
        out = await _customer_tool(db_session, fix).ainvoke(
            {
                "idempotency_key": _key(),
                "sale_id": str(sale.id),
                "items": [entry],
            }
        )
        assert out["status"] in ("completed", "error")
        if out["status"] == "completed":
            assert out["items"][0]["sale_item_id"] == sale_item_id


@pytest.mark.asyncio
async def test_customer_item_ambiguity_no_guessing(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(
        db_session, product_name="Black Lawn Premium"
    )
    cat = Category(shop_id=fix.shop.id, shop=fix.shop, name="Extra")
    db_session.add(cat)
    await db_session.flush()
    extras: list[ProductVariant] = []
    for pname in ("Black Cotton", "Black Khaddar"):
        prod = Product(
            shop_id=fix.shop.id,
            category_id=cat.id,
            name=pname,
            product_type=ProductType.OPEN_FABRIC,
        )
        db_session.add(prod)
        await db_session.flush()
        var = ProductVariant(
            shop_id=fix.shop.id,
            product_id=prod.id,
            sku=f"BLK-{uuid.uuid4().hex[:6]}",
            purchase_price=Decimal("800.00"),
            selling_price=Decimal("1000.00"),
            unit=Unit.METER,
        )
        db_session.add(var)
        extras.append(var)
    await db_session.flush()
    await create_purchase(
        db_session,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(10),
                unit_cost=Decimal(800),
            ),
            *[
                PurchaseItemInput(
                    variant_id=v.id,
                    quantity=Decimal(10),
                    unit_cost=Decimal(800),
                )
                for v in extras
            ],
        ],
    )
    sale = await create_sale(
        db_session,
        shop_id=fix.shop.id,
        customer_id=fix.customer.id,
        items=[
            SaleItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(2),
                unit_price=Decimal(1000),
            ),
            *[
                SaleItemInput(
                    variant_id=v.id,
                    quantity=Decimal(2),
                    unit_price=Decimal(1000),
                )
                for v in extras
            ],
        ],
    )
    before = await _return_counts(db_session, fix)
    out = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "sale_id": str(sale.id),
            "items": [{"product_name": "black", "quantity": "2"}],
        }
    )
    assert out["status"] == "ambiguous"
    assert len(out["matches"]) == 3
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_customer_item_not_found(db_session: AsyncSession) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    before = await _return_counts(db_session, fix)
    out = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "sale_id": str(sale.id),
            "items": [
                {"product_name": "No Such Fabric", "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "not_found"
    assert await _return_counts(db_session, fix) == before


# --- Customer: tenant isolation --------------------------------------------


@pytest.mark.asyncio
async def test_customer_foreign_sale_is_not_found(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_return_shop(db_session, name="Shop A")
    shop_b = await _make_return_shop(db_session, name="Shop B")
    _, sale_b = await _seed_purchase_and_sale(db_session, shop_b)
    with pytest.raises(ReturnPrepNotFoundError):
        await resolve_customer_sale(
            db_session, shop_a.shop.id, sale_id=str(sale_b.id)
        )
    out = await _customer_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "sale_id": str(sale_b.id),
            "items": [
                {"product_name": shop_a.product.name, "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "not_found"
    assert "shop_id" not in out["message"]
    # Foreign invoice never leaks either.
    await create_sale(
        db_session,
        shop_id=shop_b.shop.id,
        customer_id=shop_b.customer.id,
        items=[
            SaleItemInput(
                variant_id=shop_b.variant.id,
                quantity=Decimal(1),
                unit_price=Decimal(1000),
            )
        ],
        invoice_number="FOREIGN-INV-999",
    )
    out2 = await _customer_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "sale_reference": "FOREIGN-INV-999",
            "items": [
                {"product_name": shop_a.product.name, "quantity": "1"}
            ],
        }
    )
    assert out2["status"] == "not_found"


@pytest.mark.asyncio
async def test_customer_isolation_no_cross_shop_return(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_return_shop(db_session, name="Shop A")
    shop_b = await _make_return_shop(db_session, name="Shop B")
    _, sale_b = await _seed_purchase_and_sale(db_session, shop_b)
    sale_item_b = (
        await db_session.execute(
            select(SaleItem.id).where(SaleItem.sale_id == sale_b.id)
        )
    ).scalars().first()
    assert sale_item_b is not None
    out = await _customer_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "sale_id": str(sale_b.id),
            "items": [{"sale_item_id": str(sale_item_b), "quantity": "1"}],
        }
    )
    assert out["status"] == "not_found"
    mine_a = (
        await db_session.execute(
            select(func.count(SaleReturn.id)).where(
                SaleReturn.shop_id == shop_a.shop.id
            )
        )
    ).scalar_one()
    assert mine_a == 0


# --- Customer: idempotency --------------------------------------------------


@pytest.mark.asyncio
async def test_customer_duplicate_key_executes_once(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    tool = _customer_tool(db_session, fix)
    key = _key()
    args = _customer_args(fix, sale, quantity="1", idempotency_key=key)
    first = await tool.ainvoke(args)
    assert first["status"] == "completed" and first["duplicate"] is False
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["return_id"] == first["return_id"]
    n = (
        await db_session.execute(
            select(func.count(SaleReturn.id)).where(
                SaleReturn.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    assert n == 1


@pytest.mark.asyncio
async def test_customer_failed_attempt_does_not_poison_key(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    tool = _customer_tool(db_session, fix)
    key = _key()
    failed = await tool.ainvoke(
        {
            "idempotency_key": key,
            "sale_id": str(sale.id),
            "items": [
                {"product_name": fix.product.name, "quantity": "0"}
            ],
        }
    )
    assert failed["status"] == "error"
    retry = await tool.ainvoke(_customer_args(fix, sale, idempotency_key=key))
    assert retry["status"] == "completed" and retry["duplicate"] is False


@pytest.mark.asyncio
async def test_customer_concurrent_race_returns_winner(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    tool = _customer_tool(db_session, fix)
    key = _key()
    sale_item_id = (
        await db_session.execute(
            select(SaleItem.id).where(SaleItem.sale_id == sale.id)
        )
    ).scalars().first()
    assert sale_item_id is not None
    winner = await returns_service.create_sale_return(
        db_session,
        shop_id=fix.shop.id,
        sale_id=sale.id,
        lines=[
            returns_service.SaleReturnLineInput(
                sale_item_id=sale_item_id, quantity=Decimal(1)
            )
        ],
    )
    db_session.add(
        AICustomerReturnReceipt(
            shop_id=fix.shop.id, operation_key=key, return_id=winner.id
        )
    )
    await db_session.flush()
    before = await _return_counts(db_session, fix)
    out = await tool.ainvoke(
        {
            "idempotency_key": key,
            "sale_id": str(sale.id),
            "items": [
                {"product_name": fix.product.name, "quantity": "2"}
            ],
        }
    )
    assert out["status"] == "completed" and out["duplicate"] is True
    assert out["return_id"] == str(winner.id)
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_customer_idempotency_keys_are_tenant_scoped(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_return_shop(db_session, name="Shop A")
    shop_b = await _make_return_shop(db_session, name="Shop B")
    _, sale_a = await _seed_purchase_and_sale(db_session, shop_a)
    _, sale_b = await _seed_purchase_and_sale(db_session, shop_b)
    key = _key()
    out_a = await _customer_tool(db_session, shop_a).ainvoke(
        _customer_args(shop_a, sale_a, idempotency_key=key)
    )
    out_b = await _customer_tool(db_session, shop_b).ainvoke(
        _customer_args(shop_b, sale_b, idempotency_key=key)
    )
    assert out_a["duplicate"] is False and out_b["duplicate"] is False
    assert out_a["return_id"] != out_b["return_id"]


# --- Customer: fresh-session revalidation -----------------------------------


@pytest.mark.asyncio
async def test_customer_fresh_session_revalidation(
    db_session: AsyncSession,
) -> None:
    # State changing between preview and approval must be re-checked:
    # consume the remaining quantity directly, then the AI attempt fails.
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix, sale_qty="3")
    first = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="2")
    )
    assert first["status"] == "completed"
    # The second attempt for 2 more exceeds the fresh remaining (1 left).
    before = await _return_counts(db_session, fix)
    second = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="2")
    )
    assert second["status"] == "error"
    assert second["code"] == "exceeds_remaining"
    assert await _return_counts(db_session, fix) == before


# --- Customer: preview ------------------------------------------------------


def test_build_customer_return_preview() -> None:
    text = build_customer_return_preview(
        customer_name="Ali",
        sale_reference="INV-1023",
        lines=[
            {
                "product_name": "Black Lawn",
                "quantity": "2.000",
                "unit_price": "1000.00",
            }
        ],
        estimated_total=Decimal("2000.00"),
        estimated_ar=Decimal("2000.00"),
        estimated_cash=Decimal("0.00"),
    )
    assert "Customer Return" in text
    assert "Ali" in text
    assert "INV-1023" in text
    assert "Black Lawn" in text
    assert "2.000" in text
    assert "PKR" in text
    assert "authoritative" in text.lower()
    assert "Please approve" in text


# --- Supplier: valid return + authoritative preservation --------------------


@pytest.mark.asyncio
async def test_supplier_valid_return(db_session: AsyncSession) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    out = await _supplier_tool(db_session, fix).ainvoke(
        _supplier_args(fix, purchase, quantity="2")
    )
    assert out["status"] == "completed"
    assert out["duplicate"] is False
    assert out["purchase_id"] == str(purchase.id)
    stored = await db_session.get(
        PurchaseReturn, uuid.UUID(out["return_id"])
    )
    assert stored is not None
    assert out["total"] == str(stored.total_amount.quantize(Decimal("0.01")))
    # Supplier return total is net of header-discount share (800*2 here).
    assert stored.total_amount == Decimal("1600.00")
    assert out["remaining_quantities"] is not None


@pytest.mark.asyncio
async def test_supplier_ai_path_calls_service_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    calls: list[dict[str, Any]] = []
    real_create = returns_service.create_purchase_return

    async def _spy(session: AsyncSession, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await real_create(session, **kwargs)

    monkeypatch.setattr(returns_service, "create_purchase_return", _spy)
    out = await _supplier_tool(db_session, fix).ainvoke(
        _supplier_args(fix, purchase, quantity="2")
    )
    assert out["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["shop_id"] == fix.shop.id
    assert calls[0]["purchase_id"] == purchase.id
    assert calls[0]["lines"][0].quantity == Decimal("2.000")
    assert not hasattr(calls[0]["lines"][0], "unit_cost")


@pytest.mark.asyncio
async def test_supplier_partial_and_remaining(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(
        db_session, fix, purchase_qty="20"
    )
    out = await _supplier_tool(db_session, fix).ainvoke(
        _supplier_args(fix, purchase, quantity="3")
    )
    assert out["status"] == "completed"
    remaining = out["remaining_quantities"]
    purchase_item_id = out["items"][0]["purchase_item_id"]
    assert remaining[purchase_item_id] == "17.000"


@pytest.mark.asyncio
async def test_supplier_multi_line_return(db_session: AsyncSession) -> None:
    fix = await _make_return_shop(db_session, product_name="Blue Cotton")
    cat = Category(shop_id=fix.shop.id, shop=fix.shop, name="Extra")
    db_session.add(cat)
    await db_session.flush()
    prod2 = Product(
        shop_id=fix.shop.id,
        category_id=cat.id,
        name="Green Cotton",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(prod2)
    await db_session.flush()
    var2 = ProductVariant(
        shop_id=fix.shop.id,
        product_id=prod2.id,
        sku=f"GRN-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("500.00"),
        selling_price=Decimal("700.00"),
        unit=Unit.METER,
    )
    db_session.add(var2)
    await db_session.flush()
    purchase = await create_purchase(
        db_session,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(10),
                unit_cost=Decimal(800),
            ),
            PurchaseItemInput(
                variant_id=var2.id,
                quantity=Decimal(6),
                unit_cost=Decimal(500),
            ),
        ],
    )
    out = await _supplier_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "purchase_id": str(purchase.id),
            "items": [
                {"product_name": "Blue Cotton", "quantity": "2"},
                {"product_name": "Green Cotton", "quantity": "3"},
            ],
        }
    )
    assert out["status"] == "completed"
    assert out["items_count"] == 2
    assert out["total"] == "3100.00"


@pytest.mark.asyncio
async def test_supplier_fully_returned_then_over_return(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(
        db_session, fix, purchase_qty="5", sale_qty="1"
    )
    # Sell 1 so 4 remain in stock; returning all 5 still works for the
    # purchase-quantity check but the stock check needs care — use a fresh
    # fixture with no sale consuming stock for the full-return path.
    fix2 = await _make_return_shop(db_session, name="Full Return Shop")
    purchase2 = await create_purchase(
        db_session,
        shop_id=fix2.shop.id,
        supplier_id=fix2.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fix2.variant.id,
                quantity=Decimal(3),
                unit_cost=Decimal(800),
            )
        ],
    )
    first = await _supplier_tool(db_session, fix2).ainvoke(
        _supplier_args(fix2, purchase2, quantity="3")
    )
    assert first["status"] == "completed"
    before = await _return_counts(db_session, fix2)
    second = await _supplier_tool(db_session, fix2).ainvoke(
        _supplier_args(fix2, purchase2, quantity="1")
    )
    assert second["status"] == "error"
    assert second["code"] == "exceeds_remaining"
    assert await _return_counts(db_session, fix2) == before
    assert purchase is not None


@pytest.mark.asyncio
async def test_supplier_over_return_surfaced(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    before = await _return_counts(db_session, fix)
    out = await _supplier_tool(db_session, fix).ainvoke(
        _supplier_args(fix, purchase, quantity="99")
    )
    assert out["status"] == "error"
    assert out["code"] == "exceeds_remaining"
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_supplier_insufficient_inventory_surfaced(
    db_session: AsyncSession,
) -> None:
    # Purchase 5, sell 5 (stock 0), then supplier return must fail cleanly.
    fix = await _make_return_shop(db_session)
    purchase = await create_purchase(
        db_session,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(5),
                unit_cost=Decimal(800),
            )
        ],
    )
    await create_sale(
        db_session,
        shop_id=fix.shop.id,
        customer_id=fix.customer.id,
        items=[
            SaleItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(5),
                unit_price=Decimal(1000),
            )
        ],
    )
    assert await _inventory_qty(db_session, fix) == Decimal("0.000")
    before = await _return_counts(db_session, fix)
    out = await _supplier_tool(db_session, fix).ainvoke(
        _supplier_args(fix, purchase, quantity="2")
    )
    assert out["status"] == "error"
    assert out["code"] == "insufficient_stock"
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_supplier_domain_validation_errors(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    tool = _supplier_tool(db_session, fix)
    before = await _return_counts(db_session, fix)
    for bad_qty in ["0", "-2", "lots"]:
        out = await tool.ainvoke(
            _supplier_args(fix, purchase, quantity=bad_qty)
        )
        assert out["status"] == "error", bad_qty
    assert await _return_counts(db_session, fix) == before


# --- Supplier: purchase resolution ------------------------------------------


@pytest.mark.asyncio
async def test_supplier_purchase_resolution_by_id(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    resolved = await resolve_supplier_purchase(
        db_session, fix.shop.id, purchase_id=str(purchase.id)
    )
    assert resolved.id == purchase.id


@pytest.mark.asyncio
async def test_supplier_purchase_resolution_by_invoice(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(
        db_session, fix, purchase_invoice="P-102"
    )
    assert purchase.invoice_number == "P-102"
    resolved = await resolve_supplier_purchase(
        db_session, fix.shop.id, purchase_reference="P-102"
    )
    assert resolved.id == purchase.id
    out = await _supplier_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "purchase_reference": "P-102",
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "completed"
    assert out["purchase_id"] == str(purchase.id)


@pytest.mark.asyncio
async def test_supplier_resolution_by_supplier_name(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    resolved = await resolve_supplier_purchase(
        db_session, fix.shop.id, supplier_name=fix.supplier.name
    )
    assert resolved.id == purchase.id
    out = await _supplier_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "completed"


@pytest.mark.asyncio
async def test_supplier_purchase_ambiguity(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    await _seed_purchase_and_sale(db_session, fix)
    await create_purchase(
        db_session,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(2),
                unit_cost=Decimal(800),
            )
        ],
    )
    with pytest.raises(ReturnPrepAmbiguousError):
        await resolve_supplier_purchase(
            db_session, fix.shop.id, supplier_name=fix.supplier.name
        )
    before = await _return_counts(db_session, fix)
    out = await _supplier_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "ambiguous"
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_supplier_item_ambiguity_no_guessing(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session, product_name="Blue Cotton A")
    cat = Category(shop_id=fix.shop.id, shop=fix.shop, name="Extra")
    db_session.add(cat)
    await db_session.flush()
    prod2 = Product(
        shop_id=fix.shop.id,
        category_id=cat.id,
        name="Blue Cotton B",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(prod2)
    await db_session.flush()
    var2 = ProductVariant(
        shop_id=fix.shop.id,
        product_id=prod2.id,
        sku=f"BLU-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("800.00"),
        selling_price=Decimal("1000.00"),
        unit=Unit.METER,
    )
    db_session.add(var2)
    await db_session.flush()
    purchase = await create_purchase(
        db_session,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(5),
                unit_cost=Decimal(800),
            ),
            PurchaseItemInput(
                variant_id=var2.id,
                quantity=Decimal(5),
                unit_cost=Decimal(800),
            ),
        ],
    )
    before = await _return_counts(db_session, fix)
    out = await _supplier_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "purchase_id": str(purchase.id),
            "items": [{"product_name": "Blue Cotton", "quantity": "1"}],
        }
    )
    assert out["status"] == "ambiguous"
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_supplier_item_not_found(db_session: AsyncSession) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    before = await _return_counts(db_session, fix)
    out = await _supplier_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "purchase_id": str(purchase.id),
            "items": [
                {"product_name": "No Such Fabric", "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "not_found"
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_supplier_foreign_purchase_is_not_found(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_return_shop(db_session, name="Shop A")
    shop_b = await _make_return_shop(db_session, name="Shop B")
    purchase_b, _ = await _seed_purchase_and_sale(db_session, shop_b)
    with pytest.raises(ReturnPrepNotFoundError):
        await resolve_supplier_purchase(
            db_session, shop_a.shop.id, purchase_id=str(purchase_b.id)
        )
    out = await _supplier_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "purchase_id": str(purchase_b.id),
            "items": [
                {"product_name": shop_a.product.name, "quantity": "1"}
            ],
        }
    )
    assert out["status"] == "not_found"
    assert "shop_id" not in out["message"]


# --- Supplier: idempotency --------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_duplicate_key_executes_once(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    tool = _supplier_tool(db_session, fix)
    key = _key()
    args = _supplier_args(fix, purchase, quantity="1", idempotency_key=key)
    first = await tool.ainvoke(args)
    assert first["status"] == "completed" and first["duplicate"] is False
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["return_id"] == first["return_id"]
    n = (
        await db_session.execute(
            select(func.count(PurchaseReturn.id)).where(
                PurchaseReturn.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    assert n == 1


@pytest.mark.asyncio
async def test_supplier_failed_attempt_does_not_poison_key(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    tool = _supplier_tool(db_session, fix)
    key = _key()
    failed = await tool.ainvoke(
        {
            "idempotency_key": key,
            "purchase_id": str(purchase.id),
            "items": [
                {"product_name": fix.product.name, "quantity": "0"}
            ],
        }
    )
    assert failed["status"] == "error"
    retry = await tool.ainvoke(
        _supplier_args(fix, purchase, idempotency_key=key)
    )
    assert retry["status"] == "completed" and retry["duplicate"] is False


@pytest.mark.asyncio
async def test_supplier_concurrent_race_returns_winner(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    tool = _supplier_tool(db_session, fix)
    key = _key()
    purchase_item_id = (
        await db_session.execute(
            select(PurchaseItem.id).where(
                PurchaseItem.purchase_id == purchase.id
            )
        )
    ).scalars().first()
    assert purchase_item_id is not None
    winner = await returns_service.create_purchase_return(
        db_session,
        shop_id=fix.shop.id,
        purchase_id=purchase.id,
        lines=[
            returns_service.PurchaseReturnLineInput(
                purchase_item_id=purchase_item_id,
                quantity=Decimal(1),
            )
        ],
    )
    db_session.add(
        AISupplierReturnReceipt(
            shop_id=fix.shop.id, operation_key=key, return_id=winner.id
        )
    )
    await db_session.flush()
    before = await _return_counts(db_session, fix)
    out = await tool.ainvoke(
        {
            "idempotency_key": key,
            "purchase_id": str(purchase.id),
            "items": [
                {"product_name": fix.product.name, "quantity": "2"}
            ],
        }
    )
    assert out["status"] == "completed" and out["duplicate"] is True
    assert out["return_id"] == str(winner.id)
    assert await _return_counts(db_session, fix) == before


def test_build_supplier_return_preview() -> None:
    text = build_supplier_return_preview(
        supplier_name="Ahmed Traders",
        purchase_reference="PUR-102",
        lines=[
            {
                "product_name": "Blue Cotton",
                "quantity": "3.000",
                "unit_cost": "2500.00",
            }
        ],
        estimated_total=Decimal("7500.00"),
    )
    assert "Supplier Return" in text
    assert "Ahmed Traders" in text
    assert "PUR-102" in text
    assert "Blue Cotton" in text
    assert "3.000" in text
    assert "authoritative" in text.lower()
    assert "Please approve" in text


# --- Notes passthrough ------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_notes_passthrough_and_length(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    out = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="1", notes="damaged piece")
    )
    assert out["status"] == "completed"
    stored = await db_session.get(SaleReturn, uuid.UUID(out["return_id"]))
    assert stored is not None and stored.notes == "damaged piece"
    bad = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="1", notes="x" * 501)
    )
    assert bad["status"] == "error"


@pytest.mark.asyncio
async def test_supplier_notes_passthrough(db_session: AsyncSession) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    out = await _supplier_tool(db_session, fix).ainvoke(
        _supplier_args(fix, purchase, quantity="1", notes="defective lot")
    )
    assert out["status"] == "completed"
    stored = await db_session.get(
        PurchaseReturn, uuid.UUID(out["return_id"])
    )
    assert stored is not None and stored.notes == "defective lot"


# --- Rollback: no partial state on failure ----------------------------------


@pytest.mark.asyncio
async def test_customer_rollback_on_service_failure(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    before = await _return_counts(db_session, fix)
    qty_before = await _inventory_qty(db_session, fix)

    import app.services.returns as returns_module

    old = returns_module.create_sale_return

    async def _boom(session: AsyncSession, **kwargs: Any) -> Any:
        raise returns_module.InvalidReturnQuantityError("boom")

    returns_module.create_sale_return = _boom  # type: ignore[assignment]
    try:
        out = await _customer_tool(db_session, fix).ainvoke(
            _customer_args(fix, sale, quantity="1")
        )
    finally:
        returns_module.create_sale_return = old
    assert out["status"] == "error"
    assert await _return_counts(db_session, fix) == before
    assert await _inventory_qty(db_session, fix) == qty_before


@pytest.mark.asyncio
async def test_supplier_rollback_on_service_failure(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    before = await _return_counts(db_session, fix)
    qty_before = await _inventory_qty(db_session, fix)

    import app.services.returns as returns_module

    old = returns_module.create_purchase_return

    async def _boom(session: AsyncSession, **kwargs: Any) -> Any:
        raise returns_module.InvalidReturnQuantityError("boom")

    returns_module.create_purchase_return = _boom  # type: ignore[assignment]
    try:
        out = await _supplier_tool(db_session, fix).ainvoke(
            _supplier_args(fix, purchase, quantity="1")
        )
    finally:
        returns_module.create_purchase_return = old
    assert out["status"] == "error"
    assert await _return_counts(db_session, fix) == before
    assert await _inventory_qty(db_session, fix) == qty_before


# --- Agent-level HITL -------------------------------------------------------


def _customer_return_fake(args: dict[str, Any]) -> BaseChatModel:
    class _Fake(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "customer-return-fake"

        def bind_tools(self, tools: Any, **kwargs: Any) -> "_Fake":
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
                message = AIMessage(
                    content=f"Return recorded: {tool_msgs[-1].content}"
                )
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_customer_return",
                            "args": dict(args),
                            "id": "call_ret_1",
                            "type": "tool_call",
                        }
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return _Fake()


def _supplier_return_fake(args: dict[str, Any]) -> BaseChatModel:
    class _Fake(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "supplier-return-fake"

        def bind_tools(self, tools: Any, **kwargs: Any) -> "_Fake":
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
                message = AIMessage(
                    content=f"Return recorded: {tool_msgs[-1].content}"
                )
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_supplier_return",
                            "args": dict(args),
                            "id": "call_ret_2",
                            "type": "tool_call",
                        }
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return _Fake()


@pytest.mark.asyncio
async def test_hitl_pause_then_approve_customer_return(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    before = await _return_counts(db_session, fix)
    args = _customer_args(fix, sale, quantity="2")
    agent = build_master_agent(
        model=_customer_return_fake(args),
        include_hitl_demo=False,
        write_tools=build_return_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"cust-ret-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Ali ki sale se 2 meter wapas"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    action = paused.interrupts[0].value["action_requests"][0]
    assert action["name"] == "create_customer_return"
    assert await _return_counts(db_session, fix) == before

    resumed = await agent.ainvoke(
        Command(resume=hitl_resume_payload([approve_decision()])),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    after = await _return_counts(db_session, fix)
    assert after["sale_returns"] == before["sale_returns"] + 1
    assert after["cust_receipts"] == before["cust_receipts"] + 1


@pytest.mark.asyncio
async def test_hitl_reject_customer_return_creates_nothing(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    before = await _return_counts(db_session, fix)
    agent = build_master_agent(
        model=_customer_return_fake(_customer_args(fix, sale)),
        include_hitl_demo=False,
        write_tools=build_return_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"cust-rej-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "return karo"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    resumed = await agent.ainvoke(
        Command(
            resume=hitl_resume_payload(
                [reject_decision("User rejected the return. Do not record anything.")]
            )
        ),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    assert await _return_counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_hitl_edit_executes_corrected_customer_return(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    before = await _return_counts(db_session, fix)
    agent = build_master_agent(
        model=_customer_return_fake(_customer_args(fix, sale, quantity="2")),
        include_hitl_demo=False,
        write_tools=build_return_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"cust-edit-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "return karo"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    edited = _customer_args(fix, sale, quantity="1")
    resumed = await agent.ainvoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {
                            "name": "create_customer_return",
                            "args": edited,
                        },
                    }
                ]
            }
        ),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    after = await _return_counts(db_session, fix)
    assert after["sale_returns"] == before["sale_returns"] + 1


@pytest.mark.asyncio
async def test_hitl_pause_then_approve_supplier_return(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    before = await _return_counts(db_session, fix)
    args = _supplier_args(fix, purchase, quantity="2")
    agent = build_master_agent(
        model=_supplier_return_fake(args),
        include_hitl_demo=False,
        write_tools=build_return_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"supp-ret-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "supplier ko wapas"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    action = paused.interrupts[0].value["action_requests"][0]
    assert action["name"] == "create_supplier_return"
    assert await _return_counts(db_session, fix) == before

    resumed = await agent.ainvoke(
        Command(resume=hitl_resume_payload([approve_decision()])),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    after = await _return_counts(db_session, fix)
    assert after["purchase_returns"] == before["purchase_returns"] + 1
    assert after["supp_receipts"] == before["supp_receipts"] + 1


@pytest.mark.asyncio
async def test_hitl_reject_supplier_return_creates_nothing(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    before = await _return_counts(db_session, fix)
    agent = build_master_agent(
        model=_supplier_return_fake(_supplier_args(fix, purchase)),
        include_hitl_demo=False,
        write_tools=build_return_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"supp-rej-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "supplier return"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    resumed = await agent.ainvoke(
        Command(
            resume=hitl_resume_payload(
                [reject_decision("User rejected the return. Do not record anything.")]
            )
        ),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    assert await _return_counts(db_session, fix) == before


# --- Natural-language style references --------------------------------------


@pytest.mark.asyncio
async def test_customer_natural_reference_ali_bill(
    db_session: AsyncSession,
) -> None:
    # "Ali's bill" with a single Ali sale resolves without guessing.
    fix = await _make_return_shop(db_session, customer_name="Ali")
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    out = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "sale_reference": "Ali's bill",
            "items": [
                {"product_name": fix.product.name, "quantity": "2 meter"}
            ],
        }
    )
    assert out["status"] == "completed"
    assert out["sale_id"] == str(sale.id)


@pytest.mark.asyncio
async def test_supplier_natural_reference_last_purchase_ambiguous(
    db_session: AsyncSession,
) -> None:
    # "last purchase" with several candidates must clarify, never pick one.
    fix = await _make_return_shop(db_session)
    await _seed_purchase_and_sale(db_session, fix)
    await create_purchase(
        db_session,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(2),
                unit_cost=Decimal(800),
            )
        ],
    )
    out = await _supplier_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [
                {"product_name": fix.product.name, "quantity": "5 suits"}
            ],
        }
    )
    # Supplier is unambiguous but the purchase choice is: the tool reports
    # ambiguity for the purchase, never a silent "last" pick.
    assert out["status"] == "ambiguous"


@pytest.mark.asyncio
async def test_customer_ledger_and_inventory_effects(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    qty_before = await _inventory_qty(db_session, fix)
    out = await _customer_tool(db_session, fix).ainvoke(
        _customer_args(fix, sale, quantity="2")
    )
    assert out["status"] == "completed"
    assert await _inventory_qty(db_session, fix) == qty_before + Decimal("2.000")
    rows = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.shop_id == fix.shop.id,
                LedgerEntry.reference_id == uuid.UUID(out["return_id"]),
            )
        )
    ).scalars().all()
    assert rows
    assert {r.reference_type for r in rows} == {"SALE_RETURN"}
    assert sum(r.debit for r in rows) == sum(r.credit for r in rows)


@pytest.mark.asyncio
async def test_supplier_ledger_and_inventory_effects(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    qty_before = await _inventory_qty(db_session, fix)
    out = await _supplier_tool(db_session, fix).ainvoke(
        _supplier_args(fix, purchase, quantity="2")
    )
    assert out["status"] == "completed"
    assert await _inventory_qty(db_session, fix) == qty_before - Decimal("2.000")
    rows = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.shop_id == fix.shop.id,
                LedgerEntry.reference_id == uuid.UUID(out["return_id"]),
            )
        )
    ).scalars().all()
    assert rows
    assert {r.reference_type for r in rows} == {"PURCHASE_RETURN"}


@pytest.mark.asyncio
async def test_no_automatic_customer_or_supplier_creation(
    db_session: AsyncSession,
) -> None:
    fix = await _make_return_shop(db_session)
    _, sale = await _seed_purchase_and_sale(db_session, fix)
    purchase, _ = await _seed_purchase_and_sale(db_session, fix)
    cust_before = (
        await db_session.execute(
            select(func.count(Customer.id)).where(
                Customer.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    supp_before = (
        await db_session.execute(
            select(func.count(Supplier.id)).where(
                Supplier.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    out_c = await _customer_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Nobody Here",
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out_c["status"] == "not_found"
    out_s = await _supplier_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Nobody Here",
            "items": [
                {"product_name": fix.product.name, "quantity": "1"}
            ],
        }
    )
    assert out_s["status"] == "not_found"
    assert (
        await db_session.execute(
            select(func.count(Customer.id)).where(
                Customer.shop_id == fix.shop.id
            )
        )
    ).scalar_one() == cust_before
    assert (
        await db_session.execute(
            select(func.count(Supplier.id)).where(
                Supplier.shop_id == fix.shop.id
            )
        )
    ).scalar_one() == supp_before
    assert sale is not None and purchase is not None

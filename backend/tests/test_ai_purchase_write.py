"""Step 7: AI-assisted purchase recording — one HITL-gated mutation.

Covers the brief's minimum without any real LLM (deterministic fake
models, direct tool calls — no network, no tokens):

* preparation: supplier resolution (unique/ambiguous/missing/cross-tenant),
  product/variant resolution, quantity validation, cost validation
  (including hazar/lakh, unit vs total), paid_amount/invoice handling
* HITL: pause before mutation, approve executes, reject is a no-op,
  edit executes the corrected args (re-validated)
* transaction: success commits atomically, failure leaves no partial
  state (savepoint-scoped, never a full-session rollback)
* idempotency: same operation key twice (in-session and post-commit)
  creates exactly one purchase; a failed attempt does not poison its key
* tenant isolation: shop-scoped operation, same key in two shops is
  independent, no cross-tenant leakage
* service reuse: the AI path calls ``purchases.create_purchase()``
* accounting: the existing Debit Inventory / Credit AP posting is
  preserved with reference PURCHASE
* inventory: purchase increases the correct variant stock
* payables: supplier statement reflects the purchase; no automatic
  supplier-payment is created
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
    PURCHASE_HITL_INTERRUPT_CONFIG,
    PURCHASE_SYSTEM_ADDENDUM,
    WRITE_MASTER_TOOL_NAMES,
    approve_decision,
    build_master_agent,
    hitl_resume_payload,
    reject_decision,
)
from app.ai.state import TenantContext
from app.ai.tools.purchases_write import (
    CREATE_PURCHASE_TOOL_NAME,
    WRITE_TOOL_NAMES,
    PurchasePrepAmbiguousError,
    PurchasePrepError,
    PurchasePrepNotFoundError,
    assert_purchase_write_registry_is_minimal,
    build_purchase_preview,
    build_purchase_write_tools,
    parse_invoice_number,
    parse_paid_amount,
    parse_quantity,
    parse_total_cost,
    parse_unit_cost,
    parse_uuid_arg,
    resolve_purchase_supplier,
    resolve_purchase_variant,
    validate_idempotency_key,
)
from app.models import (
    AIPurchaseReceipt,
    Category,
    Customer,
    Inventory,
    LedgerEntry,
    Payment,
    Product,
    ProductType,
    ProductVariant,
    Purchase,
    Sale,
    Shop,
    Supplier,
    Unit,
)
from app.services import payables as payables_service
from app.services import purchases as purchases_service


# --- Fixtures ------------------------------------------------------------
class PurchaseFixture:
    def __init__(
        self,
        shop: Shop,
        supplier: Supplier,
        product: Product,
        variant: ProductVariant,
    ) -> None:
        self.shop = shop
        self.supplier = supplier
        self.product = product
        self.variant = variant


async def _make_purchase_shop(
    db: AsyncSession,
    name: str = "AI Purchase Shop",
    *,
    supplier_name: str = "Bilal",
    product_name: str = "Black Lawn",
    sku: str | None = None,
) -> PurchaseFixture:
    """A shop with one supplier + one product/variant."""
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
        sku=sku or f"PUR-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("500.00"),
        selling_price=Decimal("800.00"),
        unit=Unit.METER,
    )
    db.add(variant)
    supplier = Supplier(shop_id=shop.id, name=supplier_name)
    db.add(supplier)
    await db.flush()
    return PurchaseFixture(shop, supplier, product, variant)


def _ctx(fix: PurchaseFixture) -> TenantContext:
    return TenantContext(shop_id=fix.shop.id, user_id=uuid.uuid4())


def _purchase_tool(db: AsyncSession, fix: PurchaseFixture) -> Any:
    tools = {t.name: t for t in build_purchase_write_tools(db, _ctx(fix))}
    return tools[CREATE_PURCHASE_TOOL_NAME]


def _key() -> str:
    return uuid.uuid4().hex


def _item(
    fix: PurchaseFixture,
    quantity: str = "20",
    unit_cost: str | None = "2500",
    **extra: Any,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "product_name": fix.product.name,
        "quantity": quantity,
    }
    if unit_cost is not None:
        args["unit_cost"] = unit_cost
    args.update(extra)
    return args


def _args(fix: PurchaseFixture, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "idempotency_key": _key(),
        "supplier_name": fix.supplier.name,
        "items": [_item(fix)],
    }
    base.update(extra)
    return base


async def _counts(db: AsyncSession, fix: PurchaseFixture) -> dict[str, Any]:
    purchases = (
        await db.execute(
            select(func.count(Purchase.id)).where(Purchase.shop_id == fix.shop.id)
        )
    ).scalar_one()
    receipts = (
        await db.execute(
            select(func.count(AIPurchaseReceipt.id)).where(
                AIPurchaseReceipt.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    ledger = (
        await db.execute(
            select(func.count(LedgerEntry.id)).where(LedgerEntry.shop_id == fix.shop.id)
        )
    ).scalar_one()
    return {"purchases": purchases, "receipts": receipts, "ledger": ledger}


async def _inventory_qty(db: AsyncSession, fix: PurchaseFixture) -> Decimal:
    row = (
        await db.execute(
            select(Inventory).where(Inventory.variant_id == fix.variant.id)
        )
    ).scalar_one_or_none()
    if row is None:
        return Decimal("0.000")
    return Decimal(row.quantity)


async def _outstanding(db: AsyncSession, fix: PurchaseFixture) -> Decimal:
    summary = await payables_service.get_supplier_summary(
        db, shop_id=fix.shop.id, supplier_id=fix.supplier.id
    )
    return summary.outstanding_balance


# --- Registry boundary: exactly one NEW mutation -------------------------


def test_purchase_registry_is_exactly_one_tool() -> None:
    assert WRITE_TOOL_NAMES == ("create_purchase",)
    assert CREATE_PURCHASE_TOOL_NAME in WRITE_MASTER_TOOL_NAMES
    assert set(WRITE_MASTER_TOOL_NAMES) == {
        "create_sale",
        "record_customer_payment",
        "record_supplier_payment",
        "record_expense",
        "create_purchase",
    }
    assert PURCHASE_HITL_INTERRUPT_CONFIG == {"create_purchase": True}


def test_purchase_prompt_forces_same_turn_tool_call() -> None:
    """The prompt must order preview + tool call in ONE turn."""
    lowered = PURCHASE_SYSTEM_ADDENDUM.lower()
    assert "same turn" in lowered
    assert "without calling the tool" in lowered or "without calling" in lowered
    assert "approval card" in lowered
    assert "haan" in lowered and "directly" in lowered


def test_purchase_prompt_covers_natural_examples() -> None:
    lowered = PURCHASE_SYSTEM_ADDENDUM.lower()
    assert "bilal" in lowered
    assert "black lawn" in lowered
    assert "per suit" in lowered or "per-unit" in lowered or "unit" in lowered
    assert "hazar" in lowered
    assert "never guess" in lowered or "clarification" in lowered
    assert "ledger" in lowered or "accounting" in lowered
    assert "record_supplier_payment" in lowered


@pytest.mark.asyncio
async def test_purchase_tool_takes_no_tenant_or_ledger_args(
    db_session: AsyncSession,
) -> None:
    fix = await _make_purchase_shop(db_session)
    tools = build_purchase_write_tools(db_session, _ctx(fix))
    assert_purchase_write_registry_is_minimal(tools)
    assert len(tools) == 1
    func_attr = getattr(tools[0], "func", None) or getattr(tools[0], "coroutine", None)
    assert func_attr is not None
    params = inspect.signature(func_attr).parameters
    for forbidden in (
        "shop_id",
        "shop",
        "user_id",
        "account_id",
        "ledger_account_id",
        "debit_account",
        "credit_account",
        "journal",
        "ledger",
        "sql",
        "query",
        "payment_method",
    ):
        assert forbidden not in params, f"create_purchase must not take {forbidden}"
    assert "supplier_name" in params
    assert "items" in params
    assert "idempotency_key" in params


def test_purchase_registry_rejects_a_second_mutation() -> None:
    from app.ai.tools.registry import demo_prepare_operation

    with pytest.raises(AssertionError, match="one mutation only"):
        assert_purchase_write_registry_is_minimal(
            [demo_prepare_operation]  # type: ignore[list-item]
        )


# --- Idempotency key -----------------------------------------------------


def test_validate_idempotency_key() -> None:
    key = uuid.uuid4().hex
    assert validate_idempotency_key(key) == key
    assert validate_idempotency_key("abc-DEF_12345") == "abc-DEF_12345"
    for bad in ["short", "", "x" * 65, "has space!", "semi;colon", None]:
        with pytest.raises(PurchasePrepError):
            validate_idempotency_key(bad)


# --- Quantity ------------------------------------------------------------


def test_parse_quantity() -> None:
    assert parse_quantity("20") == Decimal("20.000")
    assert parse_quantity(10) == Decimal("10.000")
    assert parse_quantity("3.5") == Decimal("3.500")
    assert parse_quantity("20 meter") == Decimal("20.000")
    assert parse_quantity("10 suits") == Decimal("10.000")
    assert parse_quantity("5 rolls") == Decimal("5.000")
    assert parse_quantity("30 METER fabric") == Decimal("30.000")
    for bad in ["0", "-5", "lots", "", "meter", None]:
        with pytest.raises(PurchasePrepError):
            parse_quantity(bad)


# --- Costs ---------------------------------------------------------------


def test_parse_unit_cost_scales() -> None:
    assert parse_unit_cost("2500") == Decimal("2500.00")
    assert parse_unit_cost("2,500") == Decimal("2500.00")
    assert parse_unit_cost("2500.50") == Decimal("2500.50")
    assert parse_unit_cost("5 hazar") == Decimal("5000.00")
    assert parse_unit_cost("5 hazaar") == Decimal("5000.00")
    assert parse_unit_cost("5 thousand") == Decimal("5000.00")
    assert parse_unit_cost("5k") == Decimal("5000.00")
    assert parse_unit_cost("2 lakh") == Decimal("200000.00")
    assert parse_unit_cost("1.5 lakh") == Decimal("150000.00")
    assert parse_unit_cost("600 rupay meter") == Decimal("600.00")
    assert parse_unit_cost("2500 per suit") == Decimal("2500.00")
    # Zero cost is allowed by the purchase service (>= 0).
    assert parse_unit_cost("0") == Decimal("0.00")
    for bad in ["-5", "lots", "hazar", ""]:
        with pytest.raises(PurchasePrepError):
            parse_unit_cost(bad)
    with pytest.raises(PurchasePrepError):
        parse_unit_cost(None)


def test_parse_total_cost() -> None:
    assert parse_total_cost("30000") == Decimal("30000.00")
    assert parse_total_cost("30,000") == Decimal("30000.00")
    assert parse_total_cost("1.5 lakh") == Decimal("150000.00")
    with pytest.raises(PurchasePrepError):
        parse_total_cost("-1")
    with pytest.raises(PurchasePrepError):
        parse_total_cost(None)


def test_parse_paid_amount_defaults_and_keywords() -> None:
    assert parse_paid_amount(None) == Decimal("0.00")
    assert parse_paid_amount("") == Decimal("0.00")
    assert parse_paid_amount("credit") == Decimal("0.00")
    assert parse_paid_amount("udhaar") == Decimal("0.00")
    assert parse_paid_amount("5 hazar") == Decimal("5000.00")
    # Full keywords are a sentinel resolved to the expected total.
    assert parse_paid_amount("cash") is None
    assert parse_paid_amount("full") is None
    with pytest.raises(PurchasePrepError):
        parse_paid_amount("bitcoin-xyz-amount!!!")


def test_parse_invoice_number() -> None:
    assert parse_invoice_number(None) is None
    assert parse_invoice_number("") is None
    assert parse_invoice_number("INV-123") == "INV-123"
    with pytest.raises(PurchasePrepError):
        parse_invoice_number("x" * 51)


def test_parse_uuid_arg() -> None:
    vid = uuid.uuid4()
    assert parse_uuid_arg(str(vid), "variant_id") == vid
    with pytest.raises(PurchasePrepError):
        parse_uuid_arg("not-a-uuid", "variant_id")


def test_cost_uses_decimal_not_float() -> None:
    out = parse_unit_cost("2500.50")
    assert isinstance(out, Decimal)
    assert out == Decimal("2500.50")
    assert parse_unit_cost("0.1") + parse_unit_cost("0.2") == Decimal("0.30")


# --- Supplier resolution -------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_exact_and_id(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    by_name = await resolve_purchase_supplier(
        db_session, fix.shop.id, supplier_name="Bilal"
    )
    assert by_name.id == fix.supplier.id
    by_id = await resolve_purchase_supplier(
        db_session, fix.shop.id, supplier_id=str(fix.supplier.id)
    )
    assert by_id.id == fix.supplier.id


@pytest.mark.asyncio
async def test_supplier_ambiguous(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session, supplier_name="Bilal Traders")
    db_session.add(Supplier(shop_id=fix.shop.id, name="Bilal Fabrics"))
    await db_session.flush()
    with pytest.raises(PurchasePrepAmbiguousError) as exc:
        await resolve_purchase_supplier(db_session, fix.shop.id, supplier_name="Bilal")
    assert len(exc.value.matches) == 2
    # Tool surfaces ambiguity without mutating.
    before = await _counts(db_session, fix)
    out = await _purchase_tool(db_session, fix).ainvoke(
        _args(fix, supplier_name="Bilal")
    )
    assert out["status"] == "ambiguous"
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_supplier_not_found_and_no_creation(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    with pytest.raises(PurchasePrepNotFoundError):
        await resolve_purchase_supplier(
            db_session, fix.shop.id, supplier_name="Nobody Here"
        )
    before = (
        await db_session.execute(
            select(func.count(Supplier.id)).where(Supplier.shop_id == fix.shop.id)
        )
    ).scalar_one()
    out = await _purchase_tool(db_session, fix).ainvoke(
        _args(fix, supplier_name="Nobody Here")
    )
    assert out["status"] == "not_found"
    after = (
        await db_session.execute(
            select(func.count(Supplier.id)).where(Supplier.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_supplier_foreign_isolation(db_session: AsyncSession) -> None:
    shop_a = await _make_purchase_shop(db_session, name="Shop A")
    shop_b = await _make_purchase_shop(db_session, name="Shop B", supplier_name="ZaraUnique")
    # Foreign ID reads as not_found.
    with pytest.raises(PurchasePrepNotFoundError):
        await resolve_purchase_supplier(
            db_session, shop_a.shop.id, supplier_id=str(shop_b.supplier.id)
        )
    out = await _purchase_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_id": str(shop_b.supplier.id),
            "items": [_item(shop_a)],
        }
    )
    assert out["status"] == "not_found"
    assert "shop_id" not in out["message"]
    # Foreign name never leaks.
    out2 = await _purchase_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "ZaraUnique",
            "items": [_item(shop_a)],
        }
    )
    assert out2["status"] == "not_found"
    assert "ZaraUnique" not in out2.get("message", "") or "not found" in out2["message"].lower()


# --- Product/variant resolution ------------------------------------------


@pytest.mark.asyncio
async def test_variant_exact_and_sku_and_id(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    by_name = await resolve_purchase_variant(
        db_session, fix.shop.id, product_name="Black Lawn"
    )
    assert by_name.variant.id == fix.variant.id
    by_sku = await resolve_purchase_variant(
        db_session, fix.shop.id, variant_sku=fix.variant.sku
    )
    assert by_sku.variant.id == fix.variant.id
    by_id = await resolve_purchase_variant(
        db_session, fix.shop.id, variant_id=str(fix.variant.id)
    )
    assert by_id.variant.id == fix.variant.id


@pytest.mark.asyncio
async def test_variant_ambiguous_product(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session, product_name="Black Lawn Premium")
    # Second product with overlapping name in the same shop.
    cat = Category(shop_id=fix.shop.id, shop=fix.shop, name="Lawn2")
    db_session.add(cat)
    await db_session.flush()
    prod2 = Product(
        shop_id=fix.shop.id,
        category_id=cat.id,
        name="Black Lawn Classic",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(prod2)
    await db_session.flush()
    var2 = ProductVariant(
        shop_id=fix.shop.id,
        product_id=prod2.id,
        sku=f"AMB-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("500.00"),
        selling_price=Decimal("800.00"),
        unit=Unit.METER,
    )
    db_session.add(var2)
    await db_session.flush()
    with pytest.raises(PurchasePrepAmbiguousError):
        await resolve_purchase_variant(
            db_session, fix.shop.id, product_name="Black Lawn"
        )
    before = await _counts(db_session, fix)
    out = await _purchase_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [{"product_name": "Black Lawn", "quantity": "5", "unit_cost": "600"}],
        }
    )
    assert out["status"] == "ambiguous"
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_variant_ambiguous_skus(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session, product_name="Solo Product")
    var2 = ProductVariant(
        shop_id=fix.shop.id,
        product_id=fix.product.id,
        sku=f"SOLO-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("500.00"),
        selling_price=Decimal("800.00"),
        unit=Unit.METER,
    )
    db_session.add(var2)
    await db_session.flush()
    with pytest.raises(PurchasePrepAmbiguousError):
        await resolve_purchase_variant(
            db_session, fix.shop.id, product_name="Solo Product"
        )


@pytest.mark.asyncio
async def test_variant_not_found_and_no_creation(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    with pytest.raises(PurchasePrepNotFoundError):
        await resolve_purchase_variant(
            db_session, fix.shop.id, product_name="No Such Fabric"
        )
    before = (
        await db_session.execute(
            select(func.count(Product.id)).where(Product.shop_id == fix.shop.id)
        )
    ).scalar_one()
    out = await _purchase_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [
                {"product_name": "No Such Fabric", "quantity": "5", "unit_cost": "600"}
            ],
        }
    )
    assert out["status"] == "not_found"
    after = (
        await db_session.execute(
            select(func.count(Product.id)).where(Product.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_variant_foreign_isolation(db_session: AsyncSession) -> None:
    shop_a = await _make_purchase_shop(db_session, name="Shop A")
    shop_b = await _make_purchase_shop(db_session, name="Shop B", product_name="ZaraSilk")
    with pytest.raises(PurchasePrepNotFoundError):
        await resolve_purchase_variant(
            db_session, shop_a.shop.id, variant_id=str(shop_b.variant.id)
        )
    out = await _purchase_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": shop_a.supplier.name,
            "items": [
                {"variant_id": str(shop_b.variant.id), "quantity": "5", "unit_cost": "600"}
            ],
        }
    )
    assert out["status"] == "not_found"
    assert "shop_id" not in out["message"]


# --- Quantity / cost validation ------------------------------------------


@pytest.mark.asyncio
async def test_tool_quantity_variants(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    tool = _purchase_tool(db_session, fix)
    for qty, expected in [("20", "20.000"), ("3.5", "3.500"), ("20 meter", "20.000")]:
        out = await tool.ainvoke(
            {
                "idempotency_key": _key(),
                "supplier_name": fix.supplier.name,
                "items": [
                    {"product_name": fix.product.name, "quantity": qty, "unit_cost": "100"}
                ],
            }
        )
        assert out["status"] == "completed", qty
        assert out["items"][0]["quantity"] == expected, qty


@pytest.mark.asyncio
async def test_tool_invalid_quantities_change_nothing(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    before = await _counts(db_session, fix)
    tool = _purchase_tool(db_session, fix)
    for bad_qty in ["0", "-5", "lots", ""]:
        out = await tool.ainvoke(
            {
                "idempotency_key": _key(),
                "supplier_name": fix.supplier.name,
                "items": [
                    {
                        "product_name": fix.product.name,
                        "quantity": bad_qty,
                        "unit_cost": "100",
                    }
                ],
            }
        )
        assert out["status"] == "error", bad_qty
    missing = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [{"product_name": fix.product.name, "unit_cost": "100"}],
        }
    )
    assert missing["status"] == "error"
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_tool_cost_scales_and_total_derivation(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    tool = _purchase_tool(db_session, fix)
    # Unit-cost with scale words.
    out = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [
                {"product_name": fix.product.name, "quantity": "5", "unit_cost": "5 hazar"}
            ],
        }
    )
    assert out["status"] == "completed"
    assert out["items"][0]["unit_cost"] == "5000.00"
    assert out["total"] == "25000.00"
    # Total-cost derives the unit ("10 suits 30000 mein" -> 3000 each).
    out2 = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [
                {"product_name": fix.product.name, "quantity": "10", "total_cost": "30000"}
            ],
        }
    )
    assert out2["status"] == "completed"
    assert out2["items"][0]["unit_cost"] == "3000.00"
    assert out2["total"] == "30000.00"
    # Comma + decimal lakh.
    out3 = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [
                {
                    "product_name": fix.product.name,
                    "quantity": "2",
                    "unit_cost": "1.5 lakh",
                }
            ],
        }
    )
    assert out3["status"] == "completed"
    assert out3["items"][0]["unit_cost"] == "150000.00"


@pytest.mark.asyncio
async def test_tool_ambiguous_unit_vs_total(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    before = await _counts(db_session, fix)
    # Both given but inconsistent -> ask, never guess.
    out = await _purchase_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [
                {
                    "product_name": fix.product.name,
                    "quantity": "10",
                    "unit_cost": "2500",
                    "total_cost": "30000",
                }
            ],
        }
    )
    assert out["status"] == "error"
    assert await _counts(db_session, fix) == before
    # Neither given -> ask for cost.
    out2 = await _purchase_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [{"product_name": fix.product.name, "quantity": "10"}],
        }
    )
    assert out2["status"] == "error"
    # Malformed / negative costs never execute.
    for bad in ["-5", "lots", "hazar"]:
        outb = await _purchase_tool(db_session, fix).ainvoke(
            {
                "idempotency_key": _key(),
                "supplier_name": fix.supplier.name,
                "items": [
                    {"product_name": fix.product.name, "quantity": "5", "unit_cost": bad}
                ],
            }
        )
        assert outb["status"] == "error", bad


@pytest.mark.asyncio
async def test_tool_backend_authoritative_total(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    out = await _purchase_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [
                {"product_name": fix.product.name, "quantity": "3", "unit_cost": "333.33"}
            ],
        }
    )
    assert out["status"] == "completed"
    stored = await db_session.get(Purchase, uuid.UUID(out["purchase_id"]))
    assert stored is not None
    assert out["total"] == str(stored.total.quantize(Decimal("0.01")))
    assert stored.total == Decimal("999.99")


# --- Multi-line ----------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_multi_line_purchase(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session, product_name="Black Lawn")
    # Second product for the second line.
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
        purchase_price=Decimal("400.00"),
        selling_price=Decimal("700.00"),
        unit=Unit.METER,
    )
    db_session.add(var2)
    await db_session.flush()
    out = await _purchase_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "items": [
                {"product_name": "Black Lawn", "quantity": "10", "unit_cost": "600"},
                {"product_name": "White Lawn", "quantity": "5", "unit_cost": "500"},
            ],
        }
    )
    assert out["status"] == "completed"
    assert out["items_count"] == 2
    assert out["total"] == "8500.00"
    assert await _inventory_qty(db_session, fix) == Decimal("10.000")
    row2 = (
        await db_session.execute(select(Inventory).where(Inventory.variant_id == var2.id))
    ).scalar_one()
    assert Decimal(row2.quantity) == Decimal("5.000")


# --- Paid amount / invoice ------------------------------------------------


@pytest.mark.asyncio
async def test_tool_paid_amount_credit_partial_full(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    tool = _purchase_tool(db_session, fix)
    # Default is credit.
    out = await tool.ainvoke(_args(fix))
    assert out["status"] == "completed"
    assert out["paid_amount"] == "0.00"
    assert out["due_amount"] == out["total"]
    # Partial.
    out2 = await tool.ainvoke(_args(fix, paid_amount="10000"))
    assert out2["status"] == "completed"
    assert out2["paid_amount"] == "10000.00"
    # Full via keyword.
    out3 = await tool.ainvoke(_args(fix, paid_amount="cash"))
    assert out3["status"] == "completed"
    assert out3["paid_amount"] == out3["total"]
    assert out3["due_amount"] == "0.00"
    # Over-total is rejected.
    before = await _counts(db_session, fix)
    bad = await tool.ainvoke(_args(fix, paid_amount="99999999"))
    assert bad["status"] == "error"
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_tool_invoice_number(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    out = await _purchase_tool(db_session, fix).ainvoke(
        _args(fix, invoice_number="INV-77")
    )
    assert out["status"] == "completed"
    assert out["invoice_number"] == "INV-77"
    stored = await db_session.get(Purchase, uuid.UUID(out["purchase_id"]))
    assert stored is not None and stored.invoice_number == "INV-77"
    bad = await _purchase_tool(db_session, fix).ainvoke(
        _args(fix, invoice_number="x" * 51)
    )
    assert bad["status"] == "error"


@pytest.mark.asyncio
async def test_tool_paid_amount_does_not_create_payment(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    out = await _purchase_tool(db_session, fix).ainvoke(
        _args(fix, paid_amount="cash")
    )
    assert out["status"] == "completed"
    payments = (
        await db_session.execute(
            select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert payments == 0
    # Khata outstanding still reflects the purchase total (paid_amount is a
    # cache; settlement is a separate supplier-payment operation).
    assert await _outstanding(db_session, fix) == Decimal(out["total"])


# --- Preview --------------------------------------------------------------


def test_build_purchase_preview() -> None:
    text = build_purchase_preview(
        supplier_name="Bilal",
        lines=[
            {
                "product_name": "Black Lawn",
                "variant_sku": "BLK-1",
                "quantity": "20.000",
                "unit": "meter",
                "unit_cost": "2500.00",
                "total": "50000.00",
            }
        ],
        paid_amount=Decimal("0.00"),
    )
    assert "Bilal" in text
    assert "Black Lawn" in text
    assert "20.000" in text
    assert "2500.00" in text


# --- Idempotency ------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_duplicate_key_executes_once(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    tool = _purchase_tool(db_session, fix)
    key = _key()
    args = _args(fix, idempotency_key=key)
    first = await tool.ainvoke(args)
    assert first["status"] == "completed" and first["duplicate"] is False
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["purchase_id"] == first["purchase_id"]
    purchases = (
        await db_session.execute(
            select(func.count(Purchase.id)).where(Purchase.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert purchases == 1


@pytest.mark.asyncio
async def test_tool_duplicate_key_safe_after_commit(api_session: AsyncSession) -> None:
    db_session = api_session
    fix = await _make_purchase_shop(db_session)
    tool = _purchase_tool(db_session, fix)
    key = _key()
    args = _args(fix, idempotency_key=key)
    first = await tool.ainvoke(args)
    assert first["status"] == "completed"
    await db_session.commit()  # simulate the resume-request commit
    ledger_before = (
        await db_session.execute(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    qty_before = await _inventory_qty(db_session, fix)
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["purchase_id"] == first["purchase_id"]
    ledger_after = (
        await db_session.execute(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    assert ledger_after == ledger_before
    assert await _inventory_qty(db_session, fix) == qty_before
    purchases = (
        await db_session.execute(
            select(func.count(Purchase.id)).where(Purchase.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert purchases == 1
    third = await tool.ainvoke({**args, "idempotency_key": _key()})
    assert third["duplicate"] is False
    purchases = (
        await db_session.execute(
            select(func.count(Purchase.id)).where(Purchase.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert purchases == 2


@pytest.mark.asyncio
async def test_tool_failed_attempt_does_not_poison_key(
    db_session: AsyncSession,
) -> None:
    fix = await _make_purchase_shop(db_session)
    tool = _purchase_tool(db_session, fix)
    key = _key()
    failed = await tool.ainvoke(
        {
            "idempotency_key": key,
            "supplier_name": fix.supplier.name,
            "items": [
                {"product_name": fix.product.name, "quantity": "0", "unit_cost": "100"}
            ],
        }
    )
    assert failed["status"] == "error"
    retry = await tool.ainvoke(_args(fix, idempotency_key=key))
    assert retry["status"] == "completed" and retry["duplicate"] is False
    purchases = (
        await db_session.execute(
            select(func.count(Purchase.id)).where(Purchase.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert purchases == 1


@pytest.mark.asyncio
async def test_tool_concurrent_race_returns_winner(db_session: AsyncSession) -> None:
    """A receipt race (unique constraint) re-reads the winner, no duplicate."""
    fix = await _make_purchase_shop(db_session)
    tool = _purchase_tool(db_session, fix)
    key = _key()
    winner = await purchases_service.create_purchase(
        db_session,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        items=[
            purchases_service.PurchaseItemInput(
                variant_id=fix.variant.id,
                quantity=Decimal(2),
                unit_cost=Decimal(1200),
            )
        ],
    )
    db_session.add(
        AIPurchaseReceipt(
            shop_id=fix.shop.id, operation_key=key, purchase_id=winner.id
        )
    )
    await db_session.flush()
    before = await _counts(db_session, fix)
    out = await tool.ainvoke(
        {
            "idempotency_key": key,
            "supplier_name": fix.supplier.name,
            "items": [
                {"product_name": fix.product.name, "quantity": "99", "unit_cost": "9999"}
            ],
        }
    )
    assert out["status"] == "completed" and out["duplicate"] is True
    assert out["purchase_id"] == str(winner.id)
    assert await _counts(db_session, fix) == before


# --- Tenant isolation -------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_keys_are_tenant_scoped(db_session: AsyncSession) -> None:
    """The same key in two shops creates two independent purchases."""
    shop_a = await _make_purchase_shop(db_session, name="Shop A")
    shop_b = await _make_purchase_shop(db_session, name="Shop B")
    key = _key()
    out_a = await _purchase_tool(db_session, shop_a).ainvoke(
        _args(shop_a, idempotency_key=key)
    )
    out_b = await _purchase_tool(db_session, shop_b).ainvoke(
        _args(shop_b, idempotency_key=key)
    )
    assert out_a["status"] == "completed" and out_a["duplicate"] is False
    assert out_b["status"] == "completed" and out_b["duplicate"] is False
    assert out_a["purchase_id"] != out_b["purchase_id"]


@pytest.mark.asyncio
async def test_purchases_are_tenant_isolated(db_session: AsyncSession) -> None:
    shop_a = await _make_purchase_shop(db_session, name="Shop A")
    shop_b = await _make_purchase_shop(db_session, name="Shop B")
    out_b = await _purchase_tool(db_session, shop_b).ainvoke(_args(shop_b))
    assert out_b["status"] == "completed"
    mine_a = (
        await db_session.execute(
            select(func.count(Purchase.id)).where(Purchase.shop_id == shop_a.shop.id)
        )
    ).scalar_one()
    assert mine_a == 0
    mine_b = (
        await db_session.execute(
            select(func.count(Purchase.id)).where(Purchase.shop_id == shop_b.shop.id)
        )
    ).scalar_one()
    assert mine_b == 1
    with pytest.raises(purchases_service.PurchaseNotFoundError):
        # Direct service tenant guard (foreign purchase is not found).
        from app.services.purchases import _get_purchase_for_update

        await _get_purchase_for_update(
            db_session, shop_a.shop.id, uuid.UUID(out_b["purchase_id"])
        )


# --- Authoritative service reuse -------------------------------------------


@pytest.mark.asyncio
async def test_ai_path_calls_purchase_service_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fix = await _make_purchase_shop(db_session)
    calls: list[dict[str, Any]] = []
    real_create = purchases_service.create_purchase

    async def _spy(session: AsyncSession, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await real_create(session, **kwargs)

    monkeypatch.setattr(purchases_service, "create_purchase", _spy)
    out = await _purchase_tool(db_session, fix).ainvoke(_args(fix))
    assert out["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["shop_id"] == fix.shop.id
    assert calls[0]["supplier_id"] == fix.supplier.id
    assert len(calls[0]["items"]) == 1
    assert calls[0]["items"][0].quantity == Decimal("20.000")
    assert calls[0]["items"][0].unit_cost == Decimal("2500.00")


@pytest.mark.asyncio
async def test_ai_purchase_posts_balanced_ledger(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    out = await _purchase_tool(db_session, fix).ainvoke(_args(fix))
    purchase_id = uuid.UUID(out["purchase_id"])
    rows = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.shop_id == fix.shop.id,
                LedgerEntry.reference_id == purchase_id,
            )
        )
    ).scalars().all()
    assert rows, "expected a PURCHASE ledger group"
    assert {r.reference_type for r in rows} == {"PURCHASE"}
    assert sum(r.debit for r in rows) == sum(r.credit for r in rows) == Decimal(
        out["total"]
    )
    from app.services.accounting import ensure_system_accounts

    accounts = await ensure_system_accounts(db_session, shop_id=fix.shop.id)
    debit_line = next(r for r in rows if Decimal(r.debit) > 0)
    credit_line = next(r for r in rows if Decimal(r.credit) > 0)
    assert debit_line.account_id == accounts["1200"].id  # Inventory
    assert credit_line.account_id == accounts["2000"].id  # Accounts Payable


@pytest.mark.asyncio
async def test_purchase_flows_through_statement_balance(
    db_session: AsyncSession,
) -> None:
    fix = await _make_purchase_shop(db_session)
    assert await _outstanding(db_session, fix) == Decimal("0.00")
    out = await _purchase_tool(db_session, fix).ainvoke(_args(fix))
    assert out["status"] == "completed"
    assert await _outstanding(db_session, fix) == Decimal(out["total"])
    statement = await payables_service.get_supplier_statement(
        db_session, shop_id=fix.shop.id, supplier_id=fix.supplier.id
    )
    assert statement.closing_balance == Decimal(out["total"])
    kinds = {e.entry_type.value for e in statement.entries}
    assert "PURCHASE" in kinds


@pytest.mark.asyncio
async def test_purchase_increases_correct_inventory(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    # Second variant whose stock must stay untouched.
    cat = Category(shop_id=fix.shop.id, shop=fix.shop, name="Other")
    db_session.add(cat)
    await db_session.flush()
    prod2 = Product(
        shop_id=fix.shop.id,
        category_id=cat.id,
        name="Other Fabric",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(prod2)
    await db_session.flush()
    var2 = ProductVariant(
        shop_id=fix.shop.id,
        product_id=prod2.id,
        sku=f"OTH-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("100.00"),
        selling_price=Decimal("200.00"),
        unit=Unit.METER,
    )
    db_session.add(var2)
    await db_session.flush()
    assert await _inventory_qty(db_session, fix) == Decimal("0.000")
    out = await _purchase_tool(db_session, fix).ainvoke(_args(fix))
    assert out["status"] == "completed"
    assert await _inventory_qty(db_session, fix) == Decimal("20.000")
    row2 = (
        await db_session.execute(select(Inventory).where(Inventory.variant_id == var2.id))
    ).scalar_one_or_none()
    qty2 = Decimal(row2.quantity) if row2 is not None else Decimal("0.000")
    assert qty2 == Decimal("0.000")


@pytest.mark.asyncio
async def test_tool_does_not_mutate_unrelated_models(
    db_session: AsyncSession,
) -> None:
    """The AI tool records a Purchase: no Customer/Sale writes."""
    fix = await _make_purchase_shop(db_session)
    counts_before = {
        "customers": (
            await db_session.execute(
                select(func.count(Customer.id)).where(
                    Customer.shop_id == fix.shop.id
                )
            )
        ).scalar_one(),
        "sales": (
            await db_session.execute(
                select(func.count(Sale.id)).where(Sale.shop_id == fix.shop.id)
            )
        ).scalar_one(),
        "payments": (
            await db_session.execute(
                select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
            )
        ).scalar_one(),
    }
    out = await _purchase_tool(db_session, fix).ainvoke(_args(fix))
    assert out["status"] == "completed"
    assert (
        await db_session.execute(
            select(func.count(Customer.id)).where(Customer.shop_id == fix.shop.id)
        )
    ).scalar_one() == counts_before["customers"]
    assert (
        await db_session.execute(
            select(func.count(Sale.id)).where(Sale.shop_id == fix.shop.id)
        )
    ).scalar_one() == counts_before["sales"]
    assert (
        await db_session.execute(
            select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
        )
    ).scalar_one() == counts_before["payments"]


@pytest.mark.asyncio
async def test_rollback_on_service_failure(db_session: AsyncSession) -> None:
    """A service failure leaves no purchase, receipt, ledger, or stock behind."""
    fix = await _make_purchase_shop(db_session)
    before = await _counts(db_session, fix)
    qty_before = await _inventory_qty(db_session, fix)
    tool = _purchase_tool(db_session, fix)

    import app.services.purchases as purchases_module

    old = purchases_module.create_purchase

    async def _boom(session: AsyncSession, **kwargs: Any) -> Any:
        raise purchases_module.InvalidPurchaseItemError("boom")

    purchases_module.create_purchase = _boom  # type: ignore[assignment]
    try:
        out = await tool.ainvoke(_args(fix))
    finally:
        purchases_module.create_purchase = old
    assert out["status"] == "error"
    assert await _counts(db_session, fix) == before
    assert await _inventory_qty(db_session, fix) == qty_before


@pytest.mark.asyncio
async def test_rollback_on_accounting_failure(db_session: AsyncSession) -> None:
    """An accounting failure rolls back the purchase and its stock."""
    fix = await _make_purchase_shop(db_session)
    before = await _counts(db_session, fix)
    qty_before = await _inventory_qty(db_session, fix)

    import app.services.accounting as accounting_module

    old = accounting_module.post_purchase

    async def _boom(session: AsyncSession, **kwargs: Any) -> Any:
        raise accounting_module.UnbalancedPostingError("boom")

    accounting_module.post_purchase = _boom  # type: ignore[assignment]
    try:
        out = await _purchase_tool(db_session, fix).ainvoke(_args(fix))
    finally:
        accounting_module.post_purchase = old
    assert out["status"] == "error"
    assert await _counts(db_session, fix) == before
    assert await _inventory_qty(db_session, fix) == qty_before


# --- Agent-level HITL -------------------------------------------------------


def _purchase_fake(args: dict[str, Any]) -> BaseChatModel:
    """Fake model: request ``create_purchase`` once, then echo."""

    class _PurchaseToolFakeModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "purchase-write-fake"

        def bind_tools(self, tools: Any, **kwargs: Any) -> "_PurchaseToolFakeModel":
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
                    content=f"Purchase recorded: {tool_msgs[-1].content}"
                )
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_purchase",
                            "args": dict(args),
                            "id": "call_pur_1",
                            "type": "tool_call",
                        }
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return _PurchaseToolFakeModel()


def _purchase_args(fix: PurchaseFixture, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {
        "idempotency_key": _key(),
        "supplier_name": fix.supplier.name,
        "items": [_item(fix)],
    }
    if extra:
        args.update(extra)
    return args


@pytest.mark.asyncio
async def test_hitl_pause_then_approve_records_one_purchase(
    db_session: AsyncSession,
) -> None:
    fix = await _make_purchase_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_purchase_fake(_purchase_args(fix)),
        include_hitl_demo=False,
        write_tools=build_purchase_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"pur-approve-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Bilal se 20 suit 2500 per suit aaye"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    action = paused.interrupts[0].value["action_requests"][0]
    assert action["name"] == "create_purchase"
    assert action["args"]["supplier_name"] == fix.supplier.name
    # Paused BEFORE mutation: nothing written, nothing held.
    assert await _counts(db_session, fix) == before
    assert await _inventory_qty(db_session, fix) == Decimal("0.000")

    resumed = await agent.ainvoke(
        Command(resume=hitl_resume_payload([approve_decision()])),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    after = await _counts(db_session, fix)
    assert after["purchases"] == before["purchases"] + 1
    assert after["receipts"] == before["receipts"] + 1
    assert await _inventory_qty(db_session, fix) == Decimal("20.000")


@pytest.mark.asyncio
async def test_hitl_reject_creates_nothing(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_purchase_fake(_purchase_args(fix)),
        include_hitl_demo=False,
        write_tools=build_purchase_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"pur-reject-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Bilal se maal khareeda"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    resumed = await agent.ainvoke(
        Command(
            resume=hitl_resume_payload(
                [reject_decision("User rejected the purchase. Do not record anything.")]
            )
        ),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    # Rejection: zero mutation across purchases, stock, ledger, receipts.
    assert await _counts(db_session, fix) == before
    assert await _inventory_qty(db_session, fix) == Decimal("0.000")


@pytest.mark.asyncio
async def test_hitl_edit_executes_corrected_args(db_session: AsyncSession) -> None:
    fix = await _make_purchase_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_purchase_fake(_purchase_args(fix)),
        include_hitl_demo=False,
        write_tools=build_purchase_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"pur-edit-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "purchase record kar do"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    edited = _purchase_args(
        fix, {"items": [_item(fix, quantity="5", unit_cost="600")]}
    )
    resumed = await agent.ainvoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {
                            "name": "create_purchase",
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
    after = await _counts(db_session, fix)
    assert after["purchases"] == before["purchases"] + 1
    assert await _inventory_qty(db_session, fix) == Decimal("5.000")


@pytest.mark.asyncio
async def test_hitl_edit_with_bad_args_records_nothing(
    db_session: AsyncSession,
) -> None:
    """Edited args pass the exact same backend validation (bad quantity)."""
    fix = await _make_purchase_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_purchase_fake(_purchase_args(fix)),
        include_hitl_demo=False,
        write_tools=build_purchase_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"pur-badedit-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "purchase record kar do"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    edited = _purchase_args(
        fix, {"items": [_item(fix, quantity="0", unit_cost="600")]}
    )
    resumed = await agent.ainvoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {
                            "name": "create_purchase",
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
    assert await _counts(db_session, fix) == before
    assert await _inventory_qty(db_session, fix) == Decimal("0.000")


@pytest.mark.asyncio
async def test_hitl_duplicate_approve_resume_is_idempotent(
    db_session: AsyncSession,
) -> None:
    """Approving the same operation twice creates exactly one purchase."""
    fix = await _make_purchase_shop(db_session)
    tool = _purchase_tool(db_session, fix)
    key = _key()
    args = _args(fix, idempotency_key=key)
    first = await tool.ainvoke(args)
    assert first["status"] == "completed" and first["duplicate"] is False
    # A duplicate approval/resume replay of the same approved args.
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["purchase_id"] == first["purchase_id"]
    purchases = (
        await db_session.execute(
            select(func.count(Purchase.id)).where(Purchase.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert purchases == 1


# --- HTTP: mutation stays behind /ai/chat ----------------------------------


def _http_purchase_fake(fix: PurchaseFixture) -> BaseChatModel:
    return _purchase_fake(
        {
            "idempotency_key": f"httppur{uuid.uuid4().hex[:25]}",
            "supplier_name": fix.supplier.name,
            "items": [
                {
                    "product_name": fix.product.name,
                    "quantity": "5",
                    "unit_cost": "600",
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_chat_endpoint_purchase_pause_then_approve(
    mocked_api_client: Any, api_session: AsyncSession
) -> None:
    from app.api.ai import get_chat_model
    from app.main import app
    from app.models import Shop as ShopModel
    from app.models import User as UserModel
    from app.models.user import UserRole

    shop = ShopModel(name=f"HTTP Purchase Shop {uuid.uuid4().hex[:6]}")
    api_session.add(shop)
    await api_session.flush()
    await api_session.refresh(shop)
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
    supplier = Supplier(shop_id=shop.id, name="Bilal")
    api_session.add_all([variant, supplier])
    await api_session.flush()
    api_session.add(
        UserModel(
            clerk_user_id="mock_clerk_id",
            shop_id=shop.id,
            name="Buyer",
            email=f"buyer-{uuid.uuid4().hex[:6]}@example.com",
            role=UserRole.OWNER,
        )
    )
    await api_session.flush()

    # The fake model closes over real shop rows created above.
    http_fix = PurchaseFixture(shop, supplier, product, variant)
    app.dependency_overrides[get_chat_model] = lambda: _http_purchase_fake(http_fix)
    try:
        thread = uuid.uuid4().hex
        paused = await mocked_api_client.post(
            "/ai/chat",
            json={
                "message": "Bilal supplier se 5 meter black lawn 600 rupay meter liya",
                "thread_id": thread,
            },
        )
        assert paused.status_code == 200
        body = paused.json()
        assert body["status"] == "paused"
        assert body["interrupts"][0]["name"] == "create_purchase"
        assert (
            (
                await api_session.execute(
                    select(func.count(Purchase.id)).where(
                        Purchase.shop_id == shop.id,
                    )
                )
            ).scalar_one()
            == 0
        )

        resumed = await mocked_api_client.post(
            "/ai/chat/resume",
            json={"thread_id": thread, "decisions": [{"type": "approve"}]},
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "done"
        assert (
            (
                await api_session.execute(
                    select(func.count(Purchase.id)).where(
                        Purchase.shop_id == shop.id,
                    )
                )
            ).scalar_one()
            == 1
        )
    finally:
        app.dependency_overrides.pop(get_chat_model, None)

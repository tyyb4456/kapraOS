"""Step 5: AI-assisted supplier payment — one HITL-gated mutation.

Covers the brief's minimum without any real LLM (deterministic fake
models, direct tool calls — no network, no tokens):

* preparation: supplier resolution (unique/ambiguous/missing/cross-tenant),
  amount validation (including hazar/lakh), payment-method mapping
* HITL: pause before mutation, approve executes, reject is a no-op,
  edit executes the corrected args (re-validated)
* transaction: success commits atomically, failure leaves no partial
  state (savepoint-scoped, never a full-session rollback)
* idempotency: same operation key twice (in-session and post-commit)
  creates exactly one payment; a failed attempt does not poison its key
* tenant isolation: foreign-shop IDs/names are rejected
* service reuse: the AI path calls ``payables.record_supplier_payment()``
* accounting: the existing Dr AP / Cr Cash-Bank posting is preserved
* balance: before -> payment -> after via the existing summary logic
* inventory: supplier payments never move stock
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
    SUPPLIER_PAYMENT_HITL_INTERRUPT_CONFIG,
    SUPPLIER_PAYMENT_SYSTEM_ADDENDUM,
    WRITE_MASTER_TOOL_NAMES,
    approve_decision,
    build_master_agent,
    hitl_resume_payload,
    reject_decision,
)
from app.ai.state import TenantContext
from app.ai.tools.supplier_payments_write import (
    RECORD_SUPPLIER_PAYMENT_TOOL_NAME,
    WRITE_TOOL_NAMES,
    SupplierPaymentPrepAmbiguousError,
    SupplierPaymentPrepError,
    SupplierPaymentPrepNotFoundError,
    assert_supplier_payment_write_registry_is_minimal,
    build_supplier_payment_preview,
    build_supplier_payment_write_tools,
    parse_reference,
    parse_supplier_payment_amount,
    parse_supplier_payment_method,
    parse_uuid_arg,
    resolve_payment_supplier,
    validate_idempotency_key,
)
from app.models import (
    AISupplierPaymentReceipt,
    Category,
    Customer,
    Inventory,
    LedgerEntry,
    Payment,
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
from app.services import payables as payables_service
from app.services.purchases import PurchaseItemInput, create_purchase


# --- Fixtures ------------------------------------------------------------
class SupplierPaymentFixture:
    def __init__(
        self,
        shop: Shop,
        supplier: Supplier,
        variant: ProductVariant,
    ) -> None:
        self.shop = shop
        self.supplier = supplier
        self.variant = variant


async def _make_supplier_shop(
    db: AsyncSession,
    name: str = "AI Supplier Shop",
    *,
    supplier_name: str = "Bilal",
    outstanding: str = "8000",
) -> SupplierPaymentFixture:
    """A shop with one supplier owed ``outstanding`` (via a purchase)."""
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
    supplier = Supplier(shop_id=shop.id, name=supplier_name)
    db.add_all([variant, supplier])
    await db.flush()
    # One purchase creates the payable outstanding (total = outstanding).
    total = Decimal(outstanding)
    # unit_cost 800, qty = total / 800 (3dp scale).
    qty = (total / Decimal(800)).quantize(Decimal("1.000"))
    assert qty > 0
    await create_purchase(
        db,
        shop_id=shop.id,
        supplier_id=supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=variant.id,
                quantity=qty,
                unit_cost=Decimal(800),
            )
        ],
    )
    return SupplierPaymentFixture(shop, supplier, variant)


def _ctx(fix: SupplierPaymentFixture) -> TenantContext:
    return TenantContext(shop_id=fix.shop.id, user_id=uuid.uuid4())


def _pay_tool(db: AsyncSession, fix: SupplierPaymentFixture) -> Any:
    tools = {t.name: t for t in build_supplier_payment_write_tools(db, _ctx(fix))}
    return tools[RECORD_SUPPLIER_PAYMENT_TOOL_NAME]


def _key() -> str:
    return uuid.uuid4().hex


async def _counts(db: AsyncSession, fix: SupplierPaymentFixture) -> dict[str, Any]:
    payments = (
        await db.execute(
            select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
        )
    ).scalar_one()
    receipts = (
        await db.execute(
            select(func.count(AISupplierPaymentReceipt.id)).where(
                AISupplierPaymentReceipt.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    ledger = (
        await db.execute(
            select(func.count(LedgerEntry.id)).where(LedgerEntry.shop_id == fix.shop.id)
        )
    ).scalar_one()
    return {"payments": payments, "receipts": receipts, "ledger": ledger}


async def _outstanding(db: AsyncSession, fix: SupplierPaymentFixture) -> Decimal:
    summary = await payables_service.get_supplier_summary(
        db, shop_id=fix.shop.id, supplier_id=fix.supplier.id
    )
    return summary.outstanding_balance


async def _inventory_qty(db: AsyncSession, fix: SupplierPaymentFixture) -> Decimal:
    row = (
        await db.execute(
            select(Inventory).where(Inventory.variant_id == fix.variant.id)
        )
    ).scalar_one_or_none()
    if row is None:
        return Decimal("0.000")
    return Decimal(row.quantity)


# --- Registry boundary: exactly one NEW mutation -------------------------


def test_supplier_registry_is_exactly_one_tool() -> None:
    assert WRITE_TOOL_NAMES == ("record_supplier_payment",)
    assert RECORD_SUPPLIER_PAYMENT_TOOL_NAME in WRITE_MASTER_TOOL_NAMES
    assert SUPPLIER_PAYMENT_HITL_INTERRUPT_CONFIG == {"record_supplier_payment": True}


def test_supplier_prompt_forces_same_turn_tool_call() -> None:
    """The prompt must order preview + tool call in ONE turn."""
    lowered = SUPPLIER_PAYMENT_SYSTEM_ADDENDUM.lower()
    assert "same turn" in lowered
    assert "without calling the tool" in lowered or "without calling" in lowered
    assert "approval card" in lowered
    assert "haan" in lowered and "directly" in lowered


def test_supplier_prompt_distinguishes_customer_vs_supplier() -> None:
    lowered = SUPPLIER_PAYMENT_SYSTEM_ADDENDUM.lower()
    assert "customer" in lowered and "supplier" in lowered
    assert "ambiguous" in lowered or "clarification" in lowered


@pytest.mark.asyncio
async def test_supplier_tool_takes_no_tenant_or_ledger_args(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    tools = build_supplier_payment_write_tools(db_session, _ctx(fix))
    assert_supplier_payment_write_registry_is_minimal(tools)
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
        "ap_account_id",
        "ledger_entry_id",
        "purchase_id",
    ):
        assert forbidden not in params, (
            f"record_supplier_payment must not take {forbidden}"
        )


def test_supplier_registry_rejects_a_second_mutation() -> None:
    from app.ai.tools.registry import demo_prepare_operation

    with pytest.raises(AssertionError, match="one mutation only"):
        assert_supplier_payment_write_registry_is_minimal(
            [demo_prepare_operation]  # type: ignore[list-item]
        )


@pytest.mark.asyncio
async def test_supplier_tools_require_a_checkpointer(
    db_session: AsyncSession,
) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    fix = await _make_supplier_shop(db_session)
    with pytest.raises(ValueError, match="checkpointer"):
        build_master_agent(
            model=GenericFakeChatModel(messages=iter(["x"])),
            checkpointer=None,
            include_hitl_demo=False,
            write_tools=build_supplier_payment_write_tools(db_session, _ctx(fix)),
        )


# --- Supplier resolution -------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_payment_supplier_unique_and_by_id(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    shop_id = fix.shop.id
    one = await resolve_payment_supplier(db_session, shop_id, supplier_name="Bilal")
    assert one.id == fix.supplier.id
    by_id = await resolve_payment_supplier(
        db_session, shop_id, supplier_id=str(fix.supplier.id)
    )
    assert by_id.id == fix.supplier.id
    with pytest.raises(SupplierPaymentPrepNotFoundError):
        await resolve_payment_supplier(db_session, shop_id, supplier_name="Nobody")
    with pytest.raises(SupplierPaymentPrepError):
        await resolve_payment_supplier(
            db_session,
            shop_id,
            supplier_id=str(fix.supplier.id),
            supplier_name="Bilal",
        )


@pytest.mark.asyncio
async def test_resolve_payment_supplier_needs_a_supplier(
    db_session: AsyncSession,
) -> None:
    """Supplier payments need a supplier: never auto-created."""
    fix = await _make_supplier_shop(db_session)
    with pytest.raises(SupplierPaymentPrepError, match="Supplier is required"):
        await resolve_payment_supplier(db_session, fix.shop.id)
    with pytest.raises(SupplierPaymentPrepNotFoundError):
        await resolve_payment_supplier(
            db_session, fix.shop.id, supplier_name="Ghost Traders"
        )


@pytest.mark.asyncio
async def test_resolve_payment_supplier_ambiguous_never_guesses(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    db_session.add(Supplier(shop_id=fix.shop.id, name="Bilal Traders"))
    await db_session.flush()
    with pytest.raises(SupplierPaymentPrepAmbiguousError) as exc_info:
        await resolve_payment_supplier(db_session, fix.shop.id, supplier_name="Bilal")
    assert len(exc_info.value.matches) == 2
    assert "shop_id" not in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_resolve_payment_supplier_name_too_long(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    with pytest.raises(SupplierPaymentPrepError):
        await resolve_payment_supplier(
            db_session, fix.shop.id, supplier_name="x" * 151
        )


# --- Amount + method parsing ---------------------------------------------


def test_parse_supplier_payment_amount_plain() -> None:
    assert parse_supplier_payment_amount("3000") == Decimal("3000.00")
    assert parse_supplier_payment_amount(5000) == Decimal("5000.00")
    assert parse_supplier_payment_amount("  1500.50 ") == Decimal("1500.50")
    assert parse_supplier_payment_amount("5,000") == Decimal("5000.00")
    for bad in ("0", "0.00", "-1", "-3000", "abc", "", "nan", "inf", None):
        with pytest.raises(SupplierPaymentPrepError):
            parse_supplier_payment_amount(bad)


def test_parse_supplier_payment_amount_natural_language() -> None:
    assert parse_supplier_payment_amount("5 hazar") == Decimal("5000.00")
    assert parse_supplier_payment_amount("10 hazar") == Decimal("10000.00")
    assert parse_supplier_payment_amount("5 Hazar") == Decimal("5000.00")
    assert parse_supplier_payment_amount("2 lakh") == Decimal("200000.00")
    assert parse_supplier_payment_amount("1.5 lakh") == Decimal("150000.00")
    assert parse_supplier_payment_amount("5,000") == Decimal("5000.00")
    assert parse_supplier_payment_amount("10 thousand") == Decimal("10000.00")
    assert parse_supplier_payment_amount("2 lac") == Decimal("200000.00")


def test_parse_supplier_payment_amount_no_float_arithmetic() -> None:
    # Decimal-based: 0.1 hazar is exactly 100.00, not a float artefact.
    assert parse_supplier_payment_amount("0.1 hazar") == Decimal("100.00")
    for bad in ("lots", "five thousand", "hazar", "lakh 5", "--5", "5 hazar extra"):
        with pytest.raises(SupplierPaymentPrepError):
            parse_supplier_payment_amount(bad)


def test_parse_supplier_payment_method_aliases() -> None:
    assert parse_supplier_payment_method("cash") == PaymentMethod.CASH
    assert parse_supplier_payment_method("Naqd") == PaymentMethod.CASH
    assert parse_supplier_payment_method("nagad") == PaymentMethod.CASH
    assert parse_supplier_payment_method("bank") == PaymentMethod.BANK
    assert parse_supplier_payment_method("Bank Transfer") == PaymentMethod.BANK
    assert parse_supplier_payment_method("card") == PaymentMethod.CARD
    assert parse_supplier_payment_method("Jazz Cash") == PaymentMethod.JAZZCASH
    assert parse_supplier_payment_method("jazzcash") == PaymentMethod.JAZZCASH
    assert parse_supplier_payment_method("Easy Paisa") == PaymentMethod.EASYPAISA
    assert parse_supplier_payment_method("easypaisa") == PaymentMethod.EASYPAISA
    assert parse_supplier_payment_method("other") == PaymentMethod.OTHER
    # Omitted method defaults to cash (shown on the approval card).
    assert parse_supplier_payment_method(None) == PaymentMethod.CASH
    assert parse_supplier_payment_method("") == PaymentMethod.CASH
    with pytest.raises(SupplierPaymentPrepError):
        parse_supplier_payment_method("bitcoin")


def test_parse_reference_and_uuid() -> None:
    assert parse_reference(None) is None
    assert parse_reference("") is None
    assert parse_reference("txn-123") == "txn-123"
    with pytest.raises(SupplierPaymentPrepError):
        parse_reference("x" * 101)
    sid = uuid.uuid4()
    assert parse_uuid_arg(str(sid), "supplier_id") == sid
    with pytest.raises(SupplierPaymentPrepError):
        parse_uuid_arg("not-a-uuid", "supplier_id")
    assert validate_idempotency_key(uuid.uuid4().hex) is not None
    for bad in ("", "short", "has space", None, "x" * 65):
        with pytest.raises(SupplierPaymentPrepError):
            validate_idempotency_key(bad)


def test_supplier_payment_preview_text() -> None:
    text = build_supplier_payment_preview(
        supplier_name="Bilal Traders",
        outstanding_before=Decimal(8000),
        amount=Decimal(3000),
        method=PaymentMethod.CASH,
    )
    assert "Bilal Traders" in text
    assert "Supplier Payment" in text
    assert "8000.00" in text and "3000.00" in text and "5000.00" in text
    assert "Cash" in text
    assert "toward supplier payable" in text
    with_ref = build_supplier_payment_preview(
        supplier_name="Bilal",
        outstanding_before=Decimal(8000),
        amount=Decimal(1000),
        method=PaymentMethod.BANK,
        reference="txn-1",
    )
    assert "txn-1" in with_ref
    assert "Bank" in with_ref


# --- Direct tool execution -----------------------------------------------


@pytest.mark.asyncio
async def test_tool_records_payment_and_updates_balance(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    assert await _outstanding(db_session, fix) == Decimal("8000.00")
    before = await _counts(db_session, fix)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Bilal",
            "amount": "3000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert out["duplicate"] is False
    assert out["supplier_name"] == "Bilal"
    assert out["amount"] == "3000.00"
    assert out["payment_method"] == "CASH"
    assert out["remaining_balance"] == "5000.00"
    assert uuid.UUID(out["payment_id"])
    after = await _counts(db_session, fix)
    assert after["payments"] == before["payments"] + 1
    assert after["receipts"] == before["receipts"] + 1
    assert after["ledger"] > before["ledger"]  # SUPPLIER_PAYMENT posting
    assert await _outstanding(db_session, fix) == Decimal("5000.00")


@pytest.mark.asyncio
async def test_tool_records_payment_by_supplier_id(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_id": str(fix.supplier.id),
            "amount": "1000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert out["supplier_name"] == fix.supplier.name
    assert await _outstanding(db_session, fix) == Decimal("7000.00")


@pytest.mark.asyncio
async def test_tool_natural_amount_hazar_and_lakh(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session, outstanding="300000")
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "amount": "5 hazar",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert out["amount"] == "5000.00"
    assert await _outstanding(db_session, fix) == Decimal("295000.00")
    out2 = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "amount": "2 lakh",
            "payment_method": "bank",
        }
    )
    assert out2["status"] == "completed"
    assert out2["amount"] == "200000.00"
    assert await _outstanding(db_session, fix) == Decimal("95000.00")


@pytest.mark.asyncio
async def test_tool_default_method_is_cash(db_session: AsyncSession) -> None:
    fix = await _make_supplier_shop(db_session)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Bilal",
            "amount": "1000",
        }
    )
    assert out["status"] == "completed"
    assert out["payment_method"] == "CASH"
    payment = await db_session.get(Payment, uuid.UUID(out["payment_id"]))
    assert payment is not None and payment.method == PaymentMethod.CASH


@pytest.mark.asyncio
async def test_tool_bank_and_wallet_methods(db_session: AsyncSession) -> None:
    fix = await _make_supplier_shop(db_session)
    for method_arg, expected in [
        ("bank transfer", "BANK"),
        ("jazzcash", "JAZZCASH"),
        ("Easy Paisa", "EASYPAISA"),
    ]:
        out = await _pay_tool(db_session, fix).ainvoke(
            {
                "idempotency_key": _key(),
                "supplier_name": "Bilal",
                "amount": "500",
                "payment_method": method_arg,
            }
        )
        assert out["status"] == "completed", method_arg
        assert out["payment_method"] == expected, method_arg


@pytest.mark.asyncio
async def test_tool_reference_persisted(db_session: AsyncSession) -> None:
    fix = await _make_supplier_shop(db_session)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Bilal",
            "amount": "500",
            "payment_method": "bank",
            "reference": "txn-42",
        }
    )
    assert out["status"] == "completed"
    payment = await db_session.get(Payment, uuid.UUID(out["payment_id"]))
    assert payment is not None and payment.reference == "txn-42"


@pytest.mark.asyncio
async def test_tool_invalid_inputs_change_nothing(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    before = await _counts(db_session, fix)
    tool = _pay_tool(db_session, fix)
    bad_calls: list[dict[str, Any]] = [
        {"amount": "0"},
        {"amount": "-5"},
        {"amount": "lots"},
        {"amount": ""},
        {"amount": "hazar"},
        {"payment_method": "bitcoin"},
        {"idempotency_key": "short"},
        {"supplier_name": "Nobody Here"},
        {"supplier_name": "Bilal", "supplier_id": str(fix.supplier.id)},
        {"supplier_id": "not-a-uuid"},
        {"reference": "x" * 101},
    ]
    for extra in bad_calls:
        args: dict[str, Any] = {
            "idempotency_key": _key(),
            "supplier_name": "Bilal",
            "amount": "100",
            "payment_method": "cash",
        }
        args.update(extra)
        out = await tool.ainvoke(args)
        assert out["status"] in ("error", "ambiguous", "not_found"), args
        assert "traceback" not in out["message"].lower()
        assert "shop_id" not in out["message"]
    # Missing amount asks for clarification — never executes.
    missing = await tool.ainvoke(
        {"idempotency_key": _key(), "supplier_name": "Bilal", "payment_method": "cash"}
    )
    assert missing["status"] == "error"
    assert await _counts(db_session, fix) == before
    assert await _outstanding(db_session, fix) == Decimal("8000.00")


@pytest.mark.asyncio
async def test_tool_missing_supplier_never_executes(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    before = await _counts(db_session, fix)
    out = await _pay_tool(db_session, fix).ainvoke(
        {"idempotency_key": _key(), "amount": "1000", "payment_method": "cash"}
    )
    assert out["status"] == "error"
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_tool_ambiguous_names_never_execute(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    db_session.add(Supplier(shop_id=fix.shop.id, name="Bilal Traders"))
    await db_session.flush()
    before = await _counts(db_session, fix)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Bilal",
            "amount": "1000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "ambiguous"
    assert len(out["matches"]) == 2
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_tool_supplier_not_found(db_session: AsyncSession) -> None:
    fix = await _make_supplier_shop(db_session)
    before = await _counts(db_session, fix)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Ghost Supplier",
            "amount": "1000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "not_found"
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_tool_overpayment_follows_existing_rule(
    db_session: AsyncSession,
) -> None:
    """No new overpayment rule: the service rejects, the tool surfaces it."""
    fix = await _make_supplier_shop(db_session, outstanding="2000")
    before = await _counts(db_session, fix)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "amount": "3000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "error"
    assert out["code"] == "payment_exceeds_outstanding"
    assert await _counts(db_session, fix) == before
    assert await _outstanding(db_session, fix) == Decimal("2000.00")


@pytest.mark.asyncio
async def test_tool_revalidates_supplier_state_after_preview(
    db_session: AsyncSession,
) -> None:
    """The tool re-reads the payable; a stale preview cannot overpay."""
    fix = await _make_supplier_shop(db_session, outstanding="8000")
    # Something else settles most of the payable after the preview.
    await payables_service.record_supplier_payment(
        db_session,
        shop_id=fix.shop.id,
        supplier_id=fix.supplier.id,
        amount=Decimal("7000.00"),
        method=PaymentMethod.CASH,
    )
    assert await _outstanding(db_session, fix) == Decimal("1000.00")
    before = await _counts(db_session, fix)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": fix.supplier.name,
            "amount": "2000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "error"
    assert out["code"] == "payment_exceeds_outstanding"
    # The failed attempt wrote no new AI payment (only the direct one stands).
    after = await _counts(db_session, fix)
    assert after["payments"] == before["payments"]
    assert after["receipts"] == before["receipts"]


# --- Idempotency ------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_duplicate_key_executes_once(db_session: AsyncSession) -> None:
    fix = await _make_supplier_shop(db_session)
    tool = _pay_tool(db_session, fix)
    key = _key()
    args = {
        "idempotency_key": key,
        "supplier_name": "Bilal",
        "amount": "3000",
        "payment_method": "cash",
    }
    first = await tool.ainvoke(args)
    assert first["status"] == "completed" and first["duplicate"] is False
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["payment_id"] == first["payment_id"]
    assert second["remaining_balance"] == first["remaining_balance"]
    payments = (
        await db_session.execute(
            select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert payments == 1


@pytest.mark.asyncio
async def test_tool_duplicate_key_safe_after_commit(
    api_session: AsyncSession,
) -> None:
    # Uses `api_session` (outer-transaction fixture) on purpose: the
    # commit below stays inside the test's outer transaction, so the
    # teardown rollback discards it.
    db_session = api_session
    fix = await _make_supplier_shop(db_session)
    tool = _pay_tool(db_session, fix)
    key = _key()
    args = {
        "idempotency_key": key,
        "supplier_name": "Bilal",
        "amount": "1000",
        "payment_method": "cash",
    }
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
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["payment_id"] == first["payment_id"]
    ledger_after = (
        await db_session.execute(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    # Replay creates no second payment and no second posting.
    assert ledger_after == ledger_before
    payments = (
        await db_session.execute(
            select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert payments == 1
    # Distinct keys are distinct payments.
    third = await tool.ainvoke({**args, "idempotency_key": _key()})
    assert third["duplicate"] is False
    payments = (
        await db_session.execute(
            select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert payments == 2


@pytest.mark.asyncio
async def test_tool_failed_attempt_does_not_poison_key(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session, outstanding="2000")
    tool = _pay_tool(db_session, fix)
    key = _key()
    failed = await tool.ainvoke(
        {
            "idempotency_key": key,
            "supplier_name": "Bilal",
            "amount": "9999",  # exceeds outstanding: rejected, no receipt
            "payment_method": "cash",
        }
    )
    assert failed["status"] == "error"
    retry = await tool.ainvoke(
        {
            "idempotency_key": key,
            "supplier_name": "Bilal",
            "amount": "1000",
            "payment_method": "cash",
        }
    )
    assert retry["status"] == "completed" and retry["duplicate"] is False
    payments = (
        await db_session.execute(
            select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert payments == 1


# --- Tenant isolation -------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_rejects_foreign_shop_ids(db_session: AsyncSession) -> None:
    shop_a = await _make_supplier_shop(db_session, name="Shop A")
    shop_b = await _make_supplier_shop(db_session, name="Shop B")
    before_a = await _counts(db_session, shop_a)
    before_b = await _counts(db_session, shop_b)
    out = await _pay_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_id": str(shop_b.supplier.id),
            "amount": "500",
            "payment_method": "cash",
        }
    )
    assert out["status"] in ("error", "not_found")
    assert await _counts(db_session, shop_a) == before_a
    assert await _counts(db_session, shop_b) == before_b


@pytest.mark.asyncio
async def test_tool_cannot_see_other_shop_names(db_session: AsyncSession) -> None:
    shop_a = await _make_supplier_shop(db_session, name="Shop A")
    shop_b = await _make_supplier_shop(
        db_session, name="Shop B", supplier_name="Only In B", outstanding="8000"
    )
    out = await _pay_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Only In B",
            "amount": "500",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "not_found"
    assert (await _counts(db_session, shop_a))["payments"] == 0
    # Sanity: the name resolves fine inside its own shop.
    ok = await _pay_tool(db_session, shop_b).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Only In B",
            "amount": "500",
            "payment_method": "cash",
        }
    )
    assert ok["status"] == "completed"


@pytest.mark.asyncio
async def test_idempotency_keys_are_tenant_scoped(
    db_session: AsyncSession,
) -> None:
    """The same key in two shops creates two independent payments."""
    shop_a = await _make_supplier_shop(db_session, name="Shop A")
    shop_b = await _make_supplier_shop(db_session, name="Shop B")
    key = _key()
    out_a = await _pay_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": key,
            "supplier_name": shop_a.supplier.name,
            "amount": "500",
            "payment_method": "cash",
        }
    )
    out_b = await _pay_tool(db_session, shop_b).ainvoke(
        {
            "idempotency_key": key,
            "supplier_name": shop_b.supplier.name,
            "amount": "500",
            "payment_method": "cash",
        }
    )
    assert out_a["status"] == "completed" and out_a["duplicate"] is False
    assert out_b["status"] == "completed" and out_b["duplicate"] is False
    assert out_a["payment_id"] != out_b["payment_id"]


# --- Authoritative service reuse -------------------------------------------


@pytest.mark.asyncio
async def test_ai_path_calls_supplier_payment_service_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fix = await _make_supplier_shop(db_session)
    calls: list[dict[str, Any]] = []
    real_record = payables_service.record_supplier_payment

    async def _spy(session: AsyncSession, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await real_record(session, **kwargs)

    monkeypatch.setattr(payables_service, "record_supplier_payment", _spy)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Bilal",
            "amount": "3000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["shop_id"] == fix.shop.id
    assert calls[0]["supplier_id"] == fix.supplier.id
    assert calls[0]["amount"] == Decimal("3000.00")
    assert calls[0]["method"] == PaymentMethod.CASH


@pytest.mark.asyncio
async def test_ai_supplier_payment_posts_balanced_ledger(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Bilal",
            "amount": "3000",
            "payment_method": "cash",
        }
    )
    payment_id = uuid.UUID(out["payment_id"])
    rows = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.shop_id == fix.shop.id,
                LedgerEntry.reference_id == payment_id,
            )
        )
    ).scalars().all()
    assert rows, "expected a SUPPLIER_PAYMENT ledger group"
    assert {r.reference_type for r in rows} == {"SUPPLIER_PAYMENT"}
    assert sum(r.debit for r in rows) == sum(r.credit for r in rows) == Decimal(
        "3000.00"
    )


@pytest.mark.asyncio
async def test_supplier_payment_flows_through_statement_balance(
    db_session: AsyncSession,
) -> None:
    """before -> payment -> after, using the existing summary logic."""
    fix = await _make_supplier_shop(db_session, outstanding="8000")
    assert await _outstanding(db_session, fix) == Decimal("8000.00")
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Bilal",
            "amount": "3000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert await _outstanding(db_session, fix) == Decimal("5000.00")
    statement = await payables_service.get_supplier_statement(
        db_session, shop_id=fix.shop.id, supplier_id=fix.supplier.id
    )
    assert statement.closing_balance == Decimal("5000.00")
    kinds = {e.entry_type.value for e in statement.entries}
    assert "PURCHASE" in kinds and "PAYMENT" in kinds


@pytest.mark.asyncio
async def test_supplier_payment_does_not_touch_inventory(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    qty_before = await _inventory_qty(db_session, fix)
    assert qty_before > 0
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Bilal",
            "amount": "1000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert await _inventory_qty(db_session, fix) == qty_before


@pytest.mark.asyncio
async def test_tool_does_not_mutate_unrelated_models(
    db_session: AsyncSession,
) -> None:
    """The AI tool records a Payment: no Customer/Sale/Purchase/Inventory writes."""
    fix = await _make_supplier_shop(db_session)
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
        "purchases": (
            await db_session.execute(
                select(func.count(Purchase.id)).where(
                    Purchase.shop_id == fix.shop.id
                )
            )
        ).scalar_one(),
    }
    qty_before = await _inventory_qty(db_session, fix)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "supplier_name": "Bilal",
            "amount": "1000",
            "payment_method": "cash",
        }
    )
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
            select(func.count(Purchase.id)).where(Purchase.shop_id == fix.shop.id)
        )
    ).scalar_one() == counts_before["purchases"]
    assert await _inventory_qty(db_session, fix) == qty_before


@pytest.mark.asyncio
async def test_rollback_on_service_failure(db_session: AsyncSession) -> None:
    """A service failure leaves no payment, receipt, or ledger behind."""
    fix = await _make_supplier_shop(db_session)
    before = await _counts(db_session, fix)
    tool = _pay_tool(db_session, fix)
    # Force the authoritative service to fail after the tool validated input.
    import app.ai.tools.supplier_payments_write as tool_module

    real_service = payables_service.record_supplier_payment

    async def _boom(session: AsyncSession, **kwargs: Any) -> Any:
        raise payables_service.InvalidPaymentAmountError("boom")

    # Patch where the tool looks it up (module attribute).
    import app.services.payables as payables_module

    old = payables_module.record_supplier_payment
    payables_module.record_supplier_payment = _boom  # type: ignore[assignment]
    tool_module_ref = tool_module  # keep import alive for ruff
    assert tool_module_ref is not None
    try:
        out = await tool.ainvoke(
            {
                "idempotency_key": _key(),
                "supplier_name": "Bilal",
                "amount": "500",
                "payment_method": "cash",
            }
        )
    finally:
        payables_module.record_supplier_payment = old
    assert real_service is not None
    assert out["status"] == "error"
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_rollback_on_accounting_failure(db_session: AsyncSession) -> None:
    """An accounting failure rolls back the payment and the receipt together."""
    fix = await _make_supplier_shop(db_session)
    before = await _counts(db_session, fix)
    tool = _pay_tool(db_session, fix)

    import app.services.accounting as accounting_module

    real_post = accounting_module.post_supplier_payment

    async def _boom(session: AsyncSession, **kwargs: Any) -> Any:
        raise accounting_module.AccountingError("ledger down")

    accounting_module.post_supplier_payment = _boom  # type: ignore[assignment]
    try:
        out = await tool.ainvoke(
            {
                "idempotency_key": _key(),
                "supplier_name": "Bilal",
                "amount": "500",
                "payment_method": "cash",
            }
        )
    finally:
        accounting_module.post_supplier_payment = real_post
    assert out["status"] == "error"
    assert out["code"] == "payment_failed"
    assert await _counts(db_session, fix) == before
    assert await _outstanding(db_session, fix) == Decimal("8000.00")


# --- Agent-level HITL -------------------------------------------------------


def _supplier_fake(args: dict[str, Any]) -> BaseChatModel:
    """Fake model: request ``record_supplier_payment`` once, then echo."""

    class _SupplierToolFakeModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "supplier-payment-write-fake"

        def bind_tools(self, tools: Any, **kwargs: Any) -> "_SupplierToolFakeModel":
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
                    content=f"Supplier payment recorded: {tool_msgs[-1].content}"
                )
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "record_supplier_payment",
                            "args": dict(args),
                            "id": "call_supay_1",
                            "type": "tool_call",
                        }
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return _SupplierToolFakeModel()


def _supplier_args(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {
        "idempotency_key": _key(),
        "supplier_name": "Bilal",
        "amount": "3000",
        "payment_method": "cash",
    }
    if extra:
        args.update(extra)
    return args


@pytest.mark.asyncio
async def test_hitl_pause_then_approve_records_one_payment(
    db_session: AsyncSession,
) -> None:
    fix = await _make_supplier_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_supplier_fake(_supplier_args()),
        include_hitl_demo=False,
        write_tools=build_supplier_payment_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"supay-approve-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Bilal supplier ko 3000 de diye"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    action = paused.interrupts[0].value["action_requests"][0]
    assert action["name"] == "record_supplier_payment"
    assert action["args"]["supplier_name"] == "Bilal"
    # Paused BEFORE mutation: nothing written, nothing held.
    assert await _counts(db_session, fix) == before

    resumed = await agent.ainvoke(
        Command(resume=hitl_resume_payload([approve_decision()])),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    after = await _counts(db_session, fix)
    assert after["payments"] == before["payments"] + 1
    assert after["receipts"] == before["receipts"] + 1
    assert await _outstanding(db_session, fix) == Decimal("5000.00")


@pytest.mark.asyncio
async def test_hitl_reject_creates_nothing(db_session: AsyncSession) -> None:
    fix = await _make_supplier_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_supplier_fake(_supplier_args()),
        include_hitl_demo=False,
        write_tools=build_supplier_payment_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"supay-reject-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Bilal supplier ko 3000 de diye"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    resumed = await agent.ainvoke(
        Command(
            resume=hitl_resume_payload(
                [reject_decision("User rejected the payment. Do not record anything.")]
            )
        ),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    # Rejection: zero mutation across payments, khata, ledger.
    assert await _counts(db_session, fix) == before
    assert await _outstanding(db_session, fix) == Decimal("8000.00")


@pytest.mark.asyncio
async def test_hitl_edit_executes_corrected_args(db_session: AsyncSession) -> None:
    fix = await _make_supplier_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_supplier_fake(_supplier_args()),
        include_hitl_demo=False,
        write_tools=build_supplier_payment_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"supay-edit-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Bilal supplier ko payment kar do"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    edited = _supplier_args({"amount": "2000"})
    resumed = await agent.ainvoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {
                            "name": "record_supplier_payment",
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
    assert after["payments"] == before["payments"] + 1
    assert await _outstanding(db_session, fix) == Decimal("6000.00")


@pytest.mark.asyncio
async def test_hitl_edit_with_bad_args_records_nothing(
    db_session: AsyncSession,
) -> None:
    """Edited args pass the exact same backend validation (overpayment)."""
    fix = await _make_supplier_shop(db_session, outstanding="2000")
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_supplier_fake(_supplier_args({"amount": "1000"})),
        include_hitl_demo=False,
        write_tools=build_supplier_payment_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"supay-badedit-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Bilal supplier ko payment kar do"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    edited = _supplier_args({"amount": "9999"})  # exceeds outstanding
    resumed = await agent.ainvoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {
                            "name": "record_supplier_payment",
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
    assert await _outstanding(db_session, fix) == Decimal("2000.00")


@pytest.mark.asyncio
async def test_hitl_duplicate_approve_resume_is_idempotent(
    db_session: AsyncSession,
) -> None:
    """Approving the same operation twice creates exactly one payment."""
    fix = await _make_supplier_shop(db_session)
    tool = _pay_tool(db_session, fix)
    key = _key()
    args = {
        "idempotency_key": key,
        "supplier_name": "Bilal",
        "amount": "1000",
        "payment_method": "cash",
    }
    first = await tool.ainvoke(args)
    assert first["status"] == "completed" and first["duplicate"] is False
    # A duplicate approval/resume replay of the same approved args.
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["payment_id"] == first["payment_id"]
    payments = (
        await db_session.execute(
            select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert payments == 1


# --- HTTP: mutation stays behind /ai/chat ----------------------------------


def _http_supplier_fake() -> BaseChatModel:
    return _supplier_fake(
        {
            "idempotency_key": f"httpsup{uuid.uuid4().hex[:25]}",
            "supplier_name": "Bilal",
            "amount": "1000",
            "payment_method": "cash",
        }
    )


@pytest.mark.asyncio
async def test_chat_endpoint_supplier_pause_then_approve(
    mocked_api_client: Any, api_session: AsyncSession
) -> None:
    from app.api.ai import get_chat_model
    from app.main import app
    from app.models import Shop as ShopModel
    from app.models import User as UserModel
    from app.models.user import UserRole

    shop = ShopModel(name=f"HTTP SupPay Shop {uuid.uuid4().hex[:6]}")
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
    supplier = Supplier(shop_id=shop.id, name="Bilal")
    api_session.add_all([variant, supplier])
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

    def _pay_count() -> Any:
        return api_session.execute(
            select(func.count(Payment.id)).where(
                Payment.shop_id == shop.id,
                Payment.supplier_id == supplier.id,
            )
        )

    app.dependency_overrides[get_chat_model] = _http_supplier_fake
    try:
        thread = uuid.uuid4().hex
        paused = await mocked_api_client.post(
            "/ai/chat",
            json={
                "message": "Bilal supplier ko 1000 de diye",
                "thread_id": thread,
            },
        )
        assert paused.status_code == 200
        body = paused.json()
        assert body["status"] == "paused"
        assert body["interrupts"][0]["name"] == "record_supplier_payment"
        assert (await _pay_count()).scalar_one() == 0

        resumed = await mocked_api_client.post(
            "/ai/chat/resume",
            json={"thread_id": thread, "decisions": [{"type": "approve"}]},
        )
        assert resumed.status_code == 200
        done = resumed.json()
        assert done["status"] == "done"
        assert (await _pay_count()).scalar_one() == 1
    finally:
        app.dependency_overrides.pop(get_chat_model, None)

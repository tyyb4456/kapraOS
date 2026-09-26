"""Step 4: AI-assisted customer payment — one HITL-gated mutation.

Covers the brief's minimum without any real LLM (deterministic fake
models, direct tool calls — no network, no tokens):

* preparation: customer resolution (unique/ambiguous/missing/cross-tenant),
  amount validation, payment-method mapping
* HITL: pause before mutation, approve executes, reject is a no-op,
  edit executes the corrected args (re-validated)
* transaction: success commits atomically, failure leaves no partial
  state (savepoint-scoped, never a full-session rollback)
* idempotency: same operation key twice (in-session and post-commit)
  creates exactly one payment; a failed attempt does not poison its key
* tenant isolation: foreign-shop IDs/names are rejected
* service reuse: the AI path calls ``receivables.record_customer_payment()``
* accounting: the existing Dr Cash/Bank / Cr AR posting is preserved
* balance: before -> payment -> after via the existing summary logic
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
    PAYMENT_HITL_INTERRUPT_CONFIG,
    PAYMENT_SYSTEM_ADDENDUM,
    WRITE_MASTER_TOOL_NAMES,
    approve_decision,
    build_master_agent,
    hitl_resume_payload,
    reject_decision,
)
from app.ai.state import TenantContext
from app.ai.tools.payments_write import (
    RECORD_PAYMENT_TOOL_NAME,
    WRITE_TOOL_NAMES,
    PaymentPrepAmbiguousError,
    PaymentPrepError,
    PaymentPrepNotFoundError,
    assert_payment_write_registry_is_minimal,
    build_payment_preview,
    build_payment_write_tools,
    parse_payment_amount,
    parse_payment_method,
    parse_reference,
    parse_uuid_arg,
    resolve_payment_customer,
    validate_idempotency_key,
)
from app.models import (
    AIPaymentReceipt,
    Category,
    Customer,
    LedgerEntry,
    Payment,
    PaymentMethod,
    Product,
    ProductType,
    ProductVariant,
    Shop,
    Supplier,
    Unit,
)
from app.services import receivables as receivables_service
from app.services import sales as sales_service
from app.services.purchases import PurchaseItemInput, create_purchase


# --- Fixtures ------------------------------------------------------------
class PaymentFixture:
    def __init__(
        self,
        shop: Shop,
        customer: Customer,
        variant: ProductVariant,
        supplier: Supplier,
    ) -> None:
        self.shop = shop
        self.customer = customer
        self.variant = variant
        self.supplier = supplier


async def _make_payment_shop(
    db: AsyncSession,
    name: str = "AI Payment Shop",
    *,
    customer_name: str = "Ali",
    outstanding: str = "8000",
) -> PaymentFixture:
    """A shop with one customer owing ``outstanding`` (via a credit sale)."""
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
                quantity=Decimal(30),
                unit_cost=Decimal(500),
            )
        ],
    )
    # One credit sale creates the Khata outstanding (no payment rows).
    total = Decimal(outstanding)
    qty = (total / Decimal(800)).quantize(Decimal("1.000"))
    assert qty > 0
    await sales_service.create_sale(
        db,
        shop_id=shop.id,
        items=[
            sales_service.SaleItemInput(
                variant_id=variant.id,
                quantity=qty,
                unit_price=Decimal("800.00"),
            )
        ],
        customer_id=customer.id,
        payments=[],
    )
    return PaymentFixture(shop, customer, variant, supplier)


def _ctx(fix: PaymentFixture) -> TenantContext:
    return TenantContext(shop_id=fix.shop.id, user_id=uuid.uuid4())


def _pay_tool(db: AsyncSession, fix: PaymentFixture) -> Any:
    tools = {t.name: t for t in build_payment_write_tools(db, _ctx(fix))}
    return tools[RECORD_PAYMENT_TOOL_NAME]


def _key() -> str:
    return uuid.uuid4().hex


async def _counts(db: AsyncSession, fix: PaymentFixture) -> dict[str, Any]:
    payments = (
        await db.execute(
            select(func.count(Payment.id)).where(Payment.shop_id == fix.shop.id)
        )
    ).scalar_one()
    receipts = (
        await db.execute(
            select(func.count(AIPaymentReceipt.id)).where(
                AIPaymentReceipt.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    ledger = (
        await db.execute(
            select(func.count(LedgerEntry.id)).where(LedgerEntry.shop_id == fix.shop.id)
        )
    ).scalar_one()
    return {"payments": payments, "receipts": receipts, "ledger": ledger}


async def _outstanding(db: AsyncSession, fix: PaymentFixture) -> Decimal:
    summary = await receivables_service.get_customer_summary(
        db, shop_id=fix.shop.id, customer_id=fix.customer.id
    )
    return summary.outstanding_balance


# --- Registry boundary: exactly one NEW mutation -------------------------


def test_payment_registry_is_exactly_one_tool() -> None:
    assert WRITE_TOOL_NAMES == ("record_customer_payment",)
    assert RECORD_PAYMENT_TOOL_NAME in WRITE_MASTER_TOOL_NAMES
    assert PAYMENT_HITL_INTERRUPT_CONFIG == {"record_customer_payment": True}


def test_payment_prompt_forces_same_turn_tool_call() -> None:
    """The prompt must order preview + tool call in ONE turn.

    Mirrors the Step 3 regression guard: the model must not end its turn
    after a text preview without calling the tool — the approval card only
    exists when the tool is actually called.
    """
    lowered = PAYMENT_SYSTEM_ADDENDUM.lower()
    assert "same turn" in lowered
    assert "without calling the tool" in lowered or "without calling" in lowered
    assert "approval card" in lowered
    assert "haan" in lowered and "directly" in lowered


@pytest.mark.asyncio
async def test_payment_tool_takes_no_tenant_or_ledger_args(
    db_session: AsyncSession,
) -> None:
    fix = await _make_payment_shop(db_session)
    tools = build_payment_write_tools(db_session, _ctx(fix))
    assert_payment_write_registry_is_minimal(tools)
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
        "ar_account_id",
        "ledger_entry_id",
    ):
        assert forbidden not in params, f"record_customer_payment must not take {forbidden}"


def test_payment_registry_rejects_a_second_mutation() -> None:
    from app.ai.tools.registry import demo_prepare_operation

    with pytest.raises(AssertionError, match="one mutation only"):
        assert_payment_write_registry_is_minimal(
            [demo_prepare_operation]  # type: ignore[list-item]
        )


@pytest.mark.asyncio
async def test_payment_tools_require_a_checkpointer(
    db_session: AsyncSession,
) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    fix = await _make_payment_shop(db_session)
    with pytest.raises(ValueError, match="checkpointer"):
        build_master_agent(
            model=GenericFakeChatModel(messages=iter(["x"])),
            checkpointer=None,
            include_hitl_demo=False,
            write_tools=build_payment_write_tools(db_session, _ctx(fix)),
        )


# --- Customer resolution -------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_payment_customer_unique_and_by_id(
    db_session: AsyncSession,
) -> None:
    fix = await _make_payment_shop(db_session)
    shop_id = fix.shop.id
    one = await resolve_payment_customer(db_session, shop_id, customer_name="Ali")
    assert one.id == fix.customer.id
    by_id = await resolve_payment_customer(
        db_session, shop_id, customer_id=str(fix.customer.id)
    )
    assert by_id.id == fix.customer.id
    with pytest.raises(PaymentPrepNotFoundError):
        await resolve_payment_customer(db_session, shop_id, customer_name="Nobody")
    with pytest.raises(PaymentPrepError):
        await resolve_payment_customer(
            db_session,
            shop_id,
            customer_id=str(fix.customer.id),
            customer_name="Ali",
        )


@pytest.mark.asyncio
async def test_resolve_payment_customer_needs_a_customer(
    db_session: AsyncSession,
) -> None:
    """Payments cannot be anonymous: no walk-in, no auto-create."""
    fix = await _make_payment_shop(db_session)
    with pytest.raises(PaymentPrepError, match="Customer is required"):
        await resolve_payment_customer(db_session, fix.shop.id)
    # A walk-in-style name matches nothing — never auto-created.
    with pytest.raises(PaymentPrepNotFoundError):
        await resolve_payment_customer(
            db_session, fix.shop.id, customer_name="Walk-in customer"
        )


@pytest.mark.asyncio
async def test_resolve_payment_customer_ambiguous_never_guesses(
    db_session: AsyncSession,
) -> None:
    fix = await _make_payment_shop(db_session)
    db_session.add(Customer(shop_id=fix.shop.id, name="Ali Raza"))
    await db_session.flush()
    with pytest.raises(PaymentPrepAmbiguousError) as exc_info:
        await resolve_payment_customer(db_session, fix.shop.id, customer_name="Ali")
    assert len(exc_info.value.matches) == 2
    assert "shop_id" not in str(exc_info.value).lower()


# --- Amount + method parsing ---------------------------------------------


def test_parse_payment_amount() -> None:
    assert parse_payment_amount("3000") == Decimal("3000.00")
    assert parse_payment_amount(5000) == Decimal("5000.00")
    assert parse_payment_amount("  1500.50 ") == Decimal("1500.50")
    for bad in ("0", "0.00", "-1", "-3000", "abc", "", "nan", "inf", None):
        with pytest.raises(PaymentPrepError):
            parse_payment_amount(bad)


def test_parse_payment_method_aliases() -> None:
    assert parse_payment_method("cash") == PaymentMethod.CASH
    assert parse_payment_method("Naqd") == PaymentMethod.CASH
    assert parse_payment_method("nagad") == PaymentMethod.CASH
    assert parse_payment_method("bank") == PaymentMethod.BANK
    assert parse_payment_method("Bank Transfer") == PaymentMethod.BANK
    assert parse_payment_method("card") == PaymentMethod.CARD
    assert parse_payment_method("Jazz Cash") == PaymentMethod.JAZZCASH
    assert parse_payment_method("Easy Paisa") == PaymentMethod.EASYPAISA
    assert parse_payment_method("other") == PaymentMethod.OTHER
    # Omitted method defaults to cash (shown on the approval card).
    assert parse_payment_method(None) == PaymentMethod.CASH
    assert parse_payment_method("") == PaymentMethod.CASH
    with pytest.raises(PaymentPrepError):
        parse_payment_method("bitcoin")


def test_parse_reference_and_uuid() -> None:
    assert parse_reference(None) is None
    assert parse_reference("") is None
    assert parse_reference("txn-123") == "txn-123"
    with pytest.raises(PaymentPrepError):
        parse_reference("x" * 101)
    cid = uuid.uuid4()
    assert parse_uuid_arg(str(cid), "customer_id") == cid
    with pytest.raises(PaymentPrepError):
        parse_uuid_arg("not-a-uuid", "customer_id")
    assert validate_idempotency_key(uuid.uuid4().hex) is not None
    for bad in ("", "short", "has space", None, "x" * 65):
        with pytest.raises(PaymentPrepError):
            validate_idempotency_key(bad)


def test_payment_preview_text() -> None:
    text = build_payment_preview(
        customer_name="Ali",
        outstanding_before=Decimal(8000),
        amount=Decimal(3000),
        method=PaymentMethod.CASH,
    )
    assert "Ali" in text
    assert "8000.00" in text and "3000.00" in text and "5000.00" in text
    assert "Cash" in text
    assert "Record this payment?" in text


# --- Direct tool execution -----------------------------------------------


@pytest.mark.asyncio
async def test_tool_records_payment_and_updates_balance(
    db_session: AsyncSession,
) -> None:
    fix = await _make_payment_shop(db_session)
    assert await _outstanding(db_session, fix) == Decimal("8000.00")
    before = await _counts(db_session, fix)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "amount": "3000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert out["duplicate"] is False
    assert out["customer_name"] == "Ali"
    assert out["amount"] == "3000.00"
    assert out["payment_method"] == "CASH"
    assert out["remaining_balance"] == "5000.00"
    assert uuid.UUID(out["payment_id"])
    after = await _counts(db_session, fix)
    assert after["payments"] == before["payments"] + 1
    assert after["receipts"] == before["receipts"] + 1
    assert after["ledger"] > before["ledger"]  # CUSTOMER_PAYMENT posting
    assert await _outstanding(db_session, fix) == Decimal("5000.00")


@pytest.mark.asyncio
async def test_tool_default_method_is_cash(db_session: AsyncSession) -> None:
    fix = await _make_payment_shop(db_session)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "amount": "1000",
        }
    )
    assert out["status"] == "completed"
    assert out["payment_method"] == "CASH"
    payment = await db_session.get(Payment, uuid.UUID(out["payment_id"]))
    assert payment is not None and payment.method == PaymentMethod.CASH


@pytest.mark.asyncio
async def test_tool_bank_method_maps_to_enum(db_session: AsyncSession) -> None:
    fix = await _make_payment_shop(db_session)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "amount": "2000",
            "payment_method": "bank transfer",
        }
    )
    assert out["status"] == "completed"
    assert out["payment_method"] == "BANK"


@pytest.mark.asyncio
async def test_tool_invalid_inputs_change_nothing(
    db_session: AsyncSession,
) -> None:
    fix = await _make_payment_shop(db_session)
    before = await _counts(db_session, fix)
    tool = _pay_tool(db_session, fix)
    bad_calls: list[dict[str, Any]] = [
        {"amount": "0"},
        {"amount": "-5"},
        {"amount": "lots"},
        {"amount": ""},
        {"payment_method": "bitcoin"},
        {"idempotency_key": "short"},
        {"customer_name": "Nobody Here"},
        {"customer_name": "Ali", "customer_id": str(fix.customer.id)},
        {"customer_id": "not-a-uuid"},
        {"reference": "x" * 101},
    ]
    for extra in bad_calls:
        args: dict[str, Any] = {
            "idempotency_key": _key(),
            "customer_name": "Ali",
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
        {"idempotency_key": _key(), "customer_name": "Ali", "payment_method": "cash"}
    )
    assert missing["status"] == "error"
    assert await _counts(db_session, fix) == before
    assert await _outstanding(db_session, fix) == Decimal("8000.00")


@pytest.mark.asyncio
async def test_tool_missing_customer_never_executes(
    db_session: AsyncSession,
) -> None:
    fix = await _make_payment_shop(db_session)
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
    fix = await _make_payment_shop(db_session)
    db_session.add(Customer(shop_id=fix.shop.id, name="Ali Raza"))
    await db_session.flush()
    before = await _counts(db_session, fix)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "amount": "1000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "ambiguous"
    assert len(out["matches"]) == 2
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_tool_overpayment_follows_existing_rule(
    db_session: AsyncSession,
) -> None:
    """No new overpayment rule: the service rejects, the tool surfaces it."""
    fix = await _make_payment_shop(db_session, outstanding="2000")
    before = await _counts(db_session, fix)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": fix.customer.name,
            "amount": "3000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "error"
    assert out["code"] == "payment_exceeds_outstanding"
    assert await _counts(db_session, fix) == before
    assert await _outstanding(db_session, fix) == Decimal("2000.00")


# --- Idempotency ------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_duplicate_key_executes_once(db_session: AsyncSession) -> None:
    fix = await _make_payment_shop(db_session)
    tool = _pay_tool(db_session, fix)
    key = _key()
    args = {
        "idempotency_key": key,
        "customer_name": "Ali",
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
    fix = await _make_payment_shop(db_session)
    tool = _pay_tool(db_session, fix)
    key = _key()
    args = {
        "idempotency_key": key,
        "customer_name": "Ali",
        "amount": "1000",
        "payment_method": "cash",
    }
    first = await tool.ainvoke(args)
    assert first["status"] == "completed"
    await db_session.commit()  # simulate the resume-request commit
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["payment_id"] == first["payment_id"]
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
    fix = await _make_payment_shop(db_session, outstanding="2000")
    tool = _pay_tool(db_session, fix)
    key = _key()
    failed = await tool.ainvoke(
        {
            "idempotency_key": key,
            "customer_name": "Ali",
            "amount": "9999",  # exceeds outstanding: rejected, no receipt
            "payment_method": "cash",
        }
    )
    assert failed["status"] == "error"
    retry = await tool.ainvoke(
        {
            "idempotency_key": key,
            "customer_name": "Ali",
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
    shop_a = await _make_payment_shop(db_session, name="Shop A")
    shop_b = await _make_payment_shop(db_session, name="Shop B")
    before_a = await _counts(db_session, shop_a)
    before_b = await _counts(db_session, shop_b)
    out = await _pay_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_id": str(shop_b.customer.id),
            "amount": "500",
            "payment_method": "cash",
        }
    )
    assert out["status"] in ("error", "not_found")
    assert await _counts(db_session, shop_a) == before_a
    assert await _counts(db_session, shop_b) == before_b


@pytest.mark.asyncio
async def test_tool_cannot_see_other_shop_names(db_session: AsyncSession) -> None:
    shop_a = await _make_payment_shop(db_session, name="Shop A")
    shop_b = await _make_payment_shop(
        db_session, name="Shop B", customer_name="Only In B", outstanding="8000"
    )
    out = await _pay_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Only In B",
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
            "customer_name": "Only In B",
            "amount": "500",
            "payment_method": "cash",
        }
    )
    assert ok["status"] == "completed"


# --- Authoritative service reuse -------------------------------------------


@pytest.mark.asyncio
async def test_ai_path_calls_payment_service_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fix = await _make_payment_shop(db_session)
    calls: list[dict[str, Any]] = []
    real_record = receivables_service.record_customer_payment

    async def _spy(session: AsyncSession, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await real_record(session, **kwargs)

    monkeypatch.setattr(receivables_service, "record_customer_payment", _spy)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "amount": "3000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["shop_id"] == fix.shop.id
    assert calls[0]["customer_id"] == fix.customer.id
    assert calls[0]["amount"] == Decimal("3000.00")
    assert calls[0]["method"] == PaymentMethod.CASH


@pytest.mark.asyncio
async def test_ai_payment_posts_balanced_customer_payment_ledger(
    db_session: AsyncSession,
) -> None:
    fix = await _make_payment_shop(db_session)
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
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
    assert rows, "expected a CUSTOMER_PAYMENT ledger group"
    assert {r.reference_type for r in rows} == {"CUSTOMER_PAYMENT"}
    assert sum(r.debit for r in rows) == sum(r.credit for r in rows) == Decimal(
        "3000.00"
    )


@pytest.mark.asyncio
async def test_payment_flows_through_statement_balance(
    db_session: AsyncSession,
) -> None:
    """before -> payment -> after, using the existing summary logic."""
    fix = await _make_payment_shop(db_session, outstanding="8000")
    assert await _outstanding(db_session, fix) == Decimal("8000.00")
    out = await _pay_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "customer_name": "Ali",
            "amount": "3000",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert await _outstanding(db_session, fix) == Decimal("5000.00")
    statement = await receivables_service.get_customer_statement(
        db_session, shop_id=fix.shop.id, customer_id=fix.customer.id
    )
    assert statement.closing_balance == Decimal("5000.00")
    kinds = {e.entry_type.value for e in statement.entries}
    assert "SALE" in kinds and "PAYMENT" in kinds


# --- Agent-level HITL -------------------------------------------------------


def _payment_fake(args: dict[str, Any]) -> BaseChatModel:
    """Fake model: request ``record_customer_payment`` once, then echo."""

    class _PaymentToolFakeModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "payment-write-fake"

        def bind_tools(self, tools: Any, **kwargs: Any) -> "_PaymentToolFakeModel":
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
                message = AIMessage(content=f"Payment recorded: {tool_msgs[-1].content}")
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "record_customer_payment",
                            "args": dict(args),
                            "id": "call_pay_1",
                            "type": "tool_call",
                        }
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return _PaymentToolFakeModel()


def _payment_args(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {
        "idempotency_key": _key(),
        "customer_name": "Ali",
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
    fix = await _make_payment_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_payment_fake(_payment_args()),
        include_hitl_demo=False,
        write_tools=build_payment_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"pay-approve-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Ali ne 3000 jama karwaye"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    action = paused.interrupts[0].value["action_requests"][0]
    assert action["name"] == "record_customer_payment"
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
    assert after["payments"] == before["payments"] + 1
    assert after["receipts"] == before["receipts"] + 1
    assert await _outstanding(db_session, fix) == Decimal("5000.00")


@pytest.mark.asyncio
async def test_hitl_reject_creates_nothing(db_session: AsyncSession) -> None:
    fix = await _make_payment_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_payment_fake(_payment_args()),
        include_hitl_demo=False,
        write_tools=build_payment_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"pay-reject-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Ali ne 3000 jama karwaye"}]},
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
    fix = await _make_payment_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_payment_fake(_payment_args()),
        include_hitl_demo=False,
        write_tools=build_payment_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"pay-edit-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Ali ne paise jama karwaye"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    edited = _payment_args({"amount": "2000"})
    resumed = await agent.ainvoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {
                            "name": "record_customer_payment",
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
    fix = await _make_payment_shop(db_session, outstanding="2000")
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_payment_fake(_payment_args({"amount": "1000"})),
        include_hitl_demo=False,
        write_tools=build_payment_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"pay-badedit-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Ali ne paise jama karwaye"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    edited = _payment_args({"amount": "9999"})  # exceeds outstanding
    resumed = await agent.ainvoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {
                            "name": "record_customer_payment",
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


# --- HTTP: mutation stays behind /ai/chat ----------------------------------


def _http_payment_fake() -> BaseChatModel:
    return _payment_fake(
        {
            "idempotency_key": f"httppay{uuid.uuid4().hex[:24]}",
            "customer_name": "Ali",
            "amount": "1000",
            "payment_method": "cash",
        }
    )


@pytest.mark.asyncio
async def test_chat_endpoint_payment_pause_then_approve(
    mocked_api_client: Any, api_session: AsyncSession
) -> None:
    from app.api.ai import get_chat_model
    from app.main import app
    from app.models import Shop as ShopModel
    from app.models import User as UserModel
    from app.models.user import UserRole

    shop = ShopModel(name=f"HTTP Pay Shop {uuid.uuid4().hex[:6]}")
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
    await sales_service.create_sale(
        api_session,
        shop_id=shop.id,
        items=[
            sales_service.SaleItemInput(
                variant_id=variant.id,
                quantity=Decimal(10),
                unit_price=Decimal("800.00"),
            )
        ],
        customer_id=customer.id,
        payments=[],
    )

    def _pay_count() -> Any:
        return api_session.execute(
            select(func.count(Payment.id)).where(
                Payment.shop_id == shop.id,
                Payment.customer_id == customer.id,
            )
        )

    app.dependency_overrides[get_chat_model] = _http_payment_fake
    try:
        thread = uuid.uuid4().hex
        paused = await mocked_api_client.post(
            "/ai/chat",
            json={
                "message": "Ali ne 1000 jama karwaye",
                "thread_id": thread,
            },
        )
        assert paused.status_code == 200
        body = paused.json()
        assert body["status"] == "paused"
        assert body["interrupts"][0]["name"] == "record_customer_payment"
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

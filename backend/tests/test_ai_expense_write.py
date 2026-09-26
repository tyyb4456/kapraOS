"""Step 6: AI-assisted expense recording — one HITL-gated mutation.

Covers the brief's minimum without any real LLM (deterministic fake
models, direct tool calls — no network, no tokens):

* preparation: category resolution (valid/alias/missing/unknown/ambiguous),
  amount validation (including hazar/lakh), payment-method mapping,
  description validation
* HITL: pause before mutation, approve executes, reject is a no-op,
  edit executes the corrected args (re-validated)
* transaction: success commits atomically, failure leaves no partial
  state (savepoint-scoped, never a full-session rollback)
* idempotency: same operation key twice (in-session and post-commit)
  creates exactly one expense; a failed attempt does not poison its key
* tenant isolation: shop-scoped operation, same key in two shops is
  independent, no cross-tenant leakage
* service reuse: the AI path calls ``expenses.create_expense()``
* accounting: the existing Debit Expense / Credit Cash|Bank posting is
  preserved
* inventory: expense recording never moves stock
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
    EXPENSE_HITL_INTERRUPT_CONFIG,
    EXPENSE_SYSTEM_ADDENDUM,
    WRITE_MASTER_TOOL_NAMES,
    approve_decision,
    build_master_agent,
    hitl_resume_payload,
    reject_decision,
)
from app.ai.state import TenantContext
from app.ai.tools.expenses_write import (
    RECORD_EXPENSE_TOOL_NAME,
    WRITE_TOOL_NAMES,
    ExpensePrepAmbiguousError,
    ExpensePrepError,
    ExpensePrepNotFoundError,
    assert_expense_write_registry_is_minimal,
    build_expense_preview,
    build_expense_write_tools,
    parse_description,
    parse_expense_amount,
    parse_expense_category,
    parse_expense_payment_method,
    validate_idempotency_key,
)
from app.models import (
    AIExpenseReceipt,
    Category,
    Customer,
    Expense,
    ExpenseCategory,
    Inventory,
    LedgerEntry,
    PaymentMethod,
    Product,
    ProductType,
    ProductVariant,
    Purchase,
    Sale,
    Shop,
    Unit,
)
from app.services import expenses as expenses_service


# --- Fixtures ------------------------------------------------------------
class ExpenseFixture:
    def __init__(
        self,
        shop: Shop,
        variant: ProductVariant,
    ) -> None:
        self.shop = shop
        self.variant = variant


async def _make_expense_shop(
    db: AsyncSession,
    name: str = "AI Expense Shop",
) -> ExpenseFixture:
    """A shop with catalog rows (so inventory assertions are meaningful)."""
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
        sku=f"EXP-{uuid.uuid4().hex[:6]}",
        purchase_price=Decimal("500.00"),
        selling_price=Decimal("800.00"),
        unit=Unit.METER,
    )
    db.add(variant)
    await db.flush()
    return ExpenseFixture(shop, variant)


def _ctx(fix: ExpenseFixture) -> TenantContext:
    return TenantContext(shop_id=fix.shop.id, user_id=uuid.uuid4())


def _expense_tool(db: AsyncSession, fix: ExpenseFixture) -> Any:
    tools = {t.name: t for t in build_expense_write_tools(db, _ctx(fix))}
    return tools[RECORD_EXPENSE_TOOL_NAME]


def _key() -> str:
    return uuid.uuid4().hex


async def _counts(db: AsyncSession, fix: ExpenseFixture) -> dict[str, Any]:
    expenses = (
        await db.execute(
            select(func.count(Expense.id)).where(Expense.shop_id == fix.shop.id)
        )
    ).scalar_one()
    receipts = (
        await db.execute(
            select(func.count(AIExpenseReceipt.id)).where(
                AIExpenseReceipt.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    ledger = (
        await db.execute(
            select(func.count(LedgerEntry.id)).where(LedgerEntry.shop_id == fix.shop.id)
        )
    ).scalar_one()
    return {"expenses": expenses, "receipts": receipts, "ledger": ledger}


async def _inventory_qty(db: AsyncSession, fix: ExpenseFixture) -> Decimal:
    row = (
        await db.execute(
            select(Inventory).where(Inventory.variant_id == fix.variant.id)
        )
    ).scalar_one_or_none()
    if row is None:
        return Decimal("0.000")
    return Decimal(row.quantity)


# --- Registry boundary: exactly one NEW mutation -------------------------


def test_expense_registry_is_exactly_one_tool() -> None:
    assert WRITE_TOOL_NAMES == ("record_expense",)
    assert RECORD_EXPENSE_TOOL_NAME in WRITE_MASTER_TOOL_NAMES
    assert set(WRITE_MASTER_TOOL_NAMES) == {
        "create_sale",
        "record_customer_payment",
        "record_supplier_payment",
        "record_expense",
        "create_purchase",
    }
    assert EXPENSE_HITL_INTERRUPT_CONFIG == {"record_expense": True}


def test_expense_prompt_forces_same_turn_tool_call() -> None:
    """The prompt must order preview + tool call in ONE turn."""
    lowered = EXPENSE_SYSTEM_ADDENDUM.lower()
    assert "same turn" in lowered
    assert "without calling the tool" in lowered or "without calling" in lowered
    assert "approval card" in lowered
    assert "haan" in lowered and "directly" in lowered


def test_expense_prompt_covers_natural_examples() -> None:
    lowered = EXPENSE_SYSTEM_ADDENDUM.lower()
    assert "bijli" in lowered
    assert "rent" in lowered
    assert "delivery" in lowered
    assert "chai" in lowered
    assert "hazar" in lowered
    assert "never guess" in lowered or "clarification" in lowered
    assert "ledger" in lowered or "accounting" in lowered


@pytest.mark.asyncio
async def test_expense_tool_takes_no_tenant_or_ledger_args(
    db_session: AsyncSession,
) -> None:
    fix = await _make_expense_shop(db_session)
    tools = build_expense_write_tools(db_session, _ctx(fix))
    assert_expense_write_registry_is_minimal(tools)
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
        "ledger_entry_id",
        "sql",
        "raw_sql",
        "reference_type",
    ):
        assert forbidden not in params, (
            f"record_expense must not take {forbidden}"
        )


def test_expense_registry_rejects_a_second_mutation() -> None:
    from app.ai.tools.registry import demo_prepare_operation

    with pytest.raises(AssertionError, match="one mutation only"):
        assert_expense_write_registry_is_minimal(
            [demo_prepare_operation]  # type: ignore[list-item]
        )


@pytest.mark.asyncio
async def test_expense_tools_require_a_checkpointer(
    db_session: AsyncSession,
) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    fix = await _make_expense_shop(db_session)
    with pytest.raises(ValueError, match="checkpointer"):
        build_master_agent(
            model=GenericFakeChatModel(messages=iter(["x"])),
            checkpointer=None,
            include_hitl_demo=False,
            write_tools=build_expense_write_tools(db_session, _ctx(fix)),
        )


# --- Amount parsing ------------------------------------------------------


def test_parse_expense_amount_plain() -> None:
    assert parse_expense_amount("3000") == Decimal("3000.00")
    assert parse_expense_amount(5000) == Decimal("5000.00")
    assert parse_expense_amount("  1500.50 ") == Decimal("1500.50")
    assert parse_expense_amount("5,000") == Decimal("5000.00")
    # Decimal precision: quantized to 2dp, never float.
    assert parse_expense_amount("100.005") == Decimal("100.01")
    result = parse_expense_amount("5000")
    assert isinstance(result, Decimal) and not isinstance(result, float)
    for bad in ("0", "0.00", "-1", "-3000", "abc", "", "nan", "inf", None):
        with pytest.raises(ExpensePrepError):
            parse_expense_amount(bad)


def test_parse_expense_amount_natural_language() -> None:
    assert parse_expense_amount("5 hazar") == Decimal("5000.00")
    assert parse_expense_amount("5 hazaar") == Decimal("5000.00")
    assert parse_expense_amount("5 Hazar") == Decimal("5000.00")
    assert parse_expense_amount("10 hazar") == Decimal("10000.00")
    assert parse_expense_amount("2 lakh") == Decimal("200000.00")
    assert parse_expense_amount("1.5 lakh") == Decimal("150000.00")
    assert parse_expense_amount("5,000") == Decimal("5000.00")
    assert parse_expense_amount("10 thousand") == Decimal("10000.00")
    assert parse_expense_amount("5k") == Decimal("5000.00")
    assert parse_expense_amount("2 lac") == Decimal("200000.00")
    assert parse_expense_amount("1 crore") == Decimal("10000000.00")


def test_parse_expense_amount_no_float_arithmetic() -> None:
    # Decimal-based: 0.1 hazar is exactly 100.00, not a float artefact.
    assert parse_expense_amount("0.1 hazar") == Decimal("100.00")
    for bad in ("lots", "five thousand", "hazar", "lakh 5", "--5", "5 hazar extra"):
        with pytest.raises(ExpensePrepError):
            parse_expense_amount(bad)


def test_parse_expense_amount_missing_asks_for_amount() -> None:
    with pytest.raises(ExpensePrepError, match="missing"):
        parse_expense_amount(None)
    with pytest.raises(ExpensePrepError, match="missing"):
        parse_expense_amount("")


# --- Category parsing ----------------------------------------------------


def test_parse_expense_category_exact_values() -> None:
    assert parse_expense_category("rent") == ExpenseCategory.RENT
    assert parse_expense_category("salary") == ExpenseCategory.SALARY
    assert parse_expense_category("utilities") == ExpenseCategory.UTILITIES
    assert parse_expense_category("transport") == ExpenseCategory.TRANSPORT
    assert parse_expense_category("marketing") == ExpenseCategory.MARKETING
    assert parse_expense_category("maintenance") == ExpenseCategory.MAINTENANCE
    assert parse_expense_category("supplies") == ExpenseCategory.SUPPLIES
    assert parse_expense_category("other") == ExpenseCategory.OTHER


def test_parse_expense_category_natural_aliases() -> None:
    # Spec examples: bijli/electricity -> utilities, kiraya -> rent,
    # delivery -> transport, chai -> other, tankhwa -> salary.
    assert parse_expense_category("bijli") == ExpenseCategory.UTILITIES
    assert parse_expense_category("electricity") == ExpenseCategory.UTILITIES
    assert parse_expense_category("shop ki bijli ka bill") == ExpenseCategory.UTILITIES
    assert parse_expense_category("electricity ka bill") == ExpenseCategory.UTILITIES
    assert parse_expense_category("kiraya") == ExpenseCategory.RENT
    assert parse_expense_category("rent") == ExpenseCategory.RENT
    assert parse_expense_category("delivery") == ExpenseCategory.TRANSPORT
    assert parse_expense_category("delivery ke") == ExpenseCategory.TRANSPORT
    assert parse_expense_category("chai") == ExpenseCategory.OTHER
    assert parse_expense_category("chai pani") == ExpenseCategory.OTHER
    assert parse_expense_category("chai pani ka kharcha") == ExpenseCategory.OTHER
    assert parse_expense_category("tankhwa") == ExpenseCategory.SALARY
    assert parse_expense_category("salary") == ExpenseCategory.SALARY


def test_parse_expense_category_missing_unknown_ambiguous() -> None:
    with pytest.raises(ExpensePrepError, match="missing"):
        parse_expense_category(None)
    with pytest.raises(ExpensePrepError, match="missing"):
        parse_expense_category("")
    with pytest.raises(ExpensePrepError, match="missing"):
        parse_expense_category("   ")
    # Generic words alone mean "missing category", not a guess — but
    # an outright unknown category is not_found.
    with pytest.raises(ExpensePrepNotFoundError):
        parse_expense_category("spaceship warp drive")
    with pytest.raises(ExpensePrepNotFoundError):
        parse_expense_category("crypto mining rigs")
    # Inputs naming two categories are ambiguous — never guessed.
    with pytest.raises(ExpensePrepAmbiguousError) as exc_info:
        parse_expense_category("rent bijli")
    assert len(exc_info.value.matches) == 2


def test_parse_description_and_payment_method() -> None:
    assert parse_description(None) is None
    assert parse_description("") is None
    assert parse_description("shop ki bijli ka bill") == "shop ki bijli ka bill"
    with pytest.raises(ExpensePrepError):
        parse_description("x" * 301)
    assert parse_expense_payment_method("cash") == PaymentMethod.CASH
    assert parse_expense_payment_method("Naqd") == PaymentMethod.CASH
    assert parse_expense_payment_method("nagad") == PaymentMethod.CASH
    assert parse_expense_payment_method("bank") == PaymentMethod.BANK
    assert parse_expense_payment_method("Bank Transfer") == PaymentMethod.BANK
    assert parse_expense_payment_method("card") == PaymentMethod.CARD
    assert parse_expense_payment_method("Jazz Cash") == PaymentMethod.JAZZCASH
    assert parse_expense_payment_method("Easy Paisa") == PaymentMethod.EASYPAISA
    assert parse_expense_payment_method(None) == PaymentMethod.CASH
    assert parse_expense_payment_method("") == PaymentMethod.CASH
    with pytest.raises(ExpensePrepError):
        parse_expense_payment_method("bitcoin")


def test_parse_idempotency_key() -> None:
    assert validate_idempotency_key(uuid.uuid4().hex) is not None
    for bad in ("", "short", "has space", None, "x" * 65):
        with pytest.raises(ExpensePrepError):
            validate_idempotency_key(bad)


def test_expense_preview_text() -> None:
    text = build_expense_preview(
        category=ExpenseCategory.RENT,
        amount=Decimal(5000),
        method=PaymentMethod.CASH,
        description="September rent",
    )
    assert "Expense" in text
    assert "rent" in text
    assert "5000.00" in text
    assert "Cash" in text
    assert "September rent" in text
    assert "Record Rs." in text
    bank_text = build_expense_preview(
        category=ExpenseCategory.UTILITIES,
        amount=Decimal(3000),
        method=PaymentMethod.BANK,
    )
    assert "Bank" in bank_text
    assert "utilities" in bank_text


# --- Direct tool execution -----------------------------------------------


@pytest.mark.asyncio
async def test_tool_records_expense_rent_cash(db_session: AsyncSession) -> None:
    fix = await _make_expense_shop(db_session)
    before = await _counts(db_session, fix)
    out = await _expense_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "5000",
            "expense_category": "rent",
            "description": "September rent",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert out["duplicate"] is False
    assert out["expense_category"] == "rent"
    assert out["amount"] == "5000.00"
    assert out["payment_method"] == "CASH"
    assert uuid.UUID(out["expense_id"])
    after = await _counts(db_session, fix)
    assert after["expenses"] == before["expenses"] + 1
    assert after["receipts"] == before["receipts"] + 1
    assert after["ledger"] > before["ledger"]  # EXPENSE posting


@pytest.mark.asyncio
async def test_tool_natural_examples_bijli_delivery_chai(
    db_session: AsyncSession,
) -> None:
    fix = await _make_expense_shop(db_session)
    tool = _expense_tool(db_session, fix)
    # "shop ka 3000 bijli ka bill enter kar do"
    out1 = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "3000",
            "expense_category": "shop ki bijli ka bill",
            "description": "shop ki bijli ka bill",
            "payment_method": "cash",
        }
    )
    assert out1["status"] == "completed"
    assert out1["expense_category"] == "utilities"
    # "1500 delivery ke expense record karo"
    out2 = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "1500",
            "expense_category": "delivery",
            "payment_method": "cash",
        }
    )
    assert out2["status"] == "completed"
    assert out2["expense_category"] == "transport"
    # "1000 chai pani ka kharcha add kar do"
    out3 = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "1000",
            "expense_category": "chai pani ka kharcha",
            "payment_method": "cash",
        }
    )
    assert out3["status"] == "completed"
    assert out3["expense_category"] == "other"


@pytest.mark.asyncio
async def test_tool_natural_amount_hazar_and_lakh(db_session: AsyncSession) -> None:
    fix = await _make_expense_shop(db_session)
    tool = _expense_tool(db_session, fix)
    out = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "5 hazar",
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert out["amount"] == "5000.00"
    out2 = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "1.5 lakh",
            "expense_category": "salary",
            "payment_method": "bank",
        }
    )
    assert out2["status"] == "completed"
    assert out2["amount"] == "150000.00"


@pytest.mark.asyncio
async def test_tool_default_method_is_cash(db_session: AsyncSession) -> None:
    fix = await _make_expense_shop(db_session)
    out = await _expense_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "2000",
            "expense_category": "electricity ka bill",
        }
    )
    assert out["status"] == "completed"
    assert out["payment_method"] == "CASH"
    expense = await db_session.get(Expense, uuid.UUID(out["expense_id"]))
    assert expense is not None and expense.payment_method == PaymentMethod.CASH


@pytest.mark.asyncio
async def test_tool_bank_and_wallet_methods(db_session: AsyncSession) -> None:
    fix = await _make_expense_shop(db_session)
    tool = _expense_tool(db_session, fix)
    for method_arg, expected in [
        ("bank transfer", "BANK"),
        ("jazzcash", "JAZZCASH"),
        ("Easy Paisa", "EASYPAISA"),
    ]:
        out = await tool.ainvoke(
            {
                "idempotency_key": _key(),
                "amount": "500",
                "expense_category": "rent",
                "payment_method": method_arg,
            }
        )
        assert out["status"] == "completed", method_arg
        assert out["payment_method"] == expected, method_arg


@pytest.mark.asyncio
async def test_tool_description_persisted_and_optional(
    db_session: AsyncSession,
) -> None:
    fix = await _make_expense_shop(db_session)
    tool = _expense_tool(db_session, fix)
    out = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "3000",
            "expense_category": "utilities",
            "description": "shop ki bijli ka bill",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert out["description"] == "shop ki bijli ka bill"
    stored = await db_session.get(Expense, uuid.UUID(out["expense_id"]))
    assert stored is not None and stored.description == "shop ki bijli ka bill"
    # Description is optional: omitted stays None.
    out2 = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "500",
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    assert out2["status"] == "completed"
    assert out2["description"] is None


@pytest.mark.asyncio
async def test_tool_invalid_inputs_change_nothing(
    db_session: AsyncSession,
) -> None:
    fix = await _make_expense_shop(db_session)
    before = await _counts(db_session, fix)
    tool = _expense_tool(db_session, fix)
    bad_calls: list[dict[str, Any]] = [
        {"amount": "0"},
        {"amount": "-5"},
        {"amount": "lots"},
        {"amount": ""},
        {"amount": "hazar"},
        {"payment_method": "bitcoin"},
        {"idempotency_key": "short"},
        {"expense_category": "spaceship warp drive"},
        {"description": "x" * 301},
    ]
    for extra in bad_calls:
        args: dict[str, Any] = {
            "idempotency_key": _key(),
            "amount": "100",
            "expense_category": "rent",
            "payment_method": "cash",
        }
        args.update(extra)
        out = await tool.ainvoke(args)
        assert out["status"] in ("error", "ambiguous", "not_found"), args
        assert "traceback" not in out["message"].lower()
        assert "shop_id" not in out["message"]
    # Missing amount asks for clarification — never executes.
    missing_amount = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    assert missing_amount["status"] == "error"
    # Missing category asks for clarification — never executes.
    missing_cat = await tool.ainvoke(
        {"idempotency_key": _key(), "amount": "3000", "payment_method": "cash"}
    )
    assert missing_cat["status"] == "error"
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_tool_ambiguous_and_unknown_category_never_execute(
    db_session: AsyncSession,
) -> None:
    fix = await _make_expense_shop(db_session)
    before = await _counts(db_session, fix)
    tool = _expense_tool(db_session, fix)
    ambiguous = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "1000",
            "expense_category": "rent bijli",
            "payment_method": "cash",
        }
    )
    assert ambiguous["status"] == "ambiguous"
    assert len(ambiguous["matches"]) == 2
    assert await _counts(db_session, fix) == before
    unknown = await tool.ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "1000",
            "expense_category": "spaceship warp drive",
            "payment_method": "cash",
        }
    )
    assert unknown["status"] == "not_found"
    assert await _counts(db_session, fix) == before


# --- Idempotency ------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_duplicate_key_executes_once(db_session: AsyncSession) -> None:
    fix = await _make_expense_shop(db_session)
    tool = _expense_tool(db_session, fix)
    key = _key()
    args = {
        "idempotency_key": key,
        "amount": "3000",
        "expense_category": "rent",
        "payment_method": "cash",
    }
    first = await tool.ainvoke(args)
    assert first["status"] == "completed" and first["duplicate"] is False
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["expense_id"] == first["expense_id"]
    expenses = (
        await db_session.execute(
            select(func.count(Expense.id)).where(Expense.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert expenses == 1


@pytest.mark.asyncio
async def test_tool_duplicate_key_safe_after_commit(
    api_session: AsyncSession,
) -> None:
    # Uses `api_session` (outer-transaction fixture) on purpose: the
    # commit below stays inside the test's outer transaction, so the
    # teardown rollback discards it.
    db_session = api_session
    fix = await _make_expense_shop(db_session)
    tool = _expense_tool(db_session, fix)
    key = _key()
    args = {
        "idempotency_key": key,
        "amount": "1000",
        "expense_category": "rent",
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
    assert second["expense_id"] == first["expense_id"]
    ledger_after = (
        await db_session.execute(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.shop_id == fix.shop.id
            )
        )
    ).scalar_one()
    # Replay creates no second expense and no second posting.
    assert ledger_after == ledger_before
    expenses = (
        await db_session.execute(
            select(func.count(Expense.id)).where(Expense.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert expenses == 1
    # Distinct keys are distinct expenses.
    third = await tool.ainvoke({**args, "idempotency_key": _key()})
    assert third["duplicate"] is False
    expenses = (
        await db_session.execute(
            select(func.count(Expense.id)).where(Expense.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert expenses == 2


@pytest.mark.asyncio
async def test_tool_failed_attempt_does_not_poison_key(
    db_session: AsyncSession,
) -> None:
    fix = await _make_expense_shop(db_session)
    tool = _expense_tool(db_session, fix)
    key = _key()
    failed = await tool.ainvoke(
        {
            "idempotency_key": key,
            "amount": "0",  # invalid: rejected, no receipt
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    assert failed["status"] == "error"
    retry = await tool.ainvoke(
        {
            "idempotency_key": key,
            "amount": "1000",
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    assert retry["status"] == "completed" and retry["duplicate"] is False
    expenses = (
        await db_session.execute(
            select(func.count(Expense.id)).where(Expense.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert expenses == 1


@pytest.mark.asyncio
async def test_tool_concurrent_race_returns_winner(
    db_session: AsyncSession,
) -> None:
    """A receipt race (unique constraint) re-reads the winner, no duplicate."""
    fix = await _make_expense_shop(db_session)
    tool = _expense_tool(db_session, fix)
    key = _key()
    # Simulate a concurrent winner that claimed the key first.
    winner = await expenses_service.create_expense(
        db_session,
        shop_id=fix.shop.id,
        category=ExpenseCategory.RENT,
        amount=Decimal("1200.00"),
        payment_method=PaymentMethod.CASH,
    )
    db_session.add(
        AIExpenseReceipt(
            shop_id=fix.shop.id, operation_key=key, expense_id=winner.id
        )
    )
    await db_session.flush()
    before = await _counts(db_session, fix)
    out = await tool.ainvoke(
        {
            "idempotency_key": key,
            "amount": "9999",
            "expense_category": "salary",
            "payment_method": "bank",
        }
    )
    assert out["status"] == "completed" and out["duplicate"] is True
    assert out["expense_id"] == str(winner.id)
    assert await _counts(db_session, fix) == before


# --- Tenant isolation -------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_keys_are_tenant_scoped(
    db_session: AsyncSession,
) -> None:
    """The same key in two shops creates two independent expenses."""
    shop_a = await _make_expense_shop(db_session, name="Shop A")
    shop_b = await _make_expense_shop(db_session, name="Shop B")
    key = _key()
    out_a = await _expense_tool(db_session, shop_a).ainvoke(
        {
            "idempotency_key": key,
            "amount": "500",
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    out_b = await _expense_tool(db_session, shop_b).ainvoke(
        {
            "idempotency_key": key,
            "amount": "500",
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    assert out_a["status"] == "completed" and out_a["duplicate"] is False
    assert out_b["status"] == "completed" and out_b["duplicate"] is False
    assert out_a["expense_id"] != out_b["expense_id"]


@pytest.mark.asyncio
async def test_expenses_are_tenant_isolated(db_session: AsyncSession) -> None:
    shop_a = await _make_expense_shop(db_session, name="Shop A")
    shop_b = await _make_expense_shop(db_session, name="Shop B")
    out_b = await _expense_tool(db_session, shop_b).ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "100",
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    assert out_b["status"] == "completed"
    # Shop A's listing never sees Shop B's expense (no cross-tenant leak).
    page_a = await expenses_service.list_expenses(db_session, shop_id=shop_a.shop.id)
    assert page_a.total == 0
    page_b = await expenses_service.list_expenses(db_session, shop_id=shop_b.shop.id)
    assert page_b.total == 1
    with pytest.raises(expenses_service.ExpenseNotFoundError):
        await expenses_service.get_expense(
            db_session,
            shop_id=shop_a.shop.id,
            expense_id=uuid.UUID(out_b["expense_id"]),
        )


# --- Authoritative service reuse -------------------------------------------


@pytest.mark.asyncio
async def test_ai_path_calls_expense_service_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fix = await _make_expense_shop(db_session)
    calls: list[dict[str, Any]] = []
    real_create = expenses_service.create_expense

    async def _spy(session: AsyncSession, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await real_create(session, **kwargs)

    monkeypatch.setattr(expenses_service, "create_expense", _spy)
    out = await _expense_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "3000",
            "expense_category": "bijli",
            "description": "shop ki bijli ka bill",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["shop_id"] == fix.shop.id
    assert calls[0]["category"] == ExpenseCategory.UTILITIES
    assert calls[0]["amount"] == Decimal("3000.00")
    assert calls[0]["payment_method"] == PaymentMethod.CASH
    assert calls[0]["description"] == "shop ki bijli ka bill"


@pytest.mark.asyncio
async def test_ai_expense_posts_balanced_ledger(db_session: AsyncSession) -> None:
    fix = await _make_expense_shop(db_session)
    out = await _expense_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "3000",
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    expense_id = uuid.UUID(out["expense_id"])
    rows = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.shop_id == fix.shop.id,
                LedgerEntry.reference_id == expense_id,
            )
        )
    ).scalars().all()
    assert rows, "expected an EXPENSE ledger group"
    assert {r.reference_type for r in rows} == {"EXPENSE"}
    assert sum(r.debit for r in rows) == sum(r.credit for r in rows) == Decimal(
        "3000.00"
    )
    # Debit lands on the category account, credit on Cash (cash method).
    from app.services.accounting import ensure_system_accounts

    accounts = await ensure_system_accounts(db_session, shop_id=fix.shop.id)
    debit_line = next(r for r in rows if Decimal(r.debit) > 0)
    credit_line = next(r for r in rows if Decimal(r.credit) > 0)
    assert debit_line.account_id == accounts["5100"].id  # Rent Expense
    assert credit_line.account_id == accounts["1000"].id  # Cash


@pytest.mark.asyncio
async def test_ai_expense_bank_method_credits_bank(
    db_session: AsyncSession,
) -> None:
    fix = await _make_expense_shop(db_session)
    out = await _expense_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "5000",
            "expense_category": "rent",
            "payment_method": "bank",
        }
    )
    assert out["status"] == "completed"
    rows = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.shop_id == fix.shop.id,
                LedgerEntry.reference_id == uuid.UUID(out["expense_id"]),
            )
        )
    ).scalars().all()
    from app.services.accounting import ensure_system_accounts

    accounts = await ensure_system_accounts(db_session, shop_id=fix.shop.id)
    credit_line = next(r for r in rows if Decimal(r.credit) > 0)
    assert credit_line.account_id == accounts["1010"].id  # Bank


@pytest.mark.asyncio
async def test_expense_flows_through_statement(db_session: AsyncSession) -> None:
    """The new expense is visible through the existing expense read model."""
    fix = await _make_expense_shop(db_session)
    page_before = await expenses_service.list_expenses(
        db_session, shop_id=fix.shop.id
    )
    assert page_before.total == 0
    out = await _expense_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "3000",
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    page_after = await expenses_service.list_expenses(
        db_session, shop_id=fix.shop.id
    )
    assert page_after.total == 1
    assert page_after.expenses[0].amount == Decimal("3000.00")
    assert page_after.expenses[0].category is ExpenseCategory.RENT


@pytest.mark.asyncio
async def test_expense_does_not_touch_inventory(db_session: AsyncSession) -> None:
    fix = await _make_expense_shop(db_session)
    qty_before = await _inventory_qty(db_session, fix)
    out = await _expense_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "1000",
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )
    assert out["status"] == "completed"
    assert await _inventory_qty(db_session, fix) == qty_before


@pytest.mark.asyncio
async def test_tool_does_not_mutate_unrelated_models(
    db_session: AsyncSession,
) -> None:
    """The AI tool records an Expense: no Customer/Sale/Purchase writes."""
    fix = await _make_expense_shop(db_session)
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
    out = await _expense_tool(db_session, fix).ainvoke(
        {
            "idempotency_key": _key(),
            "amount": "1000",
            "expense_category": "rent",
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
    """A service failure leaves no expense, receipt, or ledger behind."""
    fix = await _make_expense_shop(db_session)
    before = await _counts(db_session, fix)
    tool = _expense_tool(db_session, fix)

    import app.services.expenses as expenses_module

    old = expenses_module.create_expense

    async def _boom(session: AsyncSession, **kwargs: Any) -> Any:
        raise expenses_module.InvalidExpenseAmountError("boom")

    expenses_module.create_expense = _boom  # type: ignore[assignment]
    try:
        out = await tool.ainvoke(
            {
                "idempotency_key": _key(),
                "amount": "500",
                "expense_category": "rent",
                "payment_method": "cash",
            }
        )
    finally:
        expenses_module.create_expense = old
    assert out["status"] == "error"
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_rollback_on_accounting_failure(db_session: AsyncSession) -> None:
    """An accounting failure rolls back the expense and the receipt together."""
    fix = await _make_expense_shop(db_session)
    before = await _counts(db_session, fix)
    tool = _expense_tool(db_session, fix)

    import app.services.accounting as accounting_module

    real_post = accounting_module.post_expense

    async def _boom(session: AsyncSession, **kwargs: Any) -> Any:
        raise accounting_module.AccountingError("ledger down")

    accounting_module.post_expense = _boom  # type: ignore[assignment]
    try:
        out = await tool.ainvoke(
            {
                "idempotency_key": _key(),
                "amount": "500",
                "expense_category": "rent",
                "payment_method": "cash",
            }
        )
    finally:
        accounting_module.post_expense = real_post
    assert out["status"] == "error"
    assert out["code"] == "expense_failed"
    assert await _counts(db_session, fix) == before


# --- Agent-level HITL -------------------------------------------------------


def _expense_fake(args: dict[str, Any]) -> BaseChatModel:
    """Fake model: request ``record_expense`` once, then echo."""

    class _ExpenseToolFakeModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "expense-write-fake"

        def bind_tools(self, tools: Any, **kwargs: Any) -> "_ExpenseToolFakeModel":
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
                    content=f"Expense recorded: {tool_msgs[-1].content}"
                )
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "record_expense",
                            "args": dict(args),
                            "id": "call_exp_1",
                            "type": "tool_call",
                        }
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return _ExpenseToolFakeModel()


def _expense_args(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {
        "idempotency_key": _key(),
        "amount": "3000",
        "expense_category": "bijli",
        "description": "shop ki bijli ka bill",
        "payment_method": "cash",
    }
    if extra:
        args.update(extra)
    return args


@pytest.mark.asyncio
async def test_hitl_pause_then_approve_records_one_expense(
    db_session: AsyncSession,
) -> None:
    fix = await _make_expense_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_expense_fake(_expense_args()),
        include_hitl_demo=False,
        write_tools=build_expense_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"exp-approve-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "shop ka 3000 bijli ka bill record kar do"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    action = paused.interrupts[0].value["action_requests"][0]
    assert action["name"] == "record_expense"
    assert action["args"]["expense_category"] == "bijli"
    # Paused BEFORE mutation: nothing written, nothing held.
    assert await _counts(db_session, fix) == before

    resumed = await agent.ainvoke(
        Command(resume=hitl_resume_payload([approve_decision()])),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    after = await _counts(db_session, fix)
    assert after["expenses"] == before["expenses"] + 1
    assert after["receipts"] == before["receipts"] + 1


@pytest.mark.asyncio
async def test_hitl_reject_creates_nothing(db_session: AsyncSession) -> None:
    fix = await _make_expense_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_expense_fake(_expense_args()),
        include_hitl_demo=False,
        write_tools=build_expense_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"exp-reject-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "5000 rent expense record karo"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    resumed = await agent.ainvoke(
        Command(
            resume=hitl_resume_payload(
                [reject_decision("User rejected the expense. Do not record anything.")]
            )
        ),
        config=config,
        version="v2",
    )
    assert not resumed.interrupts
    # Rejection: zero mutation across expenses, ledger, receipts.
    assert await _counts(db_session, fix) == before


@pytest.mark.asyncio
async def test_hitl_edit_executes_corrected_args(db_session: AsyncSession) -> None:
    fix = await _make_expense_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_expense_fake(_expense_args()),
        include_hitl_demo=False,
        write_tools=build_expense_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"exp-edit-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "expense record kar do"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    edited = _expense_args({"amount": "2000", "expense_category": "rent"})
    resumed = await agent.ainvoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {
                            "name": "record_expense",
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
    assert after["expenses"] == before["expenses"] + 1
    page = await expenses_service.list_expenses(db_session, shop_id=fix.shop.id)
    assert page.total == 1
    assert page.expenses[0].amount == Decimal("2000.00")


@pytest.mark.asyncio
async def test_hitl_edit_with_bad_args_records_nothing(
    db_session: AsyncSession,
) -> None:
    """Edited args pass the exact same backend validation (bad amount)."""
    fix = await _make_expense_shop(db_session)
    before = await _counts(db_session, fix)
    agent = build_master_agent(
        model=_expense_fake(_expense_args({"amount": "1000"})),
        include_hitl_demo=False,
        write_tools=build_expense_write_tools(db_session, _ctx(fix)),
    )
    config = {"configurable": {"thread_id": f"exp-badedit-{uuid.uuid4().hex}"}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "expense record kar do"}]},
        config=config,
        version="v2",
    )
    assert paused.interrupts
    edited = _expense_args({"amount": "0"})  # invalid: zero
    resumed = await agent.ainvoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {
                            "name": "record_expense",
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


@pytest.mark.asyncio
async def test_hitl_duplicate_approve_resume_is_idempotent(
    db_session: AsyncSession,
) -> None:
    """Approving the same operation twice creates exactly one expense."""
    fix = await _make_expense_shop(db_session)
    tool = _expense_tool(db_session, fix)
    key = _key()
    args = {
        "idempotency_key": key,
        "amount": "1000",
        "expense_category": "rent",
        "payment_method": "cash",
    }
    first = await tool.ainvoke(args)
    assert first["status"] == "completed" and first["duplicate"] is False
    # A duplicate approval/resume replay of the same approved args.
    second = await tool.ainvoke(args)
    assert second["status"] == "completed" and second["duplicate"] is True
    assert second["expense_id"] == first["expense_id"]
    expenses = (
        await db_session.execute(
            select(func.count(Expense.id)).where(Expense.shop_id == fix.shop.id)
        )
    ).scalar_one()
    assert expenses == 1


# --- HTTP: mutation stays behind /ai/chat ----------------------------------


def _http_expense_fake() -> BaseChatModel:
    return _expense_fake(
        {
            "idempotency_key": f"httpexp{uuid.uuid4().hex[:25]}",
            "amount": "1000",
            "expense_category": "rent",
            "payment_method": "cash",
        }
    )


@pytest.mark.asyncio
async def test_chat_endpoint_expense_pause_then_approve(
    mocked_api_client: Any, api_session: AsyncSession
) -> None:
    from app.api.ai import get_chat_model
    from app.main import app
    from app.models import Shop as ShopModel
    from app.models import User as UserModel
    from app.models.user import UserRole

    shop = ShopModel(name=f"HTTP Expense Shop {uuid.uuid4().hex[:6]}")
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

    def _expense_count() -> Any:
        return api_session.execute(
            select(func.count(Expense.id)).where(
                Expense.shop_id == shop.id,
            )
        )

    app.dependency_overrides[get_chat_model] = _http_expense_fake
    try:
        thread = uuid.uuid4().hex
        paused = await mocked_api_client.post(
            "/ai/chat",
            json={
                "message": "5000 rent expense record karo",
                "thread_id": thread,
            },
        )
        assert paused.status_code == 200
        body = paused.json()
        assert body["status"] == "paused"
        assert body["interrupts"][0]["name"] == "record_expense"
        assert (await _expense_count()).scalar_one() == 0

        resumed = await mocked_api_client.post(
            "/ai/chat/resume",
            json={"thread_id": thread, "decisions": [{"type": "approve"}]},
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "done"
        assert (await _expense_count()).scalar_one() == 1
    finally:
        app.dependency_overrides.pop(get_chat_model, None)

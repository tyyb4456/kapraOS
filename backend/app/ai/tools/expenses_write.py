"""Single AI write operation: expense recording (Step 6).

Exactly ONE business-level mutation exists here: ``record_expense``.
There are deliberately no ``create_ledger_entry`` / ``post_expense`` /
``update_expense`` / ``delete_expense`` / ``adjust_inventory`` tools —
those are implementation details owned by ``expenses.create_expense()``,
which remains the authoritative business operation. The AI layer only
converts natural language into the structured input that service
requires:

    Shopkeeper -> Master Deep Agent -> resolve category/amount/method
        -> PREPARE (no DB mutation) -> HITL interrupt -> approve
        -> resume -> record_expense executes
        -> expenses.create_expense()
        -> one controlled transaction -> commit/rollback -> result

The architecture is the proven Step 3/Step 4/Step 5 pattern, reused
unchanged:

* Tenant-bound tool: ``shop_id`` is captured from the server-side
  ``TenantContext`` — it never appears in the tool schema, so the model
  cannot choose another shop.
* No live transaction is ever held across the HITL pause. PREPARE does
  zero mutation; the pause happens BEFORE the tool executes (Deep Agents
  ``interrupt_on``); execution happens later, in the resume request, on
  that request's own fresh session. The prepared operation travels through
  the agent checkpoint as plain serialisable args (strings) — never a
  session, connection, or ORM object.
* Idempotency: HITL resume/retry must not double-record. The agent
  supplies one ``idempotency_key`` per prepared expense; the tool checks
  ``ai_expense_receipts(shop_id, operation_key)`` BEFORE mutating and
  writes the receipt in the SAME transaction as the expense. A unique
  constraint is the final guard under concurrency. A dedicated
  ``ai_expense_receipts`` table (not ``ai_sale_receipts``,
  ``ai_payment_receipts`` or ``ai_supplier_payment_receipts``) keeps the
  data model honest.
* Transaction ownership: the mutating section runs inside a SAVEPOINT
  (``session.begin_nested()``), so any failure discards only the partial
  expense and leaves the surrounding session clean; the API route commits
  after a finished run (``app/api/ai.py``). The tool itself NEVER calls
  ``session.commit()`` or a full ``rollback()``.
  ``expenses.create_expense()`` only flushes, so expense + ledger +
  idempotency receipt commit atomically, or all roll back together.

Business rules are NOT reinvented here:

* Categories are the existing ``ExpenseCategory`` enum (rent, salary,
  utilities, transport, marketing, maintenance, supplies, other).
  Natural-language aliases (bijli/electricity, kiraya, delivery, chai,
  ...) map deterministically; unknown categories are rejected and
  multi-category inputs are ambiguous — never silently guessed.
* Amounts use Decimal only (never float) with the same Roman Urdu scale
  words as Step 5 (hazar/hazaar/thousand/k/lakh/crore). Missing, zero,
  negative and malformed amounts are rejected — a missing amount asks
  for clarification instead of triggering HITL.
* Payment methods are exactly ``PaymentMethod`` (cash, card, bank,
  jazzcash, easypaisa, other). Natural-language aliases (naqd/nagad,
  bank transfer, ...) map deterministically; unknown methods are
  rejected. When the shopkeeper names no method, the tool defaults to
  ``cash`` — the same convention as the Step 3/4/5 tools — and the
  method is always shown on the HITL approval card.
* Expenses never touch inventory: stock movement belongs to the
  purchase/sale domains, not the expense path.
"""

from __future__ import annotations

import re
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.state import TenantContext
from app.models.ai_operation import AIExpenseReceipt
from app.models.expense import Expense, ExpenseCategory
from app.models.payment import PaymentMethod
from app.services import expenses as expenses_service

RECORD_EXPENSE_TOOL_NAME = "record_expense"

WRITE_TOOL_NAMES: tuple[str, ...] = (RECORD_EXPENSE_TOOL_NAME,)

_MONEY_SCALE = Decimal("0.01")

# Operation identity: 8-64 chars so a UUID hex (32) fits; strict charset
# so the key is safe to echo in logs and checkpoints.
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Deterministic mapping from shopkeeper language to PaymentMethod.
# The model still does the understanding; this map keeps the tool's
# contract strict (only real PaymentMethod values reach the service).
_METHOD_ALIASES: dict[str, PaymentMethod] = {
    "cash": PaymentMethod.CASH,
    "naqd": PaymentMethod.CASH,
    "naqad": PaymentMethod.CASH,
    "nagad": PaymentMethod.CASH,
    "naghd": PaymentMethod.CASH,
    "nakad": PaymentMethod.CASH,
    "card": PaymentMethod.CARD,
    "bank": PaymentMethod.BANK,
    "bank transfer": PaymentMethod.BANK,
    "banktransfer": PaymentMethod.BANK,
    "jazzcash": PaymentMethod.JAZZCASH,
    "jazz cash": PaymentMethod.JAZZCASH,
    "easypaisa": PaymentMethod.EASYPAISA,
    "easy paisa": PaymentMethod.EASYPAISA,
    "easypaisa transfer": PaymentMethod.EASYPAISA,
    "other": PaymentMethod.OTHER,
}

_DEFAULT_METHOD = PaymentMethod.CASH

# Natural-language amount multipliers (Roman Urdu shopkeeper language).
# "hazar"/"thousand" = 1_000, "lakh"/"lac" = 100_000, crore = 10_000_000.
_AMOUNT_MULTIPLIERS: dict[str, Decimal] = {
    "hazar": Decimal(1000),
    "hazaar": Decimal(1000),
    "thousand": Decimal(1000),
    "thousands": Decimal(1000),
    "k": Decimal(1000),
    "lakh": Decimal(100000),
    "lakhs": Decimal(100000),
    "lac": Decimal(100000),
    "lacs": Decimal(100000),
    "crore": Decimal(10000000),
    "crores": Decimal(10000000),
}

_AMOUNT_RE = re.compile(
    r"^([0-9]*\.?[0-9]+)\s*(hazaar|hazar|thousands|thousand|lakhs|lakh|lacs|lac|crores|crore|k)?$",
    re.IGNORECASE,
)

# Deterministic mapping from shopkeeper language to ExpenseCategory.
# Keys are normalised (lowercase, single spaces). The model still does the
# understanding; this map keeps the tool's contract strict (only real
# ExpenseCategory values reach the service). Generic words such as
# "expense", "kharcha", "bill" alone are deliberately NOT mapped — they
# mean the category is missing, not "other".
_CATEGORY_ALIASES: dict[str, ExpenseCategory] = {
    # Rent
    "rent": ExpenseCategory.RENT,
    "rents": ExpenseCategory.RENT,
    "rental": ExpenseCategory.RENT,
    "rentals": ExpenseCategory.RENT,
    "kiraya": ExpenseCategory.RENT,
    "kiraaya": ExpenseCategory.RENT,
    "kiraay": ExpenseCategory.RENT,
    "house rent": ExpenseCategory.RENT,
    "shop rent": ExpenseCategory.RENT,
    "dukaan kiraya": ExpenseCategory.RENT,
    "makan kiraya": ExpenseCategory.RENT,
    # Salary
    "salary": ExpenseCategory.SALARY,
    "salaries": ExpenseCategory.SALARY,
    "tankhwa": ExpenseCategory.SALARY,
    "tankha": ExpenseCategory.SALARY,
    "tankhawah": ExpenseCategory.SALARY,
    "tankhwah": ExpenseCategory.SALARY,
    "pay": ExpenseCategory.SALARY,
    "wages": ExpenseCategory.SALARY,
    "wage": ExpenseCategory.SALARY,
    "staff salary": ExpenseCategory.SALARY,
    "staff pay": ExpenseCategory.SALARY,
    "payroll": ExpenseCategory.SALARY,
    "salary expense": ExpenseCategory.SALARY,
    # Utilities (bijli / electricity / gas / water bills)
    "utilities": ExpenseCategory.UTILITIES,
    "utility": ExpenseCategory.UTILITIES,
    "utility bill": ExpenseCategory.UTILITIES,
    "bijli": ExpenseCategory.UTILITIES,
    "bijli bill": ExpenseCategory.UTILITIES,
    "bijli ka bill": ExpenseCategory.UTILITIES,
    "electricity": ExpenseCategory.UTILITIES,
    "electricity bill": ExpenseCategory.UTILITIES,
    "electric": ExpenseCategory.UTILITIES,
    "light bill": ExpenseCategory.UTILITIES,
    "light": ExpenseCategory.UTILITIES,
    "gas": ExpenseCategory.UTILITIES,
    "gas bill": ExpenseCategory.UTILITIES,
    "sui gas": ExpenseCategory.UTILITIES,
    "water bill": ExpenseCategory.UTILITIES,
    "wapda": ExpenseCategory.UTILITIES,
    "lesco": ExpenseCategory.UTILITIES,
    # Transport / delivery
    "transport": ExpenseCategory.TRANSPORT,
    "transportation": ExpenseCategory.TRANSPORT,
    "transport expense": ExpenseCategory.TRANSPORT,
    "delivery": ExpenseCategory.TRANSPORT,
    "deliveries": ExpenseCategory.TRANSPORT,
    "delivery expense": ExpenseCategory.TRANSPORT,
    "delivery charges": ExpenseCategory.TRANSPORT,
    "courier": ExpenseCategory.TRANSPORT,
    "freight": ExpenseCategory.TRANSPORT,
    "petrol": ExpenseCategory.TRANSPORT,
    "diesel": ExpenseCategory.TRANSPORT,
    "fuel": ExpenseCategory.TRANSPORT,
    "fare": ExpenseCategory.TRANSPORT,
    "rickshaw": ExpenseCategory.TRANSPORT,
    # Marketing
    "marketing": ExpenseCategory.MARKETING,
    "advertising": ExpenseCategory.MARKETING,
    "advertisement": ExpenseCategory.MARKETING,
    "advertisements": ExpenseCategory.MARKETING,
    "promotion": ExpenseCategory.MARKETING,
    "promotions": ExpenseCategory.MARKETING,
    "publicity": ExpenseCategory.MARKETING,
    # Maintenance
    "maintenance": ExpenseCategory.MAINTENANCE,
    "repair": ExpenseCategory.MAINTENANCE,
    "repairs": ExpenseCategory.MAINTENANCE,
    "marammat": ExpenseCategory.MAINTENANCE,
    "fixing": ExpenseCategory.MAINTENANCE,
    # Supplies
    "supplies": ExpenseCategory.SUPPLIES,
    "supply": ExpenseCategory.SUPPLIES,
    "stationery": ExpenseCategory.SUPPLIES,
    # Other (catch-all for genuinely uncategorised spend such as chai)
    "other": ExpenseCategory.OTHER,
    "others": ExpenseCategory.OTHER,
    "misc": ExpenseCategory.OTHER,
    "miscellaneous": ExpenseCategory.OTHER,
    "chai": ExpenseCategory.OTHER,
    "chai pani": ExpenseCategory.OTHER,
    "refreshment": ExpenseCategory.OTHER,
    "refreshments": ExpenseCategory.OTHER,
    "other expense": ExpenseCategory.OTHER,
}

_MAX_DESCRIPTION_LENGTH = 300


class ExpensePrepError(ValueError):
    """An expense request that cannot proceed — no mutation was performed."""

    code = "invalid_request"


class ExpensePrepNotFoundError(ExpensePrepError):
    """The expense category is unknown (never silently guessed)."""

    code = "not_found"


class ExpensePrepAmbiguousError(ExpensePrepError):
    """Input matches several categories — the agent must ask, never guess."""

    code = "ambiguous"

    def __init__(self, message: str, matches: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.matches = matches


def validate_idempotency_key(raw: Any) -> str:
    """Validate the caller-supplied operation identity (no mutation)."""
    text = str(raw or "").strip()
    if not _KEY_RE.match(text):
        raise ExpensePrepError(
            "Invalid idempotency_key: expected 8-64 characters "
            "[A-Za-z0-9_-] (generate one UUID hex per new expense request)."
        )
    return text


def parse_expense_payment_method(raw: Any) -> PaymentMethod:
    """Map shopkeeper payment language onto the real ``PaymentMethod`` enum.

    ``None``/empty means the shopkeeper named no method: default to cash
    (the Step 3/4/5 convention). The chosen method is always echoed on the
    HITL approval card, so the shopkeeper can correct it via edit.
    Unknown method names are rejected — never invented.
    """
    if raw is None or not str(raw).strip():
        return _DEFAULT_METHOD
    norm = re.sub(r"\s+", " ", str(raw).strip().lower())
    if norm in _METHOD_ALIASES:
        return _METHOD_ALIASES[norm]
    try:
        return PaymentMethod(norm)
    except ValueError:
        valid = ", ".join(sorted(m.value for m in PaymentMethod))
        raise ExpensePrepError(
            f"Unknown payment_method {raw!r}: expected one of {valid}."
        ) from None


# Backwards-compatible alias mirroring the Step 4/Step 5 helper names.
def parse_payment_method(raw: Any) -> PaymentMethod:
    """Alias for :func:`parse_expense_payment_method`."""
    return parse_expense_payment_method(raw)


def parse_expense_amount(raw: Any) -> Decimal:
    """Parse the expense amount: explicit, finite, positive, 2dp money.

    Accepts plain numbers (``5000``, ``5,000``, ``1500.50``) and Roman Urdu
    scale words (``5 hazar``, ``2 lakh``, ``1.5 lakh``). Commas are treated
    as thousand separators. The result is quantized to NUMERIC(14,2).
    Never uses floating-point arithmetic.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ExpensePrepError(
            "Expense amount is missing: ask how much the expense was "
            "(e.g. 'Kitne paise ka expense record karna hai?'). "
            "Never invent an amount."
        )
    text = str(raw).strip()
    # Thousand separators first, so "5,000" and "5,000 hazar" both work.
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text.strip().lower())
    match = _AMOUNT_RE.match(text)
    if match is None:
        raise ExpensePrepError(
            f"Invalid amount {raw!r}: expected a positive number."
        ) from None
    number_part, unit = match.group(1), (match.group(2) or "").lower()
    try:
        number = Decimal(number_part)
    except (InvalidOperation, ValueError, AttributeError):
        raise ExpensePrepError(
            f"Invalid amount {raw!r}: expected a positive number."
        ) from None
    if number.is_nan() or number.is_infinite():
        raise ExpensePrepError(
            f"Invalid amount {raw!r}: expected a positive number."
        )
    if unit:
        multiplier = _AMOUNT_MULTIPLIERS.get(unit)
        if multiplier is None:  # pragma: no cover - regex keeps this exhaustive
            raise ExpensePrepError(
                f"Invalid amount {raw!r}: expected a positive number."
            )
        number = number * multiplier
    amount = number.quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ExpensePrepError("Expense amount must be greater than 0.")
    return amount


# Backwards-compatible alias mirroring the Step 4/Step 5 helper names.
def parse_payment_amount(raw: Any) -> Decimal:
    """Alias for :func:`parse_expense_amount`."""
    return parse_expense_amount(raw)


def _normalise_category_text(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())


def parse_expense_category(raw: Any) -> ExpenseCategory:
    """Map shopkeeper category language onto the real ``ExpenseCategory``.

    Exact enum values (``rent``, ``salary``, ...) and deterministic aliases
    (``bijli``/``electricity`` -> utilities, ``kiraya`` -> rent,
    ``delivery`` -> transport, ``chai`` -> other, ...) resolve directly.
    Generic words (``expense``, ``kharcha``, ``bill`` alone) are treated as
    missing, unknown values are ``not_found``, and inputs matching several
    categories are ``ambiguous`` — never silently guessed.
    """
    if raw is None or not str(raw).strip():
        raise ExpensePrepError(
            "Expense category is missing: ask which expense this was "
            "(e.g. rent, salary, utilities/bijli, transport/delivery, "
            "marketing, maintenance, supplies, other). Never guess."
        )
    norm = _normalise_category_text(raw)
    # Exact alias hit first (so "chai pani" wins over substring parts).
    if norm in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[norm]
    # Direct enum value (covers "rent", "salary", ... exactly).
    try:
        return ExpenseCategory(norm)
    except ValueError:
        pass
    # Substring scan: collect distinct categories whose alias appears in
    # the input. Zero -> not_found; one -> resolve; several -> ambiguous.
    hits: dict[ExpenseCategory, str] = {}
    for alias, category in _CATEGORY_ALIASES.items():
        if alias in norm and category not in hits:
            hits[category] = alias
    if not hits:
        valid = ", ".join(sorted(c.value for c in ExpenseCategory))
        raise ExpensePrepNotFoundError(
            f"Unknown expense category {raw!r}: expected one of {valid}. "
            "Ask which category the shopkeeper means."
        )
    if len(hits) == 1:
        return next(iter(hits))
    matches = [{"category": cat.value} for cat in sorted(hits, key=lambda c: c.value)]
    raise ExpensePrepAmbiguousError(
        f"Multiple expense categories match {raw!r}. Ask which one the "
        "shopkeeper means — never guess.",
        matches=matches,
    )


def parse_description(raw: Any) -> str | None:
    """Validate the optional free-form description (≤300 chars)."""
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    if len(text) > _MAX_DESCRIPTION_LENGTH:
        raise ExpensePrepError(
            f"Expense description must be ≤ {_MAX_DESCRIPTION_LENGTH} characters."
        )
    return text


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _money_str(value: Decimal | int | None) -> str:
    if value is None:
        return "0.00"
    return str(Decimal(value).quantize(_MONEY_SCALE))


def build_expense_preview(
    *,
    category: ExpenseCategory,
    amount: Decimal,
    method: PaymentMethod,
    description: str | None = None,
) -> str:
    """Human-readable confirmation text shown BEFORE the HITL approval.

    The authoritative figures come from ``expenses.create_expense()``
    after approval; this preview only echoes validated inputs.
    """
    if method == PaymentMethod.JAZZCASH:
        method_label = "JazzCash"
    elif method == PaymentMethod.EASYPAISA:
        method_label = "Easypaisa"
    else:
        method_label = method.value.capitalize()
    desc_label = description if description else "—"
    category_value = category.value if hasattr(category, "value") else str(category)
    return "\n".join(
        [
            "Expense",
            "",
            f"Category: {category_value}",
            f"Amount: Rs. {_money_str(amount)}",
            f"Method: {method_label}",
            f"Description: {desc_label}",
            "",
            f"Record Rs. {_money_str(amount)} {category_value} expense?",
        ]
    )


def _prep_error(exc: ExpensePrepError) -> dict[str, Any]:
    """Shape a preparation failure for the agent (never a traceback)."""
    if isinstance(exc, ExpensePrepAmbiguousError):
        return {
            "status": "ambiguous",
            "code": exc.code,
            "message": str(exc),
            "matches": exc.matches,
        }
    if isinstance(exc, ExpensePrepNotFoundError):
        return {"status": "not_found", "code": exc.code, "message": str(exc)}
    return {"status": "error", "code": exc.code, "message": str(exc)}


def _expense_error_code(exc: Exception) -> str:
    if isinstance(exc, expenses_service.ShopNotFoundError):
        return "shop_not_found"
    if isinstance(
        exc,
        (
            expenses_service.InvalidExpenseAmountError,
            expenses_service.InvalidExpenseCategoryError,
            expenses_service.InvalidExpensePaymentMethodError,
        ),
    ):
        return "invalid_expense"
    if isinstance(exc, expenses_service.ExpenseError):
        return "expense_failed"
    return "expense_failed"


def _expense_error_message(exc: Exception) -> str:
    """User-safe failure text: no stack traces, no SQL, no internal IDs."""
    if isinstance(exc, expenses_service.ShopNotFoundError):
        return "Shop not found. No expense recorded."
    if isinstance(exc, expenses_service.ExpenseError):
        return f"Invalid expense: {exc} No expense recorded."
    return "Expense failed and was rolled back. No partial expense was recorded."


async def _find_receipt(
    session: AsyncSession, shop_id: uuid.UUID, key: str
) -> AIExpenseReceipt | None:
    return (
        await session.execute(
            select(AIExpenseReceipt).where(
                AIExpenseReceipt.shop_id == shop_id,
                AIExpenseReceipt.operation_key == key,
            )
        )
    ).scalar_one_or_none()


async def _expense_summary(
    session: AsyncSession,
    shop_id: uuid.UUID,
    expense: Expense,
) -> dict[str, Any]:
    """Authoritative result figures, read — never recomputed by the AI."""
    category_value = (
        expense.category.value
        if hasattr(expense.category, "value")
        else str(expense.category)
    )
    method_value = (
        expense.payment_method.value
        if hasattr(expense.payment_method, "value")
        else str(expense.payment_method)
    )
    return {
        "expense_id": str(expense.id),
        "expense_category": category_value,
        "amount": _money_str(expense.amount),
        "payment_method": method_value.upper(),
        "description": expense.description,
    }


def build_expense_write_tools(
    session: AsyncSession, tenant: TenantContext
) -> list[BaseTool]:
    """Build the tenant-bound expense write tool for one AI request.

    ``shop_id`` is captured from ``tenant`` — it never appears in the
    tool schema, so the model cannot choose another shop. The tool
    flushes but never commits; the caller (``app/api/ai.py``) owns the
    commit, and ``expenses.create_expense()`` owns all business logic.
    Failures roll back to a savepoint, never the whole session.
    """
    shop_id = tenant.shop_id

    @tool
    async def record_expense(
        idempotency_key: str,
        amount: str | None = None,
        expense_category: str | None = None,
        description: str | None = None,
        payment_method: str = "cash",
    ) -> dict:
        """Record a shop expense such as rent, bills, or salaries (HITL-gated).

        Use when the shopkeeper says they spent money on the business, e.g.
        'shop ka 3000 bijli ka bill enter kar do', '5000 rent expense record
        karo', 'Ali ko 1500 delivery ke diye' (delivery expense), '1000 chai
        pani ka kharcha add kar do'. PREPARE first by understanding the
        category/amount/method, show the shopkeeper a short preview, then
        call this tool ONCE per expense.

        Args:
            idempotency_key: REQUIRED stable identity for this expense
                (generate one UUID hex per NEW expense request and reuse it
                for retries of the SAME operation). The same key never
                creates two expenses.
            amount: REQUIRED amount spent, e.g. '3000', '5,000', '5 hazar',
                '2 lakh'. Never invent an amount — if the shopkeeper did not
                state one, ask ('Kitne paise ka expense record karna hai?')
                instead of calling.
            expense_category: REQUIRED category: rent, salary, utilities,
                transport, marketing, maintenance, supplies, other (also
                understands bijli/electricity -> utilities, kiraya -> rent,
                delivery -> transport, chai -> other, tankhwa -> salary).
                Never guess — ambiguous or unknown categories ask for
                clarification.
            description: Optional note about the expense (≤300 chars),
                e.g. 'shop ki bijli ka bill'.
            payment_method: cash, card, bank, jazzcash, easypaisa, other
                (also understands naqd/nagad, bank transfer, jazz cash,
                easy paisa). Defaults to cash; always shown for approval.

        Returns a structured result: completed (with expense_id, amount,
        expense_category), ambiguous/not_found (ask the shopkeeper), or
        error (explain it).
        """
        try:
            key = validate_idempotency_key(idempotency_key)
            total = parse_expense_amount(amount)
            category = parse_expense_category(expense_category)
            method = parse_expense_payment_method(payment_method)
            memo = parse_description(description)
        except ExpensePrepError as exc:
            return _prep_error(exc)

        # Idempotency BEFORE any mutation: a duplicate resume/retry of
        # the same approved operation returns the original expense.
        existing = await _find_receipt(session, shop_id, key)
        if existing is not None:
            expense = None
            if existing.expense_id is not None:
                candidate = await session.get(Expense, existing.expense_id)
                if candidate is not None and candidate.shop_id == shop_id:
                    expense = candidate
            if expense is None:
                return {
                    "status": "error",
                    "code": "already_processed",
                    "message": (
                        "This expense operation was already processed. "
                        "No new expense was created."
                    ),
                }
            result = await _expense_summary(session, shop_id, expense)
            return {
                "status": "completed",
                "duplicate": True,
                "idempotency_key": key,
                **result,
            }

        # ONE controlled transaction for the whole approved operation:
        # expense + ledger + receipt. The savepoint (not a full rollback)
        # discards partial writes on failure while leaving the surrounding
        # session — which the tool does not own — untouched.
        try:
            async with session.begin_nested():
                expense = await expenses_service.create_expense(
                    session,
                    shop_id=shop_id,
                    category=category,
                    amount=total,
                    payment_method=method,
                    description=memo,
                )
                # Claim the operation identity in the SAME transaction as
                # the expense, so receipt + expense commit atomically. A
                # concurrent winner raises here instead of duplicating.
                session.add(
                    AIExpenseReceipt(
                        shop_id=shop_id, operation_key=key, expense_id=expense.id
                    )
                )
                await session.flush()
        except IntegrityError:
            # Most likely a concurrent duplicate won the receipt race:
            # re-check before giving up. The savepoint already discarded
            # this attempt's partial writes.
            retry = await _find_receipt(session, shop_id, key)
            if retry is not None and retry.expense_id is not None:
                winner = await session.get(Expense, retry.expense_id)
                if winner is not None and winner.shop_id == shop_id:
                    summary = await _expense_summary(session, shop_id, winner)
                    return {
                        "status": "completed",
                        "duplicate": True,
                        "idempotency_key": key,
                        **summary,
                    }
            return {
                "status": "error",
                "code": "conflict",
                "message": (
                    "A conflicting record was written concurrently. "
                    "No expense was recorded by this attempt — it is safe "
                    "to retry the same operation."
                ),
            }
        except expenses_service.ExpenseError as exc:
            return {
                "status": "error",
                "code": _expense_error_code(exc),
                "message": _expense_error_message(exc),
            }
        except Exception:  # noqa: BLE001 — boundary maps to clean errors
            return {
                "status": "error",
                "code": "expense_failed",
                "message": (
                    "Expense failed and was rolled back. "
                    "No partial expense was recorded."
                ),
            }

        # Flush only — the API route owns the commit, so the whole
        # approved operation commits or rolls back as one unit.
        await session.flush()
        summary = await _expense_summary(session, shop_id, expense)
        return {
            "status": "completed",
            "duplicate": False,
            "idempotency_key": key,
            **summary,
        }

    return [record_expense]  # type: ignore[list-item]


def get_expense_write_tool_by_name(
    session: AsyncSession, tenant: TenantContext, name: str
) -> BaseTool | None:
    """Return the bound write tool by name (tests/convenience)."""
    for t in build_expense_write_tools(session, tenant):
        if t.name == name:
            return t
    return None


def assert_expense_write_registry_is_minimal(
    tools: list[BaseTool],
) -> None:
    """Raise unless ``tools`` is exactly the single sanctioned mutation.

    Step 6 allows ONE AI mutation in this module (``record_expense``).
    Any second write tool — sales, purchases, payments, inventory, CRUD —
    fails here.
    """
    names = sorted(t.name for t in tools)
    if names != sorted(WRITE_TOOL_NAMES):
        raise AssertionError(
            f"AI expense-write registry must be exactly {sorted(WRITE_TOOL_NAMES)}; "
            f"got {names}. Step 6 allows one mutation only: expense recording."
        )


__all__ = [
    "RECORD_EXPENSE_TOOL_NAME",
    "WRITE_TOOL_NAMES",
    "ExpensePrepAmbiguousError",
    "ExpensePrepError",
    "ExpensePrepNotFoundError",
    "assert_expense_write_registry_is_minimal",
    "build_expense_preview",
    "build_expense_write_tools",
    "get_expense_write_tool_by_name",
    "parse_description",
    "parse_expense_amount",
    "parse_expense_category",
    "parse_expense_payment_method",
    "parse_payment_amount",
    "parse_payment_method",
    "validate_idempotency_key",
]

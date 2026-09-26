"""Single AI write operation: customer payment / Khata settlement (Step 4).

Exactly ONE business-level mutation exists here: ``record_customer_payment``.
There are deliberately no ``create_payment`` / ``update_customer_balance`` /
``post_ar`` / ``post_cash`` / ``create_ledger_entry`` tools — those are
implementation details owned by ``receivables.record_customer_payment()``,
which remains the authoritative business operation. The AI layer only
converts natural language into the structured input that service requires:

    Shopkeeper -> Master Deep Agent -> resolve customer
        -> PREPARE (no DB mutation) -> HITL interrupt -> approve
        -> resume -> record_customer_payment executes
        -> receivables.record_customer_payment()
        -> one controlled transaction -> commit/rollback -> result

The architecture is the proven Step 3 pattern, reused unchanged:

* Tenant-bound tool: ``shop_id`` is captured from the server-side
  ``TenantContext`` — it never appears in the tool schema, so the model
  cannot choose another shop. Names/IDs supplied by the model are
  re-validated against the authenticated shop before any mutation.
* No live transaction is ever held across the HITL pause. PREPARE does
  zero mutation; the pause happens BEFORE the tool executes (Deep Agents
  ``interrupt_on``); execution happens later, in the resume request, on
  that request's own fresh session. The prepared operation travels through
  the agent checkpoint as plain serialisable args (strings) — never a
  session, connection, or ORM object.
* Idempotency: HITL resume/retry must not double-record. The agent
  supplies one ``idempotency_key`` per prepared payment; the tool checks
  ``ai_payment_receipts(shop_id, operation_key)`` BEFORE mutating and
  writes the receipt in the SAME transaction as the payment. A unique
  constraint is the final guard under concurrency. A dedicated
  ``ai_payment_receipts`` table (not ``ai_sale_receipts``) keeps the data
  model honest — that table's ``sale_id`` FK is sale-specific.
* Transaction ownership: the mutating section runs inside a SAVEPOINT
  (``session.begin_nested()``), so any failure discards only the partial
  payment and leaves the surrounding session clean; the API route commits
  after a finished run (``app/api/ai.py``). The tool itself NEVER calls
  ``session.commit()`` or a full ``rollback()``.
  ``receivables.record_customer_payment()`` only flushes, so payment +
  ledger + idempotency receipt commit atomically, or all roll back
  together.

Business rules are NOT reinvented here:

* Overpayment follows the existing service: ``PaymentExceedsOutstandingError``
  is surfaced as a validation error (V1 rejects overpayments).
* The preview balance is informational only. The authoritative service
  re-reads and re-validates the outstanding balance inside its own write
  path (customer row locked ``FOR UPDATE``), so a payment that lands
  between preview and approval cannot corrupt the result.
* The tool always records an *unallocated* Khata payment (``sale_id=None``):
  it reduces the customer's overall outstanding balance without touching
  any individual ``Sale.paid_amount``. Invoice-level allocation is a
  separate feature and is not invented here.
* Payment methods are exactly ``PaymentMethod`` (cash, card, bank,
  jazzcash, easypaisa, other). Natural-language aliases (naqd/nagad,
  bank transfer, ...) map deterministically; unknown methods are rejected.
  When the shopkeeper names no method, the tool defaults to ``cash`` —
  the same convention as the Step 3 sale tool — and the method is always
  shown on the HITL approval card, where the shopkeeper can correct it
  via edit before anything is recorded.
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
from app.models.ai_operation import AIPaymentReceipt
from app.models.customer import Customer
from app.models.payment import Payment, PaymentMethod
from app.services import receivables as receivables_service
from app.services.receivables import ReceivablesError

RECORD_PAYMENT_TOOL_NAME = "record_customer_payment"

WRITE_TOOL_NAMES: tuple[str, ...] = (RECORD_PAYMENT_TOOL_NAME,)

_MAX_MATCHES = 5
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


class PaymentPrepError(ValueError):
    """A payment request that cannot proceed — no mutation was performed."""

    code = "invalid_request"


class PaymentPrepNotFoundError(PaymentPrepError):
    """Named customer is not in this shop (never an invitation to create)."""

    code = "not_found"


class PaymentPrepAmbiguousError(PaymentPrepError):
    """A name matches several rows — the agent must ask, never guess."""

    code = "ambiguous"

    def __init__(self, message: str, matches: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.matches = matches


def validate_idempotency_key(raw: Any) -> str:
    """Validate the caller-supplied operation identity (no mutation)."""
    text = str(raw or "").strip()
    if not _KEY_RE.match(text):
        raise PaymentPrepError(
            "Invalid idempotency_key: expected 8-64 characters "
            "[A-Za-z0-9_-] (generate one UUID hex per new payment request)."
        )
    return text


def parse_payment_method(raw: Any) -> PaymentMethod:
    """Map shopkeeper payment language onto the real ``PaymentMethod`` enum.

    ``None``/empty means the shopkeeper named no method: default to cash
    (the Step 3 sale-tool convention). The chosen method is always echoed
    on the HITL approval card, so the shopkeeper can correct it via edit.
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
        raise PaymentPrepError(
            f"Unknown payment_method {raw!r}: expected one of {valid}."
        ) from None


def parse_payment_amount(raw: Any) -> Decimal:
    """Parse the payment amount: explicit, finite, positive, 2dp money."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise PaymentPrepError(
            "Payment amount is missing: ask how much was paid "
            "(e.g. 'Kitne paise jama karwaye?'). Never invent an amount."
        )
    try:
        amount = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise PaymentPrepError(
            f"Invalid amount {raw!r}: expected a positive number."
        ) from None
    if amount.is_nan() or amount.is_infinite():
        raise PaymentPrepError(
            f"Invalid amount {raw!r}: expected a positive number."
        )
    amount = amount.quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise PaymentPrepError("Payment amount must be greater than 0.")
    return amount


def parse_uuid_arg(raw: Any, field: str) -> uuid.UUID:
    """Parse a model-supplied internal ID (still validated vs the shop)."""
    try:
        return uuid.UUID(str(raw).strip())
    except (ValueError, AttributeError):
        raise PaymentPrepError(f"Invalid {field} {raw!r}.") from None


def parse_reference(raw: Any) -> str | None:
    """Validate the optional free-form reference (≤100 chars)."""
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    if len(text) > 100:
        raise PaymentPrepError("Payment reference must be ≤ 100 characters.")
    return text


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _money_str(value: Decimal | int | None) -> str:
    if value is None:
        return "0.00"
    return str(Decimal(value).quantize(_MONEY_SCALE))


async def resolve_payment_customer(
    session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    customer_id: Any = None,
    customer_name: Any = None,
) -> Customer:
    """Resolve the paying customer within the authenticated shop.

    Unlike sales, a payment ALWAYS needs a customer: walk-in/anonymous
    payments are rejected (the existing payment model has no anonymous
    Khata). A missing customer is never auto-created — it is an error the
    agent must clarify. A model-supplied ID is re-validated against the
    shop; a foreign-shop ID reads as not-found.
    """
    name = _clean(customer_name)
    if customer_id is not None and _clean(customer_id):
        if name:
            raise PaymentPrepError(
                "Provide either customer_id or customer_name, not both."
            )
        cid = parse_uuid_arg(customer_id, "customer_id")
        customer = (
            await session.execute(
                select(Customer).where(
                    Customer.id == cid, Customer.shop_id == shop_id
                )
            )
        ).scalar_one_or_none()
        if customer is None:
            raise PaymentPrepNotFoundError("Customer not found in your shop.")
        return customer
    if not name:
        raise PaymentPrepError(
            "Customer is required for a payment: ask which customer paid "
            "(payments cannot be recorded for a walk-in customer)."
        )
    if len(name) > 150:
        raise PaymentPrepError("Customer name is too long.")
    found = (
        (
            await session.execute(
                select(Customer)
                .where(Customer.shop_id == shop_id, Customer.name.ilike(f"%{name}%"))
                .order_by(Customer.name.asc())
                .limit(_MAX_MATCHES + 1)
            )
        )
        .scalars()
        .all()
    )
    if not found:
        raise PaymentPrepNotFoundError(
            f"Customer {name!r} not found in your shop. "
            "I do not create customers automatically — ask for the "
            "correct name."
        )
    if len(found) > 1:
        raise PaymentPrepAmbiguousError(
            f"Multiple customers match {name!r}. Ask which one the "
            "shopkeeper means — never guess.",
            matches=[{"name": c.name, "phone": c.phone} for c in found[:_MAX_MATCHES]],
        )
    return found[0]


def build_payment_preview(
    *,
    customer_name: str,
    outstanding_before: Decimal,
    amount: Decimal,
    method: PaymentMethod,
) -> str:
    """Human-readable confirmation text shown BEFORE the HITL approval.

    Amounts here come from validated backend reads (the customer's current
    outstanding via the receivables service); the authoritative figures
    come from ``receivables.record_customer_payment()`` after approval.
    The preview balance is informational — the service re-reads and
    re-validates inside its write path.
    """
    remaining = (outstanding_before - amount).quantize(
        _MONEY_SCALE, rounding=ROUND_HALF_UP
    )
    method_label = method.value.capitalize() if method != PaymentMethod.JAZZCASH else "JazzCash"
    if method == PaymentMethod.EASYPAISA:
        method_label = "Easypaisa"
    return "\n".join(
        [
            "Customer Payment",
            "",
            f"Customer: {customer_name}",
            f"Current outstanding: Rs. {_money_str(outstanding_before)}",
            f"Payment: Rs. {_money_str(amount)}",
            f"Method: {method_label}",
            f"Remaining: Rs. {_money_str(remaining)}",
            "",
            "Record this payment?",
        ]
    )


def _prep_error(exc: PaymentPrepError) -> dict[str, Any]:
    """Shape a preparation failure for the agent (never a traceback)."""
    if isinstance(exc, PaymentPrepAmbiguousError):
        return {
            "status": "ambiguous",
            "code": exc.code,
            "message": str(exc),
            "matches": exc.matches,
        }
    if isinstance(exc, PaymentPrepNotFoundError):
        return {"status": "not_found", "code": exc.code, "message": str(exc)}
    return {"status": "error", "code": exc.code, "message": str(exc)}


def _payment_error_code(exc: Exception) -> str:
    if isinstance(exc, receivables_service.CustomerNotFoundError):
        return "customer_not_found"
    if isinstance(
        exc,
        (
            receivables_service.InvalidPaymentAmountError,
            receivables_service.InvalidPaymentMethodError,
        ),
    ):
        return "invalid_payment"
    if isinstance(exc, receivables_service.PaymentExceedsOutstandingError):
        return "payment_exceeds_outstanding"
    if isinstance(
        exc,
        (
            receivables_service.PaymentExceedsSaleDueError,
            receivables_service.SaleNotFoundError,
            receivables_service.SaleCustomerMismatchError,
            receivables_service.SaleNotSettleableError,
        ),
    ):
        return "invalid_payment"
    if isinstance(exc, ReceivablesError):
        return "payment_failed"
    return "payment_failed"


def _payment_error_message(exc: Exception) -> str:
    """User-safe failure text: no stack traces, no SQL, no internal IDs."""
    if isinstance(exc, receivables_service.CustomerNotFoundError):
        return "Customer not found in your shop. No payment recorded."
    if isinstance(exc, receivables_service.PaymentExceedsOutstandingError):
        return f"{exc} No payment recorded."
    if isinstance(exc, ReceivablesError):
        return f"Invalid payment: {exc} No payment recorded."
    return "Payment failed and was rolled back. No partial payment was recorded."


async def _find_receipt(
    session: AsyncSession, shop_id: uuid.UUID, key: str
) -> AIPaymentReceipt | None:
    return (
        await session.execute(
            select(AIPaymentReceipt).where(
                AIPaymentReceipt.shop_id == shop_id,
                AIPaymentReceipt.operation_key == key,
            )
        )
    ).scalar_one_or_none()


async def _payment_summary(
    session: AsyncSession,
    shop_id: uuid.UUID,
    payment: Payment,
) -> dict[str, Any]:
    """Authoritative result figures, read — never recomputed by the AI.

    The remaining balance is read live from the receivables service, so it
    reflects exactly what the customer now owes under the existing
    source-of-truth logic (Sales − Payments).
    """
    customer_name: str | None = None
    if payment.customer_id is not None:
        customer = await session.get(Customer, payment.customer_id)
        if customer is not None:
            customer_name = customer.name
    remaining = "0.00"
    if payment.customer_id is not None:
        try:
            summary = await receivables_service.get_customer_summary(
                session, shop_id=shop_id, customer_id=payment.customer_id
            )
            remaining = _money_str(summary.outstanding_balance)
        except receivables_service.CustomerNotFoundError:
            remaining = "0.00"
    method_value = (
        payment.method.value
        if hasattr(payment.method, "value")
        else str(payment.method)
    )
    return {
        "payment_id": str(payment.id),
        "customer_name": customer_name,
        "amount": _money_str(payment.amount),
        "payment_method": method_value.upper(),
        "remaining_balance": remaining,
    }


def build_payment_write_tools(
    session: AsyncSession, tenant: TenantContext
) -> list[BaseTool]:
    """Build the tenant-bound payment write tool for one AI request.

    ``shop_id`` is captured from ``tenant`` — it never appears in the
    tool schema, so the model cannot choose another shop. The tool
    flushes but never commits; the caller (``app/api/ai.py``) owns the
    commit, and ``receivables.record_customer_payment()`` owns all
    business logic. Failures roll back to a savepoint, never the whole
    session.
    """
    shop_id = tenant.shop_id

    @tool
    async def record_customer_payment(
        idempotency_key: str,
        amount: str | None = None,
        customer_name: str | None = None,
        customer_id: str | None = None,
        payment_method: str = "cash",
        reference: str | None = None,
    ) -> dict:
        """Record money a customer paid against their khata (HITL-gated).

        Use when the shopkeeper says a customer paid, e.g. 'Ali ne 3000
        jama karwaye', 'Ahmed ne 5000 cash diye', 'Bilal ne 2000 khate
        mein jama karwaye'. PREPARE first with the read tools (resolve the
        customer, check their outstanding balance), show the shopkeeper a
        short preview, then call this tool ONCE per payment.

        Args:
            idempotency_key: REQUIRED stable identity for this payment
                (generate one UUID hex per NEW payment request and reuse it
                for retries of the SAME operation). The same key never
                creates two payments.
            amount: REQUIRED amount the customer paid, e.g. '3000'. Never
                invent an amount — if the shopkeeper did not state one,
                ask ('Kitne paise jama karwaye?') instead of calling.
            customer_name: Who paid, e.g. 'Ali'. Must match exactly one
                customer, else ask for clarification. Never invent or
                create a customer. Omit only to use customer_id.
            customer_id: Advanced alternative to customer_name.
            payment_method: cash, card, bank, jazzcash, easypaisa, other
                (also understands naqd/nagad, bank transfer, jazz cash,
                easy paisa). Defaults to cash; always shown for approval.
            reference: Optional free-form reference (≤100 chars).

        Returns a structured result: completed (with payment_id, amount,
        remaining_balance), ambiguous/not_found (ask the shopkeeper), or
        error (explain it).
        """
        try:
            key = validate_idempotency_key(idempotency_key)
            total = parse_payment_amount(amount)
            method = parse_payment_method(payment_method)
            ref = parse_reference(reference)
        except PaymentPrepError as exc:
            return _prep_error(exc)

        # Idempotency BEFORE any mutation: a duplicate resume/retry of
        # the same approved operation returns the original payment.
        existing = await _find_receipt(session, shop_id, key)
        if existing is not None:
            payment = None
            if existing.payment_id is not None:
                candidate = await session.get(Payment, existing.payment_id)
                if candidate is not None and candidate.shop_id == shop_id:
                    payment = candidate
            if payment is None:
                return {
                    "status": "error",
                    "code": "already_processed",
                    "message": (
                        "This payment operation was already processed. "
                        "No new payment was created."
                    ),
                }
            result = await _payment_summary(session, shop_id, payment)
            return {
                "status": "completed",
                "duplicate": True,
                "idempotency_key": key,
                **result,
            }

        try:
            customer = await resolve_payment_customer(
                session,
                shop_id,
                customer_id=customer_id,
                customer_name=customer_name,
            )
        except PaymentPrepError as exc:
            # Validation failed before any write: nothing to undo.
            return _prep_error(exc)

        # ONE controlled transaction for the whole approved operation:
        # payment + ledger + receipt. The savepoint (not a full rollback)
        # discards partial writes on failure while leaving the surrounding
        # session — which the tool does not own — untouched.
        try:
            async with session.begin_nested():
                payment = await receivables_service.record_customer_payment(
                    session,
                    shop_id=shop_id,
                    customer_id=customer.id,
                    amount=total,
                    method=method,
                    reference=ref,
                )
                # Claim the operation identity in the SAME transaction as
                # the payment, so receipt + payment commit atomically. A
                # concurrent winner raises here instead of duplicating.
                session.add(
                    AIPaymentReceipt(
                        shop_id=shop_id, operation_key=key, payment_id=payment.id
                    )
                )
                await session.flush()
        except IntegrityError:
            # Most likely a concurrent duplicate won the receipt race:
            # re-check before giving up. The savepoint already discarded
            # this attempt's partial writes.
            retry = await _find_receipt(session, shop_id, key)
            if retry is not None and retry.payment_id is not None:
                winner = await session.get(Payment, retry.payment_id)
                if winner is not None and winner.shop_id == shop_id:
                    summary = await _payment_summary(session, shop_id, winner)
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
                    "No payment was recorded by this attempt — it is safe "
                    "to retry the same operation."
                ),
            }
        except ReceivablesError as exc:
            return {
                "status": "error",
                "code": _payment_error_code(exc),
                "message": _payment_error_message(exc),
            }
        except Exception:  # noqa: BLE001 — boundary maps to clean errors
            return {
                "status": "error",
                "code": "payment_failed",
                "message": (
                    "Payment failed and was rolled back. "
                    "No partial payment was recorded."
                ),
            }

        # Flush only — the API route owns the commit, so the whole
        # approved operation commits or rolls back as one unit.
        await session.flush()
        summary = await _payment_summary(session, shop_id, payment)
        return {
            "status": "completed",
            "duplicate": False,
            "idempotency_key": key,
            **summary,
        }

    return [record_customer_payment]  # type: ignore[list-item]


def get_payment_write_tool_by_name(
    session: AsyncSession, tenant: TenantContext, name: str
) -> BaseTool | None:
    """Return the bound write tool by name (tests/convenience)."""
    for t in build_payment_write_tools(session, tenant):
        if t.name == name:
            return t
    return None


def assert_payment_write_registry_is_minimal(tools: list[BaseTool]) -> None:
    """Raise unless ``tools`` is exactly the single sanctioned mutation.

    Step 4 allows ONE AI mutation in this module
    (``record_customer_payment``). Any second write tool — sales,
    purchases, expenses, inventory, CRUD — fails here.
    """
    names = sorted(t.name for t in tools)
    if names != sorted(WRITE_TOOL_NAMES):
        raise AssertionError(
            f"AI payment-write registry must be exactly {sorted(WRITE_TOOL_NAMES)}; "
            f"got {names}. Step 4 allows one mutation only: customer payment."
        )


__all__ = [
    "RECORD_PAYMENT_TOOL_NAME",
    "WRITE_TOOL_NAMES",
    "PaymentPrepAmbiguousError",
    "PaymentPrepError",
    "PaymentPrepNotFoundError",
    "assert_payment_write_registry_is_minimal",
    "build_payment_preview",
    "build_payment_write_tools",
    "get_payment_write_tool_by_name",
    "parse_payment_amount",
    "parse_payment_method",
    "parse_reference",
    "parse_uuid_arg",
    "resolve_payment_customer",
    "validate_idempotency_key",
]

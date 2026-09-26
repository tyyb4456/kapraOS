"""Single AI write operation: supplier payment / Khata settlement (Step 5).

Exactly ONE business-level mutation exists here: ``record_supplier_payment``.
There are deliberately no ``create_supplier`` / ``create_purchase`` /
``update_supplier_balance`` / ``post_ap`` / ``post_cash`` /
``create_ledger_entry`` tools — those are implementation details owned by
``payables.record_supplier_payment()``, which remains the authoritative
business operation. The AI layer only converts natural language into the
structured input that service requires:

    Shopkeeper -> Master Deep Agent -> resolve supplier
        -> PREPARE (no DB mutation) -> HITL interrupt -> approve
        -> resume -> record_supplier_payment executes
        -> payables.record_supplier_payment()
        -> one controlled transaction -> commit/rollback -> result

The architecture is the proven Step 3/Step 4 pattern, reused unchanged:

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
  ``ai_supplier_payment_receipts(shop_id, operation_key)`` BEFORE mutating
  and writes the receipt in the SAME transaction as the payment. A unique
  constraint is the final guard under concurrency. A dedicated
  ``ai_supplier_payment_receipts`` table (not ``ai_sale_receipts`` or
  ``ai_payment_receipts``) keeps the data model honest.
* Transaction ownership: the mutating section runs inside a SAVEPOINT
  (``session.begin_nested()``), so any failure discards only the partial
  payment and leaves the surrounding session clean; the API route commits
  after a finished run (``app/api/ai.py``). The tool itself NEVER calls
  ``session.commit()`` or a full ``rollback()``.
  ``payables.record_supplier_payment()`` only flushes, so payment +
  ledger + idempotency receipt commit atomically, or all roll back
  together.

Business rules are NOT reinvented here:

* Overpayment follows the existing service: ``PaymentExceedsOutstandingError``
  is surfaced as a validation error (V1 rejects overpayments; there is no
  supplier advance/credit workflow).
* The preview balance is informational only. The authoritative service
  re-reads and re-validates the outstanding payable inside its own write
  path (supplier row locked ``FOR UPDATE``), so a payment that lands
  between preview and approval cannot corrupt the result.
* The tool always records an *unallocated* Khata payment
  (``purchase_id=None``): it reduces the supplier's overall outstanding
  payable without touching any individual ``Purchase.paid_amount``.
  Invoice-level allocation is a separate feature and is not invented here.
* Payment methods are exactly ``PaymentMethod`` (cash, card, bank,
  jazzcash, easypaisa, other). Natural-language aliases (naqd/nagad,
  bank transfer, ...) map deterministically; unknown methods are rejected.
  When the shopkeeper names no method, the tool defaults to ``cash`` —
  the same convention as the Step 3 sale tool and Step 4 payment tool —
  and the method is always shown on the HITL approval card, where the
  shopkeeper can correct it via edit before anything is recorded.
* Supplier payments never touch inventory: stock movement belongs to the
  purchase domain, not the settlement path.
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
from app.models.ai_operation import AISupplierPaymentReceipt
from app.models.payment import Payment, PaymentMethod
from app.models.supplier import Supplier
from app.services import payables as payables_service
from app.services.payables import PayablesError

RECORD_SUPPLIER_PAYMENT_TOOL_NAME = "record_supplier_payment"

WRITE_TOOL_NAMES: tuple[str, ...] = (RECORD_SUPPLIER_PAYMENT_TOOL_NAME,)

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

# Natural-language amount multipliers (Roman Urdu shopkeeper language).
# "hazar"/"thousand" = 1_000, "lakh"/"lac" = 100_000. Crore is accepted for
# completeness (1 crore = 10_000_000) but is not part of the Step 5 brief.
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


class SupplierPaymentPrepError(ValueError):
    """A supplier payment request that cannot proceed — no mutation was performed."""

    code = "invalid_request"


class SupplierPaymentPrepNotFoundError(SupplierPaymentPrepError):
    """Named supplier is not in this shop (never an invitation to create)."""

    code = "not_found"


class SupplierPaymentPrepAmbiguousError(SupplierPaymentPrepError):
    """A name matches several rows — the agent must ask, never guess."""

    code = "ambiguous"

    def __init__(self, message: str, matches: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.matches = matches


def validate_idempotency_key(raw: Any) -> str:
    """Validate the caller-supplied operation identity (no mutation)."""
    text = str(raw or "").strip()
    if not _KEY_RE.match(text):
        raise SupplierPaymentPrepError(
            "Invalid idempotency_key: expected 8-64 characters "
            "[A-Za-z0-9_-] (generate one UUID hex per new payment request)."
        )
    return text


def parse_supplier_payment_method(raw: Any) -> PaymentMethod:
    """Map shopkeeper payment language onto the real ``PaymentMethod`` enum.

    ``None``/empty means the shopkeeper named no method: default to cash
    (the Step 3 sale-tool and Step 4 payment-tool convention). The chosen
    method is always echoed on the HITL approval card, so the shopkeeper
    can correct it via edit. Unknown method names are rejected — never
    invented.
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
        raise SupplierPaymentPrepError(
            f"Unknown payment_method {raw!r}: expected one of {valid}."
        ) from None


def parse_supplier_payment_amount(raw: Any) -> Decimal:
    """Parse the supplier payment amount: explicit, finite, positive, 2dp money.

    Accepts plain numbers (``5000``, ``5,000``, ``1500.50``) and Roman Urdu
    scale words (``5 hazar``, ``10 hazar``, ``2 lakh``). Commas are treated
    as thousand separators. The result is quantized to NUMERIC(14,2).
    Never uses floating-point arithmetic.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise SupplierPaymentPrepError(
            "Supplier payment amount is missing: ask how much was paid "
            "(e.g. 'Kitne paise diye?'). Never invent an amount."
        )
    text = str(raw).strip()
    # Thousand separators first, so "5,000" and "5,000 hazar" both work.
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text.strip().lower())
    match = _AMOUNT_RE.match(text)
    if match is None:
        raise SupplierPaymentPrepError(
            f"Invalid amount {raw!r}: expected a positive number."
        ) from None
    number_part, unit = match.group(1), (match.group(2) or "").lower()
    try:
        number = Decimal(number_part)
    except (InvalidOperation, ValueError, AttributeError):
        raise SupplierPaymentPrepError(
            f"Invalid amount {raw!r}: expected a positive number."
        ) from None
    if number.is_nan() or number.is_infinite():
        raise SupplierPaymentPrepError(
            f"Invalid amount {raw!r}: expected a positive number."
        )
    if unit:
        multiplier = _AMOUNT_MULTIPLIERS.get(unit)
        if multiplier is None:  # pragma: no cover - regex keeps this exhaustive
            raise SupplierPaymentPrepError(
                f"Invalid amount {raw!r}: expected a positive number."
            )
        number = number * multiplier
    amount = number.quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise SupplierPaymentPrepError("Supplier payment amount must be greater than 0.")
    return amount


# Backwards-compatible alias: Step 4 helpers are named ``parse_payment_*``;
# supplier tests and callers may use either spelling.
def parse_payment_amount(raw: Any) -> Decimal:
    """Alias for :func:`parse_supplier_payment_amount`."""
    return parse_supplier_payment_amount(raw)


def parse_payment_method(raw: Any) -> PaymentMethod:
    """Alias for :func:`parse_supplier_payment_method`."""
    return parse_supplier_payment_method(raw)


def parse_uuid_arg(raw: Any, field: str) -> uuid.UUID:
    """Parse a model-supplied internal ID (still validated vs the shop)."""
    try:
        return uuid.UUID(str(raw).strip())
    except (ValueError, AttributeError):
        raise SupplierPaymentPrepError(f"Invalid {field} {raw!r}.") from None


def parse_reference(raw: Any) -> str | None:
    """Validate the optional free-form reference (≤100 chars)."""
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    if len(text) > 100:
        raise SupplierPaymentPrepError("Payment reference must be ≤ 100 characters.")
    return text


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _money_str(value: Decimal | int | None) -> str:
    if value is None:
        return "0.00"
    return str(Decimal(value).quantize(_MONEY_SCALE))


async def resolve_payment_supplier(
    session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    supplier_id: Any = None,
    supplier_name: Any = None,
) -> Supplier:
    """Resolve the paid supplier within the authenticated shop.

    Unlike walk-in sales, a supplier payment ALWAYS needs a supplier.
    A missing supplier is never auto-created — it is an error the
    agent must clarify. A model-supplied ID is re-validated against the
    shop; a foreign-shop ID reads as not-found (no cross-tenant leak).
    """
    name = _clean(supplier_name)
    if supplier_id is not None and _clean(supplier_id):
        if name:
            raise SupplierPaymentPrepError(
                "Provide either supplier_id or supplier_name, not both."
            )
        sid = parse_uuid_arg(supplier_id, "supplier_id")
        supplier = (
            await session.execute(
                select(Supplier).where(
                    Supplier.id == sid, Supplier.shop_id == shop_id
                )
            )
        ).scalar_one_or_none()
        if supplier is None:
            raise SupplierPaymentPrepNotFoundError("Supplier not found in your shop.")
        return supplier
    if not name:
        raise SupplierPaymentPrepError(
            "Supplier is required for a supplier payment: ask which supplier "
            "was paid. Suppliers are never created automatically."
        )
    if len(name) > 150:
        raise SupplierPaymentPrepError("Supplier name is too long.")
    found = (
        (
            await session.execute(
                select(Supplier)
                .where(Supplier.shop_id == shop_id, Supplier.name.ilike(f"%{name}%"))
                .order_by(Supplier.name.asc())
                .limit(_MAX_MATCHES + 1)
            )
        )
        .scalars()
        .all()
    )
    if not found:
        raise SupplierPaymentPrepNotFoundError(
            f"Supplier {name!r} not found in your shop. "
            "I do not create suppliers automatically — ask for the "
            "correct name."
        )
    if len(found) > 1:
        raise SupplierPaymentPrepAmbiguousError(
            f"Multiple suppliers match {name!r}. Ask which one the "
            "shopkeeper means — never guess.",
            matches=[{"name": s.name, "phone": s.phone} for s in found[:_MAX_MATCHES]],
        )
    return found[0]


def build_supplier_payment_preview(
    *,
    supplier_name: str,
    outstanding_before: Decimal,
    amount: Decimal,
    method: PaymentMethod,
    reference: str | None = None,
) -> str:
    """Human-readable confirmation text shown BEFORE the HITL approval.

    Amounts here come from validated backend reads (the supplier's current
    outstanding payable via the payables service); the authoritative figures
    come from ``payables.record_supplier_payment()`` after approval.
    The preview balance is informational — the service re-reads and
    re-validates inside its write path.
    """
    remaining = (outstanding_before - amount).quantize(
        _MONEY_SCALE, rounding=ROUND_HALF_UP
    )
    if method == PaymentMethod.JAZZCASH:
        method_label = "JazzCash"
    elif method == PaymentMethod.EASYPAISA:
        method_label = "Easypaisa"
    else:
        method_label = method.value.capitalize()
    ref_label = reference if reference else "—"
    return "\n".join(
        [
            "Supplier Payment",
            "",
            f"Supplier: {supplier_name}",
            f"Current payable: Rs. {_money_str(outstanding_before)}",
            f"Payment: Rs. {_money_str(amount)}",
            f"Method: {method_label}",
            f"Reference: {ref_label}",
            f"Remaining: Rs. {_money_str(remaining)}",
            "",
            f"Pay Rs. {_money_str(amount)} toward supplier payable?",
        ]
    )


def _prep_error(exc: SupplierPaymentPrepError) -> dict[str, Any]:
    """Shape a preparation failure for the agent (never a traceback)."""
    if isinstance(exc, SupplierPaymentPrepAmbiguousError):
        return {
            "status": "ambiguous",
            "code": exc.code,
            "message": str(exc),
            "matches": exc.matches,
        }
    if isinstance(exc, SupplierPaymentPrepNotFoundError):
        return {"status": "not_found", "code": exc.code, "message": str(exc)}
    return {"status": "error", "code": exc.code, "message": str(exc)}


def _supplier_payment_error_code(exc: Exception) -> str:
    if isinstance(exc, payables_service.SupplierNotFoundError):
        return "supplier_not_found"
    if isinstance(
        exc,
        (
            payables_service.InvalidPaymentAmountError,
            payables_service.InvalidPaymentMethodError,
        ),
    ):
        return "invalid_payment"
    if isinstance(exc, payables_service.PaymentExceedsOutstandingError):
        return "payment_exceeds_outstanding"
    if isinstance(
        exc,
        (
            payables_service.PaymentExceedsPurchaseDueError,
            payables_service.PurchaseNotFoundError,
            payables_service.PurchaseSupplierMismatchError,
        ),
    ):
        return "invalid_payment"
    if isinstance(exc, PayablesError):
        return "payment_failed"
    return "payment_failed"


def _supplier_payment_error_message(exc: Exception) -> str:
    """User-safe failure text: no stack traces, no SQL, no internal IDs."""
    if isinstance(exc, payables_service.SupplierNotFoundError):
        return "Supplier not found in your shop. No payment recorded."
    if isinstance(exc, payables_service.PaymentExceedsOutstandingError):
        return f"{exc} No payment recorded."
    if isinstance(exc, PayablesError):
        return f"Invalid supplier payment: {exc} No payment recorded."
    return "Supplier payment failed and was rolled back. No partial payment was recorded."


async def _find_receipt(
    session: AsyncSession, shop_id: uuid.UUID, key: str
) -> AISupplierPaymentReceipt | None:
    return (
        await session.execute(
            select(AISupplierPaymentReceipt).where(
                AISupplierPaymentReceipt.shop_id == shop_id,
                AISupplierPaymentReceipt.operation_key == key,
            )
        )
    ).scalar_one_or_none()


async def _supplier_payment_summary(
    session: AsyncSession,
    shop_id: uuid.UUID,
    payment: Payment,
) -> dict[str, Any]:
    """Authoritative result figures, read — never recomputed by the AI.

    The remaining payable is read live from the payables service, so it
    reflects exactly what the shop now owes under the existing
    source-of-truth logic (Purchases − Payments).
    """
    supplier_name: str | None = None
    supplier_id_str: str | None = None
    if payment.supplier_id is not None:
        supplier = await session.get(Supplier, payment.supplier_id)
        if supplier is not None:
            supplier_name = supplier.name
            supplier_id_str = str(supplier.id)
    remaining = "0.00"
    if payment.supplier_id is not None:
        try:
            summary = await payables_service.get_supplier_summary(
                session, shop_id=shop_id, supplier_id=payment.supplier_id
            )
            remaining = _money_str(summary.outstanding_balance)
        except payables_service.SupplierNotFoundError:
            remaining = "0.00"
    method_value = (
        payment.method.value
        if hasattr(payment.method, "value")
        else str(payment.method)
    )
    return {
        "payment_id": str(payment.id),
        "supplier_id": supplier_id_str,
        "supplier_name": supplier_name,
        "amount": _money_str(payment.amount),
        "payment_method": method_value.upper(),
        "remaining_balance": remaining,
    }


def build_supplier_payment_write_tools(
    session: AsyncSession, tenant: TenantContext
) -> list[BaseTool]:
    """Build the tenant-bound supplier payment write tool for one AI request.

    ``shop_id`` is captured from ``tenant`` — it never appears in the
    tool schema, so the model cannot choose another shop. The tool
    flushes but never commits; the caller (``app/api/ai.py``) owns the
    commit, and ``payables.record_supplier_payment()`` owns all
    business logic. Failures roll back to a savepoint, never the whole
    session.
    """
    shop_id = tenant.shop_id

    @tool
    async def record_supplier_payment(
        idempotency_key: str,
        amount: str | None = None,
        supplier_name: str | None = None,
        supplier_id: str | None = None,
        payment_method: str = "cash",
        reference: str | None = None,
    ) -> dict:
        """Record money the shop paid to a supplier against their khata (HITL-gated).

        Use when the shopkeeper says they paid a supplier, e.g. 'Bilal supplier
        ko 5000 de diye', 'Ahmed supplier ko 10 hazar bank transfer kiye',
        'Bilal ko 3000 jazzcash se diye'. PREPARE first with the read tools
        (resolve the supplier, check their outstanding payable), show the
        shopkeeper a short preview, then call this tool ONCE per payment.

        Args:
            idempotency_key: REQUIRED stable identity for this payment
                (generate one UUID hex per NEW payment request and reuse it
                for retries of the SAME operation). The same key never
                creates two payments.
            amount: REQUIRED amount paid to the supplier, e.g. '5000', '5,000',
                '5 hazar', '2 lakh'. Never invent an amount — if the shopkeeper
                did not state one, ask ('Kitne paise diye?') instead of calling.
            supplier_name: Which supplier was paid, e.g. 'Bilal'. Must match
                exactly one supplier, else ask for clarification. Never invent
                or create a supplier. Omit only to use supplier_id.
            supplier_id: Advanced alternative to supplier_name.
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
            total = parse_supplier_payment_amount(amount)
            method = parse_supplier_payment_method(payment_method)
            ref = parse_reference(reference)
        except SupplierPaymentPrepError as exc:
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
                        "This supplier payment operation was already processed. "
                        "No new payment was created."
                    ),
                }
            result = await _supplier_payment_summary(session, shop_id, payment)
            return {
                "status": "completed",
                "duplicate": True,
                "idempotency_key": key,
                **result,
            }

        try:
            supplier = await resolve_payment_supplier(
                session,
                shop_id,
                supplier_id=supplier_id,
                supplier_name=supplier_name,
            )
        except SupplierPaymentPrepError as exc:
            # Validation failed before any write: nothing to undo.
            return _prep_error(exc)

        # ONE controlled transaction for the whole approved operation:
        # payment + ledger + receipt. The savepoint (not a full rollback)
        # discards partial writes on failure while leaving the surrounding
        # session — which the tool does not own — untouched.
        try:
            async with session.begin_nested():
                payment = await payables_service.record_supplier_payment(
                    session,
                    shop_id=shop_id,
                    supplier_id=supplier.id,
                    amount=total,
                    method=method,
                    reference=ref,
                )
                # Claim the operation identity in the SAME transaction as
                # the payment, so receipt + payment commit atomically. A
                # concurrent winner raises here instead of duplicating.
                session.add(
                    AISupplierPaymentReceipt(
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
                    summary = await _supplier_payment_summary(session, shop_id, winner)
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
        except PayablesError as exc:
            return {
                "status": "error",
                "code": _supplier_payment_error_code(exc),
                "message": _supplier_payment_error_message(exc),
            }
        except Exception:  # noqa: BLE001 — boundary maps to clean errors
            return {
                "status": "error",
                "code": "payment_failed",
                "message": (
                    "Supplier payment failed and was rolled back. "
                    "No partial payment was recorded."
                ),
            }

        # Flush only — the API route owns the commit, so the whole
        # approved operation commits or rolls back as one unit.
        await session.flush()
        summary = await _supplier_payment_summary(session, shop_id, payment)
        return {
            "status": "completed",
            "duplicate": False,
            "idempotency_key": key,
            **summary,
        }

    return [record_supplier_payment]  # type: ignore[list-item]


def get_supplier_payment_write_tool_by_name(
    session: AsyncSession, tenant: TenantContext, name: str
) -> BaseTool | None:
    """Return the bound write tool by name (tests/convenience)."""
    for t in build_supplier_payment_write_tools(session, tenant):
        if t.name == name:
            return t
    return None


def assert_supplier_payment_write_registry_is_minimal(
    tools: list[BaseTool],
) -> None:
    """Raise unless ``tools`` is exactly the single sanctioned mutation.

    Step 5 allows ONE AI mutation in this module
    (``record_supplier_payment``). Any second write tool — sales,
    purchases, expenses, inventory, CRUD — fails here.
    """
    names = sorted(t.name for t in tools)
    if names != sorted(WRITE_TOOL_NAMES):
        raise AssertionError(
            f"AI supplier-payment-write registry must be exactly {sorted(WRITE_TOOL_NAMES)}; "
            f"got {names}. Step 5 allows one mutation only: supplier payment."
        )


__all__ = [
    "RECORD_SUPPLIER_PAYMENT_TOOL_NAME",
    "WRITE_TOOL_NAMES",
    "SupplierPaymentPrepAmbiguousError",
    "SupplierPaymentPrepError",
    "SupplierPaymentPrepNotFoundError",
    "assert_supplier_payment_write_registry_is_minimal",
    "build_supplier_payment_preview",
    "build_supplier_payment_write_tools",
    "get_supplier_payment_write_tool_by_name",
    "parse_payment_amount",
    "parse_payment_method",
    "parse_reference",
    "parse_supplier_payment_amount",
    "parse_supplier_payment_method",
    "parse_uuid_arg",
    "resolve_payment_supplier",
    "validate_idempotency_key",
]

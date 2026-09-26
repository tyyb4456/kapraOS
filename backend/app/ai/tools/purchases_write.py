"""Single AI write operation: purchase recording (Step 7).

Exactly ONE business-level mutation exists here: ``create_purchase``.
There are deliberately no ``create_supplier`` / ``create_product`` /
``record_supplier_payment`` / ``post_purchase`` / ``update_purchase`` /
``delete_purchase`` / ``adjust_inventory`` tools — those are
implementation details owned by ``purchases.create_purchase()``, which
remains the authoritative business operation. The AI layer only converts
natural language into the structured input that service requires:

    Shopkeeper -> Master Deep Agent -> resolve supplier + product/variant
        -> PREPARE (no DB mutation) -> HITL interrupt -> approve
        -> resume -> create_purchase executes
        -> purchases.create_purchase()
        -> one controlled transaction -> commit/rollback -> result

The architecture is the proven Step 3/Step 4/Step 5/Step 6 pattern,
reused unchanged:

* Tenant-bound tool: ``shop_id`` is captured from the server-side
  ``TenantContext`` — it never appears in the tool schema, so the model
  cannot choose another shop. Names/IDs supplied by the model are
  re-validated against the authenticated shop before any mutation.
* No live transaction is ever held across the HITL pause. PREPARE does
  zero mutation; the pause happens BEFORE the tool executes (Deep Agents
  ``interrupt_on``); execution happens later, in the resume request, on
  that request's own fresh session. The prepared operation travels through
  the agent checkpoint as plain serialisable args (strings and lists of
  string dicts) — never a session, connection, or ORM object.
* Idempotency: HITL resume/retry must not double-record. The agent
  supplies one ``idempotency_key`` per prepared purchase; the tool checks
  ``ai_purchase_receipts(shop_id, operation_key)`` BEFORE mutating and
  writes the receipt in the SAME transaction as the purchase. A unique
  constraint is the final guard under concurrency. A dedicated
  ``ai_purchase_receipts`` table (not ``ai_sale_receipts``,
  ``ai_payment_receipts``, ``ai_supplier_payment_receipts`` or
  ``ai_expense_receipts``) keeps the data model honest.
* Transaction ownership: the mutating section runs inside a SAVEPOINT
  (``session.begin_nested()``), so any failure discards only the partial
  purchase and leaves the surrounding session clean; the API route commits
  after a finished run (``app/api/ai.py``). The tool itself NEVER calls
  ``session.commit()`` or a full ``rollback()``.
  ``purchases.create_purchase()`` only flushes, so purchase + items +
  stock + ledger + idempotency receipt commit atomically, or all roll
  back together.

Business rules are NOT reinvented here:

* The authoritative service is ``purchases.create_purchase()``: it owns
  supplier/variant validation, server-side totals
  (line total = round(quantity * unit_cost, 2), subtotal = sum lines,
  total = subtotal - discount), inventory increases via
  ``inventory.add_stock()``, and the Debit Inventory / Credit Accounts
  Payable posting via ``accounting.post_purchase()``. The tool never
  computes authoritative totals, never mutates inventory directly, never
  constructs accounting, and never calculates supplier balances.
* Suppliers are resolved tenant-scoped via ILIKE (Step 5 behaviour):
  exactly one match continues, multiple are ambiguous, zero are
  not_found. Suppliers are never created automatically.
* Products/variants resolve against the shop's actual catalog
  (Product ILIKE, exact SKU, or variant ID): exactly one active-or-not
  variant continues, multiple are ambiguous, zero are not_found.
  Products are never created automatically. Quantities use the variant's
  authoritative unit — the tool never converts meter <-> yard.
* Costs use Decimal only (never float) with the same Roman Urdu scale
  words as Steps 5-6 (hazar/hazaar/thousand/k/lakh/crore). Each line
  accepts either ``unit_cost`` ("600 rupay meter", "2500 per suit") or
  ``total_cost`` ("10 suits 30000 mein" -> unit = total / quantity);
  passing both requires consistency, passing neither asks for
  clarification. The backend recomputes the authoritative total.
* Payment: the purchase service tracks NO payment method — only a cached
  ``paid_amount`` (0 = credit/unpaid, total = fully paid, in between =
  partial). The tool therefore exposes ``paid_amount`` only (never a
  ``payment_method`` enum, never journal/account IDs). Omitting it means
  credit. Supplier settlement stays a separate operation
  (``record_supplier_payment`` is never called from here).
* ``invoice_number`` (the service's ``invoice_number``, ≤50 chars) is the
  only free-form reference exposed. Discount is intentionally NOT exposed:
  the tool always passes ``discount=0`` so the AI cannot invent pricing
  adjustments; header discount remains a manual API concern.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.state import TenantContext
from app.models.ai_operation import AIPurchaseReceipt
from app.models.product import Product, ProductVariant
from app.models.purchase import Purchase, PurchaseItem
from app.models.supplier import Supplier
from app.services import purchases as purchases_service
from app.services.purchases import PurchaseError

CREATE_PURCHASE_TOOL_NAME = "create_purchase"

WRITE_TOOL_NAMES: tuple[str, ...] = (CREATE_PURCHASE_TOOL_NAME,)

_MAX_MATCHES = 5
_MAX_ITEMS = 20
_MONEY_SCALE = Decimal("0.01")
_QTY_SCALE = Decimal("0.001")

# Operation identity: 8-64 chars so a UUID hex (32) fits; strict charset
# so the key is safe to echo in logs and checkpoints.
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Natural-language cost multipliers (Roman Urdu shopkeeper language).
# "hazar"/"thousand" = 1_000, "lakh"/"lac" = 100_000, crore = 10_000_000.
_COST_MULTIPLIERS: dict[str, Decimal] = {
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

_COST_RE = re.compile(
    r"^([0-9]*\.?[0-9]+)\s*(hazaar|hazar|thousands|thousand|lakhs|lakh|lacs|lac|crores|crore|k)?$",
    re.IGNORECASE,
)

_QTY_RE = re.compile(
    r"^([0-9]*\.?[0-9]+)\s*(meters?|metres?|yards?|gaz|gazz|pieces?|suits?|rolls?|sets?|thaan|than)?\s*(fabric|lawn)?\s*$",
    re.IGNORECASE,
)

# Words stripped before cost parsing so "600 rupay meter" and
# "2500 per suit" resolve deterministically without inventing conversion.
_CURRENCY_RE = re.compile(r"\b(rupay|rupees?|rs\.?|pkr)\b", re.IGNORECASE)
_PER_UNIT_RE = re.compile(
    r"\bper\s+(suits?|meters?|metres?|yards?|gaz|pieces?|rolls?|sets?|fabric|lawn)\b",
    re.IGNORECASE,
)
_TRAILING_UNIT_RE = re.compile(
    r"\s+(suits?|meters?|metres?|yards?|gaz|pieces?|rolls?|sets?|thaan|fabric|lawn)\s*$",
    re.IGNORECASE,
)
_SLASH_UNIT_RE = re.compile(r"/\s*(suits?|meters?|metres?|yards?|pieces?|rolls?)\s*$", re.IGNORECASE)

# paid_amount keywords: credit-like -> 0, full-like -> total.
_CREDIT_WORDS = frozenset(
    {"credit", "udhaar", "udhar", "unpaid", "khaata", "khata", "baqi", "due", "on-credit", "on credit"}
)
_FULL_WORDS = frozenset(
    {"full", "fully", "paid", "cash", "nagad", "naqd", "naqad", "complete", "full payment", "fully paid"}
)

_MAX_INVOICE_LENGTH = 50


class PurchasePrepError(ValueError):
    """A purchase request that cannot proceed — no mutation was performed."""

    code = "invalid_request"


class PurchasePrepNotFoundError(PurchasePrepError):
    """Named supplier/product/variant is not in this shop."""

    code = "not_found"


class PurchasePrepAmbiguousError(PurchasePrepError):
    """A name matches several rows — the agent must ask, never guess."""

    code = "ambiguous"

    def __init__(self, message: str, matches: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.matches = matches


def validate_idempotency_key(raw: Any) -> str:
    """Validate the caller-supplied operation identity (no mutation)."""
    text = str(raw or "").strip()
    if not _KEY_RE.match(text):
        raise PurchasePrepError(
            "Invalid idempotency_key: expected 8-64 characters "
            "[A-Za-z0-9_-] (generate one UUID hex per new purchase request)."
        )
    return text


def parse_quantity(raw: Any) -> Decimal:
    """Parse a purchase quantity: strictly positive, 3dp fabric scale.

    Accepts plain numbers (``20``, ``3.5``) and trailing unit words
    (``20 meter``, ``10 suits``, ``5 rolls``) which are ignored — the
    variant's authoritative unit governs. Never converts units.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise PurchasePrepError(
            "Purchase quantity is missing: ask how much was bought "
            "(e.g. 'Kitne meter/suit liye?'). Never invent a quantity."
        )
    text = re.sub(r"\s+", " ", str(raw).strip().lower())
    # Thousand separators first so "2,500 meter" works if ever stated.
    text = text.replace(",", "")
    match = _QTY_RE.match(text)
    if match is None:
        raise PurchasePrepError(
            f"Invalid quantity {raw!r}: expected a positive number."
        ) from None
    try:
        qty = Decimal(match.group(1))
    except (InvalidOperation, ValueError, AttributeError):
        raise PurchasePrepError(
            f"Invalid quantity {raw!r}: expected a positive number."
        ) from None
    if qty.is_nan() or qty.is_infinite():
        raise PurchasePrepError(f"Invalid quantity {raw!r}: expected a positive number.")
    qty = qty.quantize(_QTY_SCALE, rounding=ROUND_HALF_UP)
    if qty <= 0:
        raise PurchasePrepError("Purchase quantity must be greater than 0.")
    return qty


def _clean_cost_text(raw: Any) -> str:
    text = str(raw).strip()
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text.strip().lower())
    text = _CURRENCY_RE.sub(" ", text)
    text = _PER_UNIT_RE.sub(" ", text)
    text = _SLASH_UNIT_RE.sub(" ", text)
    text = _TRAILING_UNIT_RE.sub("", text)
    text = re.sub(r"\s+", " ", text.strip().lower())
    # Bare currency shorthands some shopkeepers use ("2500 rs", "600 rps" typo-safe).
    text = re.sub(r"\s+rs\s*$", "", text).strip()
    return text


def _parse_scaled_money(raw: Any, field: str, *, allow_zero: bool) -> Decimal:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise PurchasePrepError(
            f"Purchase {field} is missing: ask what it was "
            "(e.g. 'Per suit/meter kis rate par liya?'). Never invent a cost."
        )
    text = _clean_cost_text(raw)
    match = _COST_RE.match(text)
    if match is None:
        raise PurchasePrepError(
            f"Invalid {field} {raw!r}: expected a non-negative amount."
        ) from None
    number_part, unit = match.group(1), (match.group(2) or "").lower()
    try:
        number = Decimal(number_part)
    except (InvalidOperation, ValueError, AttributeError):
        raise PurchasePrepError(
            f"Invalid {field} {raw!r}: expected a non-negative amount."
        ) from None
    if number.is_nan() or number.is_infinite():
        raise PurchasePrepError(
            f"Invalid {field} {raw!r}: expected a non-negative amount."
        )
    if unit:
        multiplier = _COST_MULTIPLIERS.get(unit)
        if multiplier is None:  # pragma: no cover - regex keeps this exhaustive
            raise PurchasePrepError(
                f"Invalid {field} {raw!r}: expected a non-negative amount."
            )
        number = number * multiplier
    amount = number.quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    if allow_zero:
        if amount < 0:
            raise PurchasePrepError(f"Purchase {field} must be >= 0.")
    elif amount <= 0:
        raise PurchasePrepError(f"Purchase {field} must be greater than 0.")
    return amount


def parse_unit_cost(raw: Any) -> Decimal:
    """Parse a per-unit purchase cost: Decimal 2dp, >= 0, scale words allowed."""
    return _parse_scaled_money(raw, "unit_cost", allow_zero=True)


def parse_total_cost(raw: Any) -> Decimal:
    """Parse a line-total purchase cost: Decimal 2dp, >= 0, scale words allowed."""
    return _parse_scaled_money(raw, "total_cost", allow_zero=True)


def parse_paid_amount(raw: Any) -> Decimal | None:
    """Parse the optional paid amount.

    Missing/empty and credit words (credit/udhaar/unpaid) mean a
    credit purchase and return ``Decimal("0.00")``. Numeric strings (with
    hazar/lakh support) become Decimal 2dp. The keywords "full"/"cash"/
    "paid" mean fully paid and return ``None`` as a sentinel — the caller
    substitutes the expected total (see ``_is_full_paid_keyword``).
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return Decimal("0.00")
    norm = re.sub(r"\s+", " ", str(raw).strip().lower())
    if norm in _CREDIT_WORDS:
        return Decimal("0.00")
    if norm in _FULL_WORDS:
        # Sentinel: caller substitutes the expected total.
        return None
    return _parse_scaled_money(raw, "paid_amount", allow_zero=True)


def _is_full_paid_keyword(raw: Any) -> bool:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return False
    norm = re.sub(r"\s+", " ", str(raw).strip().lower())
    return norm in _FULL_WORDS


def parse_invoice_number(raw: Any) -> str | None:
    """Validate the optional invoice/reference (≤50 chars)."""
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    if len(text) > _MAX_INVOICE_LENGTH:
        raise PurchasePrepError(
            f"Purchase invoice_number must be ≤ {_MAX_INVOICE_LENGTH} characters."
        )
    return text


def parse_uuid_arg(raw: Any, field: str) -> uuid.UUID:
    """Parse a model-supplied internal ID (still validated vs the shop)."""
    try:
        return uuid.UUID(str(raw).strip())
    except (ValueError, AttributeError):
        raise PurchasePrepError(f"Invalid {field} {raw!r}.") from None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _money_str(value: Decimal | int | None) -> str:
    if value is None:
        return "0.00"
    return str(Decimal(value).quantize(_MONEY_SCALE))


def _qty_str(value: Decimal | int | None) -> str:
    if value is None:
        return "0.000"
    return str(Decimal(value).quantize(_QTY_SCALE))


async def resolve_purchase_supplier(
    session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    supplier_id: Any = None,
    supplier_name: Any = None,
) -> Supplier:
    """Resolve the purchase supplier within the authenticated shop.

    A purchase ALWAYS needs a supplier. A missing supplier is never
    auto-created — it is an error the agent must clarify. A
    model-supplied ID is re-validated against the shop; a foreign-shop ID
    reads as not-found (no cross-tenant leak).
    """
    name = _clean(supplier_name)
    if supplier_id is not None and _clean(supplier_id):
        if name:
            raise PurchasePrepError(
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
            raise PurchasePrepNotFoundError("Supplier not found in your shop.")
        return supplier
    if not name:
        raise PurchasePrepError(
            "Supplier is required for a purchase: ask which supplier "
            "the goods came from. Suppliers are never created automatically."
        )
    if len(name) > 150:
        raise PurchasePrepError("Supplier name is too long.")
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
        raise PurchasePrepNotFoundError(
            f"Supplier {name!r} not found in your shop. "
            "I do not create suppliers automatically — ask for the "
            "correct name."
        )
    if len(found) > 1:
        raise PurchasePrepAmbiguousError(
            f"Multiple suppliers match {name!r}. Ask which one the "
            "shopkeeper means — never guess.",
            matches=[{"name": s.name, "phone": s.phone} for s in found[:_MAX_MATCHES]],
        )
    return found[0]


@dataclass(frozen=True)
class ResolvedVariant:
    """A purchasable variant plus its display names (all shop-validated)."""

    variant: ProductVariant
    product_name: str


async def resolve_purchase_variant(
    session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    variant_id: Any = None,
    product_name: Any = None,
    variant_sku: Any = None,
) -> ResolvedVariant:
    """Resolve exactly one variant within the authenticated shop.

    Ambiguous names are an error with ``matches`` for clarification —
    the agent must ask, never silently pick. Unlike sales, inactive
    variants are NOT rejected here: the purchase service itself places no
    ``is_active`` restriction (restocking a deactivated SKU is a catalog
    decision, not an AI business rule), so this boundary only enforces
    existence and tenant ownership.
    """
    name = _clean(product_name)
    sku = _clean(variant_sku)
    if variant_id is not None and _clean(variant_id):
        if name or sku:
            raise PurchasePrepError(
                "Provide either variant_id or product_name/variant_sku, not both."
            )
        vid = parse_uuid_arg(variant_id, "variant_id")
        variant = (
            await session.execute(
                select(ProductVariant)
                .options(selectinload(ProductVariant.product))
                .where(
                    ProductVariant.id == vid,
                    ProductVariant.shop_id == shop_id,
                )
            )
        ).scalar_one_or_none()
        if variant is None:
            raise PurchasePrepNotFoundError("Product variant not found in your shop.")
        return ResolvedVariant(variant=variant, product_name=variant.product.name)
    if not name and not sku:
        raise PurchasePrepError(
            "Tell me which product was bought (product_name, or variant_sku)."
        )
    if sku and not name:
        # SKUs are unique per shop, so an exact SKU resolves directly.
        variant = (
            await session.execute(
                select(ProductVariant)
                .options(selectinload(ProductVariant.product))
                .where(
                    ProductVariant.shop_id == shop_id,
                    ProductVariant.sku == sku,
                )
            )
        ).scalar_one_or_none()
        if variant is None:
            raise PurchasePrepNotFoundError(f"No variant with SKU {sku!r} in your shop.")
        return ResolvedVariant(variant=variant, product_name=variant.product.name)
    if len(name) > 200:
        raise PurchasePrepError("Product name is too long.")
    products = (
        (
            await session.execute(
                select(Product)
                .options(selectinload(Product.variants))
                .where(Product.shop_id == shop_id, Product.name.ilike(f"%{name}%"))
                .order_by(Product.name.asc())
                .limit(_MAX_MATCHES + 1)
            )
        )
        .scalars()
        .all()
    )
    if not products:
        raise PurchasePrepNotFoundError(f"Product {name!r} not found in your shop.")
    if len(products) > 1:
        raise PurchasePrepAmbiguousError(
            f"Multiple products match {name!r}. Ask which one the "
            "shopkeeper means — never guess.",
            matches=[
                {
                    "product_name": p.name,
                    "variant_count": len(p.variants or []),
                }
                for p in products[:_MAX_MATCHES]
            ],
        )
    product = products[0]
    variants = list(product.variants or [])
    if sku:
        hit = next((v for v in variants if v.sku == sku), None)
        if hit is None:
            raise PurchasePrepNotFoundError(
                f"SKU {sku!r} not found for product {product.name!r}."
            )
        return ResolvedVariant(variant=hit, product_name=product.name)
    if not variants:
        raise PurchasePrepError(f"Product {product.name!r} has no variants to buy.")
    if len(variants) > 1:
        raise PurchasePrepAmbiguousError(
            f"Product {product.name!r} has multiple variants. Ask which "
            "one the shopkeeper means — never guess.",
            matches=[
                {
                    "variant_sku": v.sku,
                    "unit": v.unit.value if hasattr(v.unit, "value") else str(v.unit),
                    "purchase_price": _money_str(v.purchase_price),
                }
                for v in variants[:_MAX_MATCHES]
            ],
        )
    return ResolvedVariant(variant=variants[0], product_name=product.name)


def _normalise_items_arg(raw: Any) -> list[dict[str, Any]]:
    """Coerce the tool's ``items`` arg into a list of dicts (no mutation)."""
    if raw is None:
        raise PurchasePrepError(
            "Purchase items are missing: ask what was bought, how much, "
            "and at what rate. Never invent items."
        )
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise PurchasePrepError(
                "Purchase items are missing: ask what was bought, how much, "
                "and at what rate. Never invent items."
            )
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            raise PurchasePrepError(
                "Invalid items: expected a list of purchase lines."
            ) from None
        raw = parsed
    if not isinstance(raw, list) or not raw:
        raise PurchasePrepError(
            "A purchase must contain at least one item: ask what was bought."
        )
    if len(raw) > _MAX_ITEMS:
        raise PurchasePrepError(
            f"A purchase holds at most {_MAX_ITEMS} lines per operation."
        )
    normalised: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise PurchasePrepError(
                "Invalid items: each purchase line must be an object."
            )
        normalised.append(dict(entry))
    return normalised


@dataclass(frozen=True)
class _PreparedLine:
    variant: ProductVariant
    product_name: str
    quantity: Decimal
    unit_cost: Decimal
    total: Decimal


def build_purchase_preview(
    *,
    supplier_name: str,
    lines: list[dict[str, Any]],
    paid_amount: Decimal,
    invoice_number: str | None = None,
) -> str:
    """Human-readable confirmation text shown BEFORE the HITL approval.

    Amounts here are the agent's preview arithmetic
    (``quantity x unit_cost`` per line); the authoritative totals come from
    ``purchases.create_purchase()`` after approval.
    """
    preview_total = sum(
        (Decimal(str(line.get("total", "0"))) for line in lines),
        start=Decimal("0.00"),
    ).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    due = (preview_total - paid_amount).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    parts = [
        "Purchase ready:",
        f"Supplier: {supplier_name}",
    ]
    for line in lines:
        parts.append(
            f"- {line.get('product_name')} ({line.get('variant_sku')}): "
            f"{line.get('quantity')} {line.get('unit')} x "
            f"Rs. {line.get('unit_cost')} = Rs. {line.get('total')}"
        )
    parts.append(f"Total: Rs. {_money_str(preview_total)}")
    if paid_amount > 0:
        parts.append(f"Paid now: Rs. {_money_str(paid_amount)}")
        parts.append(f"Due: Rs. {_money_str(due)}")
    else:
        parts.append(f"Payment: Udhaar — Rs. {_money_str(due)} due")
    if invoice_number:
        parts.append(f"Invoice: {invoice_number}")
    parts.append("Do you want me to record this purchase?")
    return "\n".join(parts)


def _prep_error(exc: PurchasePrepError) -> dict[str, Any]:
    """Shape a preparation failure for the agent (never a traceback)."""
    if isinstance(exc, PurchasePrepAmbiguousError):
        return {
            "status": "ambiguous",
            "code": exc.code,
            "message": str(exc),
            "matches": exc.matches,
        }
    if isinstance(exc, PurchasePrepNotFoundError):
        return {"status": "not_found", "code": exc.code, "message": str(exc)}
    return {"status": "error", "code": exc.code, "message": str(exc)}


def _purchase_error_code(exc: Exception) -> str:
    if isinstance(exc, purchases_service.SupplierNotFoundError):
        return "supplier_not_found"
    if isinstance(exc, purchases_service.VariantNotFoundError):
        return "variant_not_found"
    if isinstance(
        exc,
        (
            purchases_service.EmptyPurchaseError,
            purchases_service.InvalidPurchaseItemError,
            purchases_service.InvalidPurchaseTotalsError,
            purchases_service.DuplicatePurchaseItemError,
        ),
    ):
        return "invalid_purchase"
    if isinstance(exc, PurchaseError):
        return "purchase_failed"
    return "purchase_failed"


def _purchase_error_message(exc: Exception) -> str:
    """User-safe failure text: no stack traces, no SQL, no internal IDs."""
    if isinstance(
        exc,
        (
            purchases_service.SupplierNotFoundError,
            purchases_service.VariantNotFoundError,
        ),
    ):
        return "Supplier or product not found in your shop. No purchase recorded."
    if isinstance(
        exc,
        (
            purchases_service.EmptyPurchaseError,
            purchases_service.InvalidPurchaseItemError,
            purchases_service.InvalidPurchaseTotalsError,
            purchases_service.DuplicatePurchaseItemError,
        ),
    ):
        return f"Invalid purchase: {exc} No purchase recorded."
    return "Purchase failed and was rolled back. No partial purchase was recorded."


async def _find_receipt(
    session: AsyncSession, shop_id: uuid.UUID, key: str
) -> AIPurchaseReceipt | None:
    return (
        await session.execute(
            select(AIPurchaseReceipt).where(
                AIPurchaseReceipt.shop_id == shop_id,
                AIPurchaseReceipt.operation_key == key,
            )
        )
    ).scalar_one_or_none()


async def _purchase_summary(
    session: AsyncSession,
    shop_id: uuid.UUID,
    purchase: Purchase,
) -> dict[str, Any]:
    """Authoritative result figures, read — never recomputed by the AI."""
    items = (
        (await session.execute(select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id)))
        .scalars()
        .all()
    )
    supplier_name: str | None = None
    if purchase.supplier_id is not None:
        supplier = await session.get(Supplier, purchase.supplier_id)
        if supplier is not None and supplier.shop_id == shop_id:
            supplier_name = supplier.name
    lines: list[dict[str, Any]] = []
    for row in items:
        variant_sku: str | None = None
        product_name: str | None = None
        unit: str | None = None
        variant = await session.get(ProductVariant, row.variant_id)
        if variant is not None:
            variant_sku = variant.sku
            unit = variant.unit.value if hasattr(variant.unit, "value") else str(variant.unit)
            product = await session.get(Product, variant.product_id)
            if product is not None:
                product_name = product.name
        lines.append(
            {
                "variant_sku": variant_sku,
                "product_name": product_name,
                "unit": unit,
                "quantity": _qty_str(row.quantity),
                "unit_cost": _money_str(row.unit_cost),
                "total": _money_str(row.total),
            }
        )
    return {
        "purchase_id": str(purchase.id),
        "supplier_id": str(purchase.supplier_id),
        "supplier_name": supplier_name,
        "invoice_number": purchase.invoice_number,
        "items": lines,
        "items_count": len(lines),
        "subtotal": _money_str(purchase.subtotal),
        "discount": _money_str(purchase.discount),
        "total": _money_str(purchase.total),
        "paid_amount": _money_str(purchase.paid_amount),
        "due_amount": _money_str(purchase.due_amount),
    }


def build_purchase_write_tools(
    session: AsyncSession, tenant: TenantContext
) -> list[BaseTool]:
    """Build the tenant-bound purchase write tool for one AI request.

    ``shop_id`` is captured from ``tenant`` — it never appears in the
    tool schema, so the model cannot choose another shop. The tool
    flushes but never commits; the caller (``app/api/ai.py``) owns the
    commit, and ``purchases.create_purchase()`` owns all business logic.
    Failures roll back to a savepoint, never the whole session.
    """
    shop_id = tenant.shop_id

    @tool
    async def create_purchase(
        idempotency_key: str,
        items: list[dict[str, Any]],
        supplier_name: str | None = None,
        supplier_id: str | None = None,
        paid_amount: str | None = None,
        invoice_number: str | None = None,
    ) -> dict:
        """Record goods bought from a supplier, increasing stock (HITL-gated).

        Use when the shopkeeper says stock arrived from a supplier, e.g.
        'Bilal supplier se 20 suit 2500 per suit ke aaye', 'Ahmed se black
        lawn 30 meter 600 rupay meter mein liya', 'Bilal se 10 suits 30000
        mein khareede'. PREPARE first with the read tools (resolve the
        supplier, check the catalog), show the shopkeeper a short preview,
        then call this tool ONCE per purchase.

        Args:
            idempotency_key: REQUIRED stable identity for this purchase
                (generate one UUID hex per NEW purchase request and reuse it
                for retries of the SAME operation). The same key never
                creates two purchases.
            items: REQUIRED non-empty list of purchase lines (1-20). Each
                line is an object with: product_name (e.g. 'Black Lawn')
                or variant_sku or variant_id; quantity (e.g. '20',
                '30 meter', '10 suits' — recorded in the variant's own
                unit, never converted); and EITHER unit_cost (per-unit
                rate, e.g. '2500', '600 rupay meter', '2.5 hazar') OR
                total_cost (line total, e.g. '30000' for '10 suits 30000
                mein' — the tool derives unit_cost = total / quantity).
                Pass exactly one of unit_cost/total_cost per line; passing
                neither asks for clarification, passing both requires
                consistency.
            supplier_name: Which supplier the goods came from, e.g.
                'Bilal'. Must match exactly one supplier, else ask for
                clarification. Never invent or create a supplier. Omit
                only to use supplier_id.
            supplier_id: Advanced alternative to supplier_name.
            paid_amount: Optional amount paid immediately (e.g. '10000',
                '5 hazar'). Omit or pass empty for a credit/unpaid
                purchase (due = total). Pass 'cash'/'full' to mark the
                purchase fully paid. A purchase tracks NO payment method
                (cash vs bank is settled later via supplier payments).
            invoice_number: Optional supplier invoice/reference (<=50
                chars).

        Returns a structured result: completed (with purchase_id,
        authoritative total, paid/due), ambiguous/not_found (ask the
        shopkeeper), or error (explain it).
        """
        try:
            key = validate_idempotency_key(idempotency_key)
            raw_lines = _normalise_items_arg(items)
            invoice = parse_invoice_number(invoice_number)
        except PurchasePrepError as exc:
            return _prep_error(exc)

        # Idempotency BEFORE any mutation: a duplicate resume/retry of
        # the same approved operation returns the original purchase.
        existing = await _find_receipt(session, shop_id, key)
        if existing is not None:
            purchase = None
            if existing.purchase_id is not None:
                candidate = await session.get(Purchase, existing.purchase_id)
                if candidate is not None and candidate.shop_id == shop_id:
                    purchase = candidate
            if purchase is None:
                return {
                    "status": "error",
                    "code": "already_processed",
                    "message": (
                        "This purchase operation was already processed. "
                        "No new purchase was created."
                    ),
                }
            result = await _purchase_summary(session, shop_id, purchase)
            return {
                "status": "completed",
                "duplicate": True,
                "idempotency_key": key,
                **result,
            }

        try:
            supplier = await resolve_purchase_supplier(
                session,
                shop_id,
                supplier_id=supplier_id,
                supplier_name=supplier_name,
            )
        except PurchasePrepError as exc:
            return _prep_error(exc)

        # Resolve every line (supplier-style isolation per variant) and
        # normalise quantity + cost. Any ambiguous/missing line aborts the
        # whole operation before any write — never a partial purchase.
        prepared: list[_PreparedLine] = []
        try:
            for entry in raw_lines:
                if not isinstance(entry, dict):
                    raise PurchasePrepError(
                        "Invalid items: each purchase line must be an object."
                    )
                resolved = await resolve_purchase_variant(
                    session,
                    shop_id,
                    variant_id=entry.get("variant_id"),
                    product_name=entry.get("product_name"),
                    variant_sku=entry.get("variant_sku"),
                )
                qty = parse_quantity(entry.get("quantity"))
                has_unit = entry.get("unit_cost") is not None and _clean(entry.get("unit_cost"))
                has_total = entry.get("total_cost") is not None and _clean(entry.get("total_cost"))
                if not has_unit and not has_total:
                    raise PurchasePrepError(
                        "Purchase cost is missing: ask the rate "
                        "(per suit/meter) or the total. Never invent a cost."
                    )
                if has_unit and has_total:
                    unit = parse_unit_cost(entry.get("unit_cost"))
                    total = parse_total_cost(entry.get("total_cost"))
                    expected = (qty * unit).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
                    if abs(expected - total) > Decimal("0.01"):
                        raise PurchasePrepError(
                            f"unit_cost Rs. {_money_str(unit)} x quantity "
                            f"{_qty_str(qty)} = Rs. {_money_str(expected)}, "
                            f"but total_cost is Rs. {_money_str(total)}. "
                            "Ask whether the rate is per-unit or total."
                        )
                elif has_unit:
                    unit = parse_unit_cost(entry.get("unit_cost"))
                    total = (qty * unit).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
                else:
                    total = parse_total_cost(entry.get("total_cost"))
                    if qty <= 0:  # pragma: no cover - parse_quantity already guards
                        raise PurchasePrepError("Purchase quantity must be greater than 0.")
                    unit = (total / qty).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
                    # Re-derive to keep preview math identical to the service.
                    total = (qty * unit).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
                prepared.append(
                    _PreparedLine(
                        variant=resolved.variant,
                        product_name=resolved.product_name,
                        quantity=qty,
                        unit_cost=unit,
                        total=total,
                    )
                )
        except PurchasePrepError as exc:
            return _prep_error(exc)

        # Paid amount: missing/credit-words -> 0 (credit purchase);
        # "full"/"cash" -> expected total; numeric -> parsed value.
        try:
            expected_total = sum(
                (line.total for line in prepared), start=Decimal("0.00")
            ).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
            if _is_full_paid_keyword(paid_amount):
                paid: Decimal = expected_total
            else:
                parsed_paid = parse_paid_amount(paid_amount)
                paid = parsed_paid if parsed_paid is not None else expected_total
            if paid < 0 or paid > expected_total:
                raise PurchasePrepError(
                    f"paid_amount Rs. {_money_str(paid)} must be between 0 and "
                    f"total Rs. {_money_str(expected_total)}."
                )
        except PurchasePrepError as exc:
            return _prep_error(exc)

        service_items = [
            purchases_service.PurchaseItemInput(
                variant_id=line.variant.id,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
            )
            for line in prepared
        ]

        # ONE controlled transaction for the whole approved operation:
        # purchase + items + stock + ledger + receipt. The savepoint (not
        # a full rollback) discards partial writes on failure while
        # leaving the surrounding session — which the tool does not
        # own — untouched.
        try:
            async with session.begin_nested():
                purchase = await purchases_service.create_purchase(
                    session,
                    shop_id=shop_id,
                    supplier_id=supplier.id,
                    items=service_items,
                    invoice_number=invoice,
                    discount=Decimal(0),
                    paid_amount=paid,
                )
                # Claim the operation identity in the SAME transaction as
                # the purchase, so receipt + purchase commit atomically. A
                # concurrent winner raises here instead of duplicating.
                session.add(
                    AIPurchaseReceipt(
                        shop_id=shop_id, operation_key=key, purchase_id=purchase.id
                    )
                )
                await session.flush()
        except IntegrityError:
            # Most likely a concurrent duplicate won the receipt race or
            # an invoice collision: re-check before giving up. The
            # savepoint already discarded this attempt's partial writes.
            retry = await _find_receipt(session, shop_id, key)
            if retry is not None and retry.purchase_id is not None:
                winner = await session.get(Purchase, retry.purchase_id)
                if winner is not None and winner.shop_id == shop_id:
                    summary = await _purchase_summary(session, shop_id, winner)
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
                    "No purchase was recorded by this attempt — it is safe "
                    "to retry the same operation."
                ),
            }
        except PurchaseError as exc:
            return {
                "status": "error",
                "code": _purchase_error_code(exc),
                "message": _purchase_error_message(exc),
            }
        except Exception:  # noqa: BLE001 — boundary maps to clean errors
            return {
                "status": "error",
                "code": "purchase_failed",
                "message": (
                    "Purchase failed and was rolled back. No partial purchase was recorded."
                ),
            }

        # Flush only — the API route owns the commit, so the whole
        # approved operation commits or rolls back as one unit.
        await session.flush()
        summary = await _purchase_summary(session, shop_id, purchase)
        return {
            "status": "completed",
            "duplicate": False,
            "idempotency_key": key,
            **summary,
        }

    return [create_purchase]  # type: ignore[list-item]


def get_purchase_write_tool_by_name(
    session: AsyncSession, tenant: TenantContext, name: str
) -> BaseTool | None:
    """Return the bound write tool by name (tests/convenience)."""
    for t in build_purchase_write_tools(session, tenant):
        if t.name == name:
            return t
    return None


def assert_purchase_write_registry_is_minimal(
    tools: list[BaseTool],
) -> None:
    """Raise unless ``tools`` is exactly the single sanctioned mutation.

    Step 7 allows ONE AI mutation in this module (``create_purchase``).
    Any second write tool — sales, payments, expenses, inventory, CRUD —
    fails here.
    """
    names = sorted(t.name for t in tools)
    if names != sorted(WRITE_TOOL_NAMES):
        raise AssertionError(
            f"AI purchase-write registry must be exactly {sorted(WRITE_TOOL_NAMES)}; "
            f"got {names}. Step 7 allows one mutation only: purchase recording."
        )


__all__ = [
    "CREATE_PURCHASE_TOOL_NAME",
    "WRITE_TOOL_NAMES",
    "PurchasePrepAmbiguousError",
    "PurchasePrepError",
    "PurchasePrepNotFoundError",
    "ResolvedVariant",
    "assert_purchase_write_registry_is_minimal",
    "build_purchase_preview",
    "build_purchase_write_tools",
    "get_purchase_write_tool_by_name",
    "parse_invoice_number",
    "parse_paid_amount",
    "parse_quantity",
    "parse_total_cost",
    "parse_unit_cost",
    "parse_uuid_arg",
    "resolve_purchase_supplier",
    "resolve_purchase_variant",
    "validate_idempotency_key",
]

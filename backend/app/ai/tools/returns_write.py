"""AI return orchestration: customer & supplier returns (Step 9).

Exactly TWO business-level mutations exist here: ``create_customer_return``
and ``create_supplier_return``. There are deliberately no
``calculate_return_total`` / ``check_returnable_quantity`` / ``adjust_ar`` /
``adjust_ap`` / ``adjust_inventory`` / ``post_return_ledger`` tools — those
are implementation details owned by ``returns.create_sale_return()`` and
``returns.create_purchase_return()``, which remain the authoritative
business operations. The AI layer only converts natural language into the
structured input those services require:

    Shopkeeper -> Master Deep Agent -> resolve sale/purchase + item
        -> PREPARE (no DB mutation) -> HITL interrupt -> approve
        -> resume -> create_*_return executes
        -> returns.create_sale_return() / create_purchase_return()
        -> one controlled transaction -> commit/rollback -> result

The architecture is the proven Step 3/Step 4/Step 5/Step 6/Step 7 pattern,
reused unchanged:

* Tenant-bound tools: ``shop_id`` is captured from the server-side
  ``TenantContext`` — it never appears in either tool schema, so the model
  cannot choose another shop. Names/IDs supplied by the model are
  re-validated against the authenticated shop before any mutation.
* No live transaction is ever held across the HITL pause. PREPARE does
  zero mutation; the pause happens BEFORE the tool executes (Deep Agents
  ``interrupt_on``); execution happens later, in the resume request, on
  that request's own fresh session. The prepared operation travels through
  the agent checkpoint as plain serialisable args (strings and lists of
  string dicts) — never a session, connection, or ORM object.
* Idempotency: HITL resume/retry must not double-record. The agent
  supplies one ``idempotency_key`` per prepared return; each tool checks
  its own receipt table (``ai_customer_return_receipts`` /
  ``ai_supplier_return_receipts`` with ``(shop_id, operation_key)``)
  BEFORE mutating and writes the receipt in the SAME transaction as the
  return. A unique constraint is the final guard under concurrency.
  Dedicated tables (not ``ai_sale_receipts`` / ``ai_purchase_receipts``)
  keep the data model honest, and the core ``sale_returns`` /
  ``purchase_returns`` tables carry no idempotency column (Step 8 is
  unchanged).
* Transaction ownership: the mutating section runs inside a SAVEPOINT
  (``session.begin_nested()``), so any failure discards only the partial
  return and leaves the surrounding session clean; the API route commits
  after a finished run (``app/api/ai.py``). The tools themselves NEVER call
  ``session.commit()`` or a full ``rollback()``.
  ``returns.create_*_return()`` only flushes, so return + items + stock +
  ledger + idempotency receipt commit atomically, or all roll back
  together.

Business rules are NOT reinvented here:

* The authoritative services own eligibility, remaining quantities,
  pricing, totals, inventory movement, receivable/payable impact and
  accounting. The tools never compute ``original - previous`` themselves
  for the mutation, never mutate inventory directly, never construct
  accounting, and never calculate AR/AP splits. Preview amounts (when
  shown) are explicitly estimates; the backend result is authoritative.
* Sales/purchases resolve tenant-scoped via UUID, exact invoice number,
  invoice fragment, or customer/supplier + document information. Exactly
  one match continues, multiple are ambiguous, zero are not_found. The
  tools never create customers, suppliers, sales, purchases, products or
  variants — a missing entity is a clarification, not a new row.
* Sale/purchase items resolve against the identified document's actual
  lines (product name, SKU, variant ID, or direct line ID). Exactly one
  match continues, multiple are ambiguous, zero are not_found. The tools
  never guess and never invent a line.
* Quantities use Decimal only (never float) with the same Roman Urdu
  scale words as Steps 5-7 (hazar/hazaar/thousand/k/lakh/crore) plus
  trailing unit words (meter/m, suits, pieces, rolls, ...) which are
  ignored — the original variant's unit remains authoritative and no
  conversion is ever performed.
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

from app.ai.state import TenantContext
from app.models.ai_operation import (
    AICustomerReturnReceipt,
    AISupplierReturnReceipt,
)
from app.models.customer import Customer
from app.models.product import Product, ProductVariant
from app.models.purchase import Purchase, PurchaseItem
from app.models.returns import PurchaseReturn, SaleReturn
from app.models.sale import Sale, SaleItem
from app.models.supplier import Supplier
from app.services import returns as returns_service
from app.services.inventory import InsufficientStockError

CREATE_CUSTOMER_RETURN_TOOL_NAME = "create_customer_return"
CREATE_SUPPLIER_RETURN_TOOL_NAME = "create_supplier_return"

WRITE_TOOL_NAMES: tuple[str, ...] = (
    CREATE_CUSTOMER_RETURN_TOOL_NAME,
    CREATE_SUPPLIER_RETURN_TOOL_NAME,
)

_MAX_MATCHES = 5
_MAX_ITEMS = 20
_MAX_NOTES_LENGTH = 500
_MONEY_SCALE = Decimal("0.01")
_QTY_SCALE = Decimal("0.001")

# Operation identity: 8-64 chars so a UUID hex (32) fits; strict charset
# so the key is safe to echo in logs and checkpoints.
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Natural-language quantity scale words (Roman Urdu shopkeeper language).
# "hazar"/"thousand" = 1_000, "lakh"/"lac" = 100_000, crore = 10_000_000.
_QTY_MULTIPLIERS: dict[str, Decimal] = {
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

_QTY_RE = re.compile(
    r"^([0-9]*\.?[0-9]+)"
    r"\s*(hazaar|hazar|thousands|thousand|lakhs|lakh|lacs|lac|crores|crore|k)?"
    r"\s*(m|meters?|metres?|yards?|gaz|gazz|pieces?|suits?|rolls?|sets?|thaan|than)?"
    r"\s*(fabric|lawn)?\s*$",
    re.IGNORECASE,
)

# Filler words stripped when inferring a customer/supplier hint from a
# free-form document reference such as "Ali's bill" or "invoice 1023".
_REF_FILLER_WORDS = frozenset(
    {
        "bill",
        "bills",
        "invoice",
        "invoices",
        "inv",
        "sale",
        "sales",
        "purchase",
        "purchases",
        "return",
        "returns",
        "wapas",
        "wapsi",
        "last",
        "latest",
        "recent",
        "ki",
        "ka",
        "ke",
        "ko",
        "se",
        "ne",
        "par",
        "per",
        "mein",
        "me",
        "the",
        "a",
        "an",
        "kar",
        "karo",
        "karna",
        "do",
        "de",
        "hai",
        "hein",
    }
)


class ReturnPrepError(ValueError):
    """A return request that cannot proceed — no mutation was performed."""

    code = "invalid_request"


class ReturnPrepNotFoundError(ReturnPrepError):
    """Named sale/purchase/item is not in this shop."""

    code = "not_found"


class ReturnPrepAmbiguousError(ReturnPrepError):
    """A reference matches several rows — the agent must ask, never guess."""

    code = "ambiguous"

    def __init__(self, message: str, matches: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.matches = matches


def validate_idempotency_key(raw: Any) -> str:
    """Validate the caller-supplied operation identity (no mutation)."""
    text = str(raw or "").strip()
    if not _KEY_RE.match(text):
        raise ReturnPrepError(
            "Invalid idempotency_key: expected 8-64 characters "
            "[A-Za-z0-9_-] (generate one UUID hex per new return request)."
        )
    return text


def parse_quantity(raw: Any) -> Decimal:
    """Parse a return quantity: strictly positive, 3dp fabric scale.

    Accepts plain numbers (``2``, ``2.5``), trailing unit words (``2 meter``,
    ``2m``, ``3 suits``, ``5 pieces``, ``10 rolls``) which are ignored, and
    Pakistani scale words (``5 hazar`` = 5000). The original variant's unit
    remains authoritative — unit words are never conversion instructions.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ReturnPrepError(
            "Return quantity is missing: ask how much is being returned "
            "(e.g. 'Kitna meter/suit wapas hai?'). Never invent a quantity."
        )
    text = str(raw).strip()
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text.strip().lower())
    match = _QTY_RE.match(text)
    if match is None:
        raise ReturnPrepError(
            f"Invalid quantity {raw!r}: expected a positive number."
        ) from None
    number_part, scale_word = match.group(1), (match.group(2) or "").lower()
    try:
        number = Decimal(number_part)
    except (InvalidOperation, ValueError, AttributeError):
        raise ReturnPrepError(
            f"Invalid quantity {raw!r}: expected a positive number."
        ) from None
    if number.is_nan() or number.is_infinite():
        raise ReturnPrepError(
            f"Invalid quantity {raw!r}: expected a positive number."
        )
    if scale_word:
        multiplier = _QTY_MULTIPLIERS.get(scale_word)
        if multiplier is None:  # pragma: no cover - regex keeps this exhaustive
            raise ReturnPrepError(
                f"Invalid quantity {raw!r}: expected a positive number."
            )
        number = number * multiplier
    qty = number.quantize(_QTY_SCALE, rounding=ROUND_HALF_UP)
    if qty <= 0:
        raise ReturnPrepError("Return quantity must be greater than 0.")
    return qty


def parse_uuid_arg(raw: Any, field: str) -> uuid.UUID:
    """Parse a model-supplied internal ID (still validated vs the shop)."""
    try:
        return uuid.UUID(str(raw).strip())
    except (ValueError, AttributeError):
        raise ReturnPrepError(f"Invalid {field} {raw!r}.") from None


def parse_notes(raw: Any) -> str | None:
    """Validate the optional free-form notes (≤500 chars, never accounting)."""
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    if len(text) > _MAX_NOTES_LENGTH:
        raise ReturnPrepError(
            f"Return notes must be ≤ {_MAX_NOTES_LENGTH} characters."
        )
    return text


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


def _normalise_items_arg(raw: Any) -> list[dict[str, Any]]:
    """Coerce the tool's ``items`` arg into a list of dicts (no mutation)."""
    if raw is None:
        raise ReturnPrepError(
            "Return items are missing: ask which item and how much is "
            "being returned. Never invent items."
        )
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ReturnPrepError(
                "Return items are missing: ask which item and how much is "
                "being returned. Never invent items."
            )
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            raise ReturnPrepError(
                "Invalid items: expected a list of return lines."
            ) from None
        raw = parsed
    if not isinstance(raw, list) or not raw:
        raise ReturnPrepError(
            "A return must contain at least one item: ask which item is "
            "being returned."
        )
    if len(raw) > _MAX_ITEMS:
        raise ReturnPrepError(
            f"A return holds at most {_MAX_ITEMS} lines per operation."
        )
    normalised: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ReturnPrepError(
                "Invalid items: each return line must be an object."
            )
        normalised.append(dict(entry))
    return normalised


def _prep_error(exc: ReturnPrepError) -> dict[str, Any]:
    """Shape a preparation failure for the agent (never a traceback)."""
    if isinstance(exc, ReturnPrepAmbiguousError):
        return {
            "status": "ambiguous",
            "code": exc.code,
            "message": str(exc),
            "matches": exc.matches,
        }
    if isinstance(exc, ReturnPrepNotFoundError):
        return {"status": "not_found", "code": exc.code, "message": str(exc)}
    return {"status": "error", "code": exc.code, "message": str(exc)}


# --------------------------------------------------------------------------
# Sale resolution
# --------------------------------------------------------------------------


async def _sale_candidates(
    session: AsyncSession,
    sales: list[Sale],
) -> list[dict[str, Any]]:
    """Build disambiguation summaries for candidate sales (max 5)."""
    out: list[dict[str, Any]] = []
    for sale in sales[:_MAX_MATCHES]:
        customer_name: str | None = None
        if sale.customer_id is not None:
            customer = await session.get(Customer, sale.customer_id)
            if customer is not None and customer.shop_id == sale.shop_id:
                customer_name = customer.name
        items = (
            (
                await session.execute(
                    select(SaleItem).where(SaleItem.sale_id == sale.id)
                )
            )
            .scalars()
            .all()
        )
        parts: list[str] = []
        for row in items[:3]:
            variant = await session.get(ProductVariant, row.variant_id)
            label = str(row.quantity)
            if variant is not None:
                product = await session.get(Product, variant.product_id)
                pname = product.name if product is not None else variant.sku
                label = f"{pname} ({_qty_str(row.quantity)})"
            parts.append(label)
        summary = "; ".join(parts) if parts else f"{len(items)} items"
        out.append(
            {
                "sale_id": str(sale.id),
                "invoice_number": sale.invoice_number,
                "customer_name": customer_name,
                "created_at": sale.created_at.isoformat()
                if sale.created_at is not None
                else None,
                "total": _money_str(sale.total),
                "items_summary": summary,
            }
        )
    return out


async def _sales_for_customer(
    session: AsyncSession,
    shop_id: uuid.UUID,
    customer_id: uuid.UUID,
    *,
    limit: int = 6,
) -> list[Sale]:
    rows = (
        await session.execute(
            select(Sale)
            .where(Sale.shop_id == shop_id, Sale.customer_id == customer_id)
            .order_by(Sale.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


def _extract_name_hint(reference: str) -> list[str]:
    """Extract customer/supplier name tokens from a free-form reference."""
    cleaned = re.sub(r"['\".,;:!?()\[\]{}]", " ", reference.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    tokens = [t for t in cleaned.split(" ") if t and t not in _REF_FILLER_WORDS]
    # Drop pure numbers / invoice fragments (they are handled separately).
    tokens = [t for t in tokens if not re.fullmatch(r"[0-9\-_/]+", t)]
    return [t for t in tokens if len(t) >= 2][:4]


async def resolve_customer_sale(
    session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    sale_id: Any = None,
    sale_reference: Any = None,
    customer_id: Any = None,
    customer_name: Any = None,
) -> Sale:
    """Resolve exactly one tenant-scoped sale for a customer return.

    Accepts a sale UUID, an invoice/reference number (exact or fragment),
    or customer + sale information. Multiple matches are ambiguous, zero
    are not_found. Foreign-tenant sales always read as not_found.
    """
    ref = _clean(sale_reference)
    cname = _clean(customer_name)
    has_sale_id = sale_id is not None and _clean(sale_id)
    has_customer_id = customer_id is not None and _clean(customer_id)

    if has_sale_id:
        if ref or cname or has_customer_id:
            raise ReturnPrepError(
                "Provide either sale_id or sale_reference/customer, not both."
            )
        sid = parse_uuid_arg(sale_id, "sale_id")
        sale = (
            await session.execute(
                select(Sale).where(Sale.id == sid, Sale.shop_id == shop_id)
            )
        ).scalar_one_or_none()
        if sale is None:
            raise ReturnPrepNotFoundError("Sale not found in your shop.")
        return sale

    if has_customer_id and cname:
        raise ReturnPrepError(
            "Provide either customer_id or customer_name, not both."
        )

    # Resolve the customer filter when one is given.
    filter_customer: Customer | None = None
    if has_customer_id:
        cid = parse_uuid_arg(customer_id, "customer_id")
        filter_customer = (
            await session.execute(
                select(Customer).where(
                    Customer.id == cid, Customer.shop_id == shop_id
                )
            )
        ).scalar_one_or_none()
        if filter_customer is None:
            raise ReturnPrepNotFoundError("Customer not found in your shop.")
    elif cname:
        if len(cname) > 150:
            raise ReturnPrepError("Customer name is too long.")
        found = (
            (
                await session.execute(
                    select(Customer)
                    .where(
                        Customer.shop_id == shop_id,
                        Customer.name.ilike(f"%{cname}%"),
                    )
                    .order_by(Customer.name.asc())
                    .limit(_MAX_MATCHES + 1)
                )
            )
            .scalars()
            .all()
        )
        if not found:
            raise ReturnPrepNotFoundError(
                f"Customer {cname!r} not found in your shop. "
                "I do not create customers automatically — ask for the "
                "correct name."
            )
        if len(found) > 1:
            raise ReturnPrepAmbiguousError(
                f"Multiple customers match {cname!r}. Ask which one the "
                "shopkeeper means — never guess.",
                matches=[
                    {"name": c.name, "phone": c.phone}
                    for c in found[:_MAX_MATCHES]
                ],
            )
        filter_customer = found[0]

    if not ref and filter_customer is None:
        raise ReturnPrepError(
            "Sale reference is missing: ask which sale is being returned "
            "(invoice number, bill, or customer name). Never guess."
        )

    if ref:
        # A UUID-shaped reference is a direct sale lookup.
        try:
            maybe_uuid = uuid.UUID(ref)
        except ValueError:
            maybe_uuid = None
        if maybe_uuid is not None:
            sale = (
                await session.execute(
                    select(Sale).where(
                        Sale.id == maybe_uuid, Sale.shop_id == shop_id
                    )
                )
            ).scalar_one_or_none()
            if sale is None:
                raise ReturnPrepNotFoundError("Sale not found in your shop.")
            if filter_customer is not None and sale.customer_id != filter_customer.id:
                raise ReturnPrepNotFoundError("Sale not found in your shop.")
            return sale

        # Exact invoice match first (invoices are unique per shop).
        exact = (
            await session.execute(
                select(Sale).where(
                    Sale.shop_id == shop_id, Sale.invoice_number == ref
                )
            )
        ).scalar_one_or_none()
        if exact is not None:
            if filter_customer is not None and exact.customer_id != filter_customer.id:
                raise ReturnPrepNotFoundError("Sale not found in your shop.")
            return exact

        # Invoice fragment: useful for "invoice 1023" style references.
        like_rows = (
            (
                await session.execute(
                    select(Sale)
                    .where(
                        Sale.shop_id == shop_id,
                        Sale.invoice_number.ilike(f"%{ref}%"),
                    )
                    .order_by(Sale.created_at.desc())
                    .limit(_MAX_MATCHES + 1)
                )
            )
            .scalars()
            .all()
        )
        if like_rows:
            scoped = list(like_rows)
            if filter_customer is not None:
                scoped = [
                    s
                    for s in scoped
                    if s.customer_id == filter_customer.id
                ]
                if not scoped:
                    raise ReturnPrepNotFoundError(
                        "Sale not found in your shop."
                    )
            if len(scoped) == 1:
                return scoped[0]
            raise ReturnPrepAmbiguousError(
                f"Multiple sales match {ref!r}. Ask which bill the "
                "shopkeeper means — never guess.",
                matches=await _sale_candidates(session, scoped),
            )

        # Customer-name inference from a free-form reference such as
        # "Ali's bill" when no explicit customer filter was given.
        if filter_customer is None:
            hints = _extract_name_hint(ref)
            hint_customers: list[Customer] = []
            seen: set[str] = set()
            for hint in hints:
                rows = (
                    (
                        await session.execute(
                            select(Customer)
                            .where(
                                Customer.shop_id == shop_id,
                                Customer.name.ilike(f"%{hint}%"),
                            )
                            .order_by(Customer.name.asc())
                            .limit(_MAX_MATCHES + 1)
                        )
                    )
                    .scalars()
                    .all()
                )
                for c in rows:
                    if str(c.id) not in seen:
                        seen.add(str(c.id))
                        hint_customers.append(c)
            if len(hint_customers) > 1:
                raise ReturnPrepAmbiguousError(
                    f"Multiple customers match {ref!r}. Ask which one the "
                    "shopkeeper means — never guess.",
                    matches=[
                        {"name": c.name, "phone": c.phone}
                        for c in hint_customers[:_MAX_MATCHES]
                    ],
                )
            if len(hint_customers) == 1:
                only_sales = await _sales_for_customer(
                    session, shop_id, hint_customers[0].id
                )
                if not only_sales:
                    raise ReturnPrepNotFoundError(
                        "Sale not found in your shop."
                    )
                if len(only_sales) == 1:
                    return only_sales[0]
                raise ReturnPrepAmbiguousError(
                    f"Multiple sales found for {hint_customers[0].name!r}. "
                    "Ask which bill the shopkeeper means — never guess.",
                    matches=await _sale_candidates(session, only_sales),
                )
        elif filter_customer is not None:
            # A customer filter plus an unmatched reference: narrow that
            # customer's sales by the reference before giving up.
            owned = await _sales_for_customer(
                session, shop_id, filter_customer.id
            )
            narrowed = [
                s
                for s in owned
                if s.invoice_number is not None
                and ref.lower() in s.invoice_number.lower()
            ]
            if len(narrowed) == 1:
                return narrowed[0]
            if len(narrowed) > 1:
                raise ReturnPrepAmbiguousError(
                    f"Multiple sales match {ref!r}. Ask which bill the "
                    "shopkeeper means — never guess.",
                    matches=await _sale_candidates(session, narrowed),
                )
        raise ReturnPrepNotFoundError("Sale not found in your shop.")

    # No reference, but a customer filter is present: the customer's sales.
    assert filter_customer is not None
    owned = await _sales_for_customer(session, shop_id, filter_customer.id)
    if not owned:
        raise ReturnPrepNotFoundError(
            f"No sales found for {filter_customer.name!r} in your shop."
        )
    if len(owned) == 1:
        return owned[0]
    raise ReturnPrepAmbiguousError(
        f"Multiple sales found for {filter_customer.name!r}. Ask which "
        "bill the shopkeeper means — never guess.",
        matches=await _sale_candidates(session, owned),
    )


async def _sale_item_display(
    session: AsyncSession,
    item: SaleItem,
) -> dict[str, Any]:
    variant = await session.get(ProductVariant, item.variant_id)
    product_name: str | None = None
    sku: str | None = None
    if variant is not None:
        sku = variant.sku
        product = await session.get(Product, variant.product_id)
        if product is not None:
            product_name = product.name
    return {
        "sale_item_id": str(item.id),
        "product_name": product_name,
        "variant_sku": sku,
        "quantity": _qty_str(item.quantity),
        "unit_price": _money_str(item.unit_price),
    }


async def resolve_sale_item(
    session: AsyncSession,
    shop_id: uuid.UUID,
    sale: Sale,
    entry: dict[str, Any],
) -> SaleItem:
    """Resolve exactly one line of ``sale`` from a return-line request.

    Supports a direct ``sale_item_id``, a ``variant_id`` / ``variant_sku``,
    or a name reference (``product_name`` / ``sale_item_reference``).
    Multiple matches are ambiguous, zero are not_found.
    """
    sale_items = (
        (await session.execute(select(SaleItem).where(SaleItem.sale_id == sale.id)))
        .scalars()
        .all()
    )
    if not sale_items:
        raise ReturnPrepNotFoundError("Sale has no items to return.")

    direct = entry.get("sale_item_id")
    if direct is not None and _clean(direct):
        others = [
            entry.get("product_name"),
            entry.get("variant_sku"),
            entry.get("variant_id"),
            entry.get("sale_item_reference"),
        ]
        if any(o is not None and _clean(o) for o in others):
            raise ReturnPrepError(
                "Provide either sale_item_id or a product reference, not both."
            )
        iid = parse_uuid_arg(direct, "sale_item_id")
        hit = next((i for i in sale_items if i.id == iid), None)
        if hit is None:
            raise ReturnPrepNotFoundError(
                "Sale item not found on this sale in your shop."
            )
        return hit

    vid_raw = entry.get("variant_id")
    sku_raw = _clean(entry.get("variant_sku"))
    name_raw = _clean(entry.get("product_name"))
    ref_raw = _clean(entry.get("sale_item_reference"))
    if not vid_raw and not sku_raw and not name_raw and not ref_raw:
        raise ReturnPrepError(
            "Sale item reference is missing: ask which item is being "
            "returned (e.g. 'Kaunsa kapra wapas hai?'). Never guess."
        )

    if vid_raw is not None and _clean(vid_raw):
        if name_raw or sku_raw or ref_raw:
            raise ReturnPrepError(
                "Provide either variant_id or a product reference, not both."
            )
        vid = parse_uuid_arg(vid_raw, "variant_id")
        hits = [i for i in sale_items if i.variant_id == vid]
        if not hits:
            raise ReturnPrepNotFoundError(
                "Sale item not found on this sale in your shop."
            )
        if len(hits) > 1:
            raise ReturnPrepAmbiguousError(
                "Multiple sale lines match. Ask which one the shopkeeper "
                "means — never guess.",
                matches=[
                    await _sale_item_display(session, h) for h in hits[:_MAX_MATCHES]
                ],
            )
        return hits[0]

    # SKU-first when it is the only reference: SKUs are unique per shop.
    if sku_raw and not name_raw and not ref_raw:
        variant = (
            await session.execute(
                select(ProductVariant).where(
                    ProductVariant.shop_id == shop_id,
                    ProductVariant.sku == sku_raw,
                )
            )
        ).scalar_one_or_none()
        if variant is None:
            raise ReturnPrepNotFoundError(
                f"No variant with SKU {sku_raw!r} in your shop."
            )
        hits = [i for i in sale_items if i.variant_id == variant.id]
        if not hits:
            raise ReturnPrepNotFoundError(
                "Sale item not found on this sale in your shop."
            )
        return hits[0]

    needle = (name_raw or ref_raw or sku_raw).strip()
    if len(needle) > 200:
        raise ReturnPrepError("Sale item reference is too long.")
    lowered = needle.lower()
    scored: list[SaleItem] = []
    displays: dict[str, dict[str, Any]] = {}
    for item in sale_items:
        variant = await session.get(ProductVariant, item.variant_id)
        pname = ""
        sku = ""
        if variant is not None:
            sku = variant.sku or ""
            product = await session.get(Product, variant.product_id)
            if product is not None:
                pname = product.name or ""
        if (
            lowered in pname.lower()
            or lowered in sku.lower()
            or (pname and pname.lower() in lowered)
        ):
            scored.append(item)
            displays[str(item.id)] = {
                "sale_item_id": str(item.id),
                "product_name": pname or None,
                "variant_sku": sku or None,
                "quantity": _qty_str(item.quantity),
                "unit_price": _money_str(item.unit_price),
            }
    if not scored:
        raise ReturnPrepNotFoundError(
            f"Item {needle!r} not found on this sale. "
            "Ask which item the shopkeeper means."
        )
    if len(scored) > 1:
        raise ReturnPrepAmbiguousError(
            f"Multiple items match {needle!r}. Ask which one the "
            "shopkeeper means — never guess.",
            matches=[displays[str(i.id)] for i in scored[:_MAX_MATCHES]],
        )
    return scored[0]


# --------------------------------------------------------------------------
# Purchase resolution
# --------------------------------------------------------------------------


async def _purchase_candidates(
    session: AsyncSession,
    purchases: list[Purchase],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for purchase in purchases[:_MAX_MATCHES]:
        supplier_name: str | None = None
        supplier = await session.get(Supplier, purchase.supplier_id)
        if supplier is not None and supplier.shop_id == purchase.shop_id:
            supplier_name = supplier.name
        items = (
            (
                await session.execute(
                    select(PurchaseItem).where(
                        PurchaseItem.purchase_id == purchase.id
                    )
                )
            )
            .scalars()
            .all()
        )
        parts: list[str] = []
        for row in items[:3]:
            variant = await session.get(ProductVariant, row.variant_id)
            label = str(row.quantity)
            if variant is not None:
                product = await session.get(Product, variant.product_id)
                pname = product.name if product is not None else variant.sku
                label = f"{pname} ({_qty_str(row.quantity)})"
            parts.append(label)
        summary = "; ".join(parts) if parts else f"{len(items)} items"
        out.append(
            {
                "purchase_id": str(purchase.id),
                "invoice_number": purchase.invoice_number,
                "supplier_name": supplier_name,
                "created_at": purchase.created_at.isoformat()
                if purchase.created_at is not None
                else None,
                "total": _money_str(purchase.total),
                "items_summary": summary,
            }
        )
    return out


async def _purchases_for_supplier(
    session: AsyncSession,
    shop_id: uuid.UUID,
    supplier_id: uuid.UUID,
    *,
    limit: int = 6,
) -> list[Purchase]:
    rows = (
        await session.execute(
            select(Purchase)
            .where(
                Purchase.shop_id == shop_id,
                Purchase.supplier_id == supplier_id,
            )
            .order_by(Purchase.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def resolve_supplier_purchase(
    session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    purchase_id: Any = None,
    purchase_reference: Any = None,
    supplier_id: Any = None,
    supplier_name: Any = None,
) -> Purchase:
    """Resolve exactly one tenant-scoped purchase for a supplier return.

    Accepts a purchase UUID, an invoice/reference number (exact or
    fragment), or supplier + purchase information. Multiple matches are
    ambiguous, zero are not_found. Foreign-tenant purchases always read
    as not_found.
    """
    ref = _clean(purchase_reference)
    sname = _clean(supplier_name)
    has_purchase_id = purchase_id is not None and _clean(purchase_id)
    has_supplier_id = supplier_id is not None and _clean(supplier_id)

    if has_purchase_id:
        if ref or sname or has_supplier_id:
            raise ReturnPrepError(
                "Provide either purchase_id or purchase_reference/supplier, "
                "not both."
            )
        pid = parse_uuid_arg(purchase_id, "purchase_id")
        purchase = (
            await session.execute(
                select(Purchase).where(
                    Purchase.id == pid, Purchase.shop_id == shop_id
                )
            )
        ).scalar_one_or_none()
        if purchase is None:
            raise ReturnPrepNotFoundError("Purchase not found in your shop.")
        return purchase

    if has_supplier_id and sname:
        raise ReturnPrepError(
            "Provide either supplier_id or supplier_name, not both."
        )

    filter_supplier: Supplier | None = None
    if has_supplier_id:
        sid = parse_uuid_arg(supplier_id, "supplier_id")
        filter_supplier = (
            await session.execute(
                select(Supplier).where(
                    Supplier.id == sid, Supplier.shop_id == shop_id
                )
            )
        ).scalar_one_or_none()
        if filter_supplier is None:
            raise ReturnPrepNotFoundError("Supplier not found in your shop.")
    elif sname:
        if len(sname) > 150:
            raise ReturnPrepError("Supplier name is too long.")
        found = (
            (
                await session.execute(
                    select(Supplier)
                    .where(
                        Supplier.shop_id == shop_id,
                        Supplier.name.ilike(f"%{sname}%"),
                    )
                    .order_by(Supplier.name.asc())
                    .limit(_MAX_MATCHES + 1)
                )
            )
            .scalars()
            .all()
        )
        if not found:
            raise ReturnPrepNotFoundError(
                f"Supplier {sname!r} not found in your shop. "
                "I do not create suppliers automatically — ask for the "
                "correct name."
            )
        if len(found) > 1:
            raise ReturnPrepAmbiguousError(
                f"Multiple suppliers match {sname!r}. Ask which one the "
                "shopkeeper means — never guess.",
                matches=[
                    {"name": s.name, "phone": s.phone}
                    for s in found[:_MAX_MATCHES]
                ],
            )
        filter_supplier = found[0]

    if not ref and filter_supplier is None:
        raise ReturnPrepError(
            "Purchase reference is missing: ask which purchase is being "
            "returned (invoice number or supplier name). Never guess."
        )

    if ref:
        try:
            maybe_uuid = uuid.UUID(ref)
        except ValueError:
            maybe_uuid = None
        if maybe_uuid is not None:
            purchase = (
                await session.execute(
                    select(Purchase).where(
                        Purchase.id == maybe_uuid,
                        Purchase.shop_id == shop_id,
                    )
                )
            ).scalar_one_or_none()
            if purchase is None:
                raise ReturnPrepNotFoundError("Purchase not found in your shop.")
            if (
                filter_supplier is not None
                and purchase.supplier_id != filter_supplier.id
            ):
                raise ReturnPrepNotFoundError("Purchase not found in your shop.")
            return purchase

        exact = (
            await session.execute(
                select(Purchase).where(
                    Purchase.shop_id == shop_id,
                    Purchase.invoice_number == ref,
                )
            )
        ).scalar_one_or_none()
        if exact is not None:
            if (
                filter_supplier is not None
                and exact.supplier_id != filter_supplier.id
            ):
                raise ReturnPrepNotFoundError("Purchase not found in your shop.")
            return exact

        like_rows = (
            (
                await session.execute(
                    select(Purchase)
                    .where(
                        Purchase.shop_id == shop_id,
                        Purchase.invoice_number.ilike(f"%{ref}%"),
                    )
                    .order_by(Purchase.created_at.desc())
                    .limit(_MAX_MATCHES + 1)
                )
            )
            .scalars()
            .all()
        )
        if like_rows:
            scoped = list(like_rows)
            if filter_supplier is not None:
                scoped = [
                    p
                    for p in scoped
                    if p.supplier_id == filter_supplier.id
                ]
                if not scoped:
                    raise ReturnPrepNotFoundError(
                        "Purchase not found in your shop."
                    )
            if len(scoped) == 1:
                return scoped[0]
            raise ReturnPrepAmbiguousError(
                f"Multiple purchases match {ref!r}. Ask which purchase the "
                "shopkeeper means — never guess.",
                matches=await _purchase_candidates(session, scoped),
            )

        if filter_supplier is None:
            hints = _extract_name_hint(ref)
            hint_suppliers: list[Supplier] = []
            seen: set[str] = set()
            for hint in hints:
                rows = (
                    (
                        await session.execute(
                            select(Supplier)
                            .where(
                                Supplier.shop_id == shop_id,
                                Supplier.name.ilike(f"%{hint}%"),
                            )
                            .order_by(Supplier.name.asc())
                            .limit(_MAX_MATCHES + 1)
                        )
                    )
                    .scalars()
                    .all()
                )
                for s in rows:
                    if str(s.id) not in seen:
                        seen.add(str(s.id))
                        hint_suppliers.append(s)
            if len(hint_suppliers) > 1:
                raise ReturnPrepAmbiguousError(
                    f"Multiple suppliers match {ref!r}. Ask which one the "
                    "shopkeeper means — never guess.",
                    matches=[
                        {"name": s.name, "phone": s.phone}
                        for s in hint_suppliers[:_MAX_MATCHES]
                    ],
                )
            if len(hint_suppliers) == 1:
                only = await _purchases_for_supplier(
                    session, shop_id, hint_suppliers[0].id
                )
                if not only:
                    raise ReturnPrepNotFoundError(
                        "Purchase not found in your shop."
                    )
                if len(only) == 1:
                    return only[0]
                raise ReturnPrepAmbiguousError(
                    f"Multiple purchases found for {hint_suppliers[0].name!r}. "
                    "Ask which purchase the shopkeeper means — never guess.",
                    matches=await _purchase_candidates(session, only),
                )
        elif filter_supplier is not None:
            owned = await _purchases_for_supplier(
                session, shop_id, filter_supplier.id
            )
            narrowed = [
                p
                for p in owned
                if p.invoice_number is not None
                and ref.lower() in p.invoice_number.lower()
            ]
            if len(narrowed) == 1:
                return narrowed[0]
            if len(narrowed) > 1:
                raise ReturnPrepAmbiguousError(
                    f"Multiple purchases match {ref!r}. Ask which purchase "
                    "the shopkeeper means — never guess.",
                    matches=await _purchase_candidates(session, narrowed),
                )
        raise ReturnPrepNotFoundError("Purchase not found in your shop.")

    assert filter_supplier is not None
    owned = await _purchases_for_supplier(session, shop_id, filter_supplier.id)
    if not owned:
        raise ReturnPrepNotFoundError(
            f"No purchases found for {filter_supplier.name!r} in your shop."
        )
    if len(owned) == 1:
        return owned[0]
    raise ReturnPrepAmbiguousError(
        f"Multiple purchases found for {filter_supplier.name!r}. Ask which "
        "purchase the shopkeeper means — never guess.",
        matches=await _purchase_candidates(session, owned),
    )


async def _purchase_item_display(
    session: AsyncSession,
    item: PurchaseItem,
) -> dict[str, Any]:
    variant = await session.get(ProductVariant, item.variant_id)
    product_name: str | None = None
    sku: str | None = None
    if variant is not None:
        sku = variant.sku
        product = await session.get(Product, variant.product_id)
        if product is not None:
            product_name = product.name
    return {
        "purchase_item_id": str(item.id),
        "product_name": product_name,
        "variant_sku": sku,
        "quantity": _qty_str(item.quantity),
        "unit_cost": _money_str(item.unit_cost),
    }


async def resolve_purchase_item(
    session: AsyncSession,
    shop_id: uuid.UUID,
    purchase: Purchase,
    entry: dict[str, Any],
) -> PurchaseItem:
    """Resolve exactly one line of ``purchase`` from a return-line request."""
    purchase_items = (
        (
            await session.execute(
                select(PurchaseItem).where(
                    PurchaseItem.purchase_id == purchase.id
                )
            )
        )
        .scalars()
        .all()
    )
    if not purchase_items:
        raise ReturnPrepNotFoundError("Purchase has no items to return.")

    direct = entry.get("purchase_item_id")
    if direct is not None and _clean(direct):
        others = [
            entry.get("product_name"),
            entry.get("variant_sku"),
            entry.get("variant_id"),
            entry.get("purchase_item_reference"),
        ]
        if any(o is not None and _clean(o) for o in others):
            raise ReturnPrepError(
                "Provide either purchase_item_id or a product reference, "
                "not both."
            )
        iid = parse_uuid_arg(direct, "purchase_item_id")
        hit = next((i for i in purchase_items if i.id == iid), None)
        if hit is None:
            raise ReturnPrepNotFoundError(
                "Purchase item not found on this purchase in your shop."
            )
        return hit

    vid_raw = entry.get("variant_id")
    sku_raw = _clean(entry.get("variant_sku"))
    name_raw = _clean(entry.get("product_name"))
    ref_raw = _clean(entry.get("purchase_item_reference"))
    if not vid_raw and not sku_raw and not name_raw and not ref_raw:
        raise ReturnPrepError(
            "Purchase item reference is missing: ask which item is being "
            "returned. Never guess."
        )

    if vid_raw is not None and _clean(vid_raw):
        if name_raw or sku_raw or ref_raw:
            raise ReturnPrepError(
                "Provide either variant_id or a product reference, not both."
            )
        vid = parse_uuid_arg(vid_raw, "variant_id")
        hits = [i for i in purchase_items if i.variant_id == vid]
        if not hits:
            raise ReturnPrepNotFoundError(
                "Purchase item not found on this purchase in your shop."
            )
        if len(hits) > 1:
            raise ReturnPrepAmbiguousError(
                "Multiple purchase lines match. Ask which one the shopkeeper "
                "means — never guess.",
                matches=[
                    await _purchase_item_display(session, h)
                    for h in hits[:_MAX_MATCHES]
                ],
            )
        return hits[0]

    if sku_raw and not name_raw and not ref_raw:
        variant = (
            await session.execute(
                select(ProductVariant).where(
                    ProductVariant.shop_id == shop_id,
                    ProductVariant.sku == sku_raw,
                )
            )
        ).scalar_one_or_none()
        if variant is None:
            raise ReturnPrepNotFoundError(
                f"No variant with SKU {sku_raw!r} in your shop."
            )
        hits = [i for i in purchase_items if i.variant_id == variant.id]
        if not hits:
            raise ReturnPrepNotFoundError(
                "Purchase item not found on this purchase in your shop."
            )
        return hits[0]

    needle = (name_raw or ref_raw or sku_raw).strip()
    if len(needle) > 200:
        raise ReturnPrepError("Purchase item reference is too long.")
    lowered = needle.lower()
    scored: list[PurchaseItem] = []
    displays: dict[str, dict[str, Any]] = {}
    for item in purchase_items:
        variant = await session.get(ProductVariant, item.variant_id)
        pname = ""
        sku = ""
        if variant is not None:
            sku = variant.sku or ""
            product = await session.get(Product, variant.product_id)
            if product is not None:
                pname = product.name or ""
        if (
            lowered in pname.lower()
            or lowered in sku.lower()
            or (pname and pname.lower() in lowered)
        ):
            scored.append(item)
            displays[str(item.id)] = {
                "purchase_item_id": str(item.id),
                "product_name": pname or None,
                "variant_sku": sku or None,
                "quantity": _qty_str(item.quantity),
                "unit_cost": _money_str(item.unit_cost),
            }
    if not scored:
        raise ReturnPrepNotFoundError(
            f"Item {needle!r} not found on this purchase. "
            "Ask which item the shopkeeper means."
        )
    if len(scored) > 1:
        raise ReturnPrepAmbiguousError(
            f"Multiple items match {needle!r}. Ask which one the "
            "shopkeeper means — never guess.",
            matches=[displays[str(i.id)] for i in scored[:_MAX_MATCHES]],
        )
    return scored[0]


# --------------------------------------------------------------------------
# Previews (HITL approval text — estimates only, backend is authoritative)
# --------------------------------------------------------------------------


def build_customer_return_preview(
    *,
    customer_name: str | None,
    sale_reference: str | None,
    lines: list[dict[str, Any]],
    estimated_total: Decimal,
    estimated_ar: Decimal | None = None,
    estimated_cash: Decimal | None = None,
) -> str:
    """Human-readable confirmation text shown BEFORE the HITL approval.

    Amounts here are preview estimates only; the authoritative totals come
    from ``returns.create_sale_return()`` after approval.
    """
    parts = [
        "Customer Return",
        "",
        f"Customer: {customer_name or 'Walk-in'}",
        f"Sale: {sale_reference or '—'}",
    ]
    for line in lines:
        label = line.get("product_name") or line.get("variant_sku") or "Item"
        parts.append(
            f"Item: {label} — Quantity: {line.get('quantity')} "
            f"(unit price Rs. {line.get('unit_price', '—')})"
        )
    parts.append("")
    parts.append(f"Estimated return value: PKR {_money_str(estimated_total)}")
    if estimated_ar is not None:
        parts.append(f"Customer Khata reduction: PKR {_money_str(estimated_ar)}")
    if estimated_cash is not None:
        parts.append(f"Cash refund: PKR {_money_str(estimated_cash)}")
    parts.append(
        "The backend return service calculates the authoritative final "
        "amount (discounts, AR-vs-cash split, inventory, accounting)."
    )
    parts.append("Please approve.")
    return "\n".join(parts)


def build_supplier_return_preview(
    *,
    supplier_name: str | None,
    purchase_reference: str | None,
    lines: list[dict[str, Any]],
    estimated_total: Decimal,
) -> str:
    """Human-readable confirmation text shown BEFORE the HITL approval.

    Amounts here are preview estimates only; the authoritative totals come
    from ``returns.create_purchase_return()`` after approval.
    """
    parts = [
        "Supplier Return",
        "",
        f"Supplier: {supplier_name or '—'}",
        f"Purchase: {purchase_reference or '—'}",
    ]
    for line in lines:
        label = line.get("product_name") or line.get("variant_sku") or "Item"
        parts.append(
            f"Item: {label} — Quantity: {line.get('quantity')} "
            f"(unit cost Rs. {line.get('unit_cost', '—')})"
        )
    parts.append("")
    parts.append(f"Estimated return value: PKR {_money_str(estimated_total)}")
    parts.append(f"Supplier payable impact: PKR {_money_str(estimated_total)}")
    parts.append(
        "The backend return service calculates the authoritative final "
        "amount (discount share, inventory, payable, accounting)."
    )
    parts.append("Please approve.")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Error mapping (domain errors surface cleanly, never tracebacks/SQL)
# --------------------------------------------------------------------------


def _customer_return_error_code(exc: Exception) -> str:
    if isinstance(exc, returns_service.SaleNotFoundError):
        return "sale_not_found"
    if isinstance(exc, returns_service.SaleItemNotFoundError):
        return "sale_item_not_found"
    if isinstance(exc, returns_service.SaleNotReturnableError):
        return "sale_not_returnable"
    if isinstance(exc, returns_service.ExceedsRemainingQuantityError):
        return "exceeds_remaining"
    if isinstance(
        exc,
        (
            returns_service.EmptyReturnError,
            returns_service.InvalidReturnQuantityError,
        ),
    ):
        return "invalid_return"
    if isinstance(exc, returns_service.ReturnError):
        return "return_failed"
    return "return_failed"


def _customer_return_error_message(exc: Exception) -> str:
    if isinstance(
        exc,
        (
            returns_service.SaleNotFoundError,
            returns_service.SaleItemNotFoundError,
        ),
    ):
        return "Sale or sale item not found in your shop. No return recorded."
    if isinstance(exc, returns_service.SaleNotReturnableError):
        return f"Sale cannot be returned: {exc} No return recorded."
    if isinstance(exc, returns_service.ExceedsRemainingQuantityError):
        return f"{exc} No return recorded."
    if isinstance(exc, returns_service.ReturnError):
        return f"Invalid return: {exc} No return recorded."
    return "Customer return failed and was rolled back. No return was recorded."


def _supplier_return_error_code(exc: Exception) -> str:
    if isinstance(exc, InsufficientStockError):
        return "insufficient_stock"
    if isinstance(exc, returns_service.PurchaseNotFoundError):
        return "purchase_not_found"
    if isinstance(exc, returns_service.PurchaseItemNotFoundError):
        return "purchase_item_not_found"
    if isinstance(exc, returns_service.ExceedsRemainingQuantityError):
        return "exceeds_remaining"
    if isinstance(
        exc,
        (
            returns_service.EmptyReturnError,
            returns_service.InvalidReturnQuantityError,
        ),
    ):
        return "invalid_return"
    if isinstance(exc, returns_service.ReturnError):
        return "return_failed"
    return "return_failed"


def _supplier_return_error_message(exc: Exception) -> str:
    if isinstance(exc, InsufficientStockError):
        return (
            f"Not enough stock: requested {exc.requested}, "
            f"only {exc.available} available. No return recorded."
        )
    if isinstance(
        exc,
        (
            returns_service.PurchaseNotFoundError,
            returns_service.PurchaseItemNotFoundError,
        ),
    ):
        return "Purchase or purchase item not found in your shop. No return recorded."
    if isinstance(exc, returns_service.ExceedsRemainingQuantityError):
        return f"{exc} No return recorded."
    if isinstance(exc, returns_service.ReturnError):
        return f"Invalid return: {exc} No return recorded."
    return "Supplier return failed and was rolled back. No return was recorded."


# --------------------------------------------------------------------------
# Receipts + authoritative summaries (read, never recomputed)
# --------------------------------------------------------------------------


async def _find_customer_receipt(
    session: AsyncSession, shop_id: uuid.UUID, key: str
) -> AICustomerReturnReceipt | None:
    return (
        await session.execute(
            select(AICustomerReturnReceipt).where(
                AICustomerReturnReceipt.shop_id == shop_id,
                AICustomerReturnReceipt.operation_key == key,
            )
        )
    ).scalar_one_or_none()


async def _find_supplier_receipt(
    session: AsyncSession, shop_id: uuid.UUID, key: str
) -> AISupplierReturnReceipt | None:
    return (
        await session.execute(
            select(AISupplierReturnReceipt).where(
                AISupplierReturnReceipt.shop_id == shop_id,
                AISupplierReturnReceipt.operation_key == key,
            )
        )
    ).scalar_one_or_none()


async def _customer_return_summary(
    session: AsyncSession,
    shop_id: uuid.UUID,
    sale_return: SaleReturn,
) -> dict[str, Any]:
    """Authoritative result figures, read — never recomputed by the AI."""
    sale = await session.get(Sale, sale_return.sale_id)
    customer_name: str | None = None
    if sale_return.customer_id is not None:
        customer = await session.get(Customer, sale_return.customer_id)
        if customer is not None and customer.shop_id == shop_id:
            customer_name = customer.name
    invoice: str | None = sale.invoice_number if sale is not None else None
    from app.models.returns import SaleReturnItem

    rows = (
        (
            await session.execute(
                select(SaleReturnItem).where(
                    SaleReturnItem.return_id == sale_return.id
                )
            )
        )
        .scalars()
        .all()
    )
    lines: list[dict[str, Any]] = []
    for row in rows:
        variant_sku: str | None = None
        product_name: str | None = None
        variant = await session.get(ProductVariant, row.variant_id)
        if variant is not None:
            variant_sku = variant.sku
            product = await session.get(Product, variant.product_id)
            if product is not None:
                product_name = product.name
        lines.append(
            {
                "sale_item_id": str(row.sale_item_id),
                "variant_sku": variant_sku,
                "product_name": product_name,
                "quantity": _qty_str(row.quantity),
                "unit_price": _money_str(row.unit_price),
                "discount": _money_str(row.discount),
                "total": _money_str(row.total),
            }
        )
    remaining: dict[str, str] = {}
    try:
        remaining_raw = await returns_service.get_remaining_sale_quantities(
            session, shop_id=shop_id, sale_id=sale_return.sale_id
        )
        remaining = {str(k): _qty_str(v) for k, v in remaining_raw.items()}
    except returns_service.ReturnError:
        remaining = {}
    return {
        "return_id": str(sale_return.id),
        "sale_id": str(sale_return.sale_id),
        "customer_name": customer_name,
        "invoice_number": invoice,
        "items": lines,
        "items_count": len(lines),
        "total": _money_str(sale_return.total_amount),
        "ar_amount": _money_str(sale_return.ar_amount),
        "cash_refund": _money_str(sale_return.cash_refund),
        "remaining_quantities": remaining,
    }


async def _supplier_return_summary(
    session: AsyncSession,
    shop_id: uuid.UUID,
    purchase_return: PurchaseReturn,
) -> dict[str, Any]:
    """Authoritative result figures, read — never recomputed by the AI."""
    purchase = await session.get(Purchase, purchase_return.purchase_id)
    supplier_name: str | None = None
    if purchase_return.supplier_id is not None:
        supplier = await session.get(Supplier, purchase_return.supplier_id)
        if supplier is not None and supplier.shop_id == shop_id:
            supplier_name = supplier.name
    invoice: str | None = purchase.invoice_number if purchase is not None else None
    from app.models.returns import PurchaseReturnItem

    rows = (
        (
            await session.execute(
                select(PurchaseReturnItem).where(
                    PurchaseReturnItem.return_id == purchase_return.id
                )
            )
        )
        .scalars()
        .all()
    )
    lines: list[dict[str, Any]] = []
    for row in rows:
        variant_sku: str | None = None
        product_name: str | None = None
        variant = await session.get(ProductVariant, row.variant_id)
        if variant is not None:
            variant_sku = variant.sku
            product = await session.get(Product, variant.product_id)
            if product is not None:
                product_name = product.name
        lines.append(
            {
                "purchase_item_id": str(row.purchase_item_id),
                "variant_sku": variant_sku,
                "product_name": product_name,
                "quantity": _qty_str(row.quantity),
                "unit_cost": _money_str(row.unit_cost),
                "discount": _money_str(row.discount),
                "total": _money_str(row.total),
            }
        )
    remaining: dict[str, str] = {}
    try:
        remaining_raw = await returns_service.get_remaining_purchase_quantities(
            session, shop_id=shop_id, purchase_id=purchase_return.purchase_id
        )
        remaining = {str(k): _qty_str(v) for k, v in remaining_raw.items()}
    except returns_service.ReturnError:
        remaining = {}
    return {
        "return_id": str(purchase_return.id),
        "purchase_id": str(purchase_return.purchase_id),
        "supplier_name": supplier_name,
        "invoice_number": invoice,
        "items": lines,
        "items_count": len(lines),
        "total": _money_str(purchase_return.total_amount),
        "remaining_quantities": remaining,
    }


# --------------------------------------------------------------------------
# Tool builders (tenant-bound, flush-only, savepoint-scoped)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _PreparedCustomerLine:
    sale_item: SaleItem
    quantity: Decimal


@dataclass(frozen=True)
class _PreparedSupplierLine:
    purchase_item: PurchaseItem
    quantity: Decimal


def build_return_write_tools(
    session: AsyncSession, tenant: TenantContext
) -> list[BaseTool]:
    """Build the tenant-bound return write tools for one AI request.

    ``shop_id`` is captured from ``tenant`` — it never appears in either
    tool schema, so the model cannot choose another shop. The tools flush
    but never commit; the caller (``app/api/ai.py``) owns the commit, and
    ``returns.create_*_return()`` owns all business logic. Failures roll
    back to a savepoint, never the whole session.
    """
    shop_id = tenant.shop_id

    @tool
    async def create_customer_return(
        idempotency_key: str,
        items: list[dict[str, Any]],
        sale_reference: str | None = None,
        sale_id: str | None = None,
        customer_name: str | None = None,
        customer_id: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Record goods a customer returned to the shop (HITL-gated).

        Use when the shopkeeper says a customer brought goods back, e.g.
        'Ali ne 2 meter black lawn wapas kar diya', 'Ali ki invoice 1023
        se 2 meter black lawn return karo', 'Ahmed ke bill mein se 1 suit
        blue cotton return hai'. PREPARE first with the read tools (find
        the original sale/bill), show the shopkeeper a short preview, then
        call this tool ONCE per return.

        Args:
            idempotency_key: REQUIRED stable identity for this return
                (generate one UUID hex per NEW return request and reuse it
                for retries of the SAME operation). The same key never
                creates two returns.
            items: REQUIRED non-empty list of return lines (1-20). Each
                line is an object with: quantity (e.g. '2', '2.5',
                '2 meter', '2m', '3 suits' — recorded in the original
                variant's own unit, never converted) plus ONE item
                reference: sale_item_id, variant_id, variant_sku,
                product_name (e.g. 'Black Lawn'), or sale_item_reference.
                Exactly one matching sale line continues; multiple ask for
                clarification, zero is not_found. Never invent a line.
            sale_reference: Which original sale, e.g. an invoice number
                ('INV-1023', '1023'), "Ali's bill". Omit only to use
                sale_id or customer_name/customer_id.
            sale_id: Advanced alternative to sale_reference.
            customer_name: Narrow the sale to one customer, e.g. 'Ali'.
                Must match exactly one customer, else ask. Never invent or
                create a customer. Omit only to use customer_id/sale_id.
            customer_id: Advanced alternative to customer_name.
            notes: Optional note (<=500 chars). Never influences pricing.

        Returns a structured result: completed (with return_id and the
        backend's authoritative total/ar/cash), ambiguous/not_found (ask
        the shopkeeper), or error (explain it).
        """
        try:
            key = validate_idempotency_key(idempotency_key)
            raw_lines = _normalise_items_arg(items)
            memo = parse_notes(notes)
        except ReturnPrepError as exc:
            return _prep_error(exc)

        existing = await _find_customer_receipt(session, shop_id, key)
        if existing is not None:
            sale_return = None
            if existing.return_id is not None:
                candidate = await session.get(SaleReturn, existing.return_id)
                if candidate is not None and candidate.shop_id == shop_id:
                    sale_return = candidate
            if sale_return is None:
                return {
                    "status": "error",
                    "code": "already_processed",
                    "message": (
                        "This customer return operation was already "
                        "processed. No new return was created."
                    ),
                }
            result = await _customer_return_summary(
                session, shop_id, sale_return
            )
            return {
                "status": "completed",
                "duplicate": True,
                "idempotency_key": key,
                **result,
            }

        try:
            sale = await resolve_customer_sale(
                session,
                shop_id,
                sale_id=sale_id,
                sale_reference=sale_reference,
                customer_id=customer_id,
                customer_name=customer_name,
            )
        except ReturnPrepError as exc:
            return _prep_error(exc)

        prepared: list[_PreparedCustomerLine] = []
        try:
            for entry in raw_lines:
                if not isinstance(entry, dict):
                    raise ReturnPrepError(
                        "Invalid items: each return line must be an object."
                    )
                item = await resolve_sale_item(session, shop_id, sale, entry)
                qty = parse_quantity(entry.get("quantity"))
                prepared.append(
                    _PreparedCustomerLine(sale_item=item, quantity=qty)
                )
        except ReturnPrepError as exc:
            return _prep_error(exc)

        service_lines = [
            returns_service.SaleReturnLineInput(
                sale_item_id=line.sale_item.id,
                quantity=line.quantity,
            )
            for line in prepared
        ]

        try:
            async with session.begin_nested():
                sale_return = await returns_service.create_sale_return(
                    session,
                    shop_id=shop_id,
                    sale_id=sale.id,
                    lines=service_lines,
                    notes=memo,
                )
                session.add(
                    AICustomerReturnReceipt(
                        shop_id=shop_id,
                        operation_key=key,
                        return_id=sale_return.id,
                    )
                )
                await session.flush()
        except IntegrityError:
            retry = await _find_customer_receipt(session, shop_id, key)
            if retry is not None and retry.return_id is not None:
                winner = await session.get(SaleReturn, retry.return_id)
                if winner is not None and winner.shop_id == shop_id:
                    summary = await _customer_return_summary(
                        session, shop_id, winner
                    )
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
                    "No return was recorded by this attempt — it is safe "
                    "to retry the same operation."
                ),
            }
        except returns_service.ReturnError as exc:
            return {
                "status": "error",
                "code": _customer_return_error_code(exc),
                "message": _customer_return_error_message(exc),
            }
        except Exception:  # noqa: BLE001 — boundary maps to clean errors
            return {
                "status": "error",
                "code": "return_failed",
                "message": (
                    "Customer return failed and was rolled back. "
                    "No partial return was recorded."
                ),
            }

        await session.flush()
        summary = await _customer_return_summary(session, shop_id, sale_return)
        return {
            "status": "completed",
            "duplicate": False,
            "idempotency_key": key,
            **summary,
        }

    @tool
    async def create_supplier_return(
        idempotency_key: str,
        items: list[dict[str, Any]],
        purchase_reference: str | None = None,
        purchase_id: str | None = None,
        supplier_name: str | None = None,
        supplier_id: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Record goods the shop returned to a supplier (HITL-gated).

        Use when the shopkeeper says goods went back to a supplier, e.g.
        'Ahmed Traders ko 3 suits blue cotton wapas karne hain',
        'Purchase invoice P-102 se 2 meter black lawn supplier ko return
        karo', 'Last purchase se 5 suits return kar do'. PREPARE first with
        the read tools (find the original purchase), show the shopkeeper a
        short preview, then call this tool ONCE per return.

        Args:
            idempotency_key: REQUIRED stable identity for this return
                (generate one UUID hex per NEW return request and reuse it
                for retries of the SAME operation). The same key never
                creates two returns.
            items: REQUIRED non-empty list of return lines (1-20). Each
                line is an object with: quantity (e.g. '3', '2 meter',
                '5 suits' — recorded in the original variant's own unit,
                never converted) plus ONE item reference: purchase_item_id,
                variant_id, variant_sku, product_name (e.g. 'Blue Cotton'),
                or purchase_item_reference. Exactly one matching purchase
                line continues; multiple ask for clarification, zero is
                not_found. Never invent a line.
            purchase_reference: Which original purchase, e.g. an invoice
                number ('P-102', 'PUR-102'). Omit only to use purchase_id
                or supplier_name/supplier_id.
            purchase_id: Advanced alternative to purchase_reference.
            supplier_name: Narrow the purchase to one supplier, e.g.
                'Ahmed Traders'. Must match exactly one supplier, else ask.
                Never invent or create a supplier.
            supplier_id: Advanced alternative to supplier_name.
            notes: Optional note (<=500 chars). Never influences pricing.

        Returns a structured result: completed (with return_id and the
        backend's authoritative total/payable impact),
        ambiguous/not_found (ask the shopkeeper), or error (explain it).
        """
        try:
            key = validate_idempotency_key(idempotency_key)
            raw_lines = _normalise_items_arg(items)
            memo = parse_notes(notes)
        except ReturnPrepError as exc:
            return _prep_error(exc)

        existing = await _find_supplier_receipt(session, shop_id, key)
        if existing is not None:
            purchase_return = None
            if existing.return_id is not None:
                candidate = await session.get(
                    PurchaseReturn, existing.return_id
                )
                if candidate is not None and candidate.shop_id == shop_id:
                    purchase_return = candidate
            if purchase_return is None:
                return {
                    "status": "error",
                    "code": "already_processed",
                    "message": (
                        "This supplier return operation was already "
                        "processed. No new return was created."
                    ),
                }
            result = await _supplier_return_summary(
                session, shop_id, purchase_return
            )
            return {
                "status": "completed",
                "duplicate": True,
                "idempotency_key": key,
                **result,
            }

        try:
            purchase = await resolve_supplier_purchase(
                session,
                shop_id,
                purchase_id=purchase_id,
                purchase_reference=purchase_reference,
                supplier_id=supplier_id,
                supplier_name=supplier_name,
            )
        except ReturnPrepError as exc:
            return _prep_error(exc)

        prepared: list[_PreparedSupplierLine] = []
        try:
            for entry in raw_lines:
                if not isinstance(entry, dict):
                    raise ReturnPrepError(
                        "Invalid items: each return line must be an object."
                    )
                item = await resolve_purchase_item(
                    session, shop_id, purchase, entry
                )
                qty = parse_quantity(entry.get("quantity"))
                prepared.append(
                    _PreparedSupplierLine(
                        purchase_item=item, quantity=qty
                    )
                )
        except ReturnPrepError as exc:
            return _prep_error(exc)

        service_lines = [
            returns_service.PurchaseReturnLineInput(
                purchase_item_id=line.purchase_item.id,
                quantity=line.quantity,
            )
            for line in prepared
        ]

        try:
            async with session.begin_nested():
                purchase_return = await returns_service.create_purchase_return(
                    session,
                    shop_id=shop_id,
                    purchase_id=purchase.id,
                    lines=service_lines,
                    notes=memo,
                )
                session.add(
                    AISupplierReturnReceipt(
                        shop_id=shop_id,
                        operation_key=key,
                        return_id=purchase_return.id,
                    )
                )
                await session.flush()
        except IntegrityError:
            retry = await _find_supplier_receipt(session, shop_id, key)
            if retry is not None and retry.return_id is not None:
                winner = await session.get(PurchaseReturn, retry.return_id)
                if winner is not None and winner.shop_id == shop_id:
                    summary = await _supplier_return_summary(
                        session, shop_id, winner
                    )
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
                    "No return was recorded by this attempt — it is safe "
                    "to retry the same operation."
                ),
            }
        except InsufficientStockError as exc:
            return {
                "status": "error",
                "code": "insufficient_stock",
                "message": _supplier_return_error_message(exc),
            }
        except returns_service.ReturnError as exc:
            return {
                "status": "error",
                "code": _supplier_return_error_code(exc),
                "message": _supplier_return_error_message(exc),
            }
        except Exception:  # noqa: BLE001 — boundary maps to clean errors
            return {
                "status": "error",
                "code": "return_failed",
                "message": (
                    "Supplier return failed and was rolled back. "
                    "No partial return was recorded."
                ),
            }

        await session.flush()
        summary = await _supplier_return_summary(
            session, shop_id, purchase_return
        )
        return {
            "status": "completed",
            "duplicate": False,
            "idempotency_key": key,
            **summary,
        }

    return [create_customer_return, create_supplier_return]  # type: ignore[list-item]


def get_return_write_tool_by_name(
    session: AsyncSession, tenant: TenantContext, name: str
) -> BaseTool | None:
    """Return the bound return write tool by name (tests/convenience)."""
    for t in build_return_write_tools(session, tenant):
        if t.name == name:
            return t
    return None


def assert_return_write_registry_is_minimal(tools: list[BaseTool]) -> None:
    """Raise unless ``tools`` is exactly the two sanctioned mutations.

    Step 9 allows TWO AI mutations in this module (``create_customer_return``
    and ``create_supplier_return``). Any other write tool — sales, payments,
    expenses, inventory, CRUD — fails here.
    """
    names = sorted(t.name for t in tools)
    if names != sorted(WRITE_TOOL_NAMES):
        raise AssertionError(
            f"AI return-write registry must be exactly {sorted(WRITE_TOOL_NAMES)}; "
            f"got {names}. Step 9 allows two mutations only: customer/supplier returns."
        )


__all__ = [
    "CREATE_CUSTOMER_RETURN_TOOL_NAME",
    "CREATE_SUPPLIER_RETURN_TOOL_NAME",
    "WRITE_TOOL_NAMES",
    "ReturnPrepAmbiguousError",
    "ReturnPrepError",
    "ReturnPrepNotFoundError",
    "assert_return_write_registry_is_minimal",
    "build_customer_return_preview",
    "build_return_write_tools",
    "build_supplier_return_preview",
    "get_return_write_tool_by_name",
    "parse_notes",
    "parse_quantity",
    "parse_uuid_arg",
    "resolve_customer_sale",
    "resolve_purchase_item",
    "resolve_sale_item",
    "resolve_supplier_purchase",
    "validate_idempotency_key",
]

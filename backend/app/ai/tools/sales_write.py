"""Single AI write operation: sale creation (Step 3).

Exactly ONE business-level mutation exists here: ``create_sale``. There
are deliberately no ``create_sale_item`` / ``create_payment`` /
``deduct_inventory`` tools — those are implementation details owned by
``SaleService.create_sale()``, which remains the authoritative business
operation. The AI layer only converts natural language into the
structured input that service requires:

    Shopkeeper -> Master Deep Agent -> resolve customer + product
        -> PREPARE (no DB mutation) -> HITL interrupt -> approve
        -> resume -> create_sale executes -> SaleService.create_sale()
        -> one controlled transaction -> commit/rollback -> result

Why this does NOT reuse the Step 2 read-tool pattern blindly:

* Reads close over ``(session, tenant)`` and SELECT. Writes need
  explicit transaction ownership: the mutating section runs inside a
  SAVEPOINT (``session.begin_nested()``), so any failure discards only
  the partial sale and leaves the surrounding session clean; the API
  route commits after a finished run (``app/api/ai.py``). The tool
  itself NEVER calls ``session.commit()`` or a full ``rollback()``.
  ``SaleService.create_sale()`` only flushes, so sale + items + stock
  + payments + ledger + idempotency receipt commit atomically, or all
  roll back together.
* No live transaction is ever held across the HITL pause. PREPARE does
  zero mutation; the pause happens BEFORE the tool executes (Deep
  Agents ``interrupt_on``); execution happens later, in the resume
  request, on that request's own fresh session. The prepared operation
  travels through the agent checkpoint as plain serialisable args
  (strings) — never a session, connection, or ORM object.
* Idempotency: HITL resume/retry must not double-create. The agent
  supplies one ``idempotency_key`` per prepared sale; the tool checks
  ``ai_sale_receipts(shop_id, operation_key)`` BEFORE mutating and
  writes the receipt in the SAME transaction as the sale. A unique
  constraint is the final guard under concurrency.

Tenant isolation: ``shop_id`` is captured from the server-side
``TenantContext`` — it never appears in the tool schema, so the model
cannot choose another shop. Names/IDs supplied by the model are
re-validated against the authenticated shop before any mutation.
"""

from __future__ import annotations

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
from app.models.ai_operation import AISaleReceipt
from app.models.customer import Customer
from app.models.payment import PaymentMethod
from app.models.product import Product, ProductVariant
from app.models.sale import Sale, SaleItem
from app.services import sales as sales_service
from app.services.inventory import InsufficientStockError
from app.services.sales import (
    CustomerNotFoundError,
    DuplicateSaleItemError,
    EmptySaleError,
    InvalidSaleItemError,
    InvalidSaleTotalsError,
    SaleError,
    ShopNotFoundError,
    VariantNotFoundError,
)

CREATE_SALE_TOOL_NAME = "create_sale"

WRITE_TOOL_NAMES: tuple[str, ...] = (CREATE_SALE_TOOL_NAME,)

_MAX_MATCHES = 5
_MONEY_SCALE = Decimal("0.01")
_QTY_SCALE = Decimal("0.001")

# Operation identity: 8-64 chars so a UUID hex (32) fits; strict charset
# so the key is safe to echo in logs and checkpoints.
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

PAYMENT_KIND_CASH = "cash"
PAYMENT_KIND_CREDIT = "credit"

# Deterministic mapping from shopkeeper language to the sale schema.
# The model still does the understanding; this map keeps the tool's
# contract strict (only "cash" | "credit" reach the service layer).
_CASH_ALIASES = frozenset({"cash", "nagad", "naqad", "full", "paid"})
_CREDIT_ALIASES = frozenset(
    {"credit", "udhaar", "udhar", "khaata", "khata", "baqi", "due", "on-credit"}
)


class SalePrepError(ValueError):
    """A sale request that cannot proceed — no mutation was performed."""

    code = "invalid_request"


class SalePrepNotFoundError(SalePrepError):
    """Named customer/product/variant is not in this shop."""

    code = "not_found"


class SalePrepAmbiguousError(SalePrepError):
    """A name matches several rows — the agent must ask, never guess."""

    code = "ambiguous"

    def __init__(self, message: str, matches: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.matches = matches


def validate_idempotency_key(raw: Any) -> str:
    """Validate the caller-supplied operation identity (no mutation)."""
    text = str(raw or "").strip()
    if not _KEY_RE.match(text):
        raise SalePrepError(
            "Invalid idempotency_key: expected 8-64 characters "
            "[A-Za-z0-9_-] (generate one UUID hex per new sale request)."
        )
    return text


def parse_payment_kind(raw: Any) -> str:
    """Map shopkeeper payment language to ``cash`` | ``credit``."""
    norm = str(raw or "").strip().lower()
    if norm in _CASH_ALIASES:
        return PAYMENT_KIND_CASH
    if norm in _CREDIT_ALIASES:
        return PAYMENT_KIND_CREDIT
    raise SalePrepError(
        f"Unknown payment_kind {raw!r}: expected 'cash' (cash/nagad) "
        "or 'credit' (udhaar/khata/baqi)."
    )


def parse_payment_method(raw: Any) -> PaymentMethod:
    """Validate the cash-settlement method against the sale schema."""
    try:
        return PaymentMethod(str(raw or "").strip().lower())
    except ValueError:
        valid = ", ".join(sorted(m.value for m in PaymentMethod))
        raise SalePrepError(
            f"Unknown payment_method {raw!r}: expected one of {valid}."
        ) from None


def parse_quantity(raw: Any) -> Decimal:
    """Parse a sale quantity: strictly positive, 3dp fabric scale."""
    try:
        qty = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise SalePrepError(
            f"Invalid quantity {raw!r}: expected a positive number."
        ) from None
    if qty.is_nan() or qty.is_infinite():
        raise SalePrepError(f"Invalid quantity {raw!r}: expected a positive number.")
    qty = qty.quantize(_QTY_SCALE, rounding=ROUND_HALF_UP)
    if qty <= 0:
        raise SalePrepError("Sale quantity must be greater than 0.")
    return qty


def parse_money(raw: Any, field: str) -> Decimal:
    """Parse a money amount: finite, 2dp, never NaN."""
    try:
        amount = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise SalePrepError(
            f"Invalid {field} {raw!r}: expected a non-negative amount."
        ) from None
    if amount.is_nan() or amount.is_infinite():
        raise SalePrepError(f"Invalid {field} {raw!r}: expected a non-negative amount.")
    return amount.quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)


def parse_uuid_arg(raw: Any, field: str) -> uuid.UUID:
    """Parse a model-supplied internal ID (still validated vs the shop)."""
    try:
        return uuid.UUID(str(raw).strip())
    except (ValueError, AttributeError):
        raise SalePrepError(f"Invalid {field} {raw!r}.") from None


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


async def resolve_customer(
    session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    customer_id: Any = None,
    customer_name: Any = None,
) -> Customer | None:
    """Resolve the sale's customer within the authenticated shop.

    Returns ``None`` for a walk-in sale (no customer given). Never
    creates a customer — a missing name is an error, not an invitation
    to invent a row. A model-supplied ID is re-validated against the
    shop; a foreign-shop ID reads as not-found.
    """
    name = _clean(customer_name)
    if customer_id is not None and _clean(customer_id):
        if name:
            raise SalePrepError(
                "Provide either customer_id or customer_name, not both."
            )
        cid = parse_uuid_arg(customer_id, "customer_id")
        customer = (
            await session.execute(
                select(Customer).where(Customer.id == cid, Customer.shop_id == shop_id)
            )
        ).scalar_one_or_none()
        if customer is None:
            raise SalePrepNotFoundError("Customer not found in your shop.")
        return customer
    if not name:
        return None  # Walk-in sale: NULL customer is first-class.
    if len(name) > 150:
        raise SalePrepError("Customer name is too long.")
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
        raise SalePrepNotFoundError(
            f"Customer {name!r} not found in your shop. "
            "I do not create customers automatically — ask for the "
            "correct name or record this as a walk-in sale."
        )
    if len(found) > 1:
        raise SalePrepAmbiguousError(
            f"Multiple customers match {name!r}. Ask which one the "
            "shopkeeper means — never guess.",
            matches=[{"name": c.name, "phone": c.phone} for c in found[:_MAX_MATCHES]],
        )
    return found[0]


@dataclass(frozen=True)
class ResolvedVariant:
    """A sellable variant plus its display names (all shop-validated)."""

    variant: ProductVariant
    product_name: str


async def resolve_variant(
    session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    variant_id: Any = None,
    product_name: Any = None,
    variant_sku: Any = None,
) -> ResolvedVariant:
    """Resolve exactly one active variant within the authenticated shop.

    Ambiguous names are an error with ``matches`` for clarification —
    the agent must ask, never silently pick. Inactive variants are
    rejected at this boundary (the sales service itself does not check
    ``is_active``; refusing to sell a deactivated SKU is AI input
    validation, not a new business rule).
    """
    name = _clean(product_name)
    sku = _clean(variant_sku)
    if variant_id is not None and _clean(variant_id):
        if name or sku:
            raise SalePrepError(
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
            raise SalePrepNotFoundError("Product variant not found in your shop.")
        if not variant.is_active:
            raise SalePrepError(
                f"Variant {variant.sku!r} is inactive and cannot be sold."
            )
        return ResolvedVariant(variant=variant, product_name=variant.product.name)
    if not name and not sku:
        raise SalePrepError(
            "Tell me which product to sell (product_name, or variant_sku)."
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
            raise SalePrepNotFoundError(f"No variant with SKU {sku!r} in your shop.")
        if not variant.is_active:
            raise SalePrepError(
                f"Variant {variant.sku!r} is inactive and cannot be sold."
            )
        return ResolvedVariant(variant=variant, product_name=variant.product.name)
    if len(name) > 200:
        raise SalePrepError("Product name is too long.")
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
        raise SalePrepNotFoundError(f"Product {name!r} not found in your shop.")
    if len(products) > 1:
        raise SalePrepAmbiguousError(
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
    active = [v for v in (product.variants or []) if v.is_active]
    if sku:
        hit = next((v for v in active if v.sku == sku), None)
        if hit is None:
            raise SalePrepNotFoundError(
                f"SKU {sku!r} not found for product {product.name!r}."
            )
        return ResolvedVariant(variant=hit, product_name=product.name)
    if not active:
        raise SalePrepError(f"Product {product.name!r} has no active variants to sell.")
    if len(active) > 1:
        raise SalePrepAmbiguousError(
            f"Product {product.name!r} has multiple variants. Ask which "
            "one the shopkeeper means — never guess.",
            matches=[
                {
                    "variant_sku": v.sku,
                    "unit": v.unit.value if hasattr(v.unit, "value") else str(v.unit),
                    "selling_price": _money_str(v.selling_price),
                }
                for v in active[:_MAX_MATCHES]
            ],
        )
    return ResolvedVariant(variant=active[0], product_name=product.name)


def build_sale_preview(
    *,
    customer_name: str | None,
    product_name: str,
    variant_sku: str,
    unit: str,
    quantity: Decimal,
    unit_price: Decimal,
    payment_kind: str,
    paid_amount: Decimal,
    due_amount: Decimal,
) -> str:
    """Human-readable confirmation text shown BEFORE the HITL approval.

    Amounts here are the agent's preview arithmetic
    (``quantity x unit_price``); the authoritative totals come from
    ``SaleService.create_sale()`` after approval.
    """
    total = (quantity * unit_price).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)
    lines = [
        "Sale ready:",
        f"Customer: {customer_name or 'Walk-in'}",
        f"Product: {product_name} ({variant_sku})",
        f"Quantity: {_qty_str(quantity)} {unit}",
        f"Unit price: Rs. {_money_str(unit_price)}",
        f"Total: Rs. {_money_str(total)}",
    ]
    if payment_kind == PAYMENT_KIND_CASH:
        lines.append(
            f"Payment: Cash — Rs. {_money_str(paid_amount)} received"
            + (f", Rs. {_money_str(due_amount)} due" if due_amount > 0 else "")
        )
    else:
        lines.append(f"Payment: Udhaar — Rs. {_money_str(due_amount)} due")
    lines.append("Do you want me to record this sale?")
    return "\n".join(lines)


def _prep_error(exc: SalePrepError) -> dict[str, Any]:
    """Shape a preparation failure for the agent (never a traceback)."""
    if isinstance(exc, SalePrepAmbiguousError):
        return {
            "status": "ambiguous",
            "code": exc.code,
            "message": str(exc),
            "matches": exc.matches,
        }
    if isinstance(exc, SalePrepNotFoundError):
        return {"status": "not_found", "code": exc.code, "message": str(exc)}
    return {"status": "error", "code": exc.code, "message": str(exc)}


def _sale_error_code(exc: Exception) -> str:
    if isinstance(exc, InsufficientStockError):
        return "insufficient_stock"
    if isinstance(exc, CustomerNotFoundError):
        return "customer_not_found"
    if isinstance(exc, VariantNotFoundError):
        return "variant_not_found"
    if isinstance(
        exc,
        (
            EmptySaleError,
            InvalidSaleItemError,
            InvalidSaleTotalsError,
            DuplicateSaleItemError,
        ),
    ):
        return "invalid_sale"
    if isinstance(exc, ShopNotFoundError):
        return "shop_not_found"
    if isinstance(exc, SaleError):
        return "sale_failed"
    return "sale_failed"


def _sale_error_message(exc: Exception) -> str:
    """User-safe failure text: no stack traces, no SQL, no internal IDs."""
    if isinstance(exc, InsufficientStockError):
        return (
            f"Not enough stock: requested {exc.requested}, "
            f"only {exc.available} available. The sale was not recorded."
        )
    if isinstance(exc, (CustomerNotFoundError, VariantNotFoundError)):
        return "Customer or product not found in your shop. No sale recorded."
    if isinstance(
        exc,
        (
            EmptySaleError,
            InvalidSaleItemError,
            InvalidSaleTotalsError,
            DuplicateSaleItemError,
        ),
    ):
        return f"Invalid sale: {exc}. No sale recorded."
    return "Sale failed and was rolled back. No partial sale was recorded."


async def _find_receipt(
    session: AsyncSession, shop_id: uuid.UUID, key: str
) -> AISaleReceipt | None:
    return (
        await session.execute(
            select(AISaleReceipt).where(
                AISaleReceipt.shop_id == shop_id,
                AISaleReceipt.operation_key == key,
            )
        )
    ).scalar_one_or_none()


async def _sale_summary(
    session: AsyncSession,
    shop_id: uuid.UUID,
    sale: Sale,
) -> dict[str, Any]:
    """Authoritative result figures, read — never recomputed by the AI.

    Queries the sale's first line/variant instead of touching lazy
    relationships, so this is safe on freshly-flushed or re-loaded rows.
    """
    items = (
        (await session.execute(select(SaleItem).where(SaleItem.sale_id == sale.id)))
        .scalars()
        .all()
    )
    first = items[0] if items else None
    variant_sku: str | None = None
    product_name: str | None = None
    unit: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    if first is not None:
        variant = await session.get(ProductVariant, first.variant_id)
        if variant is not None:
            variant_sku = variant.sku
            unit = (
                variant.unit.value
                if hasattr(variant.unit, "value")
                else str(variant.unit)
            )
            product = await session.get(Product, variant.product_id)
            if product is not None:
                product_name = product.name
        quantity = first.quantity
        unit_price = first.unit_price
    customer_name: str | None = None
    if sale.customer_id is not None:
        customer = await session.get(Customer, sale.customer_id)
        if customer is not None:
            customer_name = customer.name
    return {
        "sale_id": str(sale.id),
        "invoice_number": sale.invoice_number,
        "customer_name": customer_name,
        "product_name": product_name,
        "variant_sku": variant_sku,
        "unit": unit,
        "quantity": _qty_str(quantity) if quantity is not None else None,
        "unit_price": _money_str(unit_price) if unit_price is not None else None,
        "items_count": len(items),
        "total": _money_str(sale.total),
        "paid_amount": _money_str(sale.paid_amount),
        "due_amount": _money_str(sale.due_amount),
        "payment_status": sale.status.value
        if hasattr(sale.status, "value")
        else str(sale.status),
    }


def _build_payments(
    kind: str,
    method: PaymentMethod,
    paid_raw: Any,
    expected_total: Decimal,
) -> list[sales_service.PaymentInput]:
    """Map the AI payment contract onto service payment inputs.

    * cash (no amount) → one full payment for the line total.
    * credit → no payments (the service derives due = total).
    * paid_amount → one partial payment (cash only; credit + amount is
      a contradiction and is rejected).
    A zero total needs no payment rows at all — the service completes it
    directly (a zero-amount payment row would violate ``amount > 0``).
    """
    if expected_total <= 0:
        return []
    if paid_raw is not None and _clean(paid_raw):
        if kind == PAYMENT_KIND_CREDIT:
            raise SalePrepError(
                "paid_amount cannot be combined with credit: use cash "
                "with a partial amount, or credit with no amount."
            )
        paid = parse_money(paid_raw, "paid_amount")
        if paid <= 0:
            raise SalePrepError("paid_amount must be greater than 0.")
        if paid > expected_total:
            raise SalePrepError(
                f"paid_amount Rs. {_money_str(paid)} exceeds the sale "
                f"total Rs. {_money_str(expected_total)}."
            )
        return [sales_service.PaymentInput(amount=paid, method=method)]
    if kind == PAYMENT_KIND_CREDIT:
        return []
    return [sales_service.PaymentInput(amount=expected_total, method=method)]


def build_sale_write_tools(
    session: AsyncSession, tenant: TenantContext
) -> list[BaseTool]:
    """Build the tenant-bound sale write tool for one AI request.

    ``shop_id`` is captured from ``tenant`` — it never appears in the
    tool schema, so the model cannot choose another shop. The tool
    flushes but never commits; the caller (``app/api/ai.py``) owns the
    commit, and ``SaleService.create_sale()`` owns all business logic.
    Failures roll back to a savepoint, never the whole session.
    """
    shop_id = tenant.shop_id

    @tool
    async def create_sale(
        idempotency_key: str,
        quantity: str = "1",
        product_name: str | None = None,
        variant_sku: str | None = None,
        variant_id: str | None = None,
        customer_name: str | None = None,
        customer_id: str | None = None,
        unit_price: str | None = None,
        payment_kind: str = "credit",
        payment_method: str = "cash",
        paid_amount: str | None = None,
    ) -> dict:
        """Record a sale in the shop after human approval (HITL-gated).

        Use when the shopkeeper asks to give/record a sale, e.g. 'Ali ko
        3 gaz black lawn udhaar mein de do'. PREPARE first with the read
        tools (resolve customer/product, check stock), show the
        shopkeeper a short preview, then call this tool ONCE per sale.

        Args:
            idempotency_key: REQUIRED stable identity for this sale
                (generate one UUID hex per NEW sale request and reuse it
                for retries of the SAME operation). The same key never
                creates two sales.
            quantity: Amount to sell, in the variant's unit (default 1).
                Never convert units (no gaz-to-meter math).
            product_name: Product to sell, e.g. 'Black Lawn'. Must match
                exactly one product, else ask for clarification.
            variant_sku: Disambiguate when a product has variants.
            variant_id: Advanced alternative to product_name/variant_sku.
            customer_name: Who the sale is for, e.g. 'Ali'. Omit for a
                walk-in sale. Never invent a customer.
            customer_id: Advanced alternative to customer_name.
            unit_price: Per-unit price override. Omit to use the
                variant's catalog selling price.
            payment_kind: 'cash' (cash/nagad, full payment now) or
                'credit' (udhaar/khata, nothing paid now).
            payment_method: cash, card, bank, jazzcash, easypaisa, other.
            paid_amount: Optional partial payment (cash only).

        Returns a structured result: completed (with sale_id, totals),
        ambiguous/not_found (ask the shopkeeper), or error (explain it).
        """
        try:
            key = validate_idempotency_key(idempotency_key)
            kind = parse_payment_kind(payment_kind)
            method = parse_payment_method(payment_method)
            qty = parse_quantity(quantity)
        except SalePrepError as exc:
            return _prep_error(exc)

        # Idempotency BEFORE any mutation: a duplicate resume/retry of
        # the same approved operation returns the original sale.
        existing = await _find_receipt(session, shop_id, key)
        if existing is not None:
            sale = None
            if existing.sale_id is not None:
                candidate = await session.get(Sale, existing.sale_id)
                if candidate is not None and candidate.shop_id == shop_id:
                    sale = candidate
            if sale is None:
                return {
                    "status": "error",
                    "code": "already_processed",
                    "message": (
                        "This sale operation was already processed. "
                        "No new sale was created."
                    ),
                }
            result = await _sale_summary(session, shop_id, sale)
            return {
                "status": "completed",
                "duplicate": True,
                "idempotency_key": key,
                **result,
            }

        try:
            customer = await resolve_customer(
                session,
                shop_id,
                customer_id=customer_id,
                customer_name=customer_name,
            )
            resolved = await resolve_variant(
                session,
                shop_id,
                variant_id=variant_id,
                product_name=product_name,
                variant_sku=variant_sku,
            )
            if unit_price is not None and _clean(unit_price):
                price = parse_money(unit_price, "unit_price")
                if price < 0:
                    raise SalePrepError("unit_price must be >= 0.")
            else:
                # No AI pricing system: fall back to the catalog price.
                price = Decimal(resolved.variant.selling_price)
            expected_total = (qty * price).quantize(
                _MONEY_SCALE, rounding=ROUND_HALF_UP
            )
            payments = _build_payments(kind, method, paid_amount, expected_total)
        except SalePrepError as exc:
            # Validation failed before any write: nothing to undo.
            return _prep_error(exc)

        # ONE controlled transaction for the whole approved operation:
        # sale + stock + payments + ledger + receipt. The savepoint (not
        # a full rollback) discards partial writes on failure while
        # leaving the surrounding session — which the tool does not
        # own — untouched.
        try:
            async with session.begin_nested():
                sale = await sales_service.create_sale(
                    session,
                    shop_id=shop_id,
                    items=[
                        sales_service.SaleItemInput(
                            variant_id=resolved.variant.id,
                            quantity=qty,
                            unit_price=price,
                        )
                    ],
                    customer_id=customer.id if customer is not None else None,
                    payments=payments,
                )
                # Claim the operation identity in the SAME transaction as
                # the sale, so receipt + sale commit atomically. A
                # concurrent winner raises here instead of duplicating.
                session.add(
                    AISaleReceipt(shop_id=shop_id, operation_key=key, sale_id=sale.id)
                )
                await session.flush()
        except IntegrityError:
            # Most likely a concurrent duplicate won the receipt race or
            # an invoice collision: re-check before giving up. The
            # savepoint already discarded this attempt's partial writes.
            retry = await _find_receipt(session, shop_id, key)
            if retry is not None and retry.sale_id is not None:
                winner = await session.get(Sale, retry.sale_id)
                if winner is not None and winner.shop_id == shop_id:
                    summary = await _sale_summary(session, shop_id, winner)
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
                    "No sale was recorded by this attempt — it is safe "
                    "to retry the same operation."
                ),
            }
        except (SaleError, InsufficientStockError) as exc:
            return {
                "status": "error",
                "code": _sale_error_code(exc),
                "message": _sale_error_message(exc),
            }
        except Exception:  # noqa: BLE001 — boundary maps to clean errors
            return {
                "status": "error",
                "code": "sale_failed",
                "message": (
                    "Sale failed and was rolled back. No partial sale was recorded."
                ),
            }

        # Flush only — the API route owns the commit, so the whole
        # approved operation commits or rolls back as one unit.
        await session.flush()
        summary = await _sale_summary(session, shop_id, sale)
        return {
            "status": "completed",
            "duplicate": False,
            "idempotency_key": key,
            "payment_kind": kind,
            **summary,
        }

    return [create_sale]  # type: ignore[list-item]


def get_sale_write_tool_by_name(
    session: AsyncSession, tenant: TenantContext, name: str
) -> BaseTool | None:
    """Return the bound write tool by name (tests/convenience)."""
    for t in build_sale_write_tools(session, tenant):
        if t.name == name:
            return t
    return None


def assert_sale_write_registry_is_minimal(tools: list[BaseTool]) -> None:
    """Raise unless ``tools`` is exactly the single sanctioned mutation.

    Step 3 allows ONE AI mutation (``create_sale``). Any second write
    tool — purchases, payments, expenses, inventory, CRUD — fails here.
    """
    names = sorted(t.name for t in tools)
    if names != sorted(WRITE_TOOL_NAMES):
        raise AssertionError(
            f"AI write registry must be exactly {sorted(WRITE_TOOL_NAMES)}; "
            f"got {names}. Step 3 allows one mutation only: sale creation."
        )


__all__ = [
    "CREATE_SALE_TOOL_NAME",
    "PAYMENT_KIND_CASH",
    "PAYMENT_KIND_CREDIT",
    "WRITE_TOOL_NAMES",
    "ResolvedVariant",
    "SalePrepAmbiguousError",
    "SalePrepError",
    "SalePrepNotFoundError",
    "assert_sale_write_registry_is_minimal",
    "build_sale_preview",
    "build_sale_write_tools",
    "get_sale_write_tool_by_name",
    "parse_money",
    "parse_payment_kind",
    "parse_payment_method",
    "parse_quantity",
    "parse_uuid_arg",
    "resolve_customer",
    "resolve_variant",
    "validate_idempotency_key",
]

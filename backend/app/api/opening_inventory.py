"""Opening-inventory endpoints (API layer).

Exposes the two operations `app.services.opening_inventory` already
implements - a read-only preview and an atomic, idempotent post - plus the
tenant-scoped reads over the immutable batches:

* ``POST /inventory/opening/preview`` - validate proposed lines, return
  line-level errors/warnings, line values, estimated-cost flags and the
  projected post-opening weighted-average cost. Writes nothing.
* ``POST /inventory/opening`` - post a batch. Requires an idempotency key;
  the service re-validates everything (a preview is never an authorization
  token). The route owns the transaction: commit on success, rollback on
  any failure, and cache eviction only after the commit succeeded.
* ``GET /inventory/opening`` - list this shop's batches (paginated).
* ``GET /inventory/opening/{batch_id}`` - one batch with its entries,
  variant details, cost status/values and the linked stock movements.

Route order is deliberate: literal paths are registered before the
``/{batch_id}`` dynamic route, and `app.main` includes this router *before*
the `/inventory` router so ``/inventory/opening`` cannot be swallowed by
``/inventory/{variant_id}``.

Tenant isolation follows `app.api.dependencies`: `shop_id` comes from the
verified Clerk identity only. Static routes never trust a client-supplied
tenant id.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUserDep, DbSession, ShopId
from app.cache import service as cache_service
from app.models.inventory import InventoryMovement
from app.models.opening_inventory import (
    OpeningInventoryBatch,
    OpeningInventoryEntry,
    OpeningInventorySource,
)
from app.models.product import Product, ProductVariant
from app.schemas.opening_inventory import (
    OpeningInventoryBatchDetailResponse,
    OpeningInventoryBatchResponse,
    OpeningInventoryEntryResponse,
    OpeningInventoryLineRequest,
    OpeningInventoryMovementResponse,
    OpeningInventoryPreviewResponse,
    PostOpeningInventoryRequest,
    PreviewOpeningInventoryRequest,
)
from app.services import opening_inventory as opening_service
from app.services.opening_inventory import (
    OpeningInventoryError,
    OpeningInventoryIdempotencyConflictError,
    OpeningInventoryLineInput,
    OpeningInventoryPreview,
    ShopNotFoundError,
    VariantAlreadyHasOpeningError,
    VariantNotFoundError,
)

logger = logging.getLogger("app.api.opening_inventory")

router = APIRouter(prefix="/inventory/opening", tags=["opening-inventory"])


def _opening_error(exc: OpeningInventoryError) -> HTTPException:
    """Map a domain error to a safe, consistent HTTP response.

    Missing references are 404s; state conflicts (key already used with a
    different payload, variant already opened) are 409s; everything else in
    the domain hierarchy is an actionable validation error (422).
    """

    if isinstance(exc, (ShopNotFoundError, VariantNotFoundError)):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(
        exc, (VariantAlreadyHasOpeningError, OpeningInventoryIdempotencyConflictError)
    ):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(status_code=code, detail=str(exc))


def _server_error() -> HTTPException:
    """Safe 500 for an unexpected failure after the transaction was rolled back."""

    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Opening inventory could not be posted; no changes were written.",
    )


def _is_data_exception(exc: DBAPIError) -> bool:
    """True for PostgreSQL SQLSTATE class ``22`` (data exception) errors.

    The asyncpg dialect surfaces overflow/truncation as a *generic*
    ``DBAPIError`` rather than ``sqlalchemy.exc.DataError``, but the wrapped
    error still carries the SQLSTATE (``22003`` numeric overflow, ``22001``
    string truncation). Those are client-data problems (e.g. a value past
    the NUMERIC(14,x) capacity), so they get an actionable 422 instead of a
    misleading 500.
    """

    orig = exc.orig
    state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return isinstance(state, str) and state.startswith("22")


def _to_line_input(line: OpeningInventoryLineRequest) -> OpeningInventoryLineInput:
    # The service normalises every field from scratch and reports line-level
    # errors; the schema stays permissive on purpose (see the schema module).
    return OpeningInventoryLineInput(
        variant_id=line.variant_id,
        quantity=line.quantity,  # type: ignore[arg-type]
        unit_cost=line.unit_cost,  # type: ignore[arg-type]
        cost_status=line.cost_status,
    )


async def _batch_detail(
    db: AsyncSession, *, shop_id: UUID, batch_id: UUID
) -> OpeningInventoryBatchDetailResponse | None:
    """Load one batch and its entries with variant details and movements.

    Explicit tenant-scoped joins (no async lazy loading): the entry is
    joined to its variant/product for display data, and outer-joined to the
    stock movement it produced so the ledger link survives even if that
    movement row is gone (`movement` is simply null).
    """

    batch = (
        await db.execute(
            select(OpeningInventoryBatch).where(
                OpeningInventoryBatch.id == batch_id,
                OpeningInventoryBatch.shop_id == shop_id,
            )
        )
    ).scalar_one_or_none()
    if batch is None:
        return None

    rows = (
        await db.execute(
            select(
                OpeningInventoryEntry, ProductVariant, Product.name, InventoryMovement
            )
            .join(
                ProductVariant,
                and_(
                    ProductVariant.id == OpeningInventoryEntry.variant_id,
                    ProductVariant.shop_id == OpeningInventoryEntry.shop_id,
                ),
            )
            .join(Product, Product.id == ProductVariant.product_id)
            .outerjoin(
                InventoryMovement,
                InventoryMovement.id == OpeningInventoryEntry.movement_id,
            )
            .where(
                OpeningInventoryEntry.batch_id == batch_id,
                OpeningInventoryEntry.shop_id == shop_id,
            )
            .order_by(ProductVariant.sku.asc(), OpeningInventoryEntry.id.asc())
        )
    ).all()

    entries = [
        OpeningInventoryEntryResponse(
            id=entry.id,
            variant_id=entry.variant_id,
            sku=variant.sku,
            product_name=product_name,
            unit=entry.unit,
            quantity=entry.quantity,
            unit_cost=entry.unit_cost,
            cost_status=entry.cost_status,
            value=entry.value,
            created_at=entry.created_at,
            movement=(
                OpeningInventoryMovementResponse(
                    id=movement.id,
                    movement_type=movement.movement_type,
                    quantity=movement.quantity,
                    unit_cost=movement.unit_cost,
                    reference_type=movement.reference_type,
                    reference_id=movement.reference_id,
                    created_at=movement.created_at,
                )
                if movement is not None
                else None
            ),
        )
        for entry, variant, product_name, movement in rows
    ]

    return OpeningInventoryBatchDetailResponse(
        id=batch.id,
        shop_id=batch.shop_id,
        source=batch.source,
        notes=batch.notes,
        entry_count=batch.entry_count,
        estimated_entry_count=batch.estimated_entry_count,
        total_value=batch.total_value,
        created_by_user_id=batch.created_by_user_id,
        created_at=batch.created_at,
        entries=entries,
    )


async def _post_once(
    db: AsyncSession,
    *,
    shop_id: UUID,
    body: PostOpeningInventoryRequest,
    lines: list[OpeningInventoryLineInput],
    user_id: UUID,
) -> OpeningInventoryBatch:
    """One posting attempt: service call plus the route-owned commit."""

    batch = await opening_service.post_opening_inventory(
        db,
        shop_id=shop_id,
        idempotency_key=body.idempotency_key,
        lines=lines,
        source=body.source,
        notes=body.notes,
        created_by_user_id=user_id,
    )
    await db.commit()
    return batch


# --------------------------------------------------------------------------
# Static routes first: /preview must never be read as a batch id.
# --------------------------------------------------------------------------


@router.post("/preview", response_model=OpeningInventoryPreviewResponse)
async def preview_opening_inventory(
    shop_id: ShopId,
    db: DbSession,
    body: PreviewOpeningInventoryRequest,
) -> OpeningInventoryPreviewResponse:
    """Validate proposed opening stock without writing anything.

    Line-level problems come back in each line's `errors` (unpostable),
    informational situations in `warnings`. `value` and the projected
    post-opening weighted-average cost are only populated for error-free
    lines. This endpoint opens no transaction beyond the request session's
    read and never commits or rolls back (it writes nothing).
    """

    try:
        preview: OpeningInventoryPreview = (
            await opening_service.preview_opening_inventory(
                db,
                shop_id=shop_id,
                lines=[_to_line_input(line) for line in body.lines],
            )
        )
    except OpeningInventoryError as exc:
        raise _opening_error(exc) from exc

    return OpeningInventoryPreviewResponse.model_validate(preview)


@router.post(
    "",
    response_model=OpeningInventoryBatchDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_opening_inventory(
    shop_id: ShopId,
    current_user: CurrentUserDep,
    db: DbSession,
    body: PostOpeningInventoryRequest,
) -> OpeningInventoryBatchDetailResponse:
    """Post an opening inventory batch (batch + entries + stock + ledger).

    The transaction is owned here: commit on success, rollback on every
    failure - a failure never reports success and never leaves a partial
    batch, movement or ledger line. The idempotency key is required:
    resubmitting the same key and payload returns the committed batch (no
    second stock receipt, no second ledger posting); reusing a key with a
    different payload is a 409.
    """

    lines = [_to_line_input(line) for line in body.lines]
    user_id = current_user.id

    try:
        batch = await _post_once(
            db, shop_id=shop_id, body=body, lines=lines, user_id=user_id
        )
    except OpeningInventoryError as exc:
        await db.rollback()
        raise _opening_error(exc) from exc
    except IntegrityError:
        # The unique-constraint backstop fired: a concurrent writer of the
        # same key slipped past the per-shop lock. Roll back, retry once -
        # the retry resolves the key to the winner's committed batch.
        await db.rollback()
        try:
            batch = await _post_once(
                db, shop_id=shop_id, body=body, lines=lines, user_id=user_id
            )
        except OpeningInventoryError as retry_exc:
            await db.rollback()
            raise _opening_error(retry_exc) from retry_exc
        except Exception as retry_exc:
            await db.rollback()
            logger.exception(
                "opening inventory posting failed on retry; transaction rolled back"
            )
            raise _server_error() from retry_exc
    except DBAPIError as exc:
        # e.g. a value past the column capacity (NUMERIC(14,3)/(14,2)).
        # Known edge debt, but never reported as success: roll back and
        # answer with an actionable, safe error for data exceptions;
        # anything else is an unexpected failure.
        await db.rollback()
        if _is_data_exception(exc):
            logger.warning("opening inventory value out of database range: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "One or more values exceed the supported numeric range. "
                    "Split the line into smaller quantities or costs."
                ),
            ) from exc
        logger.exception("opening inventory posting failed; transaction rolled back")
        raise _server_error() from exc
    except Exception as exc:
        await db.rollback()
        logger.exception("opening inventory posting failed; transaction rolled back")
        raise _server_error() from exc

    # Evict cached stock/dashboard/financial reads only after the commit
    # succeeded, so a rolled-back post never invalidates as if it had landed.
    await cache_service.after_opening_inventory_committed(
        shop_id, [line.variant_id for line in lines]
    )

    detail = await _batch_detail(db, shop_id=shop_id, batch_id=batch.id)
    if detail is None:  # pragma: no cover - committed batch must be readable
        raise _server_error()
    return detail


@router.get("", response_model=list[OpeningInventoryBatchResponse])
async def list_opening_inventory_batches(
    shop_id: ShopId,
    db: DbSession,
    source: OpeningInventorySource | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OpeningInventoryBatchResponse]:
    """List this shop's opening-inventory batches, newest first."""

    stmt = select(OpeningInventoryBatch).where(OpeningInventoryBatch.shop_id == shop_id)
    if source is not None:
        stmt = stmt.where(OpeningInventoryBatch.source == source)
    stmt = stmt.order_by(
        OpeningInventoryBatch.created_at.desc(), OpeningInventoryBatch.id.desc()
    )
    stmt = stmt.offset(offset).limit(limit)
    batches = (await db.execute(stmt)).scalars().all()
    return [OpeningInventoryBatchResponse.model_validate(batch) for batch in batches]


@router.get("/{batch_id}", response_model=OpeningInventoryBatchDetailResponse)
async def get_opening_inventory_batch(
    batch_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> OpeningInventoryBatchDetailResponse:
    """Retrieve one batch with its entries (another shop's batch is a 404)."""

    detail = await _batch_detail(db, shop_id=shop_id, batch_id=batch_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opening inventory batch not found",
        )
    return detail

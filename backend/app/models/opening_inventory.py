"""Opening inventory domain: one-time starting stock for a shop.

When a shop starts using KapraOS it already has goods on the shelf. Those
goods are not a purchase (no supplier, no payable) - they are the point at
which the shop's books start. This module records that event exactly once
per variant:

* `OpeningInventoryBatch` - one posting run (a manual entry form or an
  imported CSV/XLSX file), keyed by an idempotency key so a retried
  submission never double-posts.
* `OpeningInventoryEntry` - one variant's opening line inside the batch,
  carrying the quantity, a *snapshot* of the variant's unit, the cost
  status (`known` / `estimated`) and a strictly positive unit cost.

Costing policy (deliberate, V1): the pooled moving-average model cannot
represent stock with no basis, so every posted opening line must have a
strictly positive known or estimated unit cost. `unknown` exists only as an
import/preview resolution state - persisted entries are `known` or
`estimated`, and an estimated cost stays visibly identified as one.
`UNIQUE(shop_id, variant_id)` is the database-level guard behind "a variant
may have only one opening entry ever".

Posting lives in `app.services.opening_inventory`; the tables are written
there and nowhere else. Batches are immutable in V1: there is intentionally
no void/edit workflow, because reversing an opening balance means unwinding
pooled WAC state that later receipts have already moved (`db_arch.md`
sections 13-14, 29).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.inventory import InventoryMovement
    from app.models.product import ProductVariant
    from app.models.shop import Shop
    from app.models.user import User


class OpeningInventorySource(str, Enum):
    """How a batch was entered: typed by hand or imported from a file."""

    MANUAL = "manual"
    IMPORT = "import"


class OpeningInventoryCostStatus(str, Enum):
    """Whether an entry's cost is factual (`known`) or a priestimate.

    `unknown` is deliberately *not* a member: it exists only as a
    preview/import resolution state, and an unresolved line can never be
    persisted - the service rejects it before posting.
    """

    KNOWN = "known"
    ESTIMATED = "estimated"


class OpeningInventoryBatch(Base, UUIDMixin, TimestampMixin):
    """One opening-inventory posting run (manual or imported)."""

    __tablename__ = "opening_inventory_batches"

    __table_args__ = (
        # Supports `OpeningInventoryEntry`'s composite foreign key
        # (batch_id, shop_id), so a line can never attach to another shop's
        # batch - the same supporting-unique pattern as `purchases`.
        UniqueConstraint(
            "id", "shop_id", name="uq_opening_inventory_batches_id_shop_id"
        ),
        # Idempotency: resubmitting the same key (a network retry, a doubled
        # click) must return the already-posted batch instead of creating a
        # second one. The service checks first; this constraint is the final
        # guard under concurrency.
        UniqueConstraint(
            "shop_id",
            "idempotency_key",
            name="uq_opening_inventory_batches_shop_idempotency",
        ),
        Index(
            "ix_opening_inventory_batches_shop_created_at",
            "shop_id",
            "created_at",
        ),
        CheckConstraint(
            "length(trim(idempotency_key)) > 0",
            name="ck_opening_inventory_batches_key_not_blank",
        ),
        CheckConstraint(
            "entry_count > 0",
            name="ck_opening_inventory_batches_entry_count_positive",
        ),
        # Strict costing policy at the database level: a posted batch always
        # carries a positive value, because every line must have a positive
        # cost (see the entries table). A "zero opening balance" is not a
        # thing this flow can represent.
        CheckConstraint(
            "total_value > 0",
            name="ck_opening_inventory_batches_total_value_positive",
        ),
        CheckConstraint(
            "estimated_entry_count >= 0 AND estimated_entry_count <= entry_count",
            name="ck_opening_inventory_batches_estimated_count_valid",
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stable caller-supplied key (UUID hex from the frontend). Scoped per
    # shop by the unique constraint above.
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)

    source: Mapped[OpeningInventorySource] = mapped_column(
        SQLEnum(
            OpeningInventorySource,
            name="opening_inventory_source",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    # Import provenance, when the batch came from a file. Nullable: manual
    # batches have no file.
    reference_filename: Mapped[str | None] = mapped_column(String(255))
    file_hash: Mapped[str | None] = mapped_column(String(64))

    notes: Mapped[str | None] = mapped_column(String(500))

    # Document totals, computed server-side: number of posted lines, how many
    # of them used an *estimated* cost, and the total posted value that was
    # debited to Inventory / credited to Opening Balance Equity.
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_entry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    total_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # Audit-only reference to the authenticated user who posted the batch.
    # Deliberately a plain FK (SET NULL) rather than a composite tenant FK:
    # it identifies a person, it does not move stock or money, and the batch
    # must survive user removal for the audit trail to stay honest.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    shop: Mapped["Shop"] = relationship(
        "Shop",
        back_populates="opening_inventory_batches",
        overlaps="opening_inventory_batches,shop",
    )

    created_by_user: Mapped["User | None"] = relationship("User")

    entries: Mapped[list["OpeningInventoryEntry"]] = relationship(
        "OpeningInventoryEntry",
        back_populates="batch",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"OpeningInventoryBatch(id={self.id!r}, source={self.source!r}, "
            f"total_value={self.total_value!r})"
        )


class OpeningInventoryEntry(Base, UUIDMixin):
    """One variant's opening line inside an `OpeningInventoryBatch`.

    No `TimestampMixin`: like other line tables, a line has no independent
    audit trail - its lifetime is bound to its parent batch. Implicit in the
    schema:

    * `quantity > 0` and `unit_cost > 0` - no zero-cost or negative opening
      lines can ever reach the database;
    * `UNIQUE(shop_id, variant_id)` - one opening entry per variant per shop,
      ever (rejected duplicates are a feature, not an accident);
    * `UNIQUE(batch_id, variant_id)` - one line per variant per batch, so a
      repeated identifier inside one submission is a validation error;
    * `value = round(quantity * unit_cost, 2)` - the line total is checked
      math, not a free-form number.
    """

    __tablename__ = "opening_inventory_entries"

    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "variant_id",
            name="uq_opening_inventory_entries_batch_variant",
        ),
        # "A variant may have only one opening entry ever." This is the
        # database-level half; the service raises a typed error first.
        UniqueConstraint(
            "shop_id",
            "variant_id",
            name="uq_opening_inventory_entries_shop_variant",
        ),
        # Tenant guards: a line can never claim Shop A while pointing at
        # Shop B's batch or variant. `product_variants` already carries the
        # supporting UNIQUE(id, shop_id) used by `inventory_movements`; the
        # batch's supporting unique is declared above.
        ForeignKeyConstraint(
            ["batch_id", "shop_id"],
            ["opening_inventory_batches.id", "opening_inventory_batches.shop_id"],
            name="fk_opening_inventory_entries_batch_same_shop",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["variant_id", "shop_id"],
            ["product_variants.id", "product_variants.shop_id"],
            name="fk_opening_inventory_entries_variant_same_shop",
            ondelete="CASCADE",
        ),
        Index("ix_opening_inventory_entries_shop_created_at", "shop_id", "created_at"),
        CheckConstraint(
            "quantity > 0",
            name="ck_opening_inventory_entries_quantity_positive",
        ),
        CheckConstraint(
            "unit_cost > 0",
            name="ck_opening_inventory_entries_unit_cost_positive",
        ),
        CheckConstraint(
            "value > 0",
            name="ck_opening_inventory_entries_value_positive",
        ),
        # Mirrors `PurchaseItem`'s checked line math (round half away from
        # zero, as Postgres round() does for NUMERIC).
        CheckConstraint(
            "value = round(quantity * unit_cost, 2)",
            name="ck_opening_inventory_entries_value_matches_line",
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Fabric quantities need three decimals (3.500 m, 12.250 yd).
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)

    # Snapshot of `ProductVariant.unit` at posting time (same string column
    # shape as `Product.unit`). A snapshot, not a live join: what the unit
    # was when the balance was entered is part of the record.
    unit: Mapped[str] = mapped_column(String(20), nullable=False)

    # `known` or `estimated` - never `unknown` once persisted. Estimated
    # costs stay visibly identified for reporting/audit.
    cost_status: Mapped[OpeningInventoryCostStatus] = mapped_column(
        SQLEnum(
            OpeningInventoryCostStatus,
            name="opening_inventory_cost_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # Persisted document value: round(quantity * unit_cost, 2), checked by
    # the constraint above.
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # The inventory ledger line this entry created. NULL only if that
    # movement row is gone (variant/shop cleanup) - the opening record stays.
    movement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_movements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    batch: Mapped["OpeningInventoryBatch"] = relationship(
        "OpeningInventoryBatch",
        back_populates="entries",
    )

    variant: Mapped["ProductVariant"] = relationship(
        "ProductVariant",
        # `shop_id` is copied by three relationships (this one, `batch`, and
        # `batch.entries`) because it is part of both composite foreign keys.
        # The service sets it explicitly; the overlaps declaration tells the
        # ORM that the shared column is intentional.
        overlaps="batch,entries",
    )

    movement: Mapped["InventoryMovement | None"] = relationship(
        "InventoryMovement"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"OpeningInventoryEntry(batch_id={self.batch_id!r}, "
            f"variant_id={self.variant_id!r}, quantity={self.quantity!r}, "
            f"cost_status={self.cost_status!r})"
        )

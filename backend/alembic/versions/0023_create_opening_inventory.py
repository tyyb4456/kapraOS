"""create opening inventory

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-18 00:00:00.000000

Creates the opening-inventory record:

* `opening_inventory_batches` - one posting run (manual entry or imported
  CSV/XLSX), keyed by `(shop_id, idempotency_key)` so retries never
  double-post. Document totals (`entry_count`, `estimated_entry_count`,
  `total_value`) are computed server-side; `total_value > 0` encodes the
  strict costing policy at the database level.
* `opening_inventory_entries` - one variant line per batch:
  `quantity > 0`, `unit_cost > 0`, `value = round(quantity * unit_cost, 2)`,
  a snapshot of the variant's unit, a cost status (`known` / `estimated` -
  `unknown` is only a preview/import resolution state and is not a persisted
  value), and the `inventory_movements` row it created.
  `UNIQUE(shop_id, variant_id)` enforces one opening entry per variant per
  shop, ever.

Tenant isolation follows the established composite-FK pattern:
`(batch_id, shop_id)` and `(variant_id, shop_id)` are enforced by Postgres
against supporting UNIQUE constraints, so no cross-shop reference can be
stored even by a buggy writer. The `opening` enum value this feature uses
is added by migration 0022, one migration earlier, exactly because a new
enum value cannot be used in the transaction that created it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same pattern as migration 0003: declare each ENUM once with
# `create_type=False` so it is never auto-created/dropped as a side effect
# of table DDL, then create/drop the types explicitly.
opening_inventory_source_enum = postgresql.ENUM(
    "manual",
    "import",
    name="opening_inventory_source",
    create_type=False,
)

opening_inventory_cost_status_enum = postgresql.ENUM(
    "known",
    "estimated",
    name="opening_inventory_cost_status",
    create_type=False,
)


def upgrade() -> None:
    opening_inventory_source_enum.create(op.get_bind(), checkfirst=True)
    opening_inventory_cost_status_enum.create(op.get_bind(), checkfirst=True)

    # --- opening_inventory_batches: one posting run -----------------------
    op.create_table(
        "opening_inventory_batches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("source", opening_inventory_source_enum, nullable=False),
        sa.Column("reference_filename", sa.String(length=255), nullable=True),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column(
            "estimated_entry_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("total_value", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        # Audit-only creator reference (plain FK, SET NULL): identifies a
        # person, moves no stock/money, and the batch must survive user
        # removal for the audit trail to stay honest.
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        # Supports `opening_inventory_entries`' composite FK (batch_id,
        # shop_id) - the tenant guard that a line cannot attach to another
        # shop's batch.
        sa.UniqueConstraint(
            "id", "shop_id", name="uq_opening_inventory_batches_id_shop_id"
        ),
        # Idempotency: one batch per (shop, key). Concurrent duplicates race
        # here; the loser re-reads the winner instead of re-posting stock.
        sa.UniqueConstraint(
            "shop_id",
            "idempotency_key",
            name="uq_opening_inventory_batches_shop_idempotency",
        ),
        sa.CheckConstraint(
            "length(trim(idempotency_key)) > 0",
            name="ck_opening_inventory_batches_key_not_blank",
        ),
        sa.CheckConstraint(
            "entry_count > 0",
            name="ck_opening_inventory_batches_entry_count_positive",
        ),
        # Strict costing policy: every posted line has a positive cost, so
        # a posted batch always carries a positive value.
        sa.CheckConstraint(
            "total_value > 0",
            name="ck_opening_inventory_batches_total_value_positive",
        ),
        sa.CheckConstraint(
            "estimated_entry_count >= 0 AND estimated_entry_count <= entry_count",
            name="ck_opening_inventory_batches_estimated_count_valid",
        ),
    )
    op.create_index(
        "ix_opening_inventory_batches_shop_id",
        "opening_inventory_batches",
        ["shop_id"],
        unique=False,
    )
    op.create_index(
        "ix_opening_inventory_batches_shop_created_at",
        "opening_inventory_batches",
        ["shop_id", "created_at"],
        unique=False,
    )

    # --- opening_inventory_entries: one variant line per batch ------------
    op.create_table(
        "opening_inventory_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("cost_status", opening_inventory_cost_status_enum, nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("value", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("movement_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        # The movement this entry created; SET NULL so stock-ledger cleanup
        # (variant/shop deletion) never erases the opening record itself.
        sa.ForeignKeyConstraint(
            ["movement_id"], ["inventory_movements.id"], ondelete="SET NULL"
        ),
        # Tenant guards, enforced by Postgres against the supporting unique
        # constraints on the two parent tables.
        sa.ForeignKeyConstraint(
            ["batch_id", "shop_id"],
            ["opening_inventory_batches.id", "opening_inventory_batches.shop_id"],
            name="fk_opening_inventory_entries_batch_same_shop",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id", "shop_id"],
            ["product_variants.id", "product_variants.shop_id"],
            name="fk_opening_inventory_entries_variant_same_shop",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        # One line per variant per batch (a repeated variant in one
        # submission is a validation error, handled upstream).
        sa.UniqueConstraint(
            "batch_id",
            "variant_id",
            name="uq_opening_inventory_entries_batch_variant",
        ),
        # "A variant may have only one opening entry ever" - the database
        # half of the rule; the service raises a typed error first.
        sa.UniqueConstraint(
            "shop_id",
            "variant_id",
            name="uq_opening_inventory_entries_shop_variant",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_opening_inventory_entries_quantity_positive",
        ),
        sa.CheckConstraint(
            "unit_cost > 0",
            name="ck_opening_inventory_entries_unit_cost_positive",
        ),
        sa.CheckConstraint(
            "value > 0",
            name="ck_opening_inventory_entries_value_positive",
        ),
        # Checked line math, mirroring `purchase_items` (Postgres round()
        # on NUMERIC rounds half away from zero, as the service does).
        sa.CheckConstraint(
            "value = round(quantity * unit_cost, 2)",
            name="ck_opening_inventory_entries_value_matches_line",
        ),
    )
    op.create_index(
        "ix_opening_inventory_entries_batch_id",
        "opening_inventory_entries",
        ["batch_id"],
        unique=False,
    )
    op.create_index(
        "ix_opening_inventory_entries_shop_id",
        "opening_inventory_entries",
        ["shop_id"],
        unique=False,
    )
    op.create_index(
        "ix_opening_inventory_entries_variant_id",
        "opening_inventory_entries",
        ["variant_id"],
        unique=False,
    )
    op.create_index(
        "ix_opening_inventory_entries_movement_id",
        "opening_inventory_entries",
        ["movement_id"],
        unique=False,
    )
    op.create_index(
        "ix_opening_inventory_entries_shop_created_at",
        "opening_inventory_entries",
        ["shop_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_opening_inventory_entries_shop_created_at",
        table_name="opening_inventory_entries",
    )
    op.drop_index(
        "ix_opening_inventory_entries_movement_id",
        table_name="opening_inventory_entries",
    )
    op.drop_index(
        "ix_opening_inventory_entries_variant_id",
        table_name="opening_inventory_entries",
    )
    op.drop_index(
        "ix_opening_inventory_entries_shop_id",
        table_name="opening_inventory_entries",
    )
    op.drop_index(
        "ix_opening_inventory_entries_batch_id",
        table_name="opening_inventory_entries",
    )
    op.drop_table("opening_inventory_entries")

    op.drop_index(
        "ix_opening_inventory_batches_shop_created_at",
        table_name="opening_inventory_batches",
    )
    op.drop_index(
        "ix_opening_inventory_batches_shop_id",
        table_name="opening_inventory_batches",
    )
    op.drop_table("opening_inventory_batches")

    opening_inventory_cost_status_enum.drop(op.get_bind(), checkfirst=True)
    opening_inventory_source_enum.drop(op.get_bind(), checkfirst=True)

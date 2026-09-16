"""create accounting domain

Step 8 (general ledger / double-entry accounting) adds the two tables the
architecture calls for (`db_arch.md` sections 23-24):

1. `accounts` - a shop's chart of accounts. `(id, shop_id)` is UNIQUE so
   `ledger_entries` can reference it with a composite tenant-safe foreign key,
   and `(shop_id, code)` is UNIQUE so account codes are per-shop only. A
   partial-free plain unique is correct here: every account has a code.

2. `ledger_entries` - one side of a double-entry posting. Check constraints
   enforce the core invariants in the database:
     * debit/credit are non-negative;
     * exactly one side is populated (`NOT (debit > 0 AND credit > 0)`);
     * the line is non-zero (`debit + credit > 0`).

   The composite foreign key `(account_id, shop_id)` -> `accounts(id, shop_id)`
   makes a cross-tenant posting impossible at the database level, and the
   unique constraint `(shop_id, reference_type, reference_id, account_id)`
   makes duplicate posting of the same business event impossible while still
   allowing one event to post to several accounts.

No balance tables are created: balances are always derived from the ledger.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-16 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNT_TYPE = postgresql.ENUM(
    "asset",
    "liability",
    "equity",
    "revenue",
    "expense",
    name="account_type",
    create_type=False,
)


def upgrade() -> None:
    ACCOUNT_TYPE.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("account_type", ACCOUNT_TYPE, nullable=False),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
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
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name="fk_accounts_shop_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("id", "shop_id", name="uq_accounts_id_shop_id"),
        sa.UniqueConstraint("shop_id", "code", name="uq_accounts_shop_code"),
        sa.CheckConstraint("length(trim(code)) > 0", name="ck_accounts_code_not_blank"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_accounts_name_not_blank"),
    )
    op.create_index("ix_accounts_shop_id", "accounts", ["shop_id"])
    op.create_index(
        "ix_accounts_shop_type", "accounts", ["shop_id", "account_type"]
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "debit",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "credit",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("reference_type", sa.String(length=50), nullable=False),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name="fk_ledger_entries_shop_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "shop_id"],
            ["accounts.id", "accounts.shop_id"],
            name="fk_ledger_entries_account_same_shop",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ledger_entries"),
        sa.UniqueConstraint(
            "shop_id",
            "reference_type",
            "reference_id",
            "account_id",
            name="uq_ledger_entries_reference_account",
        ),
        sa.CheckConstraint(
            "debit >= 0", name="ck_ledger_entries_debit_non_negative"
        ),
        sa.CheckConstraint(
            "credit >= 0", name="ck_ledger_entries_credit_non_negative"
        ),
        sa.CheckConstraint(
            "NOT (debit > 0 AND credit > 0)",
            name="ck_ledger_entries_single_sided",
        ),
        sa.CheckConstraint(
            "debit + credit > 0", name="ck_ledger_entries_non_zero"
        ),
    )
    op.create_index("ix_ledger_entries_shop_id", "ledger_entries", ["shop_id"])
    op.create_index(
        "ix_ledger_entries_account_id", "ledger_entries", ["account_id"]
    )
    op.create_index(
        "ix_ledger_entries_shop_account_created_at",
        "ledger_entries",
        ["shop_id", "account_id", "created_at"],
    )
    op.create_index(
        "ix_ledger_entries_shop_reference",
        "ledger_entries",
        ["shop_id", "reference_type", "reference_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ledger_entries_shop_reference", table_name="ledger_entries"
    )
    op.drop_index(
        "ix_ledger_entries_shop_account_created_at", table_name="ledger_entries"
    )
    op.drop_index("ix_ledger_entries_account_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_shop_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")

    op.drop_index("ix_accounts_shop_type", table_name="accounts")
    op.drop_index("ix_accounts_shop_id", table_name="accounts")
    op.drop_table("accounts")

    ACCOUNT_TYPE.drop(op.get_bind(), checkfirst=True)
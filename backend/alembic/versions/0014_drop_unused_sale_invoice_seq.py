"""drop unused sale_invoice_seq (allocation is sales-table based)

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-25 00:00:00.000000

0013 added `shops.sale_invoice_seq` plus a backfill of NULL invoice numbers.
The backfill is kept, but the counter itself proved fragile: any deployment
where the code ran before `alembic upgrade head` 500'd on
`column shops.sale_invoice_seq does not exist` (even `_get_shop` SELECTs the
new column via the ORM model).

Allocation is now derived from `sales.invoice_number` max suffix per shop
under a `SELECT id ... FOR UPDATE` shop lock, so no extra shops column is
needed. This migration removes the column if it exists, making the schema
match the model again and letting the code run on DBs at either 0012 or
0013.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE shops DROP COLUMN IF EXISTS sale_invoice_seq"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "ALTER TABLE shops ADD COLUMN IF NOT EXISTS sale_invoice_seq "
            "INTEGER NOT NULL DEFAULT 1"
        )
    )

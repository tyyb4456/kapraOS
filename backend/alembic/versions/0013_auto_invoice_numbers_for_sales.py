"""auto invoice numbers for sales

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-25 00:00:00.000000

Every sale must carry an invoice number. Previously `sales.invoice_number`
was nullable and the POS never sent one, so the history showed "-" for every
row. This migration:

1. Adds `shops.sale_invoice_seq` - the per-shop counter the sales service
   allocates `INV-000001`-style numbers from under a row lock.
2. Backfills existing NULL invoice numbers sequentially per shop ordered by
   (created_at, id), starting after any existing `INV-<digits>` numbers so a
   hand-typed number is never clobbered.
3. Bumps each shop's counter past the highest allocated number.
"""

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INV_RE = re.compile(r"^INV-(\d+)$")


def _next_candidate(seq: int) -> str:
    return f"INV-{seq:06d}"


def upgrade() -> None:
    op.add_column(
        "shops",
        sa.Column(
            "sale_invoice_seq",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    conn = op.get_bind()

    shop_rows = list(
        conn.execute(sa.text("SELECT id FROM shops")).fetchall()
    )
    for (shop_id,) in shop_rows:
        existing_rows = conn.execute(
            sa.text(
                "SELECT invoice_number FROM sales "
                "WHERE shop_id = :shop_id AND invoice_number IS NOT NULL"
            ),
            {"shop_id": str(shop_id)},
        ).fetchall()
        existing = {row[0] for row in existing_rows if row[0]}

        max_seq = 0
        for inv in existing:
            m = _INV_RE.match(inv or "")
            if m:
                try:
                    max_seq = max(max_seq, int(m.group(1)))
                except ValueError:
                    pass

        null_rows = list(
            conn.execute(
                sa.text(
                    "SELECT id FROM sales "
                    "WHERE shop_id = :shop_id AND invoice_number IS NULL "
                    "ORDER BY created_at, id"
                ),
                {"shop_id": str(shop_id)},
            ).fetchall()
        )

        seq = max_seq + 1 if max_seq else 1
        # Skip any hand-typed numbers that already occupy the sequence.
        while _next_candidate(seq) in existing:
            seq += 1

        for (sale_id,) in null_rows:
            while _next_candidate(seq) in existing:
                seq += 1
            candidate = _next_candidate(seq)
            conn.execute(
                sa.text("UPDATE sales SET invoice_number = :inv WHERE id = :id"),
                {"inv": candidate, "id": str(sale_id)},
            )
            existing.add(candidate)
            seq += 1

        conn.execute(
            sa.text("UPDATE shops SET sale_invoice_seq = :seq WHERE id = :id"),
            {"seq": seq, "id": str(shop_id)},
        )


def downgrade() -> None:
    op.drop_column("shops", "sale_invoice_seq")

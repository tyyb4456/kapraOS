"""supplier payables integrity and indexes

Step 7 (supplier Khata / payables) needs **no new tables** - `suppliers`,
`purchases` and `payments` from Steps 4-5 already hold everything a balance or
statement is derived from. This migration only tightens integrity and supports
the new access patterns, exactly as Step 6 did for receivables:

1. A payment that names both a supplier and a purchase must name the *same*
   supplier's purchase. The existing `(purchase_id, shop_id)` and
   `(supplier_id, shop_id)` composite foreign keys each guard the tenant, but
   neither stops Shop A recording a payment against Supplier B's purchase -
   which would move money between two suppliers' Khatas. The new
   `(purchase_id, supplier_id)` foreign key closes that, enforced by the
   database rather than the service layer. It needs a supporting
   `UNIQUE(id, supplier_id)` on `purchases`.

   Postgres's default MATCH SIMPLE is exactly the behaviour wanted here: the
   check is skipped whenever either column is NULL, so an unallocated supplier
   payment (`purchase_id IS NULL`) is still valid - while a payment claiming a
   supplier *and* a purchase must agree with the purchase's own supplier.

2. `purchases` gains the `(shop_id, supplier_id, created_at)` index; the
   supplier Khata statement reads purchases by (shop, supplier) in
   chronological order.

3. `payments` gains the matching `(shop_id, supplier_id, created_at)` index and
   a `(shop_id, purchase_id)` lookup index. `(shop_id, sale_id)` already exists
   (`ix_payments_shop_sale_id`) and is not duplicated.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-16 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. a supplier's payment can only point at that supplier's purchase --
    op.create_unique_constraint(
        "uq_purchases_id_supplier_id",
        "purchases",
        ["id", "supplier_id"],
    )
    op.create_foreign_key(
        "fk_payments_purchase_same_supplier",
        "payments",
        "purchases",
        ["purchase_id", "supplier_id"],
        ["id", "supplier_id"],
        # CASCADE matches `fk_payments_purchase_same_shop`: deleting a purchase
        # takes its payments with it.
        ondelete="CASCADE",
    )

    # --- 2/3. payables access patterns ------------------------------------
    op.create_index(
        "ix_purchases_shop_supplier_created_at",
        "purchases",
        ["shop_id", "supplier_id", "created_at"],
    )
    op.create_index(
        "ix_payments_shop_purchase_id",
        "payments",
        ["shop_id", "purchase_id"],
    )
    op.create_index(
        "ix_payments_shop_supplier_created_at",
        "payments",
        ["shop_id", "supplier_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_payments_shop_supplier_created_at", table_name="payments")
    op.drop_index("ix_payments_shop_purchase_id", table_name="payments")
    op.drop_index(
        "ix_purchases_shop_supplier_created_at", table_name="purchases"
    )

    op.drop_constraint(
        "fk_payments_purchase_same_supplier", "payments", type_="foreignkey"
    )
    op.drop_constraint("uq_purchases_id_supplier_id", "purchases", type_="unique")
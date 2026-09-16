"""customer receivables integrity and indexes

Step 6 (customer Khata / receivables) needs **no new tables** - `customers`,
`sales` and `payments` from Step 5 already hold everything a balance or
statement is derived from. This migration only tightens integrity and supports
the new access patterns:

1. A payment that names both a customer and a sale must name the *same*
   customer's sale. The existing `(sale_id, shop_id)` and `(customer_id,
   shop_id)` composite foreign keys each guard the tenant, but neither stops
   Shop A recording Ahmed's payment against Bilal's invoice - which would move
   money between two customers' Khatas. The new `(sale_id, customer_id)`
   foreign key closes that, enforced by the database rather than the service
   layer, as everywhere else in this schema. It needs a supporting
   `UNIQUE(id, customer_id)` on `sales`.

   Postgres's default MATCH SIMPLE is exactly the behaviour wanted here: the
   check is skipped whenever either column is NULL, so an unallocated customer
   payment (`sale_id IS NULL`) and a walk-in sale's payment (`customer_id IS
   NULL`) are both still valid - while a payment claiming a customer *and* a
   sale must agree with the sale's own customer. The existing tenant guards are
   untouched and keep working for those NULL cases.

2. `ix_sales_shop_customer_id` is widened to include `created_at`: the Khata
   statement reads sales by (shop, customer) in chronological order. The old
   index is a strict prefix of the new one, so nothing loses coverage.

3. `payments` gains the matching `(shop_id, customer_id, created_at)` index.
   `(shop_id, sale_id)` already exists (`ix_payments_shop_sale_id`) and is not
   duplicated.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. a customer's payment can only point at that customer's sale ----
    op.create_unique_constraint(
        "uq_sales_id_customer_id",
        "sales",
        ["id", "customer_id"],
    )
    op.create_foreign_key(
        "fk_payments_sale_same_customer",
        "payments",
        "sales",
        ["sale_id", "customer_id"],
        ["id", "customer_id"],
        # CASCADE matches `fk_payments_sale_same_shop`: deleting a sale takes
        # its payments with it.
        ondelete="CASCADE",
    )

    # --- 2/3. receivables access patterns ---------------------------------
    op.drop_index("ix_sales_shop_customer_id", table_name="sales")
    op.create_index(
        "ix_sales_shop_customer_created_at",
        "sales",
        ["shop_id", "customer_id", "created_at"],
    )
    op.create_index(
        "ix_payments_shop_customer_created_at",
        "payments",
        ["shop_id", "customer_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payments_shop_customer_created_at", table_name="payments"
    )
    op.drop_index("ix_sales_shop_customer_created_at", table_name="sales")
    op.create_index(
        "ix_sales_shop_customer_id", "sales", ["shop_id", "customer_id"]
    )

    op.drop_constraint(
        "fk_payments_sale_same_customer", "payments", type_="foreignkey"
    )
    op.drop_constraint("uq_sales_id_customer_id", "sales", type_="unique")
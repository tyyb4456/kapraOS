"""allow platform administrators without a shop

A KapraOS platform operator does not need to own or operate a shop to run
platform administration. ``users.shop_id`` becomes nullable so a platform
administrator identity can exist without a tenant.

This does not change ordinary onboarding: ``POST /auth/sync`` still
provisions a fresh ``Shop`` + ``OWNER`` user for every genuinely new Clerk
identity, and all tenant-scoped endpoints keep deriving their shop from
``users.shop_id`` server-side (they now reject shop-less identities with
403 instead of relying on the NOT NULL constraint).

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "users",
        "shop_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema.

    Fails loudly if shop-less users exist - an operator must first bind
    them to a tenant (or remove them) before the NOT NULL constraint can
    be restored.
    """
    op.alter_column(
        "users",
        "shop_id",
        existing_type=sa.UUID(),
        nullable=False,
    )

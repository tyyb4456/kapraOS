"""add shop settings fields (tax_id, default_unit)

Revision ID: 0012
Revises: 048d640d6862
Create Date: 2026-09-24 00:00:00.000000

The Settings page needs persistence for the NTN/Tax ID and the primary
fabric unit. Both are shop-scoped columns so tenant isolation is automatic.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '048d640d6862'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('shops', sa.Column('tax_id', sa.String(length=20), nullable=True))
    op.add_column(
        'shops',
        sa.Column(
            'default_unit',
            sa.String(length=20),
            nullable=False,
            server_default='meters',
        ),
    )


def downgrade() -> None:
    op.drop_column('shops', 'default_unit')
    op.drop_column('shops', 'tax_id')

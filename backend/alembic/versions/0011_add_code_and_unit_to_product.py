"""add_code_and_unit_to_product

Revision ID: 0011
Revises: 9f632c58caae
Create Date: 2026-09-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '9f632c58caae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('products', sa.Column('code', sa.String(length=50), nullable=True))
    op.add_column('products', sa.Column('unit', sa.String(length=20), nullable=False, server_default='meter'))
    op.create_index('uq_products_code_shop', 'products', ['code', 'shop_id'], unique=True, postgresql_where=sa.text('code IS NOT NULL'))


def downgrade() -> None:
    op.drop_index('uq_products_code_shop', table_name='products')
    op.drop_column('products', 'unit')
    op.drop_column('products', 'code')

"""Add discount codes for course purchases

Creates the ``discount_code`` table and adds discount bookkeeping columns
(``discount_code_id``, ``original_amount``, ``discount_amount``) to both gateway
payment tables. ``amount`` on a payment remains the final amount charged.

Revision ID: q0r1s2t3u4v5
Revises: p9q0r1s2t3u4
Create Date: 2026-11-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = 'q0r1s2t3u4v5'
down_revision: Union[str, None] = 'p9q0r1s2t3u4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PAYMENT_TABLES = ('irankish_payment', 'zarinpal_payment')


def upgrade() -> None:
    op.create_table(
        'discount_code',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organization.id', ondelete='CASCADE'), nullable=False),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('course.id', ondelete='CASCADE'), nullable=True),
        sa.Column('code', sa.String(64), nullable=False),
        sa.Column('percent_off', sa.Integer(), nullable=True),
        sa.Column('amount_off', sa.Integer(), nullable=True),
        sa.Column('max_redemptions', sa.Integer(), nullable=True),
        sa.Column('redemption_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('expires_at', sa.String(40), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('creation_date', sa.String(), nullable=True),
        sa.Column('update_date', sa.String(), nullable=True),
        sa.UniqueConstraint('org_id', 'code', name='uq_discount_code_org_code'),
    )
    op.create_index('ix_discount_code_org_id', 'discount_code', ['org_id'])
    op.create_index('ix_discount_code_course_id', 'discount_code', ['course_id'])

    for table in PAYMENT_TABLES:
        op.add_column(table, sa.Column('discount_code_id', sa.Integer(), nullable=True))
        op.add_column(table, sa.Column('original_amount', sa.Integer(), nullable=True))
        op.add_column(table, sa.Column('discount_amount', sa.Integer(), nullable=True))
        op.create_foreign_key(
            f'fk_{table}_discount_code_id',
            table,
            'discount_code',
            ['discount_code_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    for table in PAYMENT_TABLES:
        op.drop_constraint(f'fk_{table}_discount_code_id', table, type_='foreignkey')
        op.drop_column(table, 'discount_amount')
        op.drop_column(table, 'original_amount')
        op.drop_column(table, 'discount_code_id')

    op.drop_index('ix_discount_code_course_id', table_name='discount_code')
    op.drop_index('ix_discount_code_org_id', table_name='discount_code')
    op.drop_table('discount_code')

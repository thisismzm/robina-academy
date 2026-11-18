"""Add invite_code_usage for per-code signup statistics

Revision ID: t3u4v5w6x7y8
Revises: s2t3u4v5w6x7
Create Date: 2026-11-07 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 't3u4v5w6x7y8'
down_revision: Union[str, None] = 's2t3u4v5w6x7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: safe on a schema already built by create_all.
    inspector = sa.inspect(op.get_bind())
    if 'invite_code_usage' in inspector.get_table_names():
        return
    op.create_table(
        'invite_code_usage',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('org_id', sa.BigInteger(), sa.ForeignKey('organization.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('invite_code_uuid', sa.String(length=128), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('usergroup_id', sa.Integer(), nullable=True),
        sa.Column('signed_up_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('org_id', 'user_id', name='uq_invite_code_usage_org_user'),
    )
    op.create_index('ix_invite_code_usage_org_code', 'invite_code_usage', ['org_id', 'invite_code_uuid'])
    op.create_index('ix_invite_code_usage_org_date', 'invite_code_usage', ['org_id', 'signed_up_at'])


def downgrade() -> None:
    op.drop_index('ix_invite_code_usage_org_date', table_name='invite_code_usage')
    op.drop_index('ix_invite_code_usage_org_code', table_name='invite_code_usage')
    op.drop_table('invite_code_usage')

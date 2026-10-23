"""Add course requires_purchase field

Adds a boolean flag to the course table marking a course as purchasable:
the course remains visible for preview (listing + course page) to anyone who
can read it, but enrolling requires a completed purchase. Viewing is
unaffected; only enrollment is gated.

Revision ID: p9q0r1s2t3u4
Revises: h2i3j4k5l6m7
Create Date: 2026-10-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa  # noqa: F401
import sqlmodel  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = 'p9q0r1s2t3u4'
down_revision: Union[str, None] = 'h2i3j4k5l6m7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add requires_purchase column to course table, defaulting to False so all
    # existing courses keep their current (free) enrollment behavior.
    op.add_column(
        'course',
        sa.Column('requires_purchase', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade() -> None:
    op.drop_column('course', 'requires_purchase')

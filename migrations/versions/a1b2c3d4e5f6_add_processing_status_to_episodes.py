"""add_processing_status_to_episodes

Revision ID: a1b2c3d4e5f6
Revises: f4733de23f04
Create Date: 2026-06-14 15:25:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f4733de23f04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'episodes',
        sa.Column(
            'processingStatus',
            sa.Text(),
            nullable=False,
            server_default='pending',
        ),
    )
    op.create_index(
        'ix_episodes_processing_status',
        'episodes',
        ['processingStatus'],
    )


def downgrade() -> None:
    op.drop_index('ix_episodes_processing_status', table_name='episodes')
    op.drop_column('episodes', 'processingStatus')

"""add_batch_jobs_table

Revision ID: f4733de23f04
Revises: 
Create Date: 2026-06-14 14:58:31.499163

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f4733de23f04'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'batch_jobs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('batch_number', sa.UUID(), nullable=False),
        sa.Column('person_name', sa.Text(), nullable=False),
        sa.Column('name_variations', sa.Text(), nullable=True),
        sa.Column('youtube_api_key', sa.Text(), nullable=False),
        sa.Column('listen_notes_api_key', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='queued'),
        sa.Column('total_episodes', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('processed', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('skipped', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('errors', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('results', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('completed_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_batch_jobs_batch_number', 'batch_jobs', ['batch_number'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_batch_jobs_batch_number', table_name='batch_jobs')
    op.drop_table('batch_jobs')

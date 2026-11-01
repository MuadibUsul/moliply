"""add vcc pool reservation columns

Revision ID: a5f639b206dc
Revises: 2a8f730946fe
Create Date: 2026-11-01 00:12:26.501198
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a5f639b206dc'
down_revision: Union[str, None] = '2a8f730946fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One column per batch context. SQLite batch mode rebuilds the whole table
    # and cannot order many column changes inside a single batch.
    columns = [
        sa.Column('reserved_balance', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0'),
        sa.Column('daily_limit', sa.Integer(), nullable=True),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cooldown_until', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(length=256), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('is_locked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('locked_at', sa.DateTime(), nullable=True),
        sa.Column('total_used_count', sa.Integer(), nullable=False, server_default='0'),
    ]
    for column in columns:
        with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
            batch_op.add_column(column)


def downgrade() -> None:
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.drop_column('total_used_count')
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.drop_column('locked_at')
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.drop_column('is_locked')
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.drop_column('last_used_at')
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.drop_column('last_error')
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.drop_column('cooldown_until')
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.drop_column('consecutive_failures')
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.drop_column('daily_limit')
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.drop_column('reserved_balance')

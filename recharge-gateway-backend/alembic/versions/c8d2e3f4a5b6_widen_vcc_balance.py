"""widen vcc balance columns to NUMERIC(12,2)

Revision ID: c8d2e3f4a5b6
Revises: b7c1d2e3f4a5
Create Date: 2026-11-01 01:35:00

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c8d2e3f4a5b6'
down_revision: Union[str, None] = 'b7c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One ALTER per batch: SQLite table rebuilds impose an ordering between
    # two column changes in the same batch and fail with CircularDependencyError.
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.alter_column('available_balance', existing_type=sa.Numeric(10, 2), type_=sa.Numeric(12, 2), existing_nullable=False)
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.alter_column('reserved_balance', existing_type=sa.Numeric(10, 2), type_=sa.Numeric(12, 2), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.alter_column('available_balance', existing_type=sa.Numeric(12, 2), type_=sa.Numeric(10, 2), existing_nullable=False)
        batch_op.alter_column('reserved_balance', existing_type=sa.Numeric(12, 2), type_=sa.Numeric(10, 2), existing_nullable=False)

"""add vcc card fingerprint for duplicate detection

Revision ID: b7c1d2e3f4a5
Revises: a5f639b206dc
Create Date: 2026-11-01 01:20:00

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, None] = 'a5f639b206dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.add_column(sa.Column('card_fingerprint', sa.String(length=64), nullable=True))
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vcc_cards_card_fingerprint'), ['card_fingerprint'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('vcc_cards', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vcc_cards_card_fingerprint'))
        batch_op.drop_column('card_fingerprint')

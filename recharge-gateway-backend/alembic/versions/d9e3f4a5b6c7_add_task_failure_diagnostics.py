"""add failure diagnostics to recharge tasks

Revision ID: d9e3f4a5b6c7
Revises: c8d2e3f4a5b6
Create Date: 2026-11-06 13:10:00

``error_detail`` keeps the text the checkout page showed; ``evidence_path``
points at a screenshot. Both are operator-only, so ``error_message`` (which the
customer sees) is left untouched.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d9e3f4a5b6c7"
down_revision: Union[str, None] = "c8d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("recharge_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("error_detail", sa.String(length=1024), nullable=True))
    with op.batch_alter_table("recharge_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("evidence_path", sa.String(length=512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recharge_tasks", schema=None) as batch_op:
        batch_op.drop_column("evidence_path")
    with op.batch_alter_table("recharge_tasks", schema=None) as batch_op:
        batch_op.drop_column("error_detail")

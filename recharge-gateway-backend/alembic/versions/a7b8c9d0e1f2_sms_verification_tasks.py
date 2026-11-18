"""self-service SMS verification tasks

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-11-18 19:20:00

One row per leased number. The activation code stays ``LOCKED_WAITING_OTP``
while the task is ``WAITING`` and is only consumed when an OTP arrives, so the
new status value is additive to an existing ``VARCHAR`` column — no rewrite.

``expires_at`` is the 120-second receive deadline the poller and the cleanup
sweep both compare against.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sms_verification_tasks",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("code_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("upstream_order_id", sa.String(length=64), nullable=True),
        sa.Column("service_name", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("country_code", sa.String(length=4), nullable=False, server_default="US"),
        sa.Column("phone_number", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("otp_code", sa.String(length=8), nullable=True),
        sa.Column("raw_sms", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="WAITING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(length=256), nullable=True),
        sa.Column("client_ip", sa.String(length=45), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["code_id"], ["activation_codes.id"]),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index("ix_sms_verification_tasks_code_id", "sms_verification_tasks", ["code_id"])
    op.create_index("ix_sms_verification_tasks_status", "sms_verification_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sms_verification_tasks_status", table_name="sms_verification_tasks")
    op.drop_index("ix_sms_verification_tasks_code_id", table_name="sms_verification_tasks")
    op.drop_table("sms_verification_tasks")

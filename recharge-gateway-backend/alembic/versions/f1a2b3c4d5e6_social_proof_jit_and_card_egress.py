"""storefront social proof, JIT fulfilment and per-card egress

Revision ID: f1a2b3c4d5e6
Revises: e9f4a5b6c7d8
Create Date: 2026-11-18 09:30:00

Three changes ship together because they are one commercial release:

* ``products.base_sales_count`` — a per-product social-proof offset. The
  storefront shows ``base_sales_count + sold_count`` so a brand new shop does
  not read as "0 sold" everywhere.
* ``products.fulfillment_strategy`` plus the ``free_account_pool`` and
  ``jit_account_jobs`` tables — finished accounts are upgraded only after the
  order is paid instead of ageing in inventory.
* ``vcc_cards.proxy_url`` / ``timezone_id`` / ``locale`` — the residential exit
  is bound to the card, so card domicile, billing address and IP cannot drift
  apart between workers.

Every added column has a server default, so existing rows stay valid.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e9f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("base_sales_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column(
                "fulfillment_strategy",
                sa.String(length=32),
                nullable=False,
                server_default="STOCK",
            )
        )

    with op.batch_alter_table("vcc_cards", schema=None) as batch_op:
        batch_op.add_column(sa.Column("proxy_url", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("timezone_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("locale", sa.String(length=16), nullable=True))

    op.create_table(
        "free_account_pool",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=160), nullable=False),
        sa.Column("password_enc", sa.Text(), nullable=False),
        sa.Column("recovery_enc", sa.Text(), nullable=True),
        sa.Column("session_raw_enc", sa.Text(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="AVAILABLE"),
        sa.Column("order_item_id", sa.Integer(), nullable=True),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("sold_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_free_account_pool_product_id", "free_account_pool", ["product_id"])
    op.create_index("ix_free_account_pool_email", "free_account_pool", ["email"])
    op.create_index("ix_free_account_pool_status", "free_account_pool", ["status"])
    op.create_index("ix_free_account_pool_order_item_id", "free_account_pool", ["order_item_id"])

    op.create_table(
        "jit_account_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=False),
        sa.Column("free_account_id", sa.Integer(), nullable=True),
        sa.Column("recharge_task_id", sa.String(length=36), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["free_account_id"], ["free_account_pool.id"]),
        sa.ForeignKeyConstraint(["recharge_task_id"], ["recharge_tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jit_account_jobs_order_item_id", "jit_account_jobs", ["order_item_id"])
    op.create_index("ix_jit_account_jobs_state", "jit_account_jobs", ["state"])


def downgrade() -> None:
    op.drop_index("ix_jit_account_jobs_state", table_name="jit_account_jobs")
    op.drop_index("ix_jit_account_jobs_order_item_id", table_name="jit_account_jobs")
    op.drop_table("jit_account_jobs")
    op.drop_index("ix_free_account_pool_order_item_id", table_name="free_account_pool")
    op.drop_index("ix_free_account_pool_status", table_name="free_account_pool")
    op.drop_index("ix_free_account_pool_email", table_name="free_account_pool")
    op.drop_index("ix_free_account_pool_product_id", table_name="free_account_pool")
    op.drop_table("free_account_pool")
    with op.batch_alter_table("vcc_cards", schema=None) as batch_op:
        batch_op.drop_column("locale")
        batch_op.drop_column("timezone_id")
        batch_op.drop_column("proxy_url")
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_column("fulfillment_strategy")
        batch_op.drop_column("base_sales_count")

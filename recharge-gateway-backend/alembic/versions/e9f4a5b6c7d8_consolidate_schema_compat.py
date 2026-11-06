"""consolidate the ad-hoc schema compatibility backfill

Revision ID: e9f4a5b6c7d8
Revises: d9e3f4a5b6c7
Create Date: 2026-11-06 13:40:00

``ensure_schema_compatibility()`` existed because installs predating Alembic had
columns added by hand. Every step is inspector-guarded, so this migration is
safe to run on a fresh database (where the columns already exist) and on a
legacy one that is stamped at the baseline. Once it is at head the Python helper
has nothing left to do and production can run migrations strictly.

PostgreSQL column widening is included because the old helper did it at boot;
SQLite ignores the type change, which is fine — it is dynamically typed.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9f4a5b6c7d8"
down_revision: Union[str, None] = "d9e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine], ...] = (
    ("orders", "user_id", sa.Integer()),
    ("orders", "payment_amount", sa.Numeric(24, 8)),
    ("orders", "payment_currency", sa.String(length=8)),
    ("orders", "payment_address", sa.String(length=96)),
    ("orders", "payment_network", sa.String(length=16)),
    ("activation_codes", "preferred_region", sa.String(length=16)),
    ("recharge_tasks", "executed_channel", sa.String(length=16)),
    ("recharge_tasks", "executed_region", sa.String(length=4)),
    ("recharge_tasks", "billed_currency", sa.String(length=8)),
    ("recharge_tasks", "billed_amount", sa.Numeric(10, 2)),
    ("recharge_tasks", "fallback_reason", sa.String(length=256)),
    ("vcc_cards", "card_fingerprint", sa.String(length=64)),
    ("vcc_cards", "ph_billing_address_json", sa.JSON()),
    ("vcc_cards", "three_ds_email_account", sa.String(length=128)),
    ("vcc_cards", "reserved_balance", sa.Numeric(12, 2)),
    ("vcc_cards", "daily_limit", sa.Integer()),
    ("vcc_cards", "consecutive_failures", sa.Integer()),
    ("vcc_cards", "cooldown_until", sa.DateTime()),
    ("vcc_cards", "last_error", sa.String(length=256)),
    ("vcc_cards", "last_used_at", sa.DateTime()),
    ("vcc_cards", "is_locked", sa.Boolean()),
    ("vcc_cards", "locked_at", sa.DateTime()),
    ("vcc_cards", "total_used_count", sa.Integer()),
)

DEFAULTS = {
    ("activation_codes", "preferred_region"): "'PH_FIRST'",
    ("vcc_cards", "reserved_balance"): "0",
    ("vcc_cards", "consecutive_failures"): "0",
    ("vcc_cards", "is_locked"): "FALSE",
    ("vcc_cards", "total_used_count"): "0",
}


def _existing(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    seen: dict[str, set[str]] = {}
    for table, column, type_ in COLUMNS:
        if table not in seen:
            seen[table] = _existing(table)
        if not seen[table] or column in seen[table]:
            continue
        default = DEFAULTS.get((table, column))
        nullable = default is None
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    column,
                    type_,
                    nullable=nullable,
                    server_default=sa.text(default) if default is not None else None,
                )
            )
        seen[table].add(column)

    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("ALTER TABLE orders ALTER COLUMN payment_amount TYPE NUMERIC(24,8)"))
        connection.execute(sa.text("ALTER TABLE payment_events ALTER COLUMN amount TYPE NUMERIC(24,8)"))

    if "vcc_cards" in _existing("vcc_cards") or sa.inspect(connection).has_table("vcc_cards"):
        # Earlier builds stored Python ``None`` as JSON null; PH scheduling
        # filters on SQL NULL, so normalise the stragglers.
        connection.execute(
            sa.text(
                "UPDATE vcc_cards SET ph_billing_address_json = NULL "
                "WHERE ph_billing_address_json IS NOT NULL "
                "AND CAST(ph_billing_address_json AS TEXT) = 'null'"
            )
        )


def downgrade() -> None:
    # Columns are additive and still used by the app; dropping them would lose
    # data that no later revision can restore. Keep the table shape.
    pass
